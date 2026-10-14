# Progress

**File purpose:** what is actually built today, versus the target design in [ARCHITECTURE.md](ARCHITECTURE.md) and the phases in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).
**Last updated:** 2026-10-14.

## Done (in code)

| Area | What exists | Where |
| --- | --- | --- |
| Legacy RAG pipeline | tree-sitter parse, Gemini context sentences, NetworkX graph, Voyage + Chroma, hybrid search, 1-hop graph expansion, local reranker, judge loop | `rag/`, `db/chroma.py`, `agents/retriever.py` |
| Review graph (Phase 0) | Forward-only graph, real code reaches agents, `file::qualified_name` ids, None guards, reranker on context + code, offline end-to-end test | `agents/graph.py`, `tests/unit/test_pipeline_offline.py` |
| Agents | 5 domain agents (Security, Performance, Architecture, Testing, Documentation) + synthesizer. Security checks authn/authz/IDOR on changed endpoints | `agents/` |
| Durable jobs (Phase 2, part) | Webhook verifies HMAC, dedupes on (repo, head_sha, PR) in Azure Table Storage, enqueues to Azure Storage Queue; worker with retries and poison queue | `app/api/webhook.py`, `app/jobs/`, `app/worker.py` |
| GitHub output (Phase 5, part) | One summary comment per PR, upserted by the `<!-- acr:summary -->` marker | `tools/github_tools.py` |
| Packaging and deploy | Multi-stage Docker image, compose (Azurite + API + worker), Azure setup script, GitHub Actions test → build → deploy (deploy skipped until Azure vars exist) | `Dockerfile`, `docker-compose.yml`, `infra/`, `.github/workflows/` |
| Tests | `tests/unit/` (offline, CI) and `tests/integration/` (real APIs, manual) | `tests/` |
| LLM benchmark | Model comparison on the production prompts for agents, decomposer, judge, chunker | `eval/llm_benchmark.py` |

## Not merged yet

The durable jobs, the tests split, the decisions merge, the security authz prompt and this design update are on stacked local branches (`feat/durable-job-pipeline` → `refactor/project-structure` → `feat/security-authz-review` → `docs/structural-context-design`), not on `main` yet.

## Not built yet

- Phase 1: review eval harness (`eval/review_eval.py`, golden dataset, legacy vs structural).
- Phase 2: repo guard, stale-SHA check.
- Phase 3: shared LLM client, Langfuse tracing, 3 agents (Security, Logic, Quality), `category` on findings.
- Phase 4: Postgres index, initial import, symbol diff, triage, structural context, PR text as input.
- Phase 4b: context subagent.
- Phase 5: inline review, only-new findings by fingerprint.
- Phase 6: incremental Postgres sync (the worker still re-ingests the whole repo at startup).
- Phase 7: push reviews, push coalescing.
- Azure resources: the setup script exists but has not been run.

## Known issues

- Production agents still call Groq `llama-3.3-70b-versatile`, which Groq retired.
- The latest benchmark run (2026-10-13) predates the 5 authn/authz cases and scores almost every model at 1.00, so it does not separate them.
