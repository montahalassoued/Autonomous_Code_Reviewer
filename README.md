<!--
File purpose: Short project overview: what it is, how it works, how to run it.
Inputs: A cloned repository and an .env file (see .env.example).
Outputs: A running review service (FastAPI) that receives GitHub webhooks and posts reviews back to GitHub.
-->

# autonomous-code-reviewer

An AI code reviewer that runs entirely through GitHub. You push code or open a pull request, and a multi-agent pipeline (LangGraph + RAG over your codebase) posts a review back on GitHub, grouped by category, with a recommendation for every finding.

## Architecture

```text
 GitHub (push / pull request)
        │  webhook (HMAC-signed)
        ▼
 FastAPI server ─ Azure Container Apps
        │
        ▼
 LangGraph review pipeline
   RAG context  ─►  review agents (parallel)  ─►  synthesizer
   (code index:      security · logic ·            (findings +
    parse, graph,    code quality                    recommendations)
    search)
        │
        ▼
 GitHub (review comments on the PR or commit)
```

## Tech stack

| Area | Choice |
| --- | --- |
| Orchestration | LangGraph |
| LLMs | Groq, Gemini |
| RAG | tree-sitter, NetworkX call graph, ChromaDB, BM25 + Voyage embeddings, local reranker |
| API | FastAPI + GitHub webhooks (PyGithub) |
| Tracing & LLM eval | Langfuse |
| Deploy | Docker, Azure Container Apps |
| CI/CD | GitHub Actions |

## Quick start

```bash
cp .env.example .env          # fill in the API keys and TARGET_REPO_URL
docker compose up --build     # API on http://localhost:8000 (GET /health), Postgres on localhost:5432
```

Then point a GitHub webhook (pull request and push events) at `https://<your-server>/webhook/github`, with the same secret as `GITHUB_WEBHOOK_SECRET`.

## CI/CD

Every pull request into `main` and every push to `main` runs **offline tests**, then a **Docker build**. Pushes to `main` also **deploy to Azure** once it's configured. Details: [docs/CI.md](docs/CI.md).

## Docs

- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): target design and roadmap
- [infra/README.md](infra/README.md): Azure setup and deployment
- [docs/SETUP.md](docs/SETUP.md): getting the API keys
- [docs/DECISIONS.md](docs/DECISIONS.md): architecture decisions
