# Implementation plan: reviewer redesign

**File purpose:** the phased plan for turning the current pipeline into the target design.
**Inputs:** the user's confirmed vision (2026-10-13) and a code review by the ai-engineer subagent.
**Outputs:** the order of work, the files for each phase, the tests, the acceptance criteria and the open decisions.

## Target design (confirmed by the user)

1. **Triggers**
   - A PR is reviewed right after it's opened, and again on new commits (`synchronize`).
   - A push is reviewed right after the push.
   - A push to a branch that has an open PR is reviewed once, on the PR. A push to a branch with no open PR is reviewed as commit comments.
2. **Index**
   - Always mirrors `main`, and is updated incrementally on a push to `main`.
   - PR and branch code is a temporary overlay that is never stored.
   - The index persists across restarts, and indexing doesn't block the server.
3. **Analysis**
   - A deterministic diff analyzer finds the added, modified, deleted and renamed functions.
   - Rule-based triage skips docs-only, formatting-only and lockfile changes.
   - Context is the full new code, plus callers, callees and tests.
   - Hybrid search and the reranker are an optional supplement.
4. **Review:** 3 agents (Security, Logic, Code quality) running on one shared runner. Every finding has a recommendation.
5. **Output**
   - On a PR: inline comments on the changed lines, plus one summary grouped by category, edited in place.
   - On a push: commit comments.

## Verified problems the plan fixes

| # | Problem | Where |
|---|---|---|
| V1 | A clean PR (no findings) loops back to the agents until the recursion limit, and no comment is posted | `agents/graph.py:79-80`, `:161` |
| V2 | Empty retrieval after 3 attempts loops back to the retriever the same way | `agents/graph.py:77-78`, `:90-95` |
| V3 | Agents get no code: Chroma stores code only in `documents`, and `retriever.py` reads `metadata["code"]` | `db/chroma.py:78-92`, `rag/hybrid_search.py:173-174`, `agents/retriever.py:80` |
| V4 | The reranker and the judge see only names, and the reranker duplicates the best chunk | `rag/reranker.py:86`, `:151`; `agents/retriever.py:100-103` |
| V5 | Parser: decorators are excluded, nested defs are double-counted, methods aren't class-qualified, `self.x()` calls are ignored | `rag/ast_parser.py:61-122` |
| V6 | Call resolution by bare name creates false edges | `rag/knowledge_graph.py:70-86` |
| V7 | Full re-ingest on every startup, with no deletes, no 429 retry and one unbatched Voyage call | `rag/ingestion.py`, `app/main.py:70-72`, `rag/contextual_chunker.py:79-84`, `rag/embedder.py:64` |
| V8 | `message.content` can be None, which crashes `.strip()`, and the decomposer accepts `[]` | `rag/query_decomposer.py:84,91`, `agents/retriever.py:112`, `agents/security.py:119,199` |
| V10 | Webhook: no dedupe, no head SHA, no error handling, no repo guard, PR only, a new comment each time | `app/api/webhook.py:98-171`, `tools/github_tools.py:88` |
| V12 | No test of the graph routing or of what the agents actually receive. Most tests make real API calls | `test/` |

## Phases

Every phase leaves the system working. The fast offline tests plus that phase's new tests must pass before moving on.

### Phase 0: Make the current pipeline correct (S)

**Changes:**
- **Graph routing** (`agents/graph.py`)
  - Remove the no-op supervisor, so the flow is `START → retriever (loop while score < threshold and attempts < 3) → agents → join_agents → synthesizer → END`.
  - Use `QUALITY_THRESHOLD` and `MAX_RETRIEVAL_ATTEMPTS` from `agents/retriever.py` instead of the hardcoded values.
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
- `test/test_pipeline_offline.py` (an ephemeral Chroma, fake embeddings, a fake reranker and a fake Groq that records prompts). It checks that:
  - a clean PR terminates, and each agent is called once
  - empty retrieval terminates
  - prompts contain real code bodies
  - there are no duplicate chunks
  - None content doesn't crash
- `test/test_ast_parser_identity.py`
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
- Replaces `eval/ragas_eval.py`.

**What it measures:**
- **Context:** symbol recall and precision, the percentage of chunks with code, the duplicate rate, context tokens.
- **Findings:** precision and recall per category, false positives on clean PRs, the injection case.
- **Ops:** calls per review, latency. Each configuration runs 3 times, and differences of about 0.02 or less count as noise.

**Langfuse:** each eval run is logged as a Langfuse dataset run, with the scores attached to the traces, so runs can be compared in the Langfuse UI. The JSON files in `eval/results/` stay the source of truth.

**Agents:** ai-engineer.

### Phase 2: Webhook reliability for PR events (S-M, can run in parallel with Phase 1)

**Changes:**
- Use the head SHA and `X-GitHub-Delivery`.
- Repo guard.
- try/except plus logging in the background task.
- Fix the inaccurate docstring about GitHub retries.
- `app/review_registry.py` (in memory, with a lock and a TTL): `try_claim(repo, sha, target)`, `set_latest_head`, `is_latest`.
- `get_pr_head_sha()` for the stale check before posting.

**Tests:** duplicate delivery runs once, a wrong repo is ignored, a stale SHA isn't posted, an exception gets logged.

**Agents:** webhook-engineer (security-engineer reviews).

### Phase 3: Robust LLM layer, shared runner, 3 agents (M)

**Files:**
- `agents/llm_client.py`:
  - timeout
  - retries on 429, 5xx, connection and timeout errors only
  - `content or ""`
  - JSON mode
  - call counters
  - a concurrency limit
  - **Langfuse tracing:** one trace per review, with a generation for each LLM call (prompt, output, tokens, latency). It's enabled only when `LANGFUSE_PUBLIC_KEY` is set, and it's a no-op in tests.
