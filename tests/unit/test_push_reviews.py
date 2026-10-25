from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.jobs.models import ReviewJob
from app.jobs.review_status import SKIP_NO_CODE, InMemoryReviewStatusStore, ReviewStatus
from app.jobs.trigger_policy import evaluate_push
from app.worker import review_pull_request
from tests.unit.test_webhook import drain, make_app, sign
from tools import github_tools
from tools.github_tools import PullRequestDiff

BEFORE, AFTER = "1" * 40, "2" * 40
CODE_DIFF = "diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n@@ -0,0 +1 @@\n+x = 1"


def push_payload(repo: str = "me/any-repo", ref: str = "refs/heads/main", files=("app/x.py",), **overrides) -> dict:
    payload = {"ref": ref, "before": BEFORE, "after": AFTER, "deleted": False,
               "repository": {"full_name": repo}, "installation": {"id": 4242},
               "commits": [{"id": AFTER, "added": [], "modified": list(files), "removed": []}]}
    return {**payload, **overrides}


def test_a_push_of_code_to_a_branch_of_an_allowed_owner_is_reviewed(monkeypatch):
    monkeypatch.setenv("ALLOWED_OWNERS", "me")

    assert evaluate_push(push_payload()).allowed
    assert evaluate_push(push_payload(repo="someone/else")).reason.startswith("repo someone/else is not")
    assert evaluate_push(push_payload(deleted=True, after="0" * 40)).reason == "branch deleted"
    assert evaluate_push(push_payload(ref="refs/tags/v1.0")).reason == "not a branch push"
    assert evaluate_push(push_payload(commits=[])).reason == "no new commit"
    assert evaluate_push(push_payload(files=("README.md", "logo.png"))).reason == "no code file added or changed"
    print("OK - only code pushed to a branch of an allowed owner spends LLM calls")


def test_the_webhook_queues_a_push_once_with_its_branch_range_and_installation(monkeypatch):
    import json

    monkeypatch.setenv("ALLOWED_OWNERS", "me")
    app = make_app()
    client = TestClient(app)
    body = json.dumps(push_payload()).encode()

    def deliver(delivery: str):
        return client.post("/webhook/github", content=body, headers={
            "X-Hub-Signature-256": sign(body), "X-GitHub-Event": "push", "X-GitHub-Delivery": delivery})

    first, again = deliver("p1"), deliver("p2")

    assert first.status_code == 202 and first.json()["ref"] == "refs/heads/main"
    assert again.json()["status"] == "duplicate", "a redelivered push is reviewed once"
    [message] = drain(app.state.job_queue)
    job = message.job
    assert (job.event, job.ref, job.base_sha, job.head_sha, job.installation_id) == ("push", "refs/heads/main", BEFORE, AFTER, 4242)
    assert job.dedupe_key.target == "push:refs/heads/main"
    print("OK - a push becomes one job: repo from the event, before..after, the App installation")


def test_a_push_review_is_posted_on_the_commit_without_a_pull_request_check(monkeypatch):
    posted, diffs = [], []
    monkeypatch.setattr("app.worker.get_push_diff", lambda repo, base, head: diffs.append((repo, base, head)) or PullRequestDiff(CODE_DIFF))
    monkeypatch.setattr("app.worker.post_commit_comment", lambda repo, sha, md: posted.append((repo, sha, md)))
    monkeypatch.setattr("app.worker.get_pull_request_head", MagicMock(side_effect=AssertionError("a push has no PR")))
    graph = MagicMock()
    graph.invoke.return_value = {"final_review_markdown": "# Review"}
    job = ReviewJob(repo_full_name="me/any-repo", pr_number=0, head_sha=AFTER, event="push", ref="refs/heads/main", base_sha=BEFORE)

    review_pull_request(graph, job, InMemoryReviewStatusStore())

    assert diffs == [("me/any-repo", BEFORE, AFTER)]
    assert posted == [("me/any-repo", AFTER, "# Review")]
    print("OK - the push's own range is reviewed and the review lands on the pushed commit")


def test_a_push_with_nothing_reviewable_is_skipped_without_a_comment(monkeypatch):
    posted = []
    monkeypatch.setattr("app.worker.get_push_diff", lambda repo, base, head: PullRequestDiff("", files_without_patch=("logo.png",)))
    monkeypatch.setattr("app.worker.post_commit_comment", lambda repo, sha, md: posted.append(md))
    store = InMemoryReviewStatusStore()
    graph = MagicMock()
    job = ReviewJob(repo_full_name="me/any-repo", pr_number=0, head_sha=AFTER, event="push", ref="refs/heads/main", base_sha=BEFORE)

    review_pull_request(graph, job, store)

    graph.invoke.assert_not_called()
    assert posted == [] and store.statuses[job.dedupe_key] == ReviewStatus("skipped", reason=SKIP_NO_CODE)
    print("OK - no LLM call and no comment for a push with nothing to review")


def test_a_push_diff_compares_its_range_or_takes_the_first_commit_of_a_new_branch(monkeypatch):
    patch = SimpleNamespace(filename="app/x.py", patch="@@ -0,0 +1 @@\n+x = 1")
    repo = MagicMock()
    repo.compare.return_value = SimpleNamespace(files=[patch])
    repo.get_commit.return_value = SimpleNamespace(files=[patch])
    monkeypatch.setattr(github_tools, "_build_github_client", lambda: SimpleNamespace(get_repo=lambda name: repo))

    assert "+x = 1" in github_tools.get_push_diff("me/r", BEFORE, AFTER).text
    repo.compare.assert_called_once_with(BEFORE, AFTER)
    assert "+x = 1" in github_tools.get_push_diff("me/r", "0" * 40, AFTER).text
    repo.get_commit.assert_called_once_with(AFTER)
    print("OK - before..after for a push; the head commit alone when the push created the branch")
