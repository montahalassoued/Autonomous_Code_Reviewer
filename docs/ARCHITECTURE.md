# Architecture

**File purpose:** the target design of the reviewer: components, workflows, the code index schema, context assembly and failure handling.
**Status:** target design (2026-10-14). The code today gives the agents the diff plus read-only tools (see [RAG.md](RAG.md)); the semantic retrieval was removed; [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) gives the order of work and [PROGRESS.md](PROGRESS.md) what is done.

## Core idea

The diff is the input under review. **Context** is what the agents need from the rest of the repo to judge it: who calls the changed code, what it calls, which tests cover it, the module-level code around it. That relevance comes from **code structure**, not from embedding similarity, so context is built deterministically from a structural index of `main`. Embeddings are optional and only answer "is there similar code?" (duplicates, project conventions).

## Components

| Component | Role | Where |
| --- | --- | --- |
| Webhook API | Verify the HMAC signature, apply event rules, insert the `reviews` row (dedupe), enqueue a job, answer 202 | `app/api/webhook.py`, `app/main.py` |
| Job queue | Durable hand-off between API and worker (Azure Storage Queue, visibility timeout, poison queue) | `app/jobs/` |
| Worker | Runs one review at a time, then updates the index on pushes to `main` | `app/worker.py` |
| Code index | Postgres: a derived, structural map of `main` (symbols, edges, reviews, findings). Git stays the source of truth | `db/` (schema + migration runner; only `repos` and `reviews` are written today) |
| Indexer | tree-sitter parse, symbol extraction, call resolution, test linking. No LLM | `rag/` (`symbol_extractor`, `call_resolver`, `code_indexer`, `index_writer`) |
| Analysis | Triage, symbol diff, context builder | `analysis/` (planned) |
| Review graph | LangGraph: build_context → optional context subagent → 3 agents → synthesizer | `review/` (`review/agents/` for the 3 agents) |
| GitHub client | Diffs, PR metadata, inline review, summary upsert, commit comments | `tools/github_tools.py` |

## Flow

```text
GitHub push / pull_request
        │
        ▼
 Webhook API ── verify HMAC ── event rules ── INSERT reviews(queued) ── enqueue (≈60 s delay) ── 202
        │                                        (unique repo,target,head_sha = dedupe)
        ▼
 Azure Storage Queue ──(visibility 900 s, 3 attempts → poison)──► Worker
                                                                   │
   1 sync index to `before` if indexed_sha ≠ before                │
   2 git diff before..after (name-status + hunks)                  │
   3 triage ──(docs / format / lockfile only)──► reviews.skipped ──┤
   4 symbol diff (parsed "before" vs parsed "after", from git)    │
   5 build context (Postgres + in-memory overlay of new code)      │
   6 context subagent (only if gaps, ≤ 6 tool calls)               │
   7 Security │ Logic │ Quality  (parallel)                        │
   8 synthesizer (group, drop already-posted fingerprints)         │
   9 post to GitHub, store review + findings                       │
  10 main only: apply symbol diff in one transaction, indexed_sha = after
  11 delete the queue message                                      ▼
                                                            GitHub comments
```

## Triggers

| Event | Behaviour |
| --- | --- |
| First push / empty repo (`before` = `000…`) | Full index, review row `skipped` (`initial_import`). No LLM calls |
| Push to `main` | Review `before...after`, post as commit comments, then update the index |
| Push to a branch with an open PR | Ignored: the PR event reviews it |
| PR `opened` / `synchronize` | Diff from the merge-base. PR title, description and commit messages are inputs, so agents can judge whether the change matches its stated intent. Inline comments + one summary edited in place; later commits post only new findings. PR code is never written to the index |
| Docs-only, formatting-only, lockfile-only | `skipped` by rule-based triage, zero LLM calls |
| Rapid pushes | Coalesced: jobs are enqueued with a ~60 s visibility delay and the latest push reviews the whole range |

## Workflow details

**Push (worker steps 1–11 above):**

