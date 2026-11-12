# autonomous-code-reviewer

A GitHub App that reviews every push and pull request on your repositories. Three LLM agents (security, logic, code quality) review the change in parallel, with read-only tools over the code, and the review is posted on GitHub as `<app-name>[bot]`.

## Architecture

```text
 git push / pull request
        │  GitHub App webhook
        ▼
 Nginx gateway ─► FastAPI   (checks the signature, filters, dedupes, answers in < 1 s)
        │ enqueue
        ▼
 Job queue      (retries, dead-letter queue)
        ▼
 Worker
   ├─ checks out the commit and builds its call graph (tree-sitter)
   ├─ security │ logic │ quality agents (LangGraph, Groq gpt-oss-120b)
   └─ synthesizer
        ▼
 Review comment on GitHub
```

## Evaluation

| What | Result |
| --- | --- |
| Model benchmark (30 cases, 3 runs) | gpt-oss-120b chosen, quality 0.99 |
| 12 golden pull requests, whole reviews | recall 1.00, precision 0.59 (from 0.43), ~30k tokens per review |
| CI regression gate | the 12 reviews are replayed on every push; a worse review fails the build |

## Quick start

1. Create and install the GitHub App: [docs/GITHUB_APP.md](docs/GITHUB_APP.md).
2. Configure and start:

   ```bash
   cp .env.example .env          # GROQ_API_KEY, ALLOWED_OWNERS, the GitHub App settings
   docker compose up --build -d  # http://localhost:8000/health
   ```

3. Expose port 8000 with a tunnel and set the App's webhook URL to `https://<public-url>/webhook/github`.
4. Push code to one of your repositories: the review appears on the commit.

## Tech stack

Python, LangGraph, FastAPI, Groq, tree-sitter, PostgreSQL, Docker, Nginx, GitHub Actions, Langfuse.

## Docs

[Architecture](docs/ARCHITECTURE.md) · [Agents](docs/AGENTS.md) · [Code index](docs/CODE_INDEX.md) · [Progress](docs/PROGRESS.md) · [Decisions](docs/DECISIONS.md) · [Setup](docs/SETUP.md)
