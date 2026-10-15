# CLAUDE.md

Autonomous multi-agent GitHub PR reviewer. A GitHub webhook hits FastAPI and enqueues a job, a worker runs a LangGraph pipeline that gathers repo context, the domain agents analyze the diff in parallel, and a synthesizer posts the review back to GitHub.

**Target design (docs only, not built yet):** context is **structural**, not semantic. A Postgres code index of `main` (symbols, edges, reviews, findings) feeds a deterministic context builder; 3 agents (Security, Logic, Quality); an optional bounded context subagent. See `docs/ARCHITECTURE.md`. New work should move toward it; don't extend the legacy semantic retrieval.

## Working with me (these rules come first)

These rules override any skill or subagent instruction that says otherwise.

1. **Terminal commands: there are two kinds.**
   - **Run these yourself, without asking me:** local git work (`git status`, `diff`, `log`, `show`, `add`, `rm`, `restore` on files you changed, `commit` following the `commit-message` skill), local branches (`git switch -c`), the offline unit tests (`pytest tests/unit`), and read-only checks. After each commit, tell me the commit title and which files went into it.
   - **Give these to me to run, because I want to learn them:** Docker (`docker build/run/compose`), Azure (`az ...`), `git push` and any other command that touches GitHub or the cloud, `pip install`, starting servers (`uvicorn`), tests that make real API calls (they use quota), and anything destructive (`git reset --hard`, force-push, deleting branches). For each one:
     - Give the exact command in a code block, ready to copy.
     - Explain what it does, what each important flag means, and what output to expect.
     - Wait for me to run it and paste the result before continuing.
2. **Show me every file change.**
   - Before editing, say which files you're going to change and why.
   - After editing, list every changed file as a clickable link, with a short summary of what changed in each one.
   - Never change files I didn't expect without telling me.
3. **Explain before asking.** Before asking me any question, first explain the context: what you found, what the options are, and what each option means for the project. Then ask the question, so I can read the explanation before I answer.
4. **One branch per piece of work.** Every feature, fix or refactor starts on its own branch off `main` (`feat/…`, `fix/…`, `refactor/…`, `ci/…`, `docs/…`, `chore/…`), and reaches `main` only through a PR. Never commit directly on `main`. Follow the `git-workflow` skill.
5. **Senior-level structure.** Every new file and folder goes in the folder for its role, with a name that says that role (never `utils.py`, `helpers.py`, `new.py`). Follow the `project-structure` skill.

## Project layout

```text
app/        api/ (HTTP routers) · jobs/ (queue, dedupe) · main.py (API) · worker.py · bootstrap.py
review/     graph.py (LangGraph orchestrator) · state.py · synthesizer.py · retriever.py (legacy) · agents/ (agent_runner.py, prompt_assembler.py, prompts/ shared, tools/ shared, one folder per agent: security/ logic/ quality/ with agent.py, prompts/*.md, rules.py, examples.json, tools/ (toolset.py + one file per own tool))
rag/        parsing, chunking, call graph, embeddings, search, reranking
tools/      external service clients (GitHub)       db/      postgres.py · migration_runner.py · migrations/*.sql · chroma.py (legacy)
eval/       LLM benchmark, datasets, results         infra/   Azure setup
tests/      unit/ (offline, CI) · integration/ (real APIs, manual) · conftest.py
docs/       IMPLEMENTATION_PLAN, CI, SETUP, DECISIONS, ...
```

## Pipeline (current code, legacy retrieval)

```text
GitHub webhook → app/api/webhook.py (verify, dedupe, enqueue) → Azure Storage Queue → app/worker.py → review/graph.py (LangGraph)
  START → retriever (corrective RAG loop: retry while quality < QUALITY_THRESHOLD and attempts < MAX_RETRIEVAL_ATTEMPTS)
        → security | logic | quality  (parallel, one shared runner: review/agents/agent_runner.py)
        → join_agents (barrier) → synthesizer → END → tools/github_tools.py   (forward-only: always terminates)
```

