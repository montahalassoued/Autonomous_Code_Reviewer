# Agents

**File purpose:** what each node of the LangGraph review pipeline does, and how the three review agents share one runner.
**Target design:** [ARCHITECTURE.md](ARCHITECTURE.md). Retrieval details: [RAG.md](RAG.md).

## Graph

```text
START → retriever ─(quality < QUALITY_THRESHOLD and attempts < MAX_RETRIEVAL_ATTEMPTS)─► retriever
                  └─(otherwise)─► security | logic | quality  (parallel)
      → join_agents (barrier) → synthesizer → END
```

The graph is forward-only, so it always terminates (`agents/graph.py`). In the target design the retriever is replaced by `build_context` and an optional context subagent; the three agents and the synthesizer stay.

## The three review agents

Each agent is one category of the review. Its name is its LangGraph node, the `agent` field of its findings, and its section in the PR comment.

| Agent | Looks for | Does not report | File |
| --- | --- | --- | --- |
| **Security** | OWASP / CWE vulnerabilities (injection, XSS, secrets, SSRF, path traversal, weak crypto) and authn / authz / IDOR on every changed endpoint | logic bugs, quality | `agents/security.py` |
| **Logic** | Correctness bugs: wrong conditions, off-by-one, unhandled None / empty / edge cases, swallowed errors, broken contracts for existing callers, wrong API use, async and concurrency mistakes | security, quality | `agents/logic.py` |
| **Quality** | Performance (N+1, quadratic loops, blocking I/O in async), maintainability (duplication, functions doing too much, dead code), missing tests, missing docs on public interfaces | security, logic bugs | `agents/quality.py` |

Each prompt says what the other reviewers cover, so the same issue isn't reported twice under two categories.

## Shared runner (`agents/agent_runner.py`)

An agent file holds only its prompt and an `AgentSpec(name, prompt_template)`. Everything else is shared:

- **Model:** `REVIEW_AGENT_MODEL` (default `openai/gpt-oss-120b`, provisional until the benchmark picks one), temperature 0.1.
- **Prompt:** `{diff}` and `{context_summary}` are filled from `ReviewState`; every prompt ends with the same `FINDINGS_OUTPUT_INSTRUCTIONS` (a JSON array of findings).
- **Parsing:** strips code fences, falls back to the outermost `[...]`, and returns `[]` on anything unparseable. A malformed response never crashes the review.
- **Validation:** a finding is kept only with a known severity, a positive integer line and a non-empty file, title, description and suggestion. Dropped findings are logged.
- **Output:** `{"findings": [...]}`, merged by the `operator.add` reducer in `agents/state.py`.

**Finding fields:** `agent`, `severity` (CRITICAL / HIGH / MEDIUM / LOW / INFO), `file_path`, `line_number`, `title`, `description`, `evidence`, `suggestion`.

## Retriever (legacy, `agents/retriever.py`)

Runs the semantic RAG pipeline (query decomposition, hybrid search, graph expansion, reranking) and an LLM judge that retries while the context score is below `QUALITY_THRESHOLD`, at most `MAX_RETRIEVAL_ATTEMPTS` times. Kept until the eval shows structural context is at least as good; see [RAG.md](RAG.md).

## Synthesizer (`agents/synthesizer.py`)

No LLM call. It builds one Markdown comment:

1. A heading and the total number of findings.
2. A summary table: one row per category, one column per severity.
3. One section per category, always in the order **Security → Logic → Code quality**, each with its count. Findings are sorted by severity inside a section. An empty category says "No issues found", so the reader can see that all three reviews ran.
4. Findings from an unknown agent go under "Other" instead of being dropped.

A review with no findings at all is a single "No issues found" line. The comment is posted, or edited in place, by `tools/github_tools.py`.
