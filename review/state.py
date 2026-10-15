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


# The full state that flows through the graph during a review.
class ReviewState(TypedDict):

    # Input - populated from the GitHub webhook payload
    pr_number: int
    repo_full_name: str
    pr_diff: str
    changed_files: list[str]

    # Repository context for the agents; empty until the structural context builder (Phase 4) fills it
    review_context: str

    # Findings - populated by the review agents IN PARALLEL
    # operator.add concatenates lists instead of overwriting them
    findings: Annotated[list[Finding], operator.add]

    # Final output - populated by review/synthesizer.py
    final_review_markdown: str
