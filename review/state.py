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
    # Files left out of the diff (over the size budget, or no patch from GitHub): named in the review, never reviewed
    unreviewed_files: list[str]

    # Repository context for the agents; empty until the structural context builder (Phase 4) fills it
    review_context: str

    # Findings - populated by the review agents IN PARALLEL
    # operator.add concatenates lists instead of overwriting them
    findings: Annotated[list[Finding], operator.add]

    # Agents that raised (Groq 429/5xx, timeout): the review is posted without their category
    failed_agents: Annotated[list[str], operator.add]

    # What the review cost: model responses and tokens, summed over the parallel agents (stored in reviews)
    llm_calls: Annotated[int, operator.add]
    tokens_in: Annotated[int, operator.add]
    tokens_out: Annotated[int, operator.add]

    # Final output - populated by review/synthesizer.py
    final_review_markdown: str


# The state a review starts from; the worker and the review eval both build it here, so the eval runs what production runs.
def initial_state(pr_number: int, repo_full_name: str, diff: str, changed_files: list[str],
                  unreviewed_files: list[str]) -> ReviewState:
    return ReviewState(
        pr_number=pr_number, repo_full_name=repo_full_name, pr_diff=diff, changed_files=changed_files,
        unreviewed_files=unreviewed_files, review_context="", findings=[], failed_agents=[],
        llm_calls=0, tokens_in=0, tokens_out=0, final_review_markdown="",
    )
