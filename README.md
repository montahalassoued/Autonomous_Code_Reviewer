<!--
File purpose: Project overview for a first-time reader: what it does, how it is built, how it is measured, how to run it.
-->

# autonomous-code-reviewer

A GitHub App that reviews every push and pull request on any repository of an allowed owner. Three tool-using LLM agents (security, logic, code quality) review the change in parallel, and a synthesizer posts one review as `<app-name>[bot]`. Review quality is measured on 12 golden pull requests, and CI fails when a change makes the reviews worse.

## What a review looks like

From a real push to a test repository (abridged):

> **🤖 Autonomous Code Review**
> ⛔ **2 blocking issue(s)** to fix before merging, out of 2 finding(s).
>
> 🔴 **[blocking] CRITICAL - CWE-78: OS command injection via unsanitized user input**
> Location: `app.py:30` · Confidence: high
> `return os.system("report " + command)`
> **Suggestion:** use `subprocess.run` with a list of arguments and an allowlist of commands.
>
> 🟠 **[blocking] HIGH - N+1 queries in load_all_orders** ...

## Architecture

```text
 git push / pull request (any repo of an allowed owner)
        │  GitHub App webhook (HMAC-signed)
        ▼
 Nginx gateway ── 2 routes, body cap, per-IP rate limit
        ▼
 FastAPI ── signature check · trigger policy · dedupe (Postgres) ──► 202 in < 1 s
        │ enqueue
        ▼
 Job queue (Azure Storage Queue; Azurite locally) ── retries · dead-letter queue
        ▼
 Worker
   ├─ checks out the reviewed commit and builds its call graph (tree-sitter, code_index/)
   ├─ LangGraph:  security │ logic │ quality   (in parallel, read-only tools over the checkout)
   │                    └──► synthesizer (sanitized Markdown)
   └─ LLM gateway: Groq gpt-oss-120b · retries · rate limits · token budgets · review deadline
        ▼
 GitHub comment on the commit (push) or the pull request (one summary, edited on each push)

 Quality loop: 12 golden PRs ─► recorded LLM calls replayed offline in CI ─► gate against a baseline
```

- **API, queue, worker.** GitHub waits 10 seconds for a webhook answer and a review takes 1-3 minutes, so the API only checks and enqueues. The queue keeps every job through restarts, retries a job whose worker crashed, and moves it to a dead-letter queue after 3 attempts. More workers read the same queue to review more at once.
- **Idempotent.** Delivery is at least once, so a redelivered event is deduplicated in Postgres, and a retried review edits the bot's comment instead of posting a second one.
- **Structural retrieval, not vector RAG.** A review needs exact relationships (who calls this function, which test covers it), not similar text. The worker parses the reviewed commit into a call graph, and the agents query it through tools (`find_callers`, `find_tests`, `read_file`, ...). See [docs/CODE_INDEX.md](docs/CODE_INDEX.md).

## Evaluation

| What | How | Result |
| --- | --- | --- |
| Model choice | 30 labelled cases on the real agent prompts, 3 runs per model, scored like production (`eval/llm_benchmark.py`) | gpt-oss-120b chosen for all three agents (quality 0.99) |
| Whole reviews | 12 golden PRs written as edits to a fixture repo, run through the production graph (`eval/review_eval.py`) | recall 1.00 in every category, posted-review precision 0.43, 5.5 LLM calls and ~30k tokens per review |
| Regression gate | CI replays the recorded LLM calls for free and compares each review with a committed baseline | a review that finds less or adds noise fails the build |

Precision is the current target: the recurring false alarms (unbounded queries and missing timeouts flagged as HIGH) are known and measured against the baseline.

## Engineering highlights

- **Failure handling.** An agent that fails, or answers with something that is not a JSON array, makes the review partial and names the missing category; it never reads as "no issues".
- **Prompt-injection defenses.** The diff, the context and tool output are wrapped in random-nonce markers as data, and lines that address the AI reviewer turn the verdict into "Needs human review".
- **Output and secrets.** Secrets are redacted before the model and before posting. Every LLM-written field is escaped: no links, mentions, HTML or broken code fences.
- **LLM gateway.** One client for every provider (`provider:model` routing, Groq or any OpenAI-compatible server such as vLLM), with retries on per-minute 429s, no retry on a spent daily quota, a concurrency limit, a token budget per agent and a review deadline.
- **GitHub App auth.** Installation tokens last an hour and are scoped to the repos the App is installed on; a new repository is covered the day it is created.

## Tech stack

| Area | Choice |
| --- | --- |
| Agents | LangGraph, Groq (gpt-oss-120b), OpenAI-compatible providers (vLLM, Ollama) |
| Code context | tree-sitter symbols, resolved call edges, NetworkX call graph read by bounded tools |
| Service | FastAPI, Azure Storage Queue (Azurite locally), PostgreSQL, Nginx |
| GitHub | GitHub App (PyGithub), commit and pull request comments |
| Observability | Langfuse traces (one per review), Prometheus metrics |
| Delivery | Docker (multi-stage), docker compose, GitHub Actions |

## Quick start

1. Create and install the GitHub App: [docs/GITHUB_APP.md](docs/GITHUB_APP.md).
2. Configure and start:

   ```bash
   cp .env.example .env          # GROQ_API_KEY, ALLOWED_OWNERS, the GitHub App and its webhook secret
   docker compose up --build -d  # gateway on http://localhost:8000 (GET /health)
   ```

3. Expose port 8000 with a tunnel (ngrok, VS Code port forwarding) and set the App's webhook URL to `https://<public-url>/webhook/github`.
4. Push code to any of your repositories; the review appears on the commit within a few minutes.

API keys and the other settings: [docs/SETUP.md](docs/SETUP.md).

## CI/CD

Every push to `main` runs the offline unit tests, the **eval gate** (the 12 golden PRs replayed against the baseline), the Postgres tests and the Docker build. Deployment to Azure Container Apps is scripted (`infra/azure-setup.sh`) and runs once Azure is configured. Details: [docs/CI.md](docs/CI.md).

## Roadmap

- Structural context builder: a symbol diff and a prioritized context dossier under a token budget, A/B-tested against today's tool calls on the golden PRs.
- Self-hosted serving: vLLM with a 7B coding model, benchmarked against the hosted model ([docs/SERVING.md](docs/SERVING.md)).
- Kubernetes with KEDA, scaling the workers with the queue length.
- Only-new findings across pushes, and inline comments on pull request lines.

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): target design
- [docs/AGENTS.md](docs/AGENTS.md): the review agents, their prompts and tools
- [docs/CODE_INDEX.md](docs/CODE_INDEX.md): how the code around a change is found
- [docs/PROGRESS.md](docs/PROGRESS.md): what is built today
- [docs/DECISIONS.md](docs/DECISIONS.md): architecture decisions and their trade-offs
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md), [docs/SERVING.md](docs/SERVING.md), [infra/README.md](infra/README.md)
