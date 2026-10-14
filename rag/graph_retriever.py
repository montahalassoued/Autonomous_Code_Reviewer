from dataclasses import dataclass
import networkx as nx
from rag.knowledge_graph import get_callers, get_callees
from rag.hybrid_search import SearchResult


# A search result plus why it's included: a direct hit, or a caller/callee of one.
@dataclass
class EnrichedResult:
    chunk_id: str
    metadata: dict
    source: str          # "hybrid_search", "caller_of_X", or "callee_of_X"
    rrf_score: float | None  # None for graph-only additions (no search score)


# Graph node -> the same metadata shape Chroma returns, so every result looks alike.
def _node_to_metadata(graph: nx.DiGraph, node_id: str) -> dict:
    fn = graph.nodes[node_id]["function"]
    return {
        "file_path": fn.file_path,
        "function_name": fn.name,
        "qualified_name": fn.qualified_name,
        "start_line": fn.start_line,
        "end_line": fn.end_line,
        "context": "",
        "code": fn.code,
        "calls": ",".join(fn.calls),
    }


# Add the direct callers and callees of each search hit.
def enrich_with_graph_context(
    search_results: list[SearchResult],
    graph: nx.DiGraph,
) -> list[EnrichedResult]:
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
        # Graph node ids use the qualified name ("Class.method"); fall
        # back to the bare name for metadata without one (module-level
        # functions have qualified_name == function_name anyway).
        qualified_name = result.metadata.get("qualified_name", result.metadata["function_name"])

        for caller_id in get_callers(graph, file_path, qualified_name):
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

        for callee_id in get_callees(graph, file_path, qualified_name):
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