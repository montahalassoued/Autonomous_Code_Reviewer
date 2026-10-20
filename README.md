<!--
File purpose: Short project overview: what it is, how it works, how to run it.
Inputs: A cloned repository and an .env file (see .env.example).
Outputs: A running review service (FastAPI) that receives GitHub webhooks and posts reviews back to GitHub.
-->

# autonomous-code-reviewer

An AI code reviewer that runs entirely through GitHub. You open a pull request (or push to it), and a multi-agent pipeline (LangGraph, with read-only tools over your codebase) posts a review back on GitHub, grouped by category, with a recommendation for every finding.

## Architecture

```text
 GitHub (pull request)
        │  webhook (HMAC-signed)
        ▼
 FastAPI server ─ Azure Container Apps
        │
        ▼
 LangGraph review pipeline
   review agents (parallel)  ─►  synthesizer
   security · logic ·            (findings +
   code quality                   recommendations)
   (read-only tools over the
    checkout and call graph)
        │
        ▼
 GitHub (review comments on the PR or commit)
```

## Tech stack

| Area | Choice |
| --- | --- |
| Orchestration | LangGraph |
| LLMs | Groq (Gemini only in the model benchmark) |
| Code context | tree-sitter, NetworkX call graph read by bounded agent tools; Postgres code index planned |
| API | FastAPI + GitHub webhooks (PyGithub) |
| Tracing & LLM eval | Langfuse (planned, Phase 3) |
| Deploy | Docker, Azure Container Apps |
| CI/CD | GitHub Actions |

## Quick start

```bash
cp .env.example .env          # fill in the API keys and TARGET_REPO_URL
docker compose up --build     # API on http://localhost:8000 (GET /health), Postgres on localhost:5432
```

Then point a GitHub webhook at `https://<your-server>/webhook/github` with content type `application/json`, the same secret as `GITHUB_WEBHOOK_SECRET`, and only the **Pull requests** event (see [docs/SETUP.md](docs/SETUP.md)). Reviews of pushes to `main` (commit comments) are planned, not built.

## CI/CD

Every pull request into `main` and every push to `main` runs **offline tests**, then a **Docker build**. Pushes to `main` also **deploy to Azure** once it's configured. Details: [docs/CI.md](docs/CI.md).

## Docs

- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): target design and roadmap
- [infra/README.md](infra/README.md): Azure setup and deployment
- [docs/SETUP.md](docs/SETUP.md): getting the API keys
- [docs/DECISIONS.md](docs/DECISIONS.md): architecture decisions
