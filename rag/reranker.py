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


# One reranked context chunk, converted to a RetrievedChunk by the retriever.
@dataclass
class RankedChunk:
    chunk_id: str
    metadata: dict
    rerank_score: float
    source: str  # carried over from EnrichedResult: "hybrid_search", "caller_of_X", etc.


# Lazily loads the cross-encoder once and caches it at module level.
def _get_model() -> "CrossEncoder":
    global _model_cache
    if _model_cache is None:
        from sentence_transformers import CrossEncoder
        _model_cache = CrossEncoder(RERANKER_MODEL)
    return _model_cache


# Text the cross-encoder scores: context sentence + truncated code.
def _build_document_text(metadata: dict) -> str:
    context = metadata.get("context", "")
    code = metadata.get("code", "")[:MAX_RERANK_CODE_CHARS]
    if not context and not code:
        name = metadata.get("qualified_name", metadata["function_name"])
        return f"{metadata['file_path']}::{name}"
    return f"{context}\n{code}"


# Score each candidate with the cross-encoder; return the top_k.
def rerank(
    query: str,
    candidates: list[EnrichedResult],
    top_k: int = 5,
) -> list[RankedChunk]:
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