1. If `repos.indexed_sha ≠ before`, sync the index to `before` first, so the context (callers, callees, tests) reflects the code before the push.
2. `git diff --name-status before..after`, plus hunks.
3. Triage (rules only).
4. **Symbol diff:** for each changed file, parse both sides from git (`git show before:path` and `git show after:path`) and match symbols by `qualified_name`. Postgres is used for context only:
   - `UNCHANGED` (same `content_hash`), `FORMATTING_ONLY` (same `norm_hash`), `MODIFIED`, `ADDED`, `DELETED`
   - `RENAMED`: a DELETED + ADDED pair with the same or near `norm_hash`
   - signature changes are flagged; hunks are mapped to symbol line ranges.
5. Build context (next section). New symbols' edges are computed on the fly in an in-memory overlay, never stored.
6. Optional context subagent.
7. Three agents in parallel: Security, Logic, Quality.
8. Synthesizer: group findings by category, drop findings whose fingerprint was already posted, sanitize the Markdown.
9. Post; store the `reviews` row and its `findings`.
10. `main` only: apply the symbol diff to Postgres in **one transaction** and set `indexed_sha = after`. The review runs before the index update, so it sees the old code as "before".
11. Delete the queue message.

**Pull request:** the same steps, except the base is the merge-base, step 10 never runs (the index mirrors `main` only), and output is an inline review plus the summary comment found by its `<!-- acr:summary -->` marker.

## Code index (Postgres)

The index is derived from `main` and can always be rebuilt from git. Raw files are not stored, except the plain text of non-Python files, kept for grep.

```text
repos(id, full_name, default_branch, indexed_sha, schema_version)   both NULL until the first index
files(id, repo_id, path, language, content_hash, content NULL for Python, parse_error)
symbols(id, file_id, name, qualified_name, kind[function|method|class_header|module], parent_id,
        signature, start_line, end_line, code, content_hash)
        UNIQUE (file_id, qualified_name), INDEX (name), INDEX (parent_id)
edges(src_id, kind[calls|imports|inherits|tested_by], dst_id)          ON DELETE CASCADE
        PK (src_id, kind, dst_id), INDEX (dst_id, kind); tested_by: src = code, dst = test
unresolved_calls(src_id, name, reason[not_found|ambiguous|dynamic])    no external library calls
symbol_embeddings(symbol_id, embedding vector)                          optional, pgvector, later migration
reviews(id, repo_id, target[pr:<n>|push:<branch>], base_sha, head_sha,
        status[queued|running|posted|skipped|failed], status_reason, missing_categories,
        llm_calls, tokens_in, tokens_out, latency_ms, created_at, updated_at (trigger))
        UNIQUE (repo_id, target, head_sha)            -- the webhook dedupe key
findings(id, review_id, category[security|logic|quality], severity, rule_id, confidence,
         file_path, line_number, qualified_name, title, description, evidence, suggestion,
         fingerprint, github_comment_id)              INDEX (review_id, fingerprint)
```

The call graph is the `edges` table. NetworkX is used only ad hoc when a graph algorithm is needed.

**Indexing (no LLM):**

- tree-sitter extracts functions, methods as `Class.method` (with decorators), class headers without method bodies, and one `<module>` pseudo-symbol for top-level code. It does not recurse into function bodies.
- `content_hash` is the hash of the code, stored in `symbols`; `norm_hash` is the hash of the code without comments, docstrings and whitespace, computed in memory by the symbol diff (not stored).
- Calls are matched on the bare `symbols.name` (a call site only knows `login(...)`) and resolve in this order: `self`/`cls` method (including base classes) → imported name → same file → name unique in the repo → otherwise a row in `unresolved_calls`. Never guess.
- Tests are linked with `tested_by` edges (`src` = the code under test, `dst` = the test). Import edges start from the importing file's `<module>` symbol.
- A full index runs only on first install or a `schema_version` change; otherwise it is incremental by `content_hash`.

## Context assembly (deterministic)

Candidates are ranked by priority and added until the token budget (~12k) is full:

1. Changed code, full and line-numbered, with its class header. Never truncated; if too large, the review is split per file.
2. Callers of symbols whose signature changed or that were deleted.
3. Other callers.
4. Callees.
5. Tests.
6. Changed module-level code, plus a grep of affected names in config and non-Python files. This is also where router-level auth dependencies and middleware show up for the security agent.
7. Similar code, only if pgvector is enabled and budget remains.

