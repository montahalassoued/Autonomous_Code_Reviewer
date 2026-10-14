# Implementation plan: reviewer redesign

**File purpose:** the phased plan for turning the current pipeline into the target design.
**Inputs:** the user's confirmed vision (2026-10-13), the structural-context redesign (2026-10-14) and a code review by the ai-engineer subagent.
**Outputs:** the order of work, the files for each phase, the tests and the acceptance criteria.

## Target design (confirmed by the user)

Full description: [ARCHITECTURE.md](ARCHITECTURE.md). In short:

1. **Triggers**
   - First push / empty repo: index everything, review `skipped` (initial import).
   - Push to `main`: review `before...after` as commit comments, then update the index.
   - Push to a branch with an open PR: ignored (reviewed on the PR).
   - PR `opened` / `synchronize`: diff from the merge-base; PR title, description and commit messages are inputs; later commits post only new findings.
   - Rapid pushes are coalesced (~60 s enqueue delay; the latest push reviews the whole range).
2. **Index:** Postgres, a derived structural map of `main` (symbols, edges, reviews, findings). PR code is an in-memory overlay and is never stored. Full index only on first install or a schema change, otherwise incremental by `content_hash`. No LLM in indexing.
3. **Analysis**
   - Rule-based triage skips docs-only, formatting-only and lockfile changes with zero LLM calls.
   - A symbol diff classifies each symbol as UNCHANGED, FORMATTING_ONLY, MODIFIED, ADDED, DELETED or RENAMED, and flags signature changes.
   - Context is structural and deterministic (changed code, callers, callees, tests, module code, optional similar code) inside a ~12k token budget.
   - An optional, bounded context subagent fills gaps and returns symbol ids only.
4. **Review:** 3 agents (Security, Logic, Code quality) running on one shared runner. Every finding has a recommendation.
5. **Output**
   - On a PR: inline comments on the changed lines, plus one summary grouped by category, edited in place.
   - On a push to `main`: commit comments.
6. **Removed:** the judge loop, the query decomposer and `graph_retriever` (after the eval shows structural ≥ legacy), the full re-ingest at worker startup, and the Azure Table dedupe (replaced by the `reviews` table).

## Verified problems the plan fixes

| # | Problem | Where |
|---|---|---|
| V1 | A clean PR (no findings) loops back to the agents until the recursion limit, and no comment is posted | `review/graph.py:79-80`, `:161` |
| V2 | Empty retrieval after 3 attempts loops back to the retriever the same way | `review/graph.py:77-78`, `:90-95` |
| V3 | Agents get no code: Chroma stores code only in `documents`, and `retriever.py` reads `metadata["code"]` | `db/chroma.py:78-92`, `rag/hybrid_search.py:173-174`, `review/retriever.py:80` |
| V4 | The reranker and the judge see only names, and the reranker duplicates the best chunk | `rag/reranker.py:86`, `:151`; `review/retriever.py:100-103` |
| V5 | Parser: decorators are excluded, nested defs are double-counted, methods aren't class-qualified, `self.x()` calls are ignored | `rag/ast_parser.py:61-122` |
| V6 | Call resolution by bare name creates false edges | `rag/knowledge_graph.py:70-86` |
| V7 | Full re-ingest on every startup, with no deletes, no 429 retry and one unbatched Voyage call | `rag/ingestion.py`, `app/main.py:70-72`, `rag/contextual_chunker.py:79-84`, `rag/embedder.py:64` |
| V8 | `message.content` can be None, which crashes `.strip()`, and the decomposer accepts `[]` | `rag/query_decomposer.py:84,91`, `review/retriever.py:112`, `review/agents/security_agent.py:119,199` |
| V10 | Webhook: no dedupe, no head SHA, no error handling, no repo guard, PR only, a new comment each time | `app/api/webhook.py:98-171`, `tools/github_tools.py:88` |
| V12 | No test of the graph routing or of what the agents actually receive. Most tests make real API calls | `tests/` |

## Phases

Every phase leaves the system working. The fast offline tests plus that phase's new tests must pass before moving on.

### Phase 0: Make the current pipeline correct (S)