- `agents/prompting.py`: `wrap_untrusted()` with nonce delimiters, so untrusted content is treated as data.
- `agents/agent_runner.py` and `agents/agent_specs.py`: an `AgentSpec(name: security|logic|quality, system_prompt, focus, model, temperature, max_findings)`, plus `run_agent()` and `make_agent_node()`. It validates severity, the line number and a non-empty recommendation, and logs every dropped finding.
- `Finding` gains a `category` (and optionally `end_line`).
- The synthesizer groups findings by category.
- Embedder retries and batching, and Gemini retries on 429 too.
- Delete the old performance, architecture, testing and documentation agents.

**Done when:** an eval before and after, reported per category.

**Agents:** ai-engineer (security-engineer on injection).

### Phase 4: Diff analyzer, triage and structural context (L)

**New:**
- `analysis/diff_parser.py`
- `analysis/symbol_diff.py`: parse base and head, compare `content_hash` and `norm_hash`, mark ADDED, MODIFIED, DELETED, RENAMED, FORMATTING_ONLY or UNCHANGED, with a `<module>` pseudo-symbol for top-level changes.
- `analysis/triage.py`
- `rag/code_index.py` (`CodeIndex`): `get`, `callers`, `callees`, `tests_referencing`, `similar` (optional), and `resolve_call`, which resolves in this order: same class, same file, imports, then a name unique in the repo.
- `analysis/context_builder.py`: a priority budget in this order:
  1. changed code, line-numbered
  2. callers, including dangling callers of deleted functions
  3. callees
  4. tests
  5. similar code

**Modified:**
- `tools/github_tools.get_pr_snapshot()`: head, base and merge-base SHAs, plus each file's patch and its base and head contents.

**Graph:** `START → analyze_diff → triage →(skip)→ synthesizer` or `→ build_context → security|logic|quality → join_agents → synthesizer → END`.

**Comparison and cleanup:**
- `REVIEW_CONTEXT_MODE=legacy|structural` keeps both paths available for A/B evaluation.
- The decomposer, the judge loop and the graph_retriever are deleted only after the eval shows structural mode doing at least as well as legacy.

**Agents:** ai-engineer.

### Phase 5: GitHub output: inline review and in-place summary (M)

**Changes:**
- The synthesizer returns a `ReviewResult(summary_markdown, inline[], findings, skipped_reason)`. A finding goes inline only if its line is part of the diff.
- Markdown safety: fences, escaping `<!--`, neutralizing @mentions, length caps.
- `post_pr_review()`, with `side: "RIGHT"` and a summary-only fallback on a 422 error.
- `upsert_summary_comment()`: finds its own comment by the `<!-- acr:summary -->` marker and edits it.

**Agents:** webhook-engineer (security-engineer on output handling).

### Phase 6: Persistent incremental index and non-blocking startup (M-L)

**Changes:**
- `sync_index()` compares `{id: content_hash}`. Only new or changed chunks are contextualized and embedded; removed ids are deleted, and a rename reuses the existing context and embedding.
- `rag/index_state.json` holds `indexed_sha`, `schema_version` and `embedding_model`. A mismatch triggers a full rebuild.
- Startup runs the sync in a background thread, and `/health` reports `indexing` and then `ok`.
- A push to `main` triggers `update_index`.

**Agents:** ai-engineer and webhook-engineer (docker-expert and devops-engineer for storage).

### Phase 7: Push reviews (M)

**Changes:**
- Handle the `push` event:
  - ignore deleted branches
  - skip the push if the branch has an open PR
  - compare `before...after` (a new branch compares against the default branch)
  - on `main`, review first, then update the index
- `get_push_snapshot()`, and `post_commit_review()`: a summary on the head commit, plus inline comments by position.

**Agents:** webhook-engineer and ai-engineer.

**Every phase:** test-runner, then pre-commit-reviewer, then a single-line commit. Add a `DECISIONS.md` entry for each design choice.

## Overview

| Phase | Goal | Effort | Depends on |
|---|---|---|---|
| 0 | Terminating graph, real code to agents, qualified ids, None guards, offline end-to-end test | S | none |
| 1 | Eval harness, golden dataset, baseline | M | 0 |
| 2 | Webhook dedupe, stale drop, error handling, repo guard | S-M | 0 (parallel with 1) |
| 3 | LLM client, injection-safe prompts, shared runner, 3 agents | M | 0, 1 |
| 4 | Diff analyzer, triage, structural context, CodeIndex | L | 3, 1 |
| 5 | Inline PR review and in-place summary | M | 4, 2 |
| 6 | Persistent incremental index, non-blocking startup | M-L | 0 (best after 4) |
| 7 | Push reviews as commit comments | M | 4, 5, 6, 2 |

**LLM calls per review** (estimated from the design, not measured): unbounded today, 7-11 after Phase 0, 5-9 after Phase 3, 3 after Phase 4. **Startup:** N Gemini calls today (one per function), 0 after Phase 6 when the index is current.

## Open decisions

| # | Question | Options | Recommended |
|---|---|---|---|
| D1 | Keep the vector index (Gemini context sentences + Voyage embeddings)? | A: always keep it. B: build it only when "similar code" is enabled. C: drop it | B |
| D2 | What happens to inline comments when new commits arrive? | a: post only new findings. b: repost everything. c: delete and repost | a |
| D3 | What key is used to dedupe reviews? | `(repo, sha)` or `(repo, sha, target)` | `(repo, sha, target)` |
| D4 | Which eval metrics? | Custom deterministic metrics only, or also RAGAS | Custom only |