- **Shared state:** `review/state.py` holds `ReviewState`. `findings` uses an `operator.add` reducer so parallel agents don't overwrite each other.
- **RAG (`rag/`, legacy, replaced in Phase 4 after the eval):** ast_parser (tree-sitter) → contextual_chunker (Gemini) → knowledge_graph (NetworkX) → embedder (Voyage) + `db/chroma.py` → hybrid_search (BM25 + dense + RRF) → graph_retriever → reranker (local BAAI/bge-reranker-v2-m3).
- **Startup:** the API (`app/main.py`) only creates the job queue (`JOBS_BACKEND=memory|azure`) and the dedupe store (`DEDUPE_BACKEND=memory|postgres`; postgres applies the SQL migrations in `db/migrations/` first and dedupes on the `reviews` table). The worker (`python -m app.worker`) clones `TARGET_REPO_URL` (or uses `REPO_PATH`) via `app/bootstrap.py`, ingests it, and compiles the graph once.
- **No web UI:** reviews are triggered only by the GitHub webhook. The Streamlit dashboard was removed.

## Commands

```bash
venv/Scripts/python.exe -m pytest tests/unit   # fast, no API calls
venv/Scripts/python.exe -m pytest tests/integration    # REAL Groq/Gemini/Voyage/GitHub calls - uses quota, user runs it
uvicorn app.main:app --host 0.0.0.0 --port 8000         # API
docker-compose up --build                               # full stack (azurite + postgres + api + worker)
venv/Scripts/python.exe -m db.migration_runner           # apply SQL migrations to DATABASE_URL
```

## Environment

Copy `.env.example` to `.env`. You need `GROQ_API_KEY`, `GOOGLE_API_KEY`, `VOYAGE_API_KEY`, `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, and `TARGET_REPO_URL` or `REPO_PATH`. `DATABASE_URL` is set by docker compose; set it yourself only to reach Postgres from your machine. Never commit `.env`.

## Conventions

- **Comments are one short `#` line.** No `"""` header at the top of files (the file and folder names say the role), no multi-line docstrings, no Args/Returns blocks. The longer *why* goes in `docs/DECISIONS.md`. See the `clean-code` skill.
- **API clients:** each module has a `_build_*_client()` that reads its key from the environment and raises a clear `RuntimeError` if it's missing.
- **LLM output parsing never crashes the pipeline.** A malformed response degrades to zero findings, and invalid findings are dropped and logged (see `parse_findings` / `to_finding` in `review/agents/agent_runner.py`).
- **Review agents** (security, logic, quality) are each a folder (`agent.py`, `prompts/NN_*.md`, `rules.py`, `examples.json`); the Groq call, parsing and validation are in `review/agents/agent_runner.py`. The model is `REVIEW_AGENT_MODEL` (provisional default `openai/gpt-oss-120b`, to be picked with `eval/llm_benchmark.py`). The legacy decomposer and judge still name the retired `llama-3.3-70b-versatile`. Use the `add-review-agent` skill to add or change a reviewer.
- **Tests:** `tests/unit/` (offline, mocked, run in CI) and `tests/integration/` (real APIs, auto-marked `integration`). Use the `write-tests` skill.
- **Architecture decisions** go in `docs/DECISIONS.md`. Use the `log-decision` skill.
- **Commits** are a single title line `type(scope): summary` with no Co-Authored-By or Claude attribution. Use the `commit-message` skill. A hook enforces this.
- Build order and status: `docs/PROGRESS.md`. Deeper docs: `docs/ARCHITECTURE.md`, `docs/RAG.md`, `docs/AGENTS.md`.

## Gotchas

- The routing functions in `review/graph.py` return real node names. `add_conditional_edges` must **not** get a mapping dict when fanning out to a list.
- The graph is forward-only. Domain agents point to `join_agents`, which goes straight to `synthesizer`. Never add an edge back to the agents or the retriever after `join_agents`, because the old supervisor loop never terminated on clean PRs.
- Chunk ids are `file::Class.method` (`make_chunk_id` in `db/chroma.py`), with a `#2` suffix for duplicate names. Never build ids by hand.
- Implementation roadmap: `docs/IMPLEMENTATION_PLAN.md` (Phase 0 is done). What is built today: `docs/PROGRESS.md`.
- Tests that set env vars must **force** them (`os.environ[...] = ...`), not use `setdefault`, because other tests `load_dotenv()` the real `.env`.
- Evaluation: `eval/llm_benchmark.py` compares models on the real prompts. The full review eval harness (Phase 1) is not built yet.
