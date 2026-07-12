"""
retriever.py - the LangGraph node that orchestrates the full RAG
pipeline and implements the corrective retrieval loop.

This is the piece that connects every RAG module built so far into one
working agent:
  query_decomposer.py  -> turns the diff into search queries
  hybrid_search.py      -> finds candidates (BM25 + dense + RRF)
  graph_retriever.py    -> enriches with direct callers/callees
  reranker.py            -> precisely re-scores down to a final top-K

On top of chaining these together, this module adds the one piece the
RAG pipeline never had: an LLM-as-judge that scores whether the
retrieved context is actually good enough, and triggers a retry with
reformulated queries if not - bounded by ReviewState.retrieval_attempts
to guarantee termination (agents/graph.py's route_after_retriever
already implements the bounded-retry routing logic; this module is
what actually DOES the retrieval work that routing function inspects).
"""

import os
import json
from groq import Groq

from agents.state import ReviewState, RetrievedChunk
from rag.query_decomposer import decompose_diff
from rag.hybrid_search import hybrid_search
from rag.graph_retriever import enrich_with_graph_context
from rag.reranker import rerank, RankedChunk


JUDGE_MODEL = "llama-3.3-70b-versatile"
QUALITY_THRESHOLD = 0.5

JUDGE_PROMPT_TEMPLATE = """You are evaluating whether retrieved code context is good enough
to review a pull request diff.

Diff:
{diff}

Retrieved context (function names and their file locations):
{context_summary}

Score from 0.0 to 1.0 how useful this retrieved context is for
understanding and reviewing the diff. A high score means the context
includes genuinely related functions (callers, callees, similar
patterns). A low score means the context is irrelevant or empty.

Output ONLY a JSON object like {{"score": 0.8}}, nothing else."""


def _build_groq_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com and set it "
            "in your .env file."
        )
    return Groq(api_key=api_key)


def _ranked_chunk_to_retrieved_chunk(chunk: RankedChunk, graph) -> RetrievedChunk:
    """
    Converts a reranker.py RankedChunk (chunk_id + metadata dict) into
    the flatter RetrievedChunk shape agents/state.py expects, since
    that's the contract every domain agent (security.py, etc.) will
    read from. Pulls caller info from the graph directly rather than
    re-deriving it, since we already have the graph loaded here.
    """
    from rag.knowledge_graph import get_callers

    file_path = chunk.metadata["file_path"]
    function_name = chunk.metadata["function_name"]
    callers = get_callers(graph, file_path, function_name) if graph is not None else []

    return RetrievedChunk(
        file_path=file_path,
        function_name=function_name,
        code=chunk.metadata.get("code", ""),
        context=chunk.metadata.get("context", ""),
        relevance_score=chunk.rerank_score,
        callers=callers,
    )


def _judge_retrieval_quality(diff: str, chunks: list[RankedChunk]) -> float:
    """
    Calls Groq to score how useful the retrieved chunks are for
    reviewing this specific diff. Returns a score in [0, 1].

    Falls back to 0.0 (treated as "insufficient, please retry") if the
    LLM response can't be parsed - a safe default, since failing to
    parse a quality score should never be silently treated as "good".
    """
    if not chunks:
        return 0.0  # no context retrieved at all - obviously insufficient

    client = _build_groq_client()
    context_summary = "\n".join(
        f"- {c.metadata['function_name']} ({c.metadata['file_path']})"
        for c in chunks
    )
    prompt = JUDGE_PROMPT_TEMPLATE.format(diff=diff, context_summary=context_summary)

    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,  # deterministic scoring, not creative
    )

    raw_output = response.choices[0].message.content.strip()
    raw_output = raw_output.strip("`").removeprefix("json").strip()

    try:
        parsed = json.loads(raw_output)
        score = float(parsed["score"])
        return max(0.0, min(1.0, score))  # clamp defensively to [0, 1]
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return 0.0


def run_retrieval(
    diff: str,
    collection,
    all_documents: list[str],
    all_ids: list[str],
    graph,
) -> tuple[list[RetrievedChunk], float]:
    """
    Runs one full pass of the RAG pipeline: decompose -> search ->
    enrich -> rerank -> judge. This is the function retriever_node()
    calls; separated out so it can be tested without needing a full
    LangGraph state object.

    Args:
        diff: the PR diff text
        collection: a populated Chroma collection (db/chroma.py)
        all_documents: full corpus text list, needed by hybrid_search's
                        BM25 index (same order as all_ids)
        all_ids: full corpus id list (same order as all_documents)
        graph: the knowledge graph (rag/knowledge_graph.py), or None
               if unavailable (degrades gracefully: no caller info)

    Returns:
        (retrieved_chunks, quality_score) - ready to be written
        directly into ReviewState's retrieved_context and
        retrieval_quality_score fields
    """
    queries = decompose_diff(diff)

    # Run hybrid search for every sub-query and merge candidates,
    # deduplicating by chunk_id - a candidate found by multiple
    # sub-queries is only kept once (its first occurrence wins, since
    # we don't need to merge scores here - reranking will re-score
    # everything against the full diff context anyway).
    all_search_results = []
    seen_ids = set()
    for query in queries:
        results = hybrid_search(query, collection, all_documents, all_ids, top_k=10)
        for r in results:
            if r.chunk_id not in seen_ids:
                all_search_results.append(r)
                seen_ids.add(r.chunk_id)

    enriched = enrich_with_graph_context(all_search_results, graph) if graph is not None else [
        # if no graph is available, wrap search results directly so
        # rerank() still has a consistent input shape to work with
        type("FakeEnriched", (), {
            "chunk_id": r.chunk_id, "metadata": r.metadata,
            "source": "hybrid_search", "rrf_score": r.rrf_score,
        })()
        for r in all_search_results
    ]

    # Use the first generated query as the reranking query - it's
    # typically the most central concern of the diff, since
    # query_decomposer.py is prompted to order by relevance.
    primary_query = queries[0] if queries else diff[:200]
    final_chunks = rerank(primary_query, enriched, top_k=5)

    quality_score = _judge_retrieval_quality(diff, final_chunks)

    retrieved_chunks = [
        _ranked_chunk_to_retrieved_chunk(chunk, graph) for chunk in final_chunks
    ]

    return retrieved_chunks, quality_score


def retriever_node(state: ReviewState, collection, all_documents, all_ids, graph) -> dict:
    """
    The actual LangGraph node function for the "retriever" node defined
    in agents/graph.py. Replaces that file's retriever_node STUB.

    Note the extra parameters beyond `state`: collection/all_documents/
    all_ids/graph are NOT part of ReviewState (they're large, repo-wide
    objects that don't belong in a per-review state dict) - they need
    to be bound via a closure or partial application when this node is
    registered in the graph, e.g.:
        graph.add_node("retriever", lambda s: retriever_node(s, collection, all_documents, all_ids, graph))
    This wiring happens when agents/graph.py is updated to use the real
    retriever instead of its current stub.
    """
    retrieved_chunks, quality_score = run_retrieval(
        diff=state["pr_diff"],
        collection=collection,
        all_documents=all_documents,
        all_ids=all_ids,
        graph=graph,
    )

    return {
        "retrieved_context": retrieved_chunks,
        "retrieval_quality_score": quality_score,
        "retrieval_attempts": state["retrieval_attempts"] + 1,
    }