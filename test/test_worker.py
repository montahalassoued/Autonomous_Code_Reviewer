"""
Unit test for app/worker.py and tools/github_tools.py (upsert_summary_comment)

No Azure, GitHub, or LLM calls: the queue is InMemoryJobQueue with a fake
clock, the review function is a stub, and PyGithub is a MagicMock. The
real graph is covered by test/test_pipeline_offline.py.
"""

import threading
from unittest.mock import MagicMock

from app.jobs.models import ReviewJob
from app.jobs.queue import InMemoryJobQueue
from app.worker import process_one, review_pull_request, run_forever
from tools.github_tools import SUMMARY_MARKER, upsert_summary_comment


VISIBILITY_S = 60


class FakeClock:
    """A controllable clock for the in-memory queue."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_queue_with_job() -> tuple[InMemoryJobQueue, FakeClock, ReviewJob]:
    """Returns a queue holding exactly one job, plus its clock and the job."""
    clock = FakeClock()
    queue = InMemoryJobQueue(clock=clock)
    job = ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc123", delivery_id="d1")
    queue.enqueue(job)
    return queue, clock, job


def test_process_one_success_deletes_message():
    queue, clock, job = make_queue_with_job()
    reviewed = []

    handled = process_one(queue, reviewed.append, max_attempts=3, visibility_timeout_s=VISIBILITY_S)

    assert handled is True
    assert reviewed == [job], "review_fn must be called exactly once with the job"
    clock.now += VISIBILITY_S + 1
    assert queue.receive(1, VISIBILITY_S) == [], "A successful job must be deleted"
    print("OK - success calls review_fn once and deletes the message")


def failing_review(_job: ReviewJob) -> None:
    raise RuntimeError("groq rate limit")


def test_process_one_failure_under_max_attempts_is_retried():
    queue, clock, _job = make_queue_with_job()

    handled = process_one(queue, failing_review, max_attempts=3, visibility_timeout_s=VISIBILITY_S)

    assert handled is True
    assert queue.poison == []
    clock.now += VISIBILITY_S + 1
    retry = queue.receive(1, VISIBILITY_S)
    assert len(retry) == 1 and retry[0].dequeue_count == 2, "Failed job must reappear for a retry"
    print("OK - a failed job is left in the queue and reappears")


def test_process_one_failure_at_max_attempts_is_dead_lettered():
    queue, clock, job = make_queue_with_job()

    for _ in range(3):
        process_one(queue, failing_review, max_attempts=3, visibility_timeout_s=VISIBILITY_S)
        clock.now += VISIBILITY_S + 1

    assert queue.poison == [job], "Third failure must move the job to the poison queue"
    assert queue.receive(1, VISIBILITY_S) == []
    print("OK - the job is dead-lettered after max attempts")


def test_process_one_returns_false_on_empty_queue():
    queue = InMemoryJobQueue(clock=FakeClock())

    assert process_one(queue, lambda job: None, max_attempts=3, visibility_timeout_s=VISIBILITY_S) is False
    print("OK - an empty queue returns False")


def test_run_forever_stops_when_event_is_set():
    queue, _clock, job = make_queue_with_job()
    stop_event = threading.Event()
    reviewed = []

    def review_then_stop(received_job: ReviewJob) -> None:
        reviewed.append(received_job)
        stop_event.set()  # simulates SIGTERM arriving during a job

    run_forever(queue, review_then_stop, stop_event, min_poll_s=0.001, max_poll_s=0.002)

    assert reviewed == [job], "The running job must finish before the loop exits"
    print("OK - run_forever finishes the current job and exits")


def test_review_pull_request_runs_graph_and_upserts(monkeypatch):
    posted = []
    monkeypatch.setattr("app.worker.get_pr_diff", lambda repo, pr: "the diff")
    monkeypatch.setattr("app.worker.upsert_summary_comment", lambda repo, pr, md: posted.append((repo, pr, md)))
    graph = MagicMock()
    graph.invoke.return_value = {"final_review_markdown": "# Review"}
    job = ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc123")

    review_pull_request(graph, job)

    state = graph.invoke.call_args.args[0]
    assert state["pr_diff"] == "the diff" and state["pr_number"] == 7
    assert posted == [("owner/repo", 7, "# Review")]
    print("OK - review_pull_request invokes the graph and upserts the comment")


def make_comment(body: str, login: str) -> MagicMock:
    """Builds a fake issue comment with a body and an author."""
    comment = MagicMock()
    comment.body = body
    comment.user.login = login
    return comment


def fake_github(monkeypatch, comments: list) -> MagicMock:
    """Patches the GitHub client; returns the fake pull request."""
    client = MagicMock()
    client.get_user.return_value.login = "review-bot"
    pull_request = client.get_repo.return_value.get_pull.return_value
    pull_request.get_issue_comments.return_value = comments
    monkeypatch.setattr("tools.github_tools._build_github_client", lambda: client)
    return pull_request


def test_upsert_edits_existing_marked_comment_by_bot(monkeypatch):
    mine = make_comment(f"{SUMMARY_MARKER}\nold review", "review-bot")
    pull_request = fake_github(monkeypatch, [make_comment("unrelated", "review-bot"), mine])

    upsert_summary_comment("owner/repo", 7, "new review")

    mine.edit.assert_called_once_with(f"{SUMMARY_MARKER}\nnew review")
    pull_request.create_issue_comment.assert_not_called()
    print("OK - an existing bot summary is edited, not duplicated")


def test_upsert_creates_when_no_marked_comment(monkeypatch):
    pull_request = fake_github(monkeypatch, [make_comment("hello", "someone")])

    upsert_summary_comment("owner/repo", 7, "new review")

    pull_request.create_issue_comment.assert_called_once_with(f"{SUMMARY_MARKER}\nnew review")
    print("OK - a summary is created when none exists")


def test_upsert_ignores_marked_comment_from_other_user(monkeypatch):
    impostor = make_comment(f"{SUMMARY_MARKER}\nfake", "someone-else")
    pull_request = fake_github(monkeypatch, [impostor])

    upsert_summary_comment("owner/repo", 7, "new review")

    impostor.edit.assert_not_called()
    pull_request.create_issue_comment.assert_called_once()
    print("OK - a marker copied by another user is ignored")


if __name__ == "__main__":
    test_process_one_success_deletes_message()
    test_process_one_failure_under_max_attempts_is_retried()
    test_process_one_failure_at_max_attempts_is_dead_lettered()
    test_process_one_returns_false_on_empty_queue()
    test_run_forever_stops_when_event_is_set()
    print("\nWorker tests passed (GitHub tests need pytest's monkeypatch).")
