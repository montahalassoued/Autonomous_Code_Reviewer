# CLAUDE.md

Autonomous multi-agent GitHub PR reviewer. A GitHub webhook hits FastAPI, a LangGraph pipeline retrieves repo context through an advanced RAG pipeline, five domain agents analyze the diff in parallel, and a synthesizer posts one Markdown review comment back to the PR.

## Working with me (these rules come first)

These rules override any skill or subagent instruction that says otherwise.

1. **Terminal commands: there are two kinds.**
   - **Run these yourself, without asking me:** local git work (`git status`, `diff`, `log`, `show`, `add`, `rm`, `restore` on files you changed, `commit` following the `commit-message` skill), the fast offline tests (`test_state.py`, `test_synthesizer.py`, `test_webhook.py`, `test_main.py`), and read-only checks. After each commit, tell me the commit title and which files went into it.
   - **Give these to me to run, because I want to learn them:** Docker (`docker build/run/compose`), Azure (`az ...`), `git push` and any other command that touches GitHub or the cloud, `pip install`, starting servers (`uvicorn`), tests that make real API calls (they use quota), and anything destructive (`git reset --hard`, force-push, deleting branches). For each one:
     - Give the exact command in a code block, ready to copy.
     - Explain what it does, what each important flag means, and what output to expect.
     - Wait for me to run it and paste the result before continuing.
2. **Show me every file change.**
   - Before editing, say which files you're going to change and why.
   - After editing, list every changed file as a clickable link, with a short summary of what changed in each one.
   - Never change files I didn't expect without telling me.
3. **Explain before asking.** Before asking me any question, first explain the context: what you found, what the options are, and what each option means for the project. Then ask the question, so I can read the explanation before I answer.

## Pipeline

```text
GitHub webhook → app/api/webhook.py (verify, dedupe, enqueue) → Azure Storage Queue → app/worker.py → agents/graph.py (LangGraph)
  START → retriever (corrective RAG loop: retry while quality < QUALITY_THRESHOLD and attempts < MAX_RETRIEVAL_ATTEMPTS)
        → security | performance | architecture | testing | documentation  (parallel)
        → join_agents (barrier) → synthesizer → END → tools/github_tools.py   (forward-only: always terminates)
```

- **Shared state:** `agents/state.py` holds `ReviewState`. `findings` uses an `operator.add` reducer so parallel agents don't overwrite each other.
- **RAG (`rag/`):** ast_parser (tree-sitter) → contextual_chunker (Gemini) → knowledge_graph (NetworkX) → embedder (Voyage) + `db/chroma.py` → hybrid_search (BM25 + dense + RRF) → graph_retriever → reranker (local BAAI/bge-reranker-v2-m3).
- **Startup:** the API (`app/main.py`) only creates the job queue and dedupe store (`JOBS_BACKEND=memory|azure`). The worker (`python -m app.worker`) clones `TARGET_REPO_URL` (or uses `REPO_PATH`) via `app/bootstrap.py`, ingests it, and compiles the graph once.
- **No web UI:** reviews are triggered only by the GitHub webhook. The Streamlit dashboard was removed.

## Commands

```bash
venv/Scripts/python.exe -m pytest test/test_state.py test/test_synthesizer.py test/test_webhook.py test/test_main.py   # fast, no API calls
venv/Scripts/python.exe -m pytest                       # full suite - many tests make REAL Groq/Gemini/Voyage/GitHub calls
uvicorn app.main:app --host 0.0.0.0 --port 8000         # API
docker-compose up --build                               # full stack
```

## Environment

Copy `.env.example` to `.env`. You need `GROQ_API_KEY`, `GOOGLE_API_KEY`, `VOYAGE_API_KEY`, `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, and `TARGET_REPO_URL` or `REPO_PATH`. Never commit `.env`.

## Conventions

- Every file starts with a module docstring: what it does, Inputs/Outputs, and **why** it's designed that way. Every function has a docstring with Args/Returns. See the `clean-code` skill.
- **API clients:** each module has a `_build_*_client()` that reads its key from the environment and raises a clear `RuntimeError` if it's missing.
- **LLM output parsing never crashes the pipeline.** A malformed response degrades to zero findings (see `_parse_findings` / `_to_finding` in `agents/security.py`).
- **Domain agents** share one pattern (Groq `llama-3.3-70b-versatile`, temperature 0.1, JSON array → `Finding`). Use the `add-domain-agent` skill to add one.
- **Tests** live in `test/test_*.py`. Integration tests hit real APIs, and unit tests monkeypatch. Use the `write-tests` skill.
- **Architecture decisions** go in the root `DECISIONS.md`. Use the `log-decision` skill.
- **Commits** are a single title line `type(scope): summary` with no Co-Authored-By or Claude attribution. Use the `commit-message` skill. A hook enforces this.
- Build order and status: `docs/PROGRESS.md`. Deeper docs: `docs/ARCHITECTURE.md`, `docs/RAG.md`, `docs/AGENTS.md`.

## Gotchas

- The routing functions in `agents/graph.py` return real node names. `add_conditional_edges` must **not** get a mapping dict when fanning out to a list.
- The graph is forward-only. Domain agents point to `join_agents`, which goes straight to `synthesizer`. Never add an edge back to the agents or the retriever after `join_agents`, because the old supervisor loop never terminated on clean PRs.
- Chunk ids are `file::Class.method` (`make_chunk_id` in `db/chroma.py`), with a `#2` suffix for duplicate names. Never build ids by hand.
- Implementation roadmap: `docs/IMPLEMENTATION_PLAN.md` (Phase 0 is done).
- Tests that set env vars must **force** them (`os.environ[...] = ...`), not use `setdefault`, because other tests `load_dotenv()` the real `.env`.
- `eval/ragas_eval.py` is still a stub.
