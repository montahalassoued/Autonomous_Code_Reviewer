from typing import TypedDict, Annotated, Literal
import operator


# A single issue detected by an agent in the analyzed code.
class Finding(TypedDict):
    agent: str  # the review category: "security", "logic" or "quality"
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    rule_id: str  # from the agent's rules.py, or "other"
    confidence: Literal["high", "medium", "low"]
    file_path: str
    line_number: int
    title: str
    description: str
    evidence: str
    suggestion: str


# A piece of code retrieved by the RAG pipeline.
class RetrievedChunk(TypedDict):
    file_path: str
    function_name: str  # qualified: "Class.method", or "func" at module level
    code: str
    context: str
    relevance_score: float
    callers: list[str]


# The full state that flows through the graph during a review.
class ReviewState(TypedDict):

    # Input - populated from the GitHub webhook payload
    pr_number: int
    repo_full_name: str
    pr_diff: str
    changed_files: list[str]

    # Retrieval - populated by review/retriever.py
    retrieved_context: list[RetrievedChunk]
    retrieval_quality_score: float
    retrieval_attempts: int

    # Findings - populated by the review agents IN PARALLEL
    # operator.add concatenates lists instead of overwriting them
    findings: Annotated[list[Finding], operator.add]

    # Final output - populated by review/synthesizer.py
    final_review_markdown: str

    # Legacy routing hint. No node reads it any more (review/graph.py
    # routes on retrieval_quality_score and retrieval_attempts), but
    # app/api/webhook.py still sets it in the initial state.
    next_step: Literal["retriever", "parallel_agents", "synthesizer", "end"]