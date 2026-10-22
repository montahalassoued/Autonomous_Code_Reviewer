import time
import threading
from unittest.mock import MagicMock

from app.jobs.models import ReviewJob
from app.jobs.queue import InMemoryJobQueue
from app.jobs.review_status import InMemoryReviewStatusStore, ReviewCost, ReviewStatus
from app.worker import process_one, review_pull_request, run_forever
from tools.github_tools import SUMMARY_MARKER, PullRequestDiff, changed_files_in_diff, upsert_summary_comment

SMALL_DIFF = "diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n@@ -0,0 +1 @@\n+x = 1"
VISIBILITY_S = 60


# A controllable clock for the in-memory queue.
class FakeClock:

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# Returns a queue holding exactly one job, plus its clock and the job.
def make_queue_with_job() -> tuple[InMemoryJobQueue, FakeClock, ReviewJob]:
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
    monkeypatch.setattr("app.worker.get_pr_diff", lambda repo, pr: PullRequestDiff(SMALL_DIFF))
    monkeypatch.setattr("app.worker.get_pull_request_head", lambda repo, pr: ("open", "abc123"))
    monkeypatch.setattr("app.worker.upsert_summary_comment", lambda repo, pr, md: posted.append((repo, pr, md)))
    graph = MagicMock()
    graph.invoke.return_value = {"final_review_markdown": "# Review"}
    job = ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc123")

    review_pull_request(graph, job, InMemoryReviewStatusStore())

    state = graph.invoke.call_args.args[0]
    assert state["pr_diff"] == SMALL_DIFF and state["pr_number"] == 7 and state["unreviewed_files"] == []
    assert posted == [("owner/repo", 7, "# Review")]
    print("OK - review_pull_request invokes the graph and upserts the comment")


# Builds a fake issue comment with a body and an author.
def make_comment(body: str, login: str) -> MagicMock:
    comment = MagicMock()
    comment.body = body
    comment.user.login = login
    return comment


# Patches the GitHub client; returns the fake pull request.
def fake_github(monkeypatch, comments: list) -> MagicMock:
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


def test_upsert_ignores_bot_comment_with_marker_not_at_the_start(monkeypatch):
    quoted = make_comment(f"I pasted {SUMMARY_MARKER} here", "review-bot")
    pull_request = fake_github(monkeypatch, [quoted])

    upsert_summary_comment("owner/repo", 7, "new review")

    quoted.edit.assert_not_called()
    pull_request.create_issue_comment.assert_called_once()
    print("OK - only a comment that starts with the marker is edited")


def test_upsert_replaces_an_oversized_body_instead_of_failing(monkeypatch):
    pull_request = fake_github(monkeypatch, [])

    upsert_summary_comment("owner/repo", 7, "x" * 70_000)

    body = pull_request.create_issue_comment.call_args.args[0]
    assert body.startswith(SUMMARY_MARKER) and len(body) < 1_000
    print("OK - no 422 retry loop on a huge review")


def test_changed_files_are_read_from_the_diff_headers():
    diff = "diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n@@ -1 +1 @@\n--- not a header\n"

    assert changed_files_in_diff(diff) == ["app/x.py"]
    print("OK - a removed line starting with '--' is not mistaken for a file")


# Patches GitHub for review_pull_request: each call to the head lookup returns the next (state, sha).
def fake_review_github(monkeypatch, heads: list[tuple[str, str]]) -> list:
    posted: list = []
    answers = iter(heads)
    monkeypatch.setattr("app.worker.get_pull_request_head", lambda repo, pr: next(answers))
    monkeypatch.setattr("app.worker.get_pr_diff", lambda repo, pr: PullRequestDiff(SMALL_DIFF))
    monkeypatch.setattr("app.worker.upsert_summary_comment", lambda repo, pr, md: posted.append(md))
    return posted


def finished_graph(failed_agents: list[str] | None = None) -> MagicMock:
    graph = MagicMock()
    graph.invoke.return_value = {"final_review_markdown": "# Review", "failed_agents": failed_agents or [],
                                 "llm_calls": 3, "tokens_in": 15_000, "tokens_out": 2_000}
    return graph


def test_files_over_the_budget_or_without_patch_are_named_not_reviewed(monkeypatch):
    big = "diff --git a/data/big.py b/data/big.py\n--- a/data/big.py\n+++ b/data/big.py\n@@ -0,0 +1 @@\n+" + "y" * 500
    monkeypatch.setenv("REVIEW_MAX_DIFF_CHARS", "200")
    fake_review_github(monkeypatch, [("open", "abc123"), ("open", "abc123")])
    monkeypatch.setattr("app.worker.get_pr_diff",
                        lambda repo, pr: PullRequestDiff(f"{big}\n{SMALL_DIFF}", files_without_patch=("logo.png",)))
    graph = finished_graph()

    review_pull_request(graph, ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc123"),
                        InMemoryReviewStatusStore())

    state = graph.invoke.call_args.args[0]
    assert state["pr_diff"] == SMALL_DIFF, "the small file that fits is still reviewed"
    assert state["unreviewed_files"] == ["logo.png", "data/big.py"]
    print("OK - the agents get what fits, the review names the rest")


def test_a_pull_request_with_nothing_reviewable_costs_no_llm_call(monkeypatch):
    posted = fake_review_github(monkeypatch, [("open", "abc123")])
    monkeypatch.setattr("app.worker.get_pr_diff", lambda repo, pr: PullRequestDiff("", files_without_patch=("logo.png",)))
    store, graph = InMemoryReviewStatusStore(), finished_graph()
    job = ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc123")

    review_pull_request(graph, job, store)

    graph.invoke.assert_not_called()
    assert "Nothing in this pull request could be reviewed" in posted[0] and "logo.png" in posted[0]
    assert store.statuses[job.dedupe_key].cost == ReviewCost()
    print("OK - a binary-only PR gets an explanation, not three empty agent calls")


