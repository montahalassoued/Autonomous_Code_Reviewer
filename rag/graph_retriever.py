"""
graph_retriever.py - enriches hybrid search results with graph context.

This connects the two RAG pillars built so far: hybrid_search.py (Step
7, content-based relevance) and knowledge_graph.py (Step 5, structural
relationships). Hybrid search alone can miss functions that are
structurally important but didn't score well on content/semantic
similarity - e.g. login() might rank low for a query specifically
about password hashing, even though it's the function that actually
CALLS verify_password() and therefore matters for understanding the
real-world impact of a change to it.

We deliberately limit traversal to 1 hop (direct callers + direct
callees only), not a deeper multi-hop walk. Two reasons: (1) it's
enough to capture the immediately useful architectural context without
exploding to "friend of a friend of a friend", and (2) this output
eventually becomes part of an LLM prompt (the agents in Step 11+) -
unbounded graph expansion would blow past any reasonable context
window long before it added more useful signal than noise.
"""

from dataclasses import dataclass
import networkx as nx
from rag.knowledge_graph import get_callers, get_callees
from rag.hybrid_search import SearchResult


@dataclass
class EnrichedResult:
    """
    A hybrid search result, optionally enriched with WHY it's included:
    either it was found directly by hybrid search, or it was pulled in
    because it's a direct caller/callee of something hybrid search found.
    """
    chunk_id: str
    metadata: dict
    source: str          # "hybrid_search", "caller_of_X", or "callee_of_X"
    rrf_score: float | None  # None for graph-only additions (no search score)


def _node_to_metadata(graph: nx.DiGraph, node_id: str) -> dict:
    """
    Builds a metadata dict for a graph node, matching the shape Chroma
    already returns (file_path, function_name, line numbers, calls),
    so EnrichedResult.metadata has a consistent structure regardless
    of whether a result came from hybrid search or from the graph.
    """
    fn = graph.nodes[node_id]["function"]
    return {
        "file_path": fn.file_path,
        "function_name": fn.name,
        "start_line": fn.start_line,
        "end_line": fn.end_line,
        "calls": ",".join(fn.calls),
    }


def enrich_with_graph_context(
    search_results: list[SearchResult],
    graph: nx.DiGraph,
) -> list[EnrichedResult]:
    """
    Takes hybrid search results and adds their direct callers and
    callees from the knowledge graph, if not already present.

    Args:
        search_results: output of rag/hybrid_search.py's hybrid_search()
        graph: the knowledge graph from rag/knowledge_graph.py,
               typically loaded via load_graph() for an existing repo

    Returns:
        a list combining the original search results (source=
        "hybrid_search") with any newly discovered callers/callees
        (source="caller_of_X" / "callee_of_X"), deduplicated by
        chunk_id - a function already found by search keeps its
        search result entry rather than being duplicated as a graph
        addition.
    """
    enriched: list[EnrichedResult] = []
    seen_ids: set[str] = set()

    # First, keep every original hybrid search result as-is.
    for result in search_results:
        enriched.append(
            EnrichedResult(
                chunk_id=result.chunk_id,
                metadata=result.metadata,
                source="hybrid_search",
                rrf_score=result.rrf_score,
            )
        )
        seen_ids.add(result.chunk_id)

    # Then, for each original result, pull in its direct neighbors.
    # We iterate over search_results (not enriched) so that graph
    # additions found via one search result don't themselves get
    # traversed again - this is what keeps us at exactly 1 hop.
    for result in search_results:
        if result.chunk_id not in graph:
            continue  # defensive: shouldn't happen if graph and
                      # search index were built from the same ingestion

        file_path = result.metadata["file_path"]
        function_name = result.metadata["function_name"]

        for caller_id in get_callers(graph, file_path, function_name):
            if caller_id in seen_ids:
                continue
            enriched.append(
                EnrichedResult(
                    chunk_id=caller_id,
                    metadata=_node_to_metadata(graph, caller_id),
                    source=f"caller_of_{result.chunk_id}",
                    rrf_score=None,
                )
            )
            seen_ids.add(caller_id)

        for callee_id in get_callees(graph, file_path, function_name):
            if callee_id in seen_ids:
                continue
            enriched.append(
                EnrichedResult(
                    chunk_id=callee_id,
                    metadata=_node_to_metadata(graph, callee_id),
                    source=f"callee_of_{result.chunk_id}",
                    rrf_score=None,
                )
            )
            seen_ids.add(callee_id)

    return enriched