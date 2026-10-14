from pathlib import Path

from review.agents.agent_runner import AgentSpec, build_groq_client as _build_groq_client, run_agent
from review.agents.logic.rules import LOGIC_RULES
from review.agents.prompt_assembler import assemble_prompt
from review.state import ReviewState


AGENT_DIR = Path(__file__).parent

LOGIC_AGENT = AgentSpec(
    name="logic",
    prompt_template=assemble_prompt(AGENT_DIR, LOGIC_RULES),
    rule_ids=frozenset(LOGIC_RULES),
)


# LangGraph node for the logic agent.
def logic_node(state: ReviewState) -> dict:
    # Looked up at call time, so tests can monkeypatch it with a fake client.
    return run_agent(LOGIC_AGENT, state, _build_groq_client())
