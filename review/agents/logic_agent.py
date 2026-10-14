from review.agents.agent_runner import (
    AgentSpec,
    FINDINGS_OUTPUT_INSTRUCTIONS,
    build_groq_client as _build_groq_client,
    run_agent,
)
from review.state import ReviewState


LOGIC_PROMPT_TEMPLATE = """You are a senior software engineer reviewing a pull request diff for
correctness.

Look for logic bugs ONLY - code that runs but produces the wrong result
or fails on inputs it should handle, such as:
- Wrong or inverted conditions, wrong comparison operators, off-by-one
  errors in ranges, slices and loop bounds
- Missing handling of None, empty collections, zero, negative numbers
  or other edge cases the code will receive
- Error handling that hides failures: swallowed exceptions, a wrong
  fallback value, an error path that leaves state half-updated
- Wrong return values or types, a missing return, a mutated argument
  or a mutable default argument the caller doesn't expect
- Changed contracts: a new signature, return value or raised exception
  that existing callers in the context don't handle
- Wrong use of a library or API (wrong argument order, units, time
  zones, integer vs float division)
- Async and concurrency mistakes: a missing await, shared state
  modified without a lock, check-then-act races

Only report a bug you can point to in the diff, and explain the input
or scenario that triggers it. Do NOT report security vulnerabilities or
style and maintainability issues: other reviewers cover them.

Diff:
{diff}

Related existing code context (may be empty):
{context_summary}

""" + FINDINGS_OUTPUT_INSTRUCTIONS

LOGIC_AGENT = AgentSpec(name="logic", prompt_template=LOGIC_PROMPT_TEMPLATE)


# LangGraph node for the logic agent.
def logic_node(state: ReviewState) -> dict:
    # _build_groq_client is looked up at call time, so tests can
    # monkeypatch agents.logic._build_groq_client with a fake.
    return run_agent(LOGIC_AGENT, state, _build_groq_client())
