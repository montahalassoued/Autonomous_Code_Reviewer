from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.jobs.models import ReviewJob
from app.jobs.review_status import InMemoryReviewStatusStore
from app.worker import review_pull_request
from tests.unit.test_webhook import PR_OPENED_PAYLOAD, make_app, post_event
from tools import github_auth
from tools.github_tools import PullRequestDiff

APP_VARIABLES = ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_PATH")


@pytest.fixture
def no_app(monkeypatch):
    for name in APP_VARIABLES:
        monkeypatch.delenv(name, raising=False)


# A fake App: each installation gets its own auth object, whose token says which installation it opens.
@pytest.fixture
def app_configured(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "-----BEGIN KEY-----\\nabc\\n-----END KEY-----")
    monkeypatch.setattr(github_auth, "_clients", {})
    minted = []

    def get_installation_auth(installation_id):
        minted.append(installation_id)
        return SimpleNamespace(token=f"ghs_for_{installation_id}")

    monkeypatch.setattr(github_auth, "_app_auth", lambda: SimpleNamespace(get_installation_auth=get_installation_auth))
    monkeypatch.setattr(github_auth, "Github", lambda auth: SimpleNamespace(auth=auth))
    return minted


def test_without_the_app_the_personal_token_is_used_and_its_absence_explained(no_app, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_personal")
    assert github_auth.github_client() is not None and github_auth.git_token(4242) is None, "git reads GITHUB_TOKEN itself"

    monkeypatch.delenv("GITHUB_TOKEN")
    with pytest.raises(RuntimeError, match="GITHUB_APP_ID"):
        github_auth.github_client()
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        github_auth.check_github_credentials()
    print("OK - a personal token still works locally; with neither credential the message says what to set")


def test_the_app_mints_one_token_per_installation_and_reuses_it(app_configured):
    with github_auth.github_installation(7):
        first = github_auth.github_client()
        again = github_auth.github_client()
    with github_auth.github_installation(8):
        other = github_auth.github_client()

    assert first is again and first.auth.token == "ghs_for_7" and other.auth.token == "ghs_for_8"
    assert github_auth.git_token(7) == "ghs_for_7" and app_configured == [7, 8], "one token per installation"
    print("OK - each installation's repos are read with its own short-lived token, minted once")


def test_the_app_refuses_an_event_without_an_installation(app_configured):
    with pytest.raises(RuntimeError, match="installation id"):
        github_auth.github_client()
    print("OK - a repo webhook's event (no installation) is not silently read with another token")


def test_a_one_line_private_key_gets_its_line_breaks_back(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "-----BEGIN KEY-----\\nabc\\n-----END KEY-----")
    assert github_auth._private_key() == "-----BEGIN KEY-----\nabc\n-----END KEY-----"
    print("OK - a PEM key pasted on one line in .env or a secret still parses")


def test_the_webhook_checks_the_author_with_the_events_installation(monkeypatch):
    seen = []

    def is_collaborator(repo, login):
        seen.append(github_auth._installation.get())
        return True

    post_event(TestClient(make_app(is_collaborator=is_collaborator)), {**PR_OPENED_PAYLOAD, "installation": {"id": 4242}})

    assert seen == [4242], "the installation reaches the lookup inside the thread pool"
    print("OK - the collaborator lookup runs with the installation that sent the event")


def test_every_github_call_of_a_job_uses_the_jobs_installation(monkeypatch):
    seen = []
    monkeypatch.setattr("app.worker.get_pull_request_head", lambda repo, pr: seen.append(github_auth._installation.get()) or ("open", "abc123"))
    monkeypatch.setattr("app.worker.get_pr_diff", lambda repo, pr: seen.append(github_auth._installation.get()) or PullRequestDiff(
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n+x = 1"))
    monkeypatch.setattr("app.worker.upsert_summary_comment", lambda repo, pr, md: seen.append(github_auth._installation.get()))
    graph = MagicMock()
    graph.invoke.return_value = {"final_review_markdown": "# Review"}

    review_pull_request(graph, ReviewJob("me/repo", 7, "abc123", installation_id=77), InMemoryReviewStatusStore())

    assert seen == [77, 77, 77, 77], "head check, diff, head check again, post"
    assert github_auth._installation.get() is None, "the next job starts clean"
    print("OK - the job's installation covers all its GitHub calls, and only them")