**Changes:**
- **Graph routing** (`review/graph.py`)
  - Remove the no-op supervisor, so the flow is `START → retriever (loop while score < threshold and attempts < 3) → agents → join_agents → synthesizer → END`.
  - Use `QUALITY_THRESHOLD` and `MAX_RETRIEVAL_ATTEMPTS` from `review/retriever.py` instead of the hardcoded values.
  - Delete the empty stub `agents/supervisor.py`.
- **Real code reaches the agents**
  - `db/chroma.py`: store `code` and `qualified_name` in the metadata.
  - `graph_retriever._node_to_metadata`: add `code`.
  - `rag/ingestion.py`: delete stale ids after each upsert.
- **Chunk identity** (`rag/ast_parser.py`)
  - Emit `qualified_name` (`Class.method`).
  - Include decorators in the chunk.
  - Don't recurse into function bodies.
  - The id becomes `file::qualified_name` everywhere.
- **None guards:** use `message.content or ""` in all LLM calls, and treat `[]` from the decomposer as a failure.
- **Reranker:** score `context + code` instead of the name, and remove the duplicated best chunk.

**Tests (offline):**
- `tests/unit/test_pipeline_offline.py` (an ephemeral Chroma, fake embeddings, a fake reranker and a fake Groq that records prompts). It checks that:
  - a clean PR terminates, and each agent is called once
  - empty retrieval terminates
  - prompts contain real code bodies
  - there are no duplicate chunks
  - None content doesn't crash
- `tests/unit/test_ast_parser_identity.py`
- an update to `test_knowledge.py`

**Done when:** all tests pass, and a real PR, including a clean one, gets one comment.

**Agents:** ai-engineer → test-runner → pre-commit-reviewer.

### Phase 1: Eval harness, golden dataset and baseline (M)

**Files:**
- `eval/fixtures/repo/`: a small synthetic repo.
- `eval/dataset/*.json`: 10-15 cases:
  - SQL and command injection
  - an off-by-one, a None dereference and an inverted condition
  - a signature change that breaks a caller
  - an N+1 query and missing tests
  - a clean refactor
  - docs-only and whitespace-only changes
  - a prompt-injection PR
- `eval/review_eval.py` (`--label`, `--runs 3`, `--mode legacy|structural`), which writes `eval/results/`.
- Sits next to `eval/llm_benchmark.py` (which compares models on single prompts, not whole reviews). No RAGAS (D4).

**What it measures:**
- **Context:** symbol recall and precision, the percentage of chunks with code, the duplicate rate, context tokens.
- **Findings:** precision and recall per category, false positives on clean PRs, the injection case.
- **Ops:** calls per review, latency. Each configuration runs 3 times, and differences of about 0.02 or less count as noise.

**Langfuse:** each eval run is logged as a Langfuse dataset run, with the scores attached to the traces, so runs can be compared in the Langfuse UI. The JSON files in `eval/results/` stay the source of truth.

**Agents:** ai-engineer.

### Phase 2: Webhook reliability for PR events (S-M, can run in parallel with Phase 1)

**Done** (durable job pipeline, `feat/durable-job-pipeline`):
- Head SHA and `X-GitHub-Delivery` in the job, dedupe claim on (repo, head_sha, target) in Azure Table Storage.
- Durable Storage Queue + separate worker with logging, retries and a poison queue (instead of the planned in-memory `app/review_registry.py`).
- Idempotent summary upsert by marker.

**Remaining:**
- Repo guard.
- `get_pr_head_sha()` for the stale check before posting.
- The Table dedupe is replaced by the `reviews` table in Phase 4 (same key, plus status and metrics).

**Tests:** duplicate delivery runs once (done), a wrong repo is ignored, a stale SHA isn't posted, an exception gets logged (done).

**Agents:** webhook-engineer (security-engineer reviews).

### Phase 3: Robust LLM layer, shared runner, 3 agents (M)

**Files:**
- `review/llm_client.py`:
  - timeout
  - retries on 429, 5xx, connection and timeout errors only
  - `content or ""`
  - JSON mode
  - call counters
  - a concurrency limit
  - **Langfuse tracing:** one trace per review, with a generation for each LLM call (prompt, output, tokens, latency). It's enabled only when `LANGFUSE_PUBLIC_KEY` is set, and it's a no-op in tests.
