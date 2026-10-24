from prometheus_client import Counter, Histogram


# A Groq call takes 1-30 s, a CPU-served model minutes; a review stops at its 600 s deadline.
LLM_CALL_BUCKETS_S = (0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 900)
REVIEW_BUCKETS_S = (5, 10, 20, 30, 60, 90, 120, 180, 300, 450, 600, 900)

LLM_CALLS = Counter("reviewer_llm_calls_total", "LLM calls, after retries: ok or error",
                    ["provider", "model", "outcome"])
LLM_CALL_SECONDS = Histogram("reviewer_llm_call_duration_seconds", "One successful LLM call, retries excluded",
                             ["provider", "model"], buckets=LLM_CALL_BUCKETS_S)
LLM_RETRIES = Counter("reviewer_llm_retries_total", "LLM calls retried, by the error that caused it",
                      ["provider", "model", "reason"])
LLM_TOKENS = Counter("reviewer_llm_tokens_total", "Tokens sent (in) and generated (out)",
                     ["provider", "model", "direction"])
AGENT_FAILURES = Counter("reviewer_agent_failures_total", "Review agents that failed, so their category was left out",
                         ["agent"])
REVIEWS = Counter("reviewer_reviews_total",
                  "Review jobs: posted, partial, unreviewable, skipped, failed (will retry) or dead_lettered", ["outcome"])
REVIEW_SECONDS = Histogram("reviewer_review_duration_seconds", "From picking up a job to the posted review",
                           buckets=REVIEW_BUCKETS_S)
POSTED_FINDINGS = Counter("reviewer_posted_findings_total", "Findings posted on pull requests",
                          ["agent", "severity"])


def record_llm_success(provider: str, model: str, response, seconds: float) -> None:
    usage = getattr(response, "usage", None)
    LLM_CALLS.labels(provider, model, "ok").inc()
    LLM_CALL_SECONDS.labels(provider, model).observe(seconds)
    LLM_TOKENS.labels(provider, model, "in").inc(getattr(usage, "prompt_tokens", 0) or 0)
    LLM_TOKENS.labels(provider, model, "out").inc(getattr(usage, "completion_tokens", 0) or 0)


# The HTTP status of a provider error, else its type: a bounded set of label values.
def error_reason(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    return str(status) if status else type(exc).__name__


def record_posted_review(final_state: dict, seconds: float) -> None:
    REVIEWS.labels("partial" if final_state.get("failed_agents") else "posted").inc()
    REVIEW_SECONDS.observe(seconds)
    for finding in final_state.get("posted_findings") or []:
        POSTED_FINDINGS.labels(finding["agent"], finding["severity"]).inc()
