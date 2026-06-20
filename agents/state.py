"""
ReviewState - the central shared state of the LangGraph.

This module defines the data structure that flows through ALL agents:
supervisor, retriever, security, performance, architecture, testing,
documentation, synthesizer.

Principle: each agent reads what it needs from this state, does its work,
writes its result back into it, and hands off to the next agent (or runs
in parallel for the 5 domain agents).
"""

from typing import TypedDict, Annotated, Literal
import operator


class Finding(TypedDict):
    """
    A single issue detected by an agent in the analyzed code.

    This is the atomic unit produced by security.py, performance.py,
    architecture.py, testing.py, documentation.py. The synthesizer
    consumes a list of Finding to produce the final review comment.
    """
    agent: str
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    file_path: str
    line_number: int
    title: str
    description: str
    evidence: str
    suggestion: str


class RetrievedChunk(TypedDict):
    """
    A piece of code retrieved by the RAG pipeline.

    Output of rag/reranker.py after the full pipeline
    (self-query + hybrid search + graph traversal + reranking).
    """
    file_path: str
    function_name: str
    code: str
    context: str
    relevance_score: float
    callers: list[str]


class ReviewState(TypedDict):
    """
    The full state that flows through the graph during a review.

    Two categories of fields:
    1. Normal fields -> the last node that writes overwrites the value
    2. Annotated[type, operator.add] fields -> writes are merged together
       (required for findings, since 5 agents write to it in parallel)
    """

    # Input - populated from the GitHub webhook payload
    pr_number: int
    repo_full_name: str
    pr_diff: str
    changed_files: list[str]

    # Retrieval - populated by agents/retriever.py
    retrieved_context: list[RetrievedChunk]
    retrieval_quality_score: float
    retrieval_attempts: int

    # Findings - populated by the 5 domain agents IN PARALLEL
    # operator.add concatenates lists instead of overwriting them
    findings: Annotated[list[Finding], operator.add]

    # Final output - populated by agents/synthesizer.py
    final_review_markdown: str

    # Routing - read/written by agents/supervisor.py
    next_step: Literal["retriever", "parallel_agents", "synthesizer", "end"]