import json
import logging
import os

from groq import APIError, Groq

from review.state import ReviewState, RetrievedChunk
from rag.query_decomposer import decompose_diff
from rag.hybrid_search import hybrid_search
from rag.graph_retriever import enrich_with_graph_context
from rag.reranker import rerank, RankedChunk
from review.agents.agent_runner import review_agent_model


logger = logging.getLogger(__name__)

# Score used when the judge call fails: high enough to stop the retry loop instead of spending more calls.
JUDGE_FALLBACK_SCORE = 1.0

# Corrective-RAG loop bounds, read by review/graph.py's routing: retry
# retrieval while the judge scores below QUALITY_THRESHOLD, but never
# more than MAX_RETRIEVAL_ATTEMPTS times in total, so a review always
# reaches the agents even when retrieval stays poor.
QUALITY_THRESHOLD = 0.5
MAX_RETRIEVAL_ATTEMPTS = 3

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


# Chunk's 'Class.method' name, or the bare name for older metadata.
def _qualified_name(metadata: dict) -> str:
    return metadata.get("qualified_name", metadata["function_name"])


# RankedChunk -> the flat RetrievedChunk shape the agents read.
def _ranked_chunk_to_retrieved_chunk(chunk: RankedChunk, graph) -> RetrievedChunk:
    from rag.knowledge_graph import get_callers

    file_path = chunk.metadata["file_path"]
    qualified_name = _qualified_name(chunk.metadata)
    callers = get_callers(graph, file_path, qualified_name) if graph is not None else []

    return RetrievedChunk(
        file_path=file_path,
        function_name=qualified_name,
        code=chunk.metadata.get("code", ""),
        context=chunk.metadata.get("context", ""),
        relevance_score=chunk.rerank_score,
        callers=callers,
    )


# Ask Groq how useful the retrieved chunks are for this diff (0-1).
def _judge_retrieval_quality(diff: str, chunks: list[RankedChunk]) -> float:
    if not chunks:
        return 0.0  # no context retrieved at all - obviously insufficient

    client = _build_groq_client()
    context_summary = "\n".join(
        f"- {_qualified_name(c.metadata)} ({c.metadata['file_path']})"
        for c in chunks
    )
    prompt = JUDGE_PROMPT_TEMPLATE.format(diff=diff, context_summary=context_summary)

    try:
        response = client.chat.completions.create(
            model=review_agent_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,  # deterministic scoring, not creative
        )
    except APIError as error:
        logger.warning("Retrieval judge failed (%s), keeping the current context", type(error).__name__)
        return JUDGE_FALLBACK_SCORE

    # content can be None on an empty or filtered completion; "" then
    # fails to parse below and scores 0.0, same as any bad output.
    raw_output = (response.choices[0].message.content or "").strip()
    raw_output = raw_output.strip("`").removeprefix("json").strip()

    try:
        parsed = json.loads(raw_output)
        score = float(parsed["score"])
        return max(0.0, min(1.0, score))  # clamp defensively to [0, 1]
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return 0.0


# One RAG pass: decompose -> search -> enrich -> rerank -> judge.
def run_retrieval(
    diff: str,
    collection,
    all_documents: list[str],
    all_ids: list[str],
    graph,
) -> tuple[list[RetrievedChunk], float]:
    queries = decompose_diff(diff, review_agent_model())

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


# LangGraph node for 'retriever': one RAG pass written into ReviewState.
def retriever_node(state: ReviewState, collection, all_documents, all_ids, graph) -> dict:
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