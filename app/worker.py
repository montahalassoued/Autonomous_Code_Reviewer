"""
worker.py - the review worker: pulls jobs from the queue and runs the review pipeline.

Run with: python -m app.worker

Inputs (environment): everything app/bootstrap.py and app/jobs/factory.py read,
plus REVIEW_VISIBILITY_TIMEOUT_S (default 900) and MAX_ATTEMPTS (default 3).
Outputs: one upserted summary comment per reviewed PR; failed jobs end in the
poison queue after MAX_ATTEMPTS tries.

Why a separate process: reviews take minutes and many LLM calls. Running them
in the API (FastAPI BackgroundTasks) lost them on every restart. Here a job is
deleted only after it succeeded; a crash lets the message reappear after the
visibility timeout. Posting is an upsert, so a retry edits the comment
instead of duplicating it.
"""

import logging
import os
import signal
import threading
from typing import Callable

from app.jobs.factory import build_queue
from app.jobs.models import ReviewJob
from app.jobs.queue import JobQueue
from tools.github_tools import get_pr_diff, upsert_summary_comment


logger = logging.getLogger(__name__)

DEFAULT_VISIBILITY_TIMEOUT_S = 900
DEFAULT_MAX_ATTEMPTS = 3
MIN_POLL_S = 1.0
MAX_POLL_S = 10.0


def review_pull_request(compiled_graph, job: ReviewJob) -> None:
    """
    Runs the full review for one job and posts the result to GitHub.

    Args:
        compiled_graph: the LangGraph from agents/graph.py's build_graph()
        job: the review job (repo, PR number, head sha)
    """
    diff = get_pr_diff(job.repo_full_name, job.pr_number)

    initial_state = {
        "pr_number": job.pr_number,
        "repo_full_name": job.repo_full_name,
        "pr_diff": diff,
        "changed_files": [],
        "retrieved_context": [],
        "retrieval_quality_score": 0.0,
        "retrieval_attempts": 0,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "retriever",
    }

    final_state = compiled_graph.invoke(initial_state)
    upsert_summary_comment(job.repo_full_name, job.pr_number, final_state["final_review_markdown"])


def process_one(
    queue: JobQueue,
    review_fn: Callable[[ReviewJob], None],
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    visibility_timeout_s: int = DEFAULT_VISIBILITY_TIMEOUT_S,
) -> bool:
    """
    Receives at most one message and handles it.

    Args:
        queue: the job queue.
        review_fn: runs the review for one job; raises on failure.
        max_attempts: once a message has been received this many times and
                      fails again, it is dead-lettered instead of retried.
        visibility_timeout_s: how long the message stays hidden while we work.
                              Must exceed the longest review.

    Returns:
        True if a message was handled (success or failure), False if the queue was empty.
    """
    messages = queue.receive(max_messages=1, visibility_timeout_s=visibility_timeout_s)
    if not messages:
        return False

    message = messages[0]
    job = message.job
    try:
        review_fn(job)
    except Exception:  # any failure must be recorded and retried, never crash the worker
        logger.exception(
            "Review failed job_id=%s repo=%s pr=%s attempt=%s/%s",
            job.job_id, job.repo_full_name, job.pr_number, message.dequeue_count, max_attempts,
        )
        if message.dequeue_count >= max_attempts:
            queue.dead_letter(message)
            logger.error("Job dead-lettered job_id=%s after %s attempts", job.job_id, message.dequeue_count)
        # Below the cap we leave the message alone: it reappears after the visibility timeout.
        return True

    queue.delete(message)
    logger.info("Review done job_id=%s repo=%s pr=%s sha=%s", job.job_id, job.repo_full_name, job.pr_number, job.head_sha)
    return True


def run_forever(
    queue: JobQueue,
    review_fn: Callable[[ReviewJob], None],
    stop_event: threading.Event,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    visibility_timeout_s: int = DEFAULT_VISIBILITY_TIMEOUT_S,
    min_poll_s: float = MIN_POLL_S,
    max_poll_s: float = MAX_POLL_S,
) -> None:
    """
    Polls the queue until stop_event is set.

    The stop flag is checked only between jobs, so a job in progress always
    finishes (graceful shutdown). While the queue is empty the wait doubles
    from min_poll_s up to max_poll_s to avoid hammering the storage account.

    Args:
        queue: the job queue.
        review_fn: runs one review.
        stop_event: set by the signal handler to request shutdown.
        max_attempts: see process_one().
        visibility_timeout_s: see process_one().
        min_poll_s: first wait after an empty poll.
        max_poll_s: longest wait between polls.
    """
    delay = min_poll_s
    while not stop_event.is_set():
        try:
            handled = process_one(queue, review_fn, max_attempts, visibility_timeout_s)
        except Exception:  # queue/storage outage: log and back off, the worker must stay alive
            logger.exception("Queue operation failed")
            handled = False
        if handled:
            delay = min_poll_s
            continue
        stop_event.wait(delay)  # wakes immediately on shutdown
        delay = min(delay * 2, max_poll_s)


def _read_int_env(name: str, default: int) -> int:
    """Reads a positive integer from the environment, falling back to default if unset."""
    raw = os.environ.get(name)
    if not raw:
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


def main() -> None:
    """Worker entrypoint: ingest once, build the graph once, then process jobs until SIGTERM/SIGINT."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from app.bootstrap import build_review_graph  # heavy imports stay out of module import time

    max_attempts = _read_int_env("MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)
    visibility_timeout_s = _read_int_env("REVIEW_VISIBILITY_TIMEOUT_S", DEFAULT_VISIBILITY_TIMEOUT_S)

    queue = build_queue()
    compiled_graph = build_review_graph()

    stop_event = threading.Event()
    for signal_number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signal_number, lambda _signum, _frame: stop_event.set())

    logger.info("Worker started max_attempts=%s visibility_timeout_s=%s", max_attempts, visibility_timeout_s)
    run_forever(
        queue,
        lambda job: review_pull_request(compiled_graph, job),
        stop_event,
        max_attempts,
        visibility_timeout_s,
    )
    logger.info("Worker stopped")


if __name__ == "__main__":
    main()
