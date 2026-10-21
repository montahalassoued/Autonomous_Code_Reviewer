import logging
import os
import signal
import threading
import time
from typing import Callable

from app.jobs.factory import build_queue, build_review_status
from app.jobs.models import ReviewJob
from app.jobs.queue import JobQueue
from app.jobs.review_status import SKIP_CLOSED, SKIP_SUPERSEDED, ReviewCost, ReviewStatusStore
from review.diff_budget import fit_diff, max_diff_chars
from review.review_tracing import flush_tracing, review_trace
from review.secret_redactor import environment_secret_values, redact_secrets
from review.state import initial_state
from review.synthesizer import unreviewable_markdown
from tools.github_tools import changed_files_in_diff, get_pr_diff, get_pull_request_head, upsert_summary_comment


logger = logging.getLogger(__name__)

DEFAULT_VISIBILITY_TIMEOUT_S = 900
DEFAULT_MAX_ATTEMPTS = 3
MIN_POLL_S = 1.0
MAX_POLL_S = 10.0


# Why this job's review must not be posted: the PR was closed, or moved past the reviewed commit.
def stale_reason(job: ReviewJob) -> str | None:
    state, head_sha = get_pull_request_head(job.repo_full_name, job.pr_number)
    if state != "open":
        return SKIP_CLOSED
    if head_sha != job.head_sha:
        return SKIP_SUPERSEDED
    return None


# The failure as stored in reviews.status_reason: its type and message, with secrets redacted.
def failure_reason(error: BaseException) -> str:
    return redact_secrets(f"{type(error).__name__}: {error}", environment_secret_values())


# Runs the full review for one job, records its status and posts the result, unless it went stale.
def review_pull_request(compiled_graph, job: ReviewJob, status_store: ReviewStatusStore) -> None:
    key = job.dedupe_key
    status_store.mark_running(key)
    started = time.monotonic()

    # Checked before the LLM calls (nothing to pay for) and again before posting (a push may land meanwhile).
    reason = stale_reason(job)
    if reason:
        status_store.mark_skipped(key, reason)
        logger.info("Skipped job_id=%s pr=%s sha=%s: %s", job.job_id, job.pr_number, job.head_sha, reason)
        return

    pull_request_diff = get_pr_diff(job.repo_full_name, job.pr_number)
    # A diff over the budget would fail every agent call on the per-minute token limit: whole files are left out instead.
    diff, over_budget = fit_diff(pull_request_diff.text, max_diff_chars())
    unreviewed = [*pull_request_diff.files_without_patch, *over_budget]
    if not diff.strip():
        upsert_summary_comment(job.repo_full_name, job.pr_number, unreviewable_markdown(unreviewed))
        status_store.mark_posted(key, int((time.monotonic() - started) * 1000), [], ReviewCost())
        logger.info("Nothing reviewable job_id=%s pr=%s: %d file(s) left out", job.job_id, job.pr_number, len(unreviewed))
        return

    review_state = initial_state(job.pr_number, job.repo_full_name, diff, changed_files_in_diff(diff), unreviewed)

    with review_trace(job.repo_full_name, job.pr_number, job.head_sha, job.job_id) as trace:
        review_state["trace_context"] = trace.context if trace else None
        final_state = compiled_graph.invoke(review_state)
        if trace:
            trace.record_outcome(final_state)
    cost = ReviewCost(final_state.get("llm_calls", 0), final_state.get("tokens_in", 0), final_state.get("tokens_out", 0))
    logger.info("Review cost job_id=%s: %d LLM calls, %d tokens in, %d tokens out",
                job.job_id, cost.llm_calls, cost.tokens_in, cost.tokens_out)
    reason = stale_reason(job)
    if reason:
        status_store.mark_skipped(key, reason, cost)
        logger.info("Dropped the finished review job_id=%s pr=%s sha=%s: %s", job.job_id, job.pr_number, job.head_sha, reason)
        return

    upsert_summary_comment(job.repo_full_name, job.pr_number, final_state["final_review_markdown"])
    latency_ms = int((time.monotonic() - started) * 1000)
    status_store.mark_posted(key, latency_ms, final_state.get("failed_agents") or [], cost)


