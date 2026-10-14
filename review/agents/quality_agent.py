from review.agents.agent_runner import (
    AgentSpec,
    FINDINGS_OUTPUT_INSTRUCTIONS,
    build_groq_client as _build_groq_client,
    run_agent,
)
from review.state import ReviewState


QUALITY_PROMPT_TEMPLATE = """You are a senior software engineer reviewing a pull request diff for
code quality.

Look for code quality issues ONLY - code that works but is costly to
run, read, test or change, such as:
- Performance: database queries inside loops (N+1), quadratic work such
  as membership tests on a list inside a loop, repeated expensive calls
  that could be done once, blocking I/O inside async code
- Maintainability: duplicated logic that already exists in the context,
  a function or class doing several unrelated things, deep nesting,
  magic numbers, misleading names, dead or commented-out code
- Tests: new behavior or a fixed bug with no test in the diff
- Documentation: a new public function, class or endpoint with no
  docstring, or a docstring that no longer matches the code

Report only issues that matter for this codebase, with a concrete fix.
Use LOW or INFO for minor issues, and MEDIUM or above only when the
issue has a real cost (a slow path, a likely future bug). Do NOT report
security vulnerabilities or logic bugs: other reviewers cover them.

Diff:
{diff}

Related existing code context (may be empty):
{context_summary}

""" + FINDINGS_OUTPUT_INSTRUCTIONS

QUALITY_AGENT = AgentSpec(name="quality", prompt_template=QUALITY_PROMPT_TEMPLATE)


# LangGraph node for the quality agent.
def quality_node(state: ReviewState) -> dict:
    # _build_groq_client is looked up at call time, so tests can
    # monkeypatch agents.quality._build_groq_client with a fake.
    return run_agent(QUALITY_AGENT, state, _build_groq_client())
