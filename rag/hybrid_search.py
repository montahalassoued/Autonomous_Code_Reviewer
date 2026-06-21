"""
hybrid_search.py - combines BM25 keyword search with dense vector search.

Why neither alone is enough:
- Dense search (Voyage embeddings via Chroma) finds SEMANTIC matches -
  it can find verify_password() for a query about "checking credentials"
  even with zero exact word overlap. But it can also be fooled: a
  function whose generated context happens to share vocabulary with
  the query (like login() mentioning "verifies") can outrank the
  function an exact keyword search would obviously surface.
- BM25 finds EXACT matches - if the query or diff literally contains
  the string "verify_password", BM25 will rank that function very
  highly based on the exact token match, regardless of how the
  semantic embedding happens to score it.

Combining both and fusing with Reciprocal Rank Fusion (RRF) gives a
result that is robust to either method's individual blind spots: a
document that ranks well in BOTH methods consistently outscores one
that only does well in a single method.

Why RRF instead of a weighted sum of raw scores: BM25 scores and
cosine similarity scores live on completely different, incomparable
scales (BM25 can be negative; cosine similarity is roughly -1 to 1
with very different distributions). RRF sidesteps this entirely by
only looking at RANK POSITION in each result list, not raw scores.
"""

import re
from dataclasses import dataclass
from rank_bm25 import BM25Okapi
from rag.embedder import embed_query
from db.chroma import query_collection


RRF_K = 60  # standard constant from the original RRF paper - softens
            # the impact of very top-ranked results, well-established
            # default used in most hybrid search implementations


@dataclass
class SearchResult:
    """
    A single fused search result, ready to be passed downstream to
    rag/graph_retriever.py and eventually rag/reranker.py.
    """
    chunk_id: str          # "file_path::function_name", matches Chroma's id scheme
    rrf_score: float
    metadata: dict          # file_path, function_name, line numbers, context, calls


def _tokenize(text: str) -> list[str]:
    """
    Tokenization for BM25: lowercase, strip code punctuation, split on
    whitespace.

    Bug fixed here after testing: a naive .lower().split() leaves code
    punctuation glued to identifiers - "verify_password(password," was
    a SINGLE token, which never matches a clean query term like
    "verify_password". Stripping everything except letters/digits/
    underscore (so identifiers with underscores stay intact as one
    token, but get separated from surrounding ()[]{}:,.  etc.) fixes
    this while still treating verify_password as one meaningful unit
    rather than splitting it into "verify" + "password".
    """
    text = text.lower()
    text = re.sub(r"[^a-z0-9_]+", " ", text)
    return text.split()


def _bm25_search(
    query: str,
    all_documents: list[str],
    all_ids: list[str],
    top_k: int,
) -> list[str]:
    """
    Runs BM25 over the full corpus and returns the top_k chunk_ids,
    ranked best-to-worst.

    Note: BM25Okapi has no persistence/index - we rebuild it from the
    full document list on every call. This is fine at the scale of a
    single repo (hundreds to low thousands of functions) but would
    need a persisted index for very large corpora.
    """
    tokenized_corpus = [_tokenize(doc) for doc in all_documents]
    bm25 = BM25Okapi(tokenized_corpus)

    tokenized_query = _tokenize(query)
    scores = bm25.get_scores(tokenized_query)

    # Pair each id with its score, sort descending, take top_k ids only
    ranked = sorted(zip(all_ids, scores), key=lambda pair: pair[1], reverse=True)
    return [chunk_id for chunk_id, _score in ranked[:top_k]]


def _dense_search(query: str, collection, top_k: int) -> list[str]:
    """
    Runs the dense vector search via Chroma (Step 6) and returns the
    top_k chunk_ids, ranked best-to-worst (Chroma already returns
    results sorted by similarity).
    """
    query_vector = embed_query(query)
    results = query_collection(collection, query_vector, top_k=top_k)
    return results["ids"][0]


def _reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = RRF_K,
) -> dict[str, float]:
    """
    Computes RRF scores for every chunk_id appearing in any of the
    given ranked lists.

    Args:
        ranked_lists: one or more lists of chunk_ids, each already
                       sorted best-to-worst by its own method
        k: the RRF smoothing constant

    Returns:
        a dict mapping chunk_id -> combined RRF score. A chunk_id
        missing from one list simply doesn't get a contribution from
        that list - it isn't penalized further than "not appearing".
    """
    scores: dict[str, float] = {}
    for ranked_list in ranked_lists:
        for rank, chunk_id in enumerate(ranked_list):
            # rank is 0-indexed here; +1 makes it 1-indexed, matching
            # the standard RRF formula 1 / (k + rank)
            contribution = 1.0 / (k + rank + 1)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + contribution
    return scores


def hybrid_search(
    query: str,
    collection,
    all_documents: list[str],
    all_ids: list[str],
    top_k: int = 10,
) -> list[SearchResult]:
    """
    Runs BM25 and dense search in parallel (conceptually - sequential
    here, since both are fast enough at this scale), fuses them with
    RRF, and returns the top_k fused results.

    Args:
        query: the search query text (e.g. one of the sub-queries from
               rag/query_decomposer.py, Step 11)
        collection: a Chroma collection (db/chroma.py) already
                    populated with embedded chunks
        all_documents: the full list of chunk texts in the collection,
                        needed to rebuild the BM25 index - same order
                        as all_ids
        all_ids: the full list of chunk_ids in the collection, same
                  order as all_documents
        top_k: how many results to retrieve from EACH method before
               fusion (the final fused list may be shorter if there's
               overlap, or contain up to 2*top_k unique results)

    Returns:
        SearchResult objects sorted by descending RRF score
    """
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