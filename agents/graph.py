"""
graph.py - builds and compiles the LangGraph StateGraph.

This module defines the TOPOLOGY of the review pipeline: which nodes
exist, how they connect, and the routing logic that decides what runs
next. At this stage, node implementations are stubs (they return fake
data) — the goal here is to validate the graph structure itself:
sequencing, parallel fan-out, and conditional routing.

Real node logic (calling Groq, querying Qdrant, etc.) will replace these
stubs in later steps (retriever.py, security.py, etc.) without touching
this file's topology.
"""

from langgraph.graph import StateGraph, END
from agents.state import ReviewState


# ---------------------------------------------------------------------------
# STUB NODES
# Each node is a plain function: reads `state`, returns a PARTIAL dict of
# the fields it updates. LangGraph merges that partial dict into the full
# state using the reducers defined in state.py (operator.add for findings).
# ---------------------------------------------------------------------------


def supervisor_node(state: ReviewState) -> dict:
    """
    Decides what happens next by inspecting the current state.

    Real version (later) will also do bookkeeping; for now it only
    decides routing, so it returns an empty update.
    """
    print("[supervisor] inspecting state...")
    return {}


def retriever_node(state: ReviewState) -> dict:
    """
    STUB - will become the corrective RAG loop (Step 10).
    For now, fakes retrieval by returning one dummy chunk with a
    passing quality score, so we can validate the graph moves on.
    """
    print("[retriever] fetching context (stub)...")
    return {
        "retrieved_context": [
            {
                "file_path": "auth/utils.py",
                "function_name": "verify_password",
                "code": "def verify_password(p, h): ...",
                "context": "stub context",
                "relevance_score": 0.9,
                "callers": [],
            }
        ],
        "retrieval_quality_score": 0.9,
        "retrieval_attempts": state["retrieval_attempts"] + 1,
    }


def security_node(state: ReviewState) -> dict:
    """STUB - will become agents/security.py (Step 11)."""
    print("[security] analyzing (stub)...")
    return {
        "findings": [
            {
                "agent": "security",
                "severity": "CRITICAL",
                "file_path": "auth/login.py",
                "line_number": 2,
                "title": "SQL Injection (stub)",
                "description": "stub finding",
                "evidence": "stub",
                "suggestion": "stub",
            }
        ]
    }


def performance_node(state: ReviewState) -> dict:
    """STUB - will become agents/performance.py (Step 14)."""
    print("[performance] analyzing (stub)...")
    return {
        "findings": [
            {
                "agent": "performance",
                "severity": "MEDIUM",
                "file_path": "auth/login.py",
                "line_number": 5,
                "title": "N+1 query (stub)",
                "description": "stub finding",
                "evidence": "stub",
                "suggestion": "stub",
            }
        ]
    }


def architecture_node(state: ReviewState) -> dict:
    """STUB - will become agents/architecture.py (Step 14)."""
    print("[architecture] analyzing (stub)...")
    return {"findings": []}


def testing_node(state: ReviewState) -> dict:
    """STUB - will become agents/testing.py (Step 14)."""
    print("[testing] analyzing (stub)...")
    return {"findings": []}


def documentation_node(state: ReviewState) -> dict:
    """STUB - will become agents/documentation.py (Step 14)."""
    print("[documentation] analyzing (stub)...")
    return {"findings": []}


def join_agents_node(state: ReviewState) -> dict:
    """
    Barrier node - LangGraph waits for ALL 5 parallel agents to finish
    before running this node, because it is the single common downstream
    target they all converge on via plain (non-conditional) edges.

    This is what fixes the "supervisor re-routes once per finished agent"
    bug: instead of each agent pointing straight back to supervisor
    (which re-evaluates routing 5 times), they all point here once, and
    THIS node is the only one that hands back to supervisor.
    """
    print(f"[join_agents] all parallel agents finished, {len(state['findings'])} findings total")
    return {}


def synthesizer_node(state: ReviewState) -> dict:
    """
    STUB - will become agents/synthesizer.py (Step 12).
    For now, just counts findings to prove they all arrived.
    """
    print(f"[synthesizer] received {len(state['findings'])} findings total")
    lines = [f"- [{f['severity']}] {f['title']} ({f['agent']})" for f in state["findings"]]
    return {"final_review_markdown": "## Review\n" + "\n".join(lines)}


# ---------------------------------------------------------------------------
# ROUTING FUNCTIONS
# These read the state and return a STRING KEY (or list of node names for
# fan-out) telling LangGraph which edge to follow.
# ---------------------------------------------------------------------------


DOMAIN_AGENTS = ["security", "performance", "architecture", "testing", "documentation"]


def route_after_supervisor(state: ReviewState) -> list[str] | str:
    """
    First routing decision: do we need to retrieve context, or do we
    already have findings (review done) and should go to synthesizer?

    IMPORTANT: when fanning out to multiple parallel nodes, LangGraph
    requires the routing function to return the ACTUAL node names
    directly (not abstract keys mapped through a dict) - that's why
    this returns "retriever" / DOMAIN_AGENTS / "synthesizer" as literal
    node names, and build_graph() below calls add_conditional_edges
    WITHOUT a mapping dict for this case.
    """
    if not state["retrieved_context"]:
        return "retriever"
    if not state["findings"]:
        return DOMAIN_AGENTS
    return "synthesizer"


def route_after_retriever(state: ReviewState) -> list[str] | str:
    """
    Corrective RAG decision: is retrieval quality good enough, or do we
    retry (bounded by retrieval_attempts to avoid infinite loops)?
    Returns actual node names, same reasoning as route_after_supervisor.
    """
    quality_ok = state["retrieval_quality_score"] >= 0.5
    attempts_exhausted = state["retrieval_attempts"] >= 3

    if quality_ok or attempts_exhausted:
        return DOMAIN_AGENTS
    return "retriever"


# ---------------------------------------------------------------------------
# GRAPH ASSEMBLY
# ---------------------------------------------------------------------------


def build_graph():
    """
    Assembles and compiles the StateGraph.

    Returns a compiled graph ready to run via .invoke(initial_state).
    """
    graph = StateGraph(ReviewState)

    # Register every node
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("retriever", retriever_node)
    graph.add_node("security", security_node)
    graph.add_node("performance", performance_node)
    graph.add_node("architecture", architecture_node)
    graph.add_node("testing", testing_node)
    graph.add_node("documentation", documentation_node)
    graph.add_node("join_agents", join_agents_node)
    graph.add_node("synthesizer", synthesizer_node)

    graph.set_entry_point("supervisor")

    # supervisor decides where to go. route_after_supervisor returns the
    # ACTUAL node name(s) directly - "retriever", DOMAIN_AGENTS (a list,
    # for true parallel fan-out), or "synthesizer". No mapping dict needed.
    graph.add_conditional_edges("supervisor", route_after_supervisor)

    # retriever decides: retry itself, or fan out to all 5 agents directly.
    graph.add_conditional_edges("retriever", route_after_retriever)

    # CONVERGENCE: every domain agent points to the SAME single downstream
    # node (join_agents) via a plain edge. LangGraph automatically waits
    # for all active parallel branches to finish before running a node
    # that they all share as their only target - this is what guarantees
    # join_agents (and therefore supervisor afterwards) runs exactly once
    # per batch, not once per agent.
    for agent in DOMAIN_AGENTS:
        graph.add_edge(agent, "join_agents")

    graph.add_edge("join_agents", "supervisor")

    graph.add_edge("synthesizer", END)

    return graph.compile()