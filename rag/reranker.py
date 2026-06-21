"""
reranker.py - precisely re-scores enriched results down to a final
top-K context using a local cross-encoder.

Why this step exists even though we already have RRF-fused hybrid
search scores: a cross-encoder evaluates the query and a candidate
document TOGETHER, in a single forward pass, rather than encoding
them separately and comparing vectors (what both BM25 and dense
search do). This lets it catch nuanced relevance signals - like a
function that mentions the right keywords but in an unrelated role -
that bi-encoder-style scoring can miss. It is also too slow to run
over an entire codebase, which is exactly why it only runs here, on
the small (~15-20) candidate set already narrowed down by
hybrid_search.py + graph_retriever.py.

Why local (BAAI/bge-reranker-v2-m3 via sentence-transformers) instead
of an API: zero cost, zero rate limits, zero API key to manage - a
deliberate choice given the quota issues hit with Gemini and Voyage
during earlier steps. The trade-off is a one-time model download
(~few hundred MB) and CPU-only inference, which is slow per-query but
entirely acceptable for reranking 15-20 candidates rather than
scanning a full corpus.

Why position-aware assembly at the end: LLMs exhibit a well-documented
recency/primacy bias - they pay more attention to the beginning and
end of their context window than the middle. We exploit this by
placing the single most relevant chunk both FIRST and LAST in the
final ordered list (a small, deliberate duplication of position, not
content) wherever the candidate count allows it.
"""

from dataclasses import dataclass
from sentence_transformers import CrossEncoder
from rag.graph_retriever import EnrichedResult


RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

_model_cache: CrossEncoder | None = None


@dataclass
class RankedChunk:
    """
    A final, reranked piece of context - this is the shape that feeds
    directly into agents/state.py's RetrievedChunk once Step 10
    (agents/retriever.py) wires this whole pipeline into the graph.
    """
    chunk_id: str
    metadata: dict
    rerank_score: float
    source: str  # carried over from EnrichedResult: "hybrid_search", "caller_of_X", etc.


def _get_model() -> CrossEncoder:
    """
    Lazily loads the cross-encoder once and caches it at module level.

    Loading a CrossEncoder is expensive (downloads + loads model
    weights into memory) - we do not want to pay that cost on every
    single call to rerank(), only once per process lifetime.
    """
    global _model_cache
    if _model_cache is None:
        _model_cache = CrossEncoder(RERANKER_MODEL)
    return _model_cache


def _build_document_text(metadata: dict) -> str:
    """
    Builds the text representation of a chunk that the cross-encoder
    will score against the query. We use file_path + function_name as
    light structural context, similar in spirit to (but lighter than)
    the full contextual_chunker.py treatment - the cross-encoder
    itself is powerful enough to not need heavy pre-enrichment here.
    """
    return f"{metadata['file_path']}::{metadata['function_name']}"


def rerank(
    query: str,
    candidates: list[EnrichedResult],
    top_k: int = 5,
) -> list[RankedChunk]:
    """
    Re-scores every candidate against the query using the cross-encoder,
    and returns the top_k, with the single best result duplicated at
    both the start and end of the list (position-aware assembly).

    Args:
        query: the original search query (same one passed to
               hybrid_search.py earlier in the pipeline)
        candidates: output of rag/graph_retriever.py's
                    enrich_with_graph_context() - the combined set of
                    direct search hits plus their graph neighbors
        top_k: how many final chunks to keep for the LLM context

    Returns:
        RankedChunk objects ordered for prompt assembly: best result
        first, then the rest by descending score, with the best
        result repeated at the end too (if top_k allows more than 1
        unique result) - the classic primacy + recency anchoring used
        in LLM context construction.
    """
    if not candidates:
        return []

    model = _get_model()

    pairs = [(query, _build_document_text(c.metadata)) for c in candidates]
    scores = model.predict(pairs)

    scored_candidates = list(zip(candidates, scores))
    scored_candidates.sort(key=lambda pair: pair[1], reverse=True)

    top_candidates = scored_candidates[:top_k]
    ranked_chunks = [
        RankedChunk(
            chunk_id=c.chunk_id,
            metadata=c.metadata,
            rerank_score=float(score),
            source=c.source,
        )
        for c, score in top_candidates
    ]

    return _assemble_position_aware(ranked_chunks)


def _assemble_position_aware(ranked_chunks: list[RankedChunk]) -> list[RankedChunk]:
    """
    Reorders so the single best chunk appears both first and last.

    With fewer than 2 unique chunks there's nothing meaningful to
    duplicate, so we return the input unchanged in that case.
    """
    if len(ranked_chunks) < 2:
        return ranked_chunks

    best = ranked_chunks[0]
    middle = ranked_chunks[1:]
    return [best] + middle + [best]