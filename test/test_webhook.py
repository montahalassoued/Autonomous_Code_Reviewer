"""
Unit test for app/api/webhook.py

No real GitHub or LLM calls: the webhook handler's own job is HTTP
plumbing (HMAC verification, payload extraction, scheduling a
background task), so get_pr_diff, post_review_comment, and the
compiled graph are all monkeypatched. The pipeline they'd trigger is
already covered end-to-end by test/test_retriever_integration.py and
the individual agent tests.
"""

import hashlib
import hmac
import json
import os

# Force (not setdefault): earlier tests may have already load_dotenv()'d a
# real .env value for this key, which would silently desync the sign()
# helper below from what _verify_signature() actually checks against.
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.webhook import router


def make_app():
    """Builds a minimal FastAPI app with just the webhook router mounted."""
    app = FastAPI()
    app.include_router(router)
    app.state.compiled_graph = None  # unused unless a request reaches the background task
    return app


def sign(body: bytes, secret: str = "test-secret") -> str:
    """Computes the X-Hub-Signature-256 header value GitHub would send."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


PR_OPENED_PAYLOAD = {
    "action": "opened",
    "pull_request": {"number": 42},
    "repository": {"full_name": "test-owner/test-repo"},
}


def test_webhook_rejects_missing_signature():
    client = TestClient(make_app())
    body = json.dumps(PR_OPENED_PAYLOAD).encode()

    response = client.post(
        "/webhook/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )

    assert response.status_code == 401
    print("OK - request with no signature header is rejected")


def test_webhook_rejects_invalid_signature():
    client = TestClient(make_app())
    body = json.dumps(PR_OPENED_PAYLOAD).encode()

    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 401
    print("OK - request with a wrong signature is rejected")


def test_webhook_ignores_non_pull_request_events():
    client = TestClient(make_app())
    body = json.dumps(PR_OPENED_PAYLOAD).encode()

    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": sign(body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    print("OK - non pull_request events are acknowledged but ignored")


def test_webhook_ignores_unhandled_actions():
    client = TestClient(make_app())
    payload = {**PR_OPENED_PAYLOAD, "action": "closed"}
    body = json.dumps(payload).encode()

    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sign(body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    print("OK - pull_request actions other than opened/synchronize are ignored")


def test_webhook_schedules_review_for_opened_pr(monkeypatch):
    import app.api.webhook as webhook_module

    called = {}

    def fake_add_task(self, func, *args, **kwargs):
        called["func"] = func
        called["args"] = args

    client = TestClient(make_app())
    body = json.dumps(PR_OPENED_PAYLOAD).encode()

    monkeypatch.setattr(
        "fastapi.BackgroundTasks.add_task",
        fake_add_task,
    )

    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sign(body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "scheduled"
    assert data["pr_number"] == 42
    assert data["repo"] == "test-owner/test-repo"
    assert called["func"] is webhook_module._run_review_pipeline
    print("OK - valid opened PR event schedules the review pipeline as a background task")


def test_webhook_rejects_malformed_payload():
    client = TestClient(make_app())
    body = json.dumps({"action": "opened"}).encode()  # missing pull_request/repository

    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sign(body),
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 400
    print("OK - malformed payload missing required fields returns 400, not a crash")


if __name__ == "__main__":
    # test_webhook_schedules_review_for_opened_pr uses pytest's monkeypatch
    # fixture, so it's exercised via `pytest test/test_webhook.py` instead.
    test_webhook_rejects_missing_signature()
    test_webhook_rejects_invalid_signature()
    test_webhook_ignores_non_pull_request_events()
    test_webhook_ignores_unhandled_actions()
    test_webhook_rejects_malformed_payload()
    print("\nAll webhook tests passed (run under pytest for full coverage).")
