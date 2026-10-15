from pathlib import Path

from review.agents.agent_runner import AgentSpec, build_groq_client as _build_groq_client, run_agent
from review.agents.prompt_assembler import assemble_prompt
from review.agents.quality.rules import QUALITY_RULES
from review.agents.quality.tools import QUALITY_TOOLS
from review.agents.tools.tool_definition import ToolContext
from review.state import ReviewState


AGENT_DIR = Path(__file__).parent

QUALITY_AGENT = AgentSpec(
    name="quality",
    prompt_template=assemble_prompt(AGENT_DIR, QUALITY_RULES),
    rule_ids=frozenset(QUALITY_RULES),
    tools=QUALITY_TOOLS,
)


# LangGraph node for the quality agent.
def quality_node(state: ReviewState, tool_context: ToolContext | None = None) -> dict:
    # Looked up at call time, so tests can monkeypatch it with a fake client.
    return run_agent(QUALITY_AGENT, state, _build_groq_client(), tool_context)
