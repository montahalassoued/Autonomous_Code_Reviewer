import re
from dataclasses import dataclass
from rank_bm25 import BM25Okapi
from rag.embedder import embed_query
from db.chroma import query_collection


RRF_K = 60  # standard constant from the original RRF paper - softens
            # the impact of very top-ranked results, well-established
            # default used in most hybrid search implementations


# One fused search result, passed on to graph expansion and reranking.
@dataclass
class SearchResult:
    chunk_id: str          # "file_path::qualified_name", matches Chroma's id scheme
    rrf_score: float
    metadata: dict          # file_path, function_name, qualified_name, line numbers, context, code, calls


# Tokenization for BM25: lowercase, strip code punctuation, split on whitespace.
def _tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9_]+", " ", text)
    return text.split()


# Runs BM25 over the full corpus and returns the top_k chunk_ids, ranked best-to-worst.
def _bm25_search(
    query: str,
    all_documents: list[str],
    all_ids: list[str],
    top_k: int,
) -> list[str]:
    tokenized_corpus = [_tokenize(doc) for doc in all_documents]
    bm25 = BM25Okapi(tokenized_corpus)

    tokenized_query = _tokenize(query)
    scores = bm25.get_scores(tokenized_query)

    # Pair each id with its score, sort descending, take top_k ids only
    ranked = sorted(zip(all_ids, scores), key=lambda pair: pair[1], reverse=True)
    return [chunk_id for chunk_id, _score in ranked[:top_k]]


# Dense search via Chroma; top_k chunk ids, best first.
def _dense_search(query: str, collection, top_k: int) -> list[str]:
    query_vector = embed_query(query)
    results = query_collection(collection, query_vector, top_k=top_k)
    return results["ids"][0]


# RRF score for every chunk id in the ranked lists.
def _reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = RRF_K,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranked_list in ranked_lists:
        for rank, chunk_id in enumerate(ranked_list):
            # rank is 0-indexed here; +1 makes it 1-indexed, matching
            # the standard RRF formula 1 / (k + rank)
            contribution = 1.0 / (k + rank + 1)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + contribution
    return scores


# BM25 + dense search fused with RRF; returns the top_k results.
def hybrid_search(
    query: str,
    collection,
    all_documents: list[str],
    all_ids: list[str],
    top_k: int = 10,
) -> list[SearchResult]:
    bm25_ranked = _bm25_search(query, all_documents, all_ids, top_k)
    dense_ranked = _dense_search(query, collection, top_k)

    fused_scores = _reciprocal_rank_fusion([bm25_ranked, dense_ranked])

    # We need metadata for the final results - fetch it from Chroma's
    # get() by id rather than re-querying, since we already know
    # exactly which ids we want.
    all_fused_ids = list(fused_scores.keys())
    fetched = collection.get(ids=all_fused_ids)
    metadata_by_id = dict(zip(fetched["ids"], fetched["metadatas"]))

    results = [
        SearchResult(
            chunk_id=chunk_id,
            rrf_score=score,
            metadata=metadata_by_id[chunk_id],
        )
        for chunk_id, score in fused_scores.items()
    ]
    results.sort(key=lambda r: r.rrf_score, reverse=True)

    return results[:top_k]