# Progress

**File purpose:** what is actually built today, versus the target design in [ARCHITECTURE.md](ARCHITECTURE.md) and the phases in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).
**Last updated:** 2026-10-18.

## Done (in code)

| Area | What exists | Where |
| --- | --- | --- |
| Code parsing | tree-sitter parse and an in-memory NetworkX call graph for the agents' graph tools, built at worker start with no LLM call. The semantic retrieval (Gemini, Voyage, Chroma, BM25, reranker, judge loop) was removed | `rag/` |
| Review graph | Forward-only graph, `START` fans out to the 3 agents, `file::qualified_name` ids, None guards, offline end-to-end test | `review/graph.py`, `tests/unit/test_pipeline_offline.py` |
| Agents (Phase 3, part) | 3 review agents (Security, Logic, Quality) on one shared runner with strict finding validation and `REVIEW_AGENT_MODEL`; synthesizer with one section per category. Security checks authn/authz/IDOR on changed endpoints | `review/`, `review/agents/` |
| Durable jobs (Phase 2, part) | Webhook verifies HMAC, dedupes on (repo, target, head_sha) in the Postgres `reviews` table, enqueues to Azure Storage Queue; worker with retries and poison queue | `app/api/webhook.py`, `app/jobs/`, `app/worker.py` |
| Trigger policy (Phase 2, part) | Before the dedupe, the webhook ignores repos outside `ALLOWED_REPOS`, draft PRs and authors GitHub does not list as collaborators; an unreachable GitHub gives 503 (fail closed); `ready_for_review` reviews a draft once it is ready | `app/jobs/trigger_policy.py`, `app/api/webhook.py` |
| GitHub output (Phase 5, part) | One summary comment per PR, upserted by the `<!-- acr:summary -->` marker | `tools/github_tools.py` |
| Packaging and deploy | Multi-stage Docker image, compose (Azurite + API + worker), Azure setup script, GitHub Actions test → build → deploy (deploy skipped until Azure vars exist) | `Dockerfile`, `docker-compose.yml`, `infra/`, `.github/workflows/` |
| Postgres (Phase 4, part) | `pgvector/pgvector:pg16` in docker compose, the full schema as `db/migrations/001_initial_schema.sql`, a migration runner with an advisory lock, a connection pool. Only `reviews` is used so far; `symbols`/`edges` wait for the indexer | `db/` |
| Indexer (Phase 4, part) | Full index of a checkout into `files`, `symbols`, `edges` (calls, imports, inherits, tested_by) and `unresolved_calls`, in one transaction, no LLM. Call resolution `self`/`cls` (with repo bases) → import (relative, aliases, `__init__` re-exports, `src/` layout) → same file → unique name; library calls and locals are skipped. Run by hand: `python -m rag.code_indexer --repo owner/name --repo-path <checkout>` | `rag/`, `tests/unit/test_call_resolver.py` |
| Tests | `tests/unit/` (offline, CI) and `tests/integration/` (real APIs, manual) | `tests/` |
| LLM benchmark | Model comparison on the production prompts of the 3 agents | `eval/llm_benchmark.py` |

## Not built yet

- Phase 1: review eval harness (`eval/review_eval.py`, golden dataset, diff + tools vs structural).
- Phase 2: stale-SHA check (the repo guard and author check are done).
- Phase 3: shared LLM client (timeouts, retries, concurrency limit), Langfuse tracing, nonce-wrapped prompts. (The 3 agents and the shared runner are done.)
- Phase 4: running the indexer from the worker (initial import), symbol diff, triage, structural context, PR text as input, agent tools reading Postgres instead of NetworkX. (Postgres, the schema, the `reviews` dedupe and the indexer itself are done.)
- Phase 4b: context subagent.
- Phase 5: inline review, only-new findings by fingerprint.
- Phase 6: incremental Postgres sync (the worker still parses the whole repo at startup, with no LLM call).
- Phase 7: push reviews, push coalescing.
- Azure resources: the setup script exists but has not been run, and it does not create a Postgres server yet (a deployed API fails at startup without `DATABASE_URL`).

## Known issues

- The review agents' model `openai/gpt-oss-120b` is a provisional default until the benchmark runs on the 3 agents.
- The latest benchmark run (2026-10-13) predates the 5 authn/authz cases and scores almost every model at 1.00, so it does not separate them.
