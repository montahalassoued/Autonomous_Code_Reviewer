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

What gets scored: the query against the chunk's context sentence plus
its code (truncated to MAX_RERANK_CODE_CHARS). Scoring only the
"file::name" label would leave the cross-encoder nothing to judge
relevance on, which defeats the point of reranking.

The output is the top_k chunks sorted by score, with no duplicates.
An earlier version repeated the best chunk at the end (to exploit LLM
primacy/recency bias), but that put the same code into every agent
prompt twice for little benefit with only 5 chunks.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from rag.graph_retriever import EnrichedResult

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder


RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

# Cap on code characters per pair. The cross-encoder truncates long
# inputs anyway (a few hundred tokens), so sending more only costs CPU
# time; ~2000 chars covers the signature and the start of the body.
MAX_RERANK_CODE_CHARS = 2000

_model_cache: "CrossEncoder | None" = None


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


def _get_model() -> "CrossEncoder":
    """
    Lazily loads the cross-encoder once and caches it at module level.

    Loading a CrossEncoder is expensive (downloads + loads model
    weights into memory) - we do not want to pay that cost on every
    single call to rerank(), only once per process lifetime. The
    sentence_transformers import itself (which pulls in torch, ~230MB)
    is deferred here too, not just the model construction - importing
    it at module load time would cost that memory at app startup even
    before any rerank() call, which is what blew past Render's 512MB
    free-tier limit during ingestion.
    """
    global _model_cache
    if _model_cache is None:
        from sentence_transformers import CrossEncoder
        _model_cache = CrossEncoder(RERANKER_MODEL)
    return _model_cache


def _build_document_text(metadata: dict) -> str:
    """
    Builds the text the cross-encoder scores against the query: the
    chunk's context sentence, a newline, then its code (truncated).

    Args:
        metadata: a chunk's metadata dict (from Chroma or from
                  graph_retriever._node_to_metadata)

    Returns:
        the document text for the (query, document) pair. Falls back
        to "file::name" only if a chunk has neither context nor code,
        so the model still gets something to score.
    """
    context = metadata.get("context", "")
    code = metadata.get("code", "")[:MAX_RERANK_CODE_CHARS]
    if not context and not code:
        name = metadata.get("qualified_name", metadata["function_name"])
        return f"{metadata['file_path']}::{name}"
    return f"{context}\n{code}"


def rerank(
    query: str,
    candidates: list[EnrichedResult],
    top_k: int = 5,
) -> list[RankedChunk]:
    """
    Re-scores every candidate against the query using the cross-encoder,
    and returns the top_k by descending score.

    Args:
        query: the original search query (same one passed to
               hybrid_search.py earlier in the pipeline)
        candidates: output of rag/graph_retriever.py's
                    enrich_with_graph_context() - the combined set of
                    direct search hits plus their graph neighbors
        top_k: how many final chunks to keep for the LLM context

    Returns:
        at most top_k RankedChunk objects, best first, each chunk at
        most once
    """
    if not candidates:
        return []

    model = _get_model()

    pairs = [(query, _build_document_text(c.metadata)) for c in candidates]
    scores = model.predict(pairs)

    scored_candidates = list(zip(candidates, scores))
    scored_candidates.sort(key=lambda pair: pair[1], reverse=True)

    top_candidates = scored_candidates[:top_k]
    return [
        RankedChunk(
            chunk_id=c.chunk_id,
            metadata=c.metadata,
            rerank_score=float(score),
            source=c.source,
        )
        for c, score in top_candidates
    ]