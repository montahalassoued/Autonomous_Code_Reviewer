from typing import TypedDict, Annotated, Literal
import operator

from review.secret_redactor import redact_for_llm


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

    # Final output - populated by review/synthesizer.py: the findings it posts (after the cross-agent merge) and the comment
    posted_findings: list[Finding]
    final_review_markdown: str

    # Where this review's Langfuse trace hangs (review/review_tracing.py); None when tracing is off
    trace_context: dict[str, str] | None

    # time.monotonic() after which no LLM call starts or waits (set by app/worker.py); None: no deadline
    review_deadline: float | None


# The state a review starts from; the worker and the review eval both build it here, so the eval runs what production runs.
def initial_state(pr_number: int, repo_full_name: str, diff: str, changed_files: list[str],
                  unreviewed_files: list[str]) -> ReviewState:
    # Redacted once here, so the agents, the locator and the injection detector all read the same text.
    return ReviewState(
        pr_number=pr_number, repo_full_name=repo_full_name, pr_diff=redact_for_llm(diff), changed_files=changed_files,
        unreviewed_files=unreviewed_files, review_context="", findings=[], failed_agents=[],
        llm_calls=0, tokens_in=0, tokens_out=0, posted_findings=[], final_review_markdown="", trace_context=None,
        review_deadline=None,
    )
