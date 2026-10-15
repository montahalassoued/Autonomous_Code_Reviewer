# Agents

**File purpose:** what each node of the LangGraph review pipeline does, and how the three review agents share one runner.
**Target design:** [ARCHITECTURE.md](ARCHITECTURE.md). Retrieval details: [RAG.md](RAG.md).

## Graph

```text
START → retriever ─(quality < QUALITY_THRESHOLD and attempts < MAX_RETRIEVAL_ATTEMPTS)─► retriever
                  └─(otherwise)─► security | logic | quality  (parallel)
      → join_agents (barrier) → synthesizer → END
```

The graph is forward-only, so it always terminates (`review/graph.py`). In the target design the retriever is replaced by `build_context` and an optional context subagent; the three agents and the synthesizer stay.

## The three review agents

Each agent is one category of the review. Its name is its LangGraph node, the `agent` field of its findings, and its section in the PR comment.

| Agent | Looks for | Does not report | File |
| --- | --- | --- | --- |
| **Security** | OWASP / CWE vulnerabilities (injection, XSS, secrets, SSRF, path traversal, weak crypto), authn / authz / IDOR on every changed endpoint, sensitive data in responses and logs, risky new dependencies | logic bugs, quality | `review/agents/security/` |
| **Logic** | Correctness bugs: wrong conditions, off-by-one, unhandled None / empty / edge cases, unchecked results of external calls, states the code forgets (flags, roles, lifecycle), swallowed errors, broken contracts for existing callers, wrong API use, async and concurrency mistakes | security, quality | `review/agents/logic/` |
| **Quality** | Performance (N+1, quadratic loops, blocking I/O in async, unbounded queries and caches, work repeated in loops), maintainability (duplication, functions doing too much, dead code), missing, weak or flaky tests, missing docs on public interfaces. Never what a linter catches | security, logic bugs | `review/agents/quality/` |

Each prompt says what the other agents cover, so the same issue isn't reported twice under two categories.

## Shared runner (`review/agents/agent_runner.py`)

Each agent is a folder with everything specific to it:

```text
review/agents/<category>/
  agent.py         AgentSpec(name, prompt_template, rule_ids) + the LangGraph node
  prompts/NN_*.md  the prompt, split by topic, read in number order (add a prompt = add a file)
  rules.py         closed list of rule_id the agent may report (Security: with the CWE)
  examples.json    few-shot: one finding to imitate, one trap that must give []
  tools/           toolset.py (the tools the agent may call) + one file per tool only this agent uses
```

**Tools.** Each agent can call a few read-only tools to look outside the diff, at most `max_tool_calls` (3) per review; at the limit it must answer (`tool_choice="none"`).

| Agent | Tools | Agent-specific |
| --- | --- | --- |
| Security | `read_file`, `search_code`, `find_auth_guards`, `find_input_guards`, `find_security_settings`, `find_sql_queries` | `security/tools/find_auth_guards.py` (router dependencies, `Depends`, middleware), `find_input_guards.py` (validation and escaping by sink kind: path, html, shell, url, xml, schema), `find_security_settings.py` (CORS, CSRF, cookies, DEBUG, TLS, JWT settings, tests excluded), `find_sql_queries.py` (where SQL is built or run) |
| Logic | `read_file`, `find_callers`, `find_callees`, `find_exception_handlers` | `logic/tools/find_exception_handlers.py` (who raises or catches an exception) |
| Quality | `read_file`, `search_code`, `find_tests`, `find_similar_functions` | `quality/tools/find_similar_functions.py` (an existing helper with a similar name, from the call graph) |

The implementations shared by several agents (and later by the context subagent) live in `review/agents/tools/`: `tool_definition.py` (the `Tool` type, `ToolContext`, and `execute_tool_call`, which never raises, caps output at 4000 characters and wraps it in a nonce), `repo_files.py`, `code_graph.py` (legacy NetworkX graph) and `related_tests.py`. A tool declares the resource it needs (`repo` or `graph`) and is only offered when that resource exists. Safety: paths are resolved inside the checkout, symlinks are never followed, secret files (`.env*`, `*.pem`, `*.key`, `.git/`) are never read, and search is literal (no regex). Limitation: the checkout is cloned once at worker start, so tools see that version, not the PR's.

`review/agents/prompt_assembler.py` builds the prompt once at import: the agent's `prompts/*.md` → its rules → its examples → the shared `review/agents/prompts/*.md` (the diff and context, then the JSON output contract). Everything else is shared:

- **Model:** `REVIEW_AGENT_MODEL` (default `openai/gpt-oss-120b`, provisional until the benchmark picks one), temperature 0.1.
- **Prompt:** `{diff}` and `{context_summary}` are filled from `ReviewState` in one pass (`fill_prompt`), so braces in examples or in the diff never break it; every prompt ends with the same output contract (`prompts/99_output_contract.md`).
- **Parsing:** strips code fences, falls back to the outermost `[...]`, and returns `[]` on anything unparseable. A malformed response never crashes the review.
- **Validation:** a finding is kept only with a known severity, a positive integer line and a non-empty file, title, description and suggestion. Dropped findings are logged.
- **Output:** `{"findings": [...]}`, merged by the `operator.add` reducer in `review/state.py`.

**Finding fields:** `agent`, `severity` (CRITICAL / HIGH / MEDIUM / LOW / INFO), `rule_id` (from the agent's `rules.py`, else `other`), `confidence` (high / medium / low; a low-confidence finding never blocks), `file_path`, `line_number`, `title`, `description`, `evidence`, `suggestion`.

## Retriever (legacy, `review/retriever.py`)

Runs the semantic RAG pipeline (query decomposition, hybrid search, graph expansion, reranking) and an LLM judge that retries while the context score is below `QUALITY_THRESHOLD`, at most `MAX_RETRIEVAL_ATTEMPTS` times. Kept until the eval shows structural context is at least as good; see [RAG.md](RAG.md).

## Synthesizer (`review/synthesizer.py`)

No LLM call. It builds one Markdown comment:

1. A heading and a **verdict**: "⛔ N blocking issue(s) to fix before merging" or "✅ Nothing blocking".
2. A **size note** when the diff has more than 400 changed lines, since reviews miss more on large PRs.
3. A summary table: one row per category, one column per severity.
4. One section per category, always in the order **Security → Logic → Code quality**, each with its count. Findings are sorted by severity inside a section. An empty category says "No issues found", so the reader can see that all three reviews ran.
5. Each finding carries a label derived from its severity: CRITICAL / HIGH → `[blocking]`, MEDIUM → `[suggestion]`, LOW / INFO → `[nit]`. Nits are folded in a `<details>` block so they don't bury the real issues.
6. Findings from an unknown agent go under "Other" instead of being dropped.

A review with no findings at all is a single "No issues found" line. The comment is posted, or edited in place, by `tools/github_tools.py`.
