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


def join_agents_node(state: ReviewState) -> dict:
    """
    Barrier node - LangGraph waits for ALL parallel agents to finish
    before running this node, because it is the single common downstream
    target they all converge on via plain (non-conditional) edges.

    Without it, each agent pointing straight at the next step would
    run that step once per finished agent instead of once per review.

    Args:
        state: the current ReviewState, with all agents' findings merged

    Returns:
        an empty update - this node only exists as a synchronization point
    """
    print(f"[join_agents] all parallel agents finished, {len(state['findings'])} findings total")
    return {}


def route_after_retriever(state: ReviewState) -> list[str] | str:
    """
    Corrective RAG decision: is retrieval quality good enough, or do we
    retry (bounded by MAX_RETRIEVAL_ATTEMPTS to guarantee termination)?

    IMPORTANT: when fanning out to multiple parallel nodes, LangGraph
    requires the routing function to return the ACTUAL node names
    directly (not abstract keys mapped through a dict) - that's why
    this returns "retriever" / REVIEW_AGENTS as literal node names, and
    build_graph() below calls add_conditional_edges WITHOUT a mapping
    dict.

    Args:
        state: the ReviewState right after a retrieval attempt

    Returns:
        "retriever" to retry, or REVIEW_AGENTS to fan out to all agents
    """
    quality_ok = state["retrieval_quality_score"] >= QUALITY_THRESHOLD
    attempts_exhausted = state["retrieval_attempts"] >= MAX_RETRIEVAL_ATTEMPTS

    if quality_ok or attempts_exhausted:
        return REVIEW_AGENTS
    return "retriever"


def build_graph(collection, all_documents: list[str], all_ids: list[str], graph_index):
    """
    Assembles and compiles the StateGraph.

    Args:
        collection: a populated Chroma collection (db/chroma.py),
                    passed straight through to review/retriever.py
        all_documents: full corpus text list backing hybrid_search.py's
                        BM25 index (same order as all_ids)
        all_ids: full corpus id list (same order as all_documents)
        graph_index: the knowledge graph (rag/knowledge_graph.py), or
                     None if unavailable (degrades gracefully: no
                     caller info in retrieved chunks)

    Returns:
        a compiled graph ready to run via .invoke(initial_state)
    """
    def retriever_node(state: ReviewState) -> dict:
        """
        Binds the RAG pipeline's repo-wide objects (collection,
        all_documents, all_ids, graph_index) into review/retriever.py's
        real retriever_node via closure, since those objects are too
        large and too repo-wide to belong in per-review ReviewState.
        """
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