def test_a_superseded_commit_is_skipped_before_any_llm_call(monkeypatch):
    posted = fake_review_github(monkeypatch, [("open", "newer-sha")])
    store, graph = InMemoryReviewStatusStore(), finished_graph()
    job = ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc123")

    review_pull_request(graph, job, store)

    graph.invoke.assert_not_called()
    assert posted == [] and store.statuses[job.dedupe_key] == ReviewStatus("skipped", reason="superseded")
    print("OK - an old commit costs no LLM call and posts nothing")


def test_a_closed_pull_request_is_skipped(monkeypatch):
    fake_review_github(monkeypatch, [("closed", "abc123")])
    store = InMemoryReviewStatusStore()
    job = ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc123")

    review_pull_request(finished_graph(), job, store)

    assert store.statuses[job.dedupe_key].reason == "closed"
    print("OK - a closed or merged PR is not reviewed")


def test_a_push_during_the_review_drops_the_result(monkeypatch):
    posted = fake_review_github(monkeypatch, [("open", "abc123"), ("open", "newer-sha")])
    store = InMemoryReviewStatusStore()
    job = ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc123")

    review_pull_request(finished_graph(), job, store)

    assert posted == [], "the review of the older commit must not overwrite the newer one"
    assert store.statuses[job.dedupe_key].status == "skipped"
    assert store.statuses[job.dedupe_key].cost == ReviewCost(3, 15_000, 2_000), "the tokens were spent anyway"
    print("OK - the head is checked again just before posting")


def test_a_posted_review_records_latency_and_missing_categories(monkeypatch):
    posted = fake_review_github(monkeypatch, [("open", "abc123"), ("open", "abc123")])
    store = InMemoryReviewStatusStore()
    job = ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc123")

    review_pull_request(finished_graph(failed_agents=["logic"]), job, store)

    status = store.statuses[job.dedupe_key]
    assert posted == ["# Review"] and status.status == "posted"
    assert status.missing_categories == ("logic",) and status.latency_ms >= 0
    assert status.cost == ReviewCost(llm_calls=3, tokens_in=15_000, tokens_out=2_000)
    print("OK - posted, with the partial categories, the latency and the LLM cost")


def test_a_dead_lettered_job_is_recorded_as_failed_with_a_redacted_reason(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "k" * 30)
    queue, clock, job = make_queue_with_job()
    store = InMemoryReviewStatusStore()

    def failing_review(_job):
        raise RuntimeError("Groq said no for key gsk_" + "k" * 30)

    for _ in range(3):
        process_one(queue, failing_review, max_attempts=3, visibility_timeout_s=VISIBILITY_S, status_store=store)
        clock.now += VISIBILITY_S + 1

    status = store.statuses[job.dedupe_key]
    assert status.status == "failed" and status.reason.startswith("RuntimeError: Groq said no")
    assert "k" * 30 not in status.reason, "the key is redacted before it reaches Postgres"
    print("OK - the third failure marks the review failed, without leaking the key")


def test_a_job_whose_worker_kept_dying_is_dead_lettered_without_running():
    queue, clock, job = make_queue_with_job()
    store = InMemoryReviewStatusStore()
    for _ in range(3):  # three leases that never finished: the process was killed each time
        queue.receive(max_messages=1, visibility_timeout_s=VISIBILITY_S)
        clock.now += VISIBILITY_S + 1
    reviewed = []

    process_one(queue, reviewed.append, max_attempts=3, visibility_timeout_s=VISIBILITY_S, status_store=store)

    assert reviewed == [], "a fourth delivery must not pay for the LLM calls again"
    assert queue.poison == [job] and store.statuses[job.dedupe_key].status == "failed"
    print("OK - a crash loop ends in the poison queue, recorded as failed")


def test_a_failure_below_the_cap_is_not_marked_failed():
    queue, _clock, job = make_queue_with_job()
    store = InMemoryReviewStatusStore()

    process_one(queue, lambda _job: 1 / 0, max_attempts=3, visibility_timeout_s=VISIBILITY_S, status_store=store)

    assert job.dedupe_key not in store.statuses, "it will be retried, so it is not failed yet"
    print("OK - only the last attempt records a failure")


if __name__ == "__main__":
    test_process_one_success_deletes_message()
    test_process_one_failure_under_max_attempts_is_retried()
    test_process_one_failure_at_max_attempts_is_dead_lettered()
    test_process_one_returns_false_on_empty_queue()
    test_run_forever_stops_when_event_is_set()
    print("\nWorker tests passed (GitHub tests need pytest's monkeypatch).")


def test_the_review_gets_a_deadline_under_the_visibility_timeout(monkeypatch):
    monkeypatch.setattr("app.worker.get_pr_diff", lambda repo, pr: PullRequestDiff(SMALL_DIFF))
    monkeypatch.setattr("app.worker.get_pull_request_head", lambda repo, pr: ("open", "abc123"))
    monkeypatch.setattr("app.worker.upsert_summary_comment", lambda repo, pr, md: None)
    monkeypatch.setenv("REVIEW_DEADLINE_S", "300")
    graph = MagicMock()
    graph.invoke.return_value = {"final_review_markdown": "# Review"}
    before = time.monotonic()

    review_pull_request(graph, ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc123"),
                        InMemoryReviewStatusStore())

    deadline = graph.invoke.call_args.args[0]["review_deadline"]
    assert before + 300 <= deadline <= time.monotonic() + 300
    print("OK - every review carries a deadline the LLM client stops at")
