---
name: project-structure
description: Decide where new code, tests and docs go and how to name them, so every file and folder is named after its role. Use before creating any new file, folder, module, test or doc in this project, and when moving or renaming code.
---

# Project structure and naming

**Rule:** a reader should know what a file or folder does **from its name and location alone**, before opening it. Names describe the **role** (what it is responsible for), never the implementation detail, the author or the date.

## Where things go

| Folder | Role | Put here |
|---|---|---|
| `app/api/` | HTTP layer | FastAPI routers only: parse the request, validate it, delegate. No business logic. |
| `app/jobs/` | Background job infrastructure | Queue, dedupe, job models, backend factory |
| `app/` (top level) | Process entry points | `main.py` (API), `worker.py` (worker), `bootstrap.py` (shared startup) |
| `review/` | Review pipeline | `graph.py` (LangGraph orchestrator), `state.py`, `synthesizer.py`, `llm_client.py` (the agents' Groq calls), `review_tracing.py` (Langfuse), input safety (`untrusted_input.py`, `injection_detector.py`), output safety (`output_sanitizer.py`, `secret_redactor.py`) |
| `review/agents/` | The review agents | `agent_runner.py` (executes any agent), `prompt_assembler.py`, shared `prompts/`, and one folder per category |
| `review/agents/<category>/` | One review agent | `agent.py`, `prompts/NN_topic.md`, `rules.py`, `examples.json`, `tools/` (`toolset.py` + one `find_*.py` per tool only this agent uses, + a role-named helper shared by those tools, e.g. `python_source_scan.py`) |
| `review/agents/tools/` | Read-only tools shared by agents | `tool_definition.py` (Tool, ToolContext, safe execution), one module per tool family |
| `rag/` | Code indexing | The Postgres indexer (`file_classifier.py`, `symbol_extractor.py`, `call_resolver.py`, `code_indexer.py`, `index_writer.py`), and the legacy NetworkX graph the agent tools read (`ast_parser.py`, `knowledge_graph.py`, `ingestion.py`) until they use Postgres |
| `tools/` | External service clients | One module per service: `github_tools.py` and so on |
| `db/` | Storage adapters | `postgres.py` (connection pool), `migration_runner.py`, `migrations/NNN_description.sql` |
| `eval/` | Quality measurement | Benchmarks and harnesses. Data goes in `eval/dataset/`, outputs in `eval/results/` |
| `tests/unit/` | Offline tests | No network, no API keys. CI runs this whole folder |
| `tests/integration/` | Real-API tests | Auto-marked `integration`, run manually |
| `infra/` | Cloud setup | Azure scripts and their guide |
| `docs/` | Project docs | The plan, CI, setup, architecture, `DECISIONS.md` |
| `.github/workflows/` | CI/CD | One workflow per purpose |

If a new piece doesn't fit any folder, **stop and propose a new folder** (its name, role and contents) to the user before creating it. Never drop files at the repo root.

## Naming rules

**Files and modules** (snake_case): name the responsibility.
- Good: `job_queue.py`, `dedupe.py`, `diff_parser.py`, `context_builder.py`, `github_tools.py`
- Bad: `utils.py`, `helpers.py`, `misc.py`, `stuff.py`, `new_file.py`, `v2.py`, `final.py`, `test2.py`
- A module named `utils` is a sign that it holds several roles. Split it by role instead.
- One main responsibility per module. If describing it needs "and", consider splitting.

**Folders** (lowercase): a plural noun or a domain name for the role: `agents/`, `jobs/`, `analysis/`.

**Classes** (PascalCase), named after what they are: `ReviewJob`, `InMemoryJobQueue`, `PostgresDedupeStore`. Use the pattern `<Backend><Role>` for implementations of one interface, and `<Role>` for the protocol.

**Functions** (snake_case verbs) say what they do: `enqueue`, `claim`, `parse_patch`, `upsert_summary_comment`. True/false values read as questions: `is_latest`, `has_secret`.

**Tests:**
- `tests/<unit|integration>/test_<module>.py`, mirroring the module name.
- Test functions describe behavior: `test_duplicate_delivery_is_not_enqueued_twice`.

**Constants** (UPPER_CASE) carry their unit: `REVIEW_VISIBILITY_TIMEOUT_S`, `MAX_TOOL_OUTPUT_CHARS`.

**Env vars** (UPPER_CASE) are prefixed by area: `REVIEW_*`, `AZURE_*`, `LANGFUSE_*`, `GITHUB_*`.

**Docs** (UPPER_CASE.md in `docs/`), named after the topic: `CI.md`, `DECISIONS.md`, `IMPLEMENTATION_PLAN.md`.

## Every new file

1. Put it in the right folder from the table above.
2. Give it a role-based name.
3. Start it with its imports: no header comment, the name and folder say its role (see the `clean-code` skill).
4. Add or update its test in the matching `tests/` folder.
5. If you created a new folder or a new kind of file, update the table above and the layout section of `CLAUDE.md`.

## When renaming or moving

Use `git mv` so history is kept. Then update every import and every reference in docs, skills, agents, workflows, `pytest.ini` and `.dockerignore`, and run `tests/unit` before committing.
