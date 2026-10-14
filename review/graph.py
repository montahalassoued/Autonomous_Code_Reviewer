from langgraph.graph import StateGraph, START, END
from review.state import ReviewState
from review.retriever import (
    retriever_node as _run_retriever_node,
    QUALITY_THRESHOLD,
    MAX_RETRIEVAL_ATTEMPTS,
)
from review.agents.security_agent import security_node
from review.agents.logic_agent import logic_node
from review.agents.quality_agent import quality_node
from review.synthesizer import synthesizer_node


# The review categories: each is a node name, an agent module and the
# `agent` field of its findings. review/synthesizer.py renders them in this order.
REVIEW_AGENTS = ["security", "logic", "quality"]


# Barrier: LangGraph waits here for all parallel agents, so the synthesizer runs once.
def join_agents_node(state: ReviewState) -> dict:
    print(f"[join_agents] all parallel agents finished, {len(state['findings'])} findings total")
    return {}


# Retry retrieval while quality is low and attempts remain, else fan out to the agents.
def route_after_retriever(state: ReviewState) -> list[str] | str:
    quality_ok = state["retrieval_quality_score"] >= QUALITY_THRESHOLD
    attempts_exhausted = state["retrieval_attempts"] >= MAX_RETRIEVAL_ATTEMPTS

    if quality_ok or attempts_exhausted:
        return REVIEW_AGENTS
    return "retriever"


# Assembles and compiles the StateGraph.
def build_graph(collection, all_documents: list[str], all_ids: list[str], graph_index):
    # Bind the repo-wide RAG objects (too big for ReviewState) into the retriever node.
    def retriever_node(state: ReviewState) -> dict:
        return _run_retriever_node(state, collection, all_documents, all_ids, graph_index)

    graph = StateGraph(ReviewState)

    # Register every node
    graph.add_node("retriever", retriever_node)
    graph.add_node("security", security_node)
    graph.add_node("logic", logic_node)
    graph.add_node("quality", quality_node)
    graph.add_node("join_agents", join_agents_node)
    graph.add_node("synthesizer", synthesizer_node)

    graph.add_edge(START, "retriever")

    # retriever decides: retry itself, or fan out to all review agents.
    # route_after_retriever returns real node names (a list for the
    # parallel fan-out), so no mapping dict is passed.
    graph.add_conditional_edges("retriever", route_after_retriever)

    # CONVERGENCE: every domain agent points to the SAME single downstream
    # node (join_agents) via a plain edge. LangGraph automatically waits
    # for all active parallel branches to finish before running a node
    # that they all share as their only target - this is what guarantees
    # join_agents (and therefore synthesizer) runs exactly once per review.
    for agent in REVIEW_AGENTS:
        graph.add_edge(agent, "join_agents")

    graph.add_edge("join_agents", "synthesizer")
    graph.add_edge("synthesizer", END)

    return graph.compile()