- `review/prompting.py`: `wrap_untrusted()` with nonce delimiters, so untrusted content is treated as data.
- **Done** (`refactor/three-review-agents`): `review/agents/agent_runner.py` with `AgentSpec(name, prompt_template)` and `run_agent()`, the agents `security`, `logic`, `quality`, strict validation (severity, integer line, non-empty text) with logged drops, `REVIEW_AGENT_MODEL`, and a synthesizer with one section per category. `Finding.agent` is the category, so no separate `category` field was added.
- Embedder retries and batching, and Gemini retries on 429 too.
- Delete the old performance, architecture, testing and documentation agents (done).

**Done when:** an eval before and after, reported per category.

**Agents:** ai-engineer (security-engineer on injection).

### Phase 4: Postgres index + symbol diff + structural context (L)

**Index (Postgres, schema in [ARCHITECTURE.md](ARCHITECTURE.md)):**
- `db/` Postgres adapter and migrations for `repos`, `files`, `symbols`, `edges`, `unresolved_calls`, `reviews`, `findings` (`symbol_embeddings` only if D1's "similar code" is enabled).
- Indexer (no LLM): tree-sitter symbols (functions, `Class.method` with decorators, class headers without bodies, a `<module>` pseudo-symbol), `content_hash` and `norm_hash`, call resolution `self`/`cls` (incl. bases) → import → same file → unique name → `unresolved_calls`, and `tested_by` edges.
- Full index build on first install or `schema_version` change (incremental sync is Phase 6).
- **Initial import:** a first push or an empty repo (`before` = `000…`) indexes everything and records the review as `skipped` (`initial_import`), with zero LLM calls.
- The `reviews` table replaces the Azure Table dedupe (unique `(repo_id, target, head_sha)`).

**Analysis:**
- `analysis/diff_parser.py`: name-status + hunks.
- `analysis/triage.py`: docs-only, formatting-only, lockfile-only → `skipped`.
- `analysis/symbol_diff.py`: "before" symbols vs parsed "after" (`git show after:path`), matched by `qualified_name` → UNCHANGED / FORMATTING_ONLY / MODIFIED / ADDED / DELETED / RENAMED, signature-change flag, hunks mapped to symbol ranges.
- `analysis/context_builder.py`: priority list into a ~12k token budget (changed code with class header → callers of changed signatures and deleted symbols → other callers → callees → tests → module code + grep of non-Python files → similar code), a one-line reason per block, "Not verifiable" list, nonce-wrapped untrusted content. New code's edges come from an in-memory overlay.

**Modified:**
- `tools/github_tools.get_pr_snapshot()`: head, base and merge-base SHAs, each file's patch, and the **PR title, description and commit messages** (inputs to the agents, to judge whether the change matches its stated intent).

**Graph:** `START → analyze_diff → triage →(skip)→ synthesizer` or `→ build_context → security|logic|quality → join_agents → synthesizer → END`.

**Comparison and cleanup:**
- `REVIEW_CONTEXT_MODE=legacy|structural` keeps both paths available for A/B evaluation.
- The decomposer, the judge loop and `graph_retriever` are deleted only after the eval shows structural mode doing at least as well as legacy.

**Agents:** ai-engineer (devops-engineer and docker-expert for Postgres in compose and Azure).

### Phase 4b: Bounded context subagent (M, after the structural-vs-legacy eval)

**Changes:**
- A LangGraph subgraph between `build_context` and the agents, run only when gaps exist (unresolved calls touched by the change, a deleted or renamed public symbol, a change spanning several modules). One shared run for all 3 agents.
- Read-only tools over Postgres: `get_symbol`, `get_callers`, `get_callees`, `get_tests`, `grep`, `similar`. At most 6 tool calls.
- Output `{selected: [{id, reason}], gaps: [...]}` with symbol ids only; the context builder fetches the exact code. Invalid output or a failure → keep the structural context.

**Done when:** the eval shows the subagent improves findings on the gap cases without hurting the rest, and calls per review stay bounded.

**Agents:** ai-engineer (security-engineer on tool and injection safety).

### Phase 5: GitHub output: inline review and in-place summary (M)

**Changes:**
- The synthesizer returns a `ReviewResult(summary_markdown, inline[], findings, skipped_reason)`. A finding goes inline only if its line is part of the diff.
- Markdown safety: fences, escaping `<!--`, neutralizing @mentions, length caps.
- `post_pr_review()`, with `side: "RIGHT"` and a summary-only fallback on a 422 error.
- `upsert_summary_comment()`: finds its own comment by the `<!-- acr:summary -->` marker and edits it (done).
- On later commits, post only new findings: each finding gets a `fingerprint` stored in `findings`, and already-posted fingerprints are dropped by the synthesizer (D2). The fingerprint must survive line shifts (open point in [ARCHITECTURE.md](ARCHITECTURE.md)).

**Agents:** webhook-engineer (security-engineer on output handling).

### Phase 6: Incremental Postgres sync (M-L)

**Changes:**
- Apply a push's symbol diff to Postgres in one transaction, **after** the review is posted, then set `repos.indexed_sha = after`. A failure rolls back.
- Before each push review, check `indexed_sha == before`; if not, sync the index to `before` first (repairs missed or failed updates).
- Only new or changed symbols (by `content_hash`) are re-parsed and re-linked; deleted symbols cascade their edges; a rename keeps the embedding if any.
- `repos.schema_version` and `embedding_model` mismatches trigger a full rebuild.
- The worker no longer re-ingests the repo in memory at startup: it reads Postgres, so it is ready in seconds.

**Agents:** ai-engineer and webhook-engineer (docker-expert and devops-engineer for storage).

### Phase 7: Push reviews (M)

**Changes:**
- Handle the `push` event:
  - ignore deleted branches
  - skip the push if the branch has an open PR
  - `before` = `000…`: initial import (Phase 4), no review
  - on `main`, compare `before...after`, review first, then update the index (Phase 6)
  - a push to a branch with no open PR: not decided yet (open point in [ARCHITECTURE.md](ARCHITECTURE.md))
- **Push coalescing:** enqueue with a ~60 s visibility delay (`JobQueue.enqueue` needs a delay parameter), and the latest push reviews the whole range; older queued reviews for the same target become `skipped` (`superseded`).
- `get_push_snapshot()`, and `post_commit_review()`: a summary on the head commit, plus inline comments by position.

**Agents:** webhook-engineer and ai-engineer.

**Every phase:** test-runner, then pre-commit-reviewer, then a single-line commit. Add a `docs/DECISIONS.md` entry for each design choice.

## Overview

| Phase | Goal | Effort | Depends on |
|---|---|---|---|
| 0 | Terminating graph, real code to agents, qualified ids, None guards, offline end-to-end test | S | none |
| 1 | Eval harness, golden dataset, baseline | M | 0 |
| 2 | Webhook dedupe, stale drop, error handling, repo guard (dedupe and queue done) | S-M | 0 (parallel with 1) |
| 3 | LLM client, injection-safe prompts, shared runner, 3 agents | M | 0, 1 |
| 4 | Postgres index, initial import, symbol diff, triage, structural context, PR text as input | L | 3, 1 |
| 4b | Bounded context subagent | M | 4 and its eval |
| 5 | Inline PR review, in-place summary, only new findings | M | 4, 2 |
| 6 | Incremental Postgres sync, review-before-update, no startup re-ingest | M-L | 4 |
| 7 | Push reviews as commit comments, push coalescing | M | 4, 5, 6, 2 |

**LLM calls per review** (estimated from the design, not measured): 7-11 today (after Phase 0), 5-9 after Phase 3, 3 after Phase 4 (+1-2 when the subagent runs), 0 for triaged-out changes. **Startup:** N Gemini calls today (one per function), 0 after Phase 6.

## Decisions (resolved 2026-10-14)

| # | Question | Decision |
| --- | --- | --- |
| D1 | Keep the vector index? | B: embeddings only for optional "similar code" (pgvector), no Gemini context sentences |
| D2 | What happens to inline comments when new commits arrive? | a: post only new findings, matched by fingerprint |
| D3 | What key is used to dedupe reviews? | `(repo, target, head_sha)`, the `reviews` unique constraint |
| D4 | Which eval metrics? | Custom deterministic metrics only, no RAGAS |