Long symbols are truncated to signature + first lines + the call site. Each block carries a one-line reason ("caller of login(), signature changes"). Anything that can't be resolved is listed under **Not verifiable**. Untrusted content (diff, PR text, repo code) is wrapped in nonce delimiters.

## Context subagent (optional, bounded)

- **Runs only when gaps exist:** unresolved calls touched by the change, a public symbol deleted or renamed, or a change spanning several modules. One shared run serves all 3 agents.
- **Tools (read-only, over Postgres):** `get_symbol`, `get_callers`, `get_callees`, `get_tests`, `grep`, `similar`. At most 6 tool calls.
- **Output:** JSON `{selected: [{id, reason}], gaps: [...]}` with symbol ids only, never paraphrased code. The deterministic builder fetches the exact code for those ids.
- **Invalid output** is ignored and the structural context is used as is.
- Implemented as a LangGraph subgraph between `build_context` and the agents.

## Failure handling

| Failure | Result |
| --- | --- |
| Worker crash mid-review | Message reappears after the visibility timeout (900 s) |
| 3 failed attempts | Message moves to the poison queue, review row `failed` |
| Context subagent fails | Continue with the structural context |
| GitHub 422 on an inline comment | Post the summary only |
| Index update fails | Transaction rolls back; the next push resyncs (step 1) |

## Observability

Langfuse traces every LLM call (one trace per review). `reviews` stores per-review calls, tokens and latency, so cost is queryable without the tracing backend.

## Open points: proposed resolutions

Proposed by the ai-engineer review (2026-10-14). Each must be confirmed by the milestone named in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

| Open point | Proposed resolution | Decide by |
| --- | --- | --- |
| PR "before" side (the index holds `main` at `indexed_sha`, a PR is diffed from its merge-base) | Always parse both sides from git (`git show base:path`), for PRs and pushes alike. Postgres is used for context only, so the symbol diff never depends on index state | Structural indexer |
| Push to a branch with no open PR | Ignore it and record the review as `skipped` (`no_pr`) | Push reviews |
| Coalescing mechanics | When a message is received, if a newer `queued` row exists in `reviews` for the same target, mark this one `skipped` (`superseded`). `before` = the last posted `head_sha` for the target (or `indexed_sha` on `main`) | Push reviews |
| "Near" `norm_hash` for renames | MVP: a rename is DELETED + ADDED. Later: `difflib` ratio >= 0.9 on normalized code, only between DELETED/ADDED pairs | Structural indexer (MVP), incremental sync |
| Fingerprint definition | `sha1(category + file + qualified_name + rule_id)`, with `rule_id` from a closed list per category. Never the LLM-written title, which changes between runs | 3 agents (schema), inline review |
| Postgres hosting | Local: `pgvector/pgvector:pg16` in docker compose. Azure: Flexible Server, Burstable tier (price to check). pgvector off until "similar code" is enabled | Postgres index |
| Multi-repo | One repo for now, but `repo_id` in every query and an `ALLOWED_REPOS` guard | Inline review |

## Known risks

- **Incoming edges lost by the cascade:** deleting and re-inserting a file's symbols cascades away the edges that point *into* them from other files. The sync must upsert on `(file_id, qualified_name)` so symbol ids survive, then re-resolve the files that import the changed module.
- **Call resolution accuracy:** dynamic Python (`Depends(...)`, dependency injection, `getattr`) escapes static resolution. The indexer publishes its resolution rate and edge precision on the eval fixture, never guesses, and lists the rest under "Not verifiable".
- **Small eval dataset:** with ~15 cases one case is worth ~0.067, so results are compared case by case (a gap of at least 2 cases, stable over 3 runs), not by a 0.02 threshold.
- **LLM rate limits:** 3 agents x ~12k tokens in parallel can hit Groq's free-tier tokens-per-minute limit. The shared LLM client caps concurrency, backs off on 429 and can fall back to Gemini.
- **GitHub API:** a 422 on a line outside the diff and secondary rate limits. One "create review" request with all comments, lines checked against the patch, a cap on inline comments, and a summary-only fallback.