# Receives at most one message and handles it.
def process_one(
    queue: JobQueue,
    review_fn: Callable[[ReviewJob], None],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    visibility_timeout_s: int = DEFAULT_VISIBILITY_TIMEOUT_S,
    status_store: ReviewStatusStore | None = None,
) -> bool:
    messages = queue.receive(max_messages=1, visibility_timeout_s=visibility_timeout_s)
    if not messages:
        return False

    message = messages[0]
    job = message.job
    # Over the cap before running: earlier attempts died without raising (killed, out of memory), so stop here.
    if message.dequeue_count > max_attempts:
        queue.dead_letter(message)
        logger.error("Job dead-lettered job_id=%s: %s deliveries never finished", job.job_id, message.dequeue_count)
        if status_store is not None:
            _record_failure(status_store, job, RuntimeError(f"worker stopped {message.dequeue_count - 1} times mid-review"))
        return True
    try:
        review_fn(job)
    except Exception as error:  # any failure must be recorded and retried, never crash the worker
        logger.exception(
            "Review failed job_id=%s repo=%s pr=%s attempt=%s/%s",
            job.job_id, job.repo_full_name, job.pr_number, message.dequeue_count, max_attempts,
        )
        if message.dequeue_count >= max_attempts:
            queue.dead_letter(message)
            logger.error("Job dead-lettered job_id=%s after %s attempts", job.job_id, message.dequeue_count)
            if status_store is not None:
                _record_failure(status_store, job, error)
        # Below the cap we leave the message alone: it reappears after the visibility timeout.
        return True

    queue.delete(message)
    logger.info("Review done job_id=%s repo=%s pr=%s sha=%s", job.job_id, job.repo_full_name, job.pr_number, job.head_sha)
    return True


# A failed review is visible in Postgres and can be retried by a redelivery; a store outage is only logged.
def _record_failure(status_store: ReviewStatusStore, job: ReviewJob, error: BaseException) -> None:
    try:
        status_store.mark_failed(job.dedupe_key, failure_reason(error))
    except Exception:  # Postgres down too: the poison queue still holds the job
        logger.exception("Could not record the failure of job_id=%s", job.job_id)


# Polls the queue until stop_event is set.
def run_forever(
    queue: JobQueue,
    review_fn: Callable[[ReviewJob], None],
    stop_event: threading.Event,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    visibility_timeout_s: int = DEFAULT_VISIBILITY_TIMEOUT_S,
    min_poll_s: float = MIN_POLL_S,
    max_poll_s: float = MAX_POLL_S,
    status_store: ReviewStatusStore | None = None,
) -> None:
    delay = min_poll_s
    while not stop_event.is_set():
        try:
            handled = process_one(queue, review_fn, max_attempts, visibility_timeout_s, status_store)
        except Exception:  # queue/storage outage: log and back off, the worker must stay alive
            logger.exception("Queue operation failed")
            handled = False
        if handled:
            delay = min_poll_s
            continue
        stop_event.wait(delay)  # wakes immediately on shutdown
        delay = min(delay * 2, max_poll_s)


# Reads a positive integer from the environment, falling back to default if unset.
def _read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


# Worker entrypoint: ingest once, build the graph once, then process jobs until SIGTERM/SIGINT.
def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from app.bootstrap import build_review_graph  # heavy imports stay out of module import time

    max_attempts = _read_int_env("MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)
    visibility_timeout_s = _read_int_env("REVIEW_VISIBILITY_TIMEOUT_S", DEFAULT_VISIBILITY_TIMEOUT_S)

    queue = build_queue()
    status_store = build_review_status()
    compiled_graph = build_review_graph()

    stop_event = threading.Event()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_number, lambda _signum, _frame: stop_event.set())

    logger.info("Worker started max_attempts=%s visibility_timeout_s=%s", max_attempts, visibility_timeout_s)
    run_forever(
        queue,
        lambda job: review_pull_request(compiled_graph, job, status_store),
        stop_event,
        max_attempts,
        visibility_timeout_s,
        status_store=status_store,
    )
    status_store.close()
    flush_tracing()
    logger.info("Worker stopped")


if __name__ == "__main__":
    main()
