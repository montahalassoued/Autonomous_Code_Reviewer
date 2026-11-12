# CI/CD pipeline

The pipeline is defined in [`.github/workflows/ci-cd.yml`](../.github/workflows/ci-cd.yml). It runs on GitHub Actions.

## When it runs

| You do | Pipeline | Jobs |
| --- | --- | --- |
| Push to a feature branch (no PR) | Doesn't run | none |
| Open a PR into `main`, or push to that PR | Runs | **Offline tests** and **Postgres tests**, then **Docker build** |
| Push or merge into `main` | Runs | **Offline tests** and **Postgres tests**, then **Docker build**, then **Deploy** (once Azure is configured, and only if both test jobs passed) |
| "Run workflow" in the Actions tab | Runs | Same as a push to `main` |

If a job fails, the jobs after it don't run, so code that fails its tests never gets built or deployed.

## Job 1: Offline tests

This job installs Python 3.11 (the same version as the Docker image) and `requirements.txt`. Then it runs only the tests that make **no network calls and need no API keys**: every LLM and GitHub call is replaced with a fake. So it's fast, free and deterministic.

| Test file | What it checks |
| --- | --- |
| `test_pipeline_offline.py` | Runs the **real LangGraph pipeline end to end** with fake LLMs. It checks that:<ul><li>a clean PR finishes and gets a "no issues" review (no infinite loop)</li><li>each agent runs exactly once, and nothing else calls the LLM</li><li>agent prompts carry the diff and the "use your tools" context note</li><li>the agents get their repo and call-graph tools through the graph</li><li>empty LLM responses don't crash the run</li></ul> |
| `test_ast_parser_identity.py` | The code parser:<ul><li>methods get class-qualified ids (`file::Class.method`)</li><li>decorators are included in the chunk</li><li>nested functions aren't double-counted</li><li>two `__init__` methods don't collide</li></ul> |
| `test_knowledge.py` | The call graph: nodes, caller and callee edges, qualified ids, saving and loading |
| `test_ingestion.py` | Parsing a small repo into the call graph, skipping `venv/`, with no API call |
| `test_state.py` | The shared review state: the shape of a `Finding`, and that parallel agent results are merged instead of overwritten |
| `test_synthesizer.py` | The final Markdown review: sorted by severity, the summary counts, and the "no issues found" case |
| `test_webhook.py` | The GitHub webhook endpoint:<ul><li>rejects a missing or invalid HMAC signature (401)</li><li>ignores non-PR events and actions</li><li>schedules a review for `opened` and `synchronize`</li><li>returns 400 for a malformed payload</li></ul> |
| `test_main.py` | App startup and `GET /health`, with indexing mocked |

The other tests in `tests/` call real APIs (Groq, GitHub) and use quota. They run only on demand, through the separate **integration-tests** workflow (Actions tab, then "Run workflow").

## Job 1a: Review eval gate

Replays the recorded model answers of the review eval (`eval/recordings/review_eval_calls.jsonl`) through the current code, with `python -m eval.review_eval --offline --baseline eval/baselines/review_eval_baseline.json`, and fails if any golden PR now gets a worse review than the baseline: a lower score, fewer bugs found, more noise, duplicates or forbidden findings, or a new error. A replay is deterministic, so every change it reports comes from the code (post-processing, merger, locator, synthesizer, scoring), not from the model. No API key, no quota; the report is added to the run's summary page. A case whose requests changed (a prompt, a tool or the client) is listed as stale instead of failing, and a gate that could replay no case fails. The deploy waits for it.

## Job 1b: Postgres tests

Runs next to the offline tests, against a throwaway `pgvector/pgvector:pg16` service container (the image docker compose uses) with `DATABASE_URL` set. It applies the migrations and runs `tests/integration/test_postgres_dedupe.py` (dedupe claim and release, the review lifecycle, the stored LLM cost) and `tests/integration/test_index_writer.py` (the code index). No API key, no quota. The deploy waits for it, so a migration that fails on Postgres is never deployed.

## Job 2: Docker build

This job builds the production image from the [`Dockerfile`](../Dockerfile) to prove it still builds. On PRs it **doesn't push** the image anywhere. Docker layers are cached between runs, which keeps later builds fast.

## Job 3: Deploy

This job runs only on `main`, and only once the Azure variables exist in GitHub. Until then it shows as **skipped**, not failed. Two Container Apps run the same image: the **API** (`CONTAINER_APP_NAME`) verifies webhooks and enqueues reviews, and the **worker** (`WORKER_APP_NAME`) runs the reviews from the queue, checking out each job's commit. When it does run:

1. Log in to Azure with OIDC, so no password is stored in GitHub.
2. Push the image to Azure Container Registry, tagged with the commit SHA.
3. Update the **worker** to that image and wait until its new revision is running (up to 5 minutes). It has no public URL, so there is no HTTP check.
4. Update the **API** to that image and wait up to 3 minutes until the **new** revision's `/health` returns `ok`.

The worker goes first, so a consumer exists before the API starts enqueueing. The worker's "running" check only proves its container started. Reviews queued before it reads the queue wait there; none are lost.

The setup is in [`infra/README.md`](../infra/README.md).
