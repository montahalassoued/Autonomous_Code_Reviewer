from pathlib import Path

from review.agents.agent_runner import AgentSpec, build_groq_client as _build_groq_client, run_agent
from review.agents.prompt_assembler import assemble_prompt
from review.agents.security.rules import SECURITY_RULES
from review.agents.security.tools import SECURITY_TOOLS
from review.agents.tools.tool_definition import ToolContext
from review.state import ReviewState


AGENT_DIR = Path(__file__).parent

SECURITY_AGENT = AgentSpec(
    name="security",
    prompt_template=assemble_prompt(AGENT_DIR, SECURITY_RULES),
    rule_ids=frozenset(SECURITY_RULES),
    tools=SECURITY_TOOLS,
)


# LangGraph node for the security agent.
def security_node(state: ReviewState, tool_context: ToolContext | None = None) -> dict:
    # Looked up at call time, so tests can monkeypatch it with a fake client.
    return run_agent(SECURITY_AGENT, state, _build_groq_client(), tool_context)
