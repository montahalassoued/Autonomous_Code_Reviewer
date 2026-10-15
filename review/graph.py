from functools import partial
from pathlib import Path

from langgraph.graph import StateGraph, START, END
from review.agents.tools.tool_definition import ToolContext
from review.state import ReviewState
from review.agents.security.agent import security_node
from review.agents.logic.agent import logic_node
from review.agents.quality.agent import quality_node
from review.synthesizer import synthesizer_node


# The review categories: each is a node name, an agent module and the
# `agent` field of its findings. review/synthesizer.py renders them in this order.
REVIEW_AGENTS = ["security", "logic", "quality"]


# Barrier: LangGraph waits here for all parallel agents, so the synthesizer runs once.
def join_agents_node(state: ReviewState) -> dict:
    print(f"[join_agents] all parallel agents finished, {len(state['findings'])} findings total")
    return {}


# Assembles and compiles the StateGraph.
def build_graph(graph_index, repo_path: str | None = None):
    graph = StateGraph(ReviewState)

    # The agents' tools read the checkout and the call graph; both stay out of ReviewState.
    tool_context = ToolContext(repo_root=Path(repo_path) if repo_path else None, code_graph=graph_index)
    graph.add_node("security", partial(security_node, tool_context=tool_context))
    graph.add_node("logic", partial(logic_node, tool_context=tool_context))
    graph.add_node("quality", partial(quality_node, tool_context=tool_context))
    graph.add_node("join_agents", join_agents_node)
    graph.add_node("synthesizer", synthesizer_node)

    # FAN-OUT: START points to every review agent, so they run in parallel.
    # CONVERGENCE: every domain agent points to the SAME single downstream
    # node (join_agents) via a plain edge. LangGraph automatically waits
    # for all active parallel branches to finish before running a node
    # that they all share as their only target - this is what guarantees
    # join_agents (and therefore synthesizer) runs exactly once per review.
    for agent in REVIEW_AGENTS:
        graph.add_edge(START, agent)
        graph.add_edge(agent, "join_agents")

    graph.add_edge("join_agents", "synthesizer")
    graph.add_edge("synthesizer", END)

    return graph.compile()
