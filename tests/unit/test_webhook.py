"""
Unit test for app/api/webhook.py

No real GitHub, Azure, or LLM calls: the webhook handler's own job is HTTP
plumbing (HMAC verification, payload extraction, dedupe, enqueueing), so
the queue and dedupe store are the in-memory backends on app.state. The
review itself runs in app/worker.py and is covered by tests/unit/test_worker.py.
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
from app.jobs.dedupe import InMemoryDedupeStore
from app.jobs.queue import InMemoryJobQueue


def make_app(job_queue=None, dedupe=None):
    """Builds a minimal FastAPI app with the webhook router and in-memory job backends."""
    app = FastAPI()
    app.include_router(router)
    app.state.job_queue = job_queue or InMemoryJobQueue()
    app.state.dedupe = dedupe or InMemoryDedupeStore()
    return app


def sign(body: bytes, secret: str = "test-secret") -> str:
    """Computes the X-Hub-Signature-256 header value GitHub would send."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def make_payload(sha: str = "sha-one", action: str = "opened") -> dict:
    """Builds a minimal pull_request payload for the given head sha."""
    return {
        "action": action,
        "pull_request": {"number": 42, "head": {"sha": sha}},
        "repository": {"full_name": "test-owner/test-repo"},
    }


PR_OPENED_PAYLOAD = make_payload()


def post_event(client, payload: dict, event: str = "pull_request", delivery: str = "delivery-1"):
    """Posts a correctly signed webhook delivery."""
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sign(body),
            "Content-Type": "application/json",
        },
    )


def drain(job_queue: InMemoryJobQueue) -> list:
    """Receives every visible job from the in-memory queue."""
    return job_queue.receive(max_messages=100, visibility_timeout_s=60)


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
    app = make_app()

    response = post_event(TestClient(app), PR_OPENED_PAYLOAD, event="issues")

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert drain(app.state.job_queue) == []
    print("OK - non pull_request events are acknowledged but ignored")


def test_webhook_ignores_unhandled_actions():
    app = make_app()

    response = post_event(TestClient(app), make_payload(action="closed"))

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert drain(app.state.job_queue) == []
    print("OK - pull_request actions other than opened/synchronize are ignored")


def test_webhook_queues_job_for_opened_pr():
    app = make_app()

    response = post_event(TestClient(app), PR_OPENED_PAYLOAD, delivery="delivery-abc")

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert data["pr_number"] == 42
    assert data["repo"] == "test-owner/test-repo"
    messages = drain(app.state.job_queue)
    assert len(messages) == 1
    job = messages[0].job
    assert job.head_sha == "sha-one" and job.delivery_id == "delivery-abc"
    assert job.repo_full_name == "test-owner/test-repo" and job.pr_number == 42
    assert job.job_id == data["job_id"]
    print("OK - valid opened PR event queues a job with head_sha and delivery_id")


def test_webhook_same_delivery_twice_queues_one_job():
    app = make_app()
    client = TestClient(app)

    first = post_event(client, PR_OPENED_PAYLOAD, delivery="delivery-1")
    second = post_event(client, PR_OPENED_PAYLOAD, delivery="delivery-1")

    assert first.status_code == 202
    assert second.status_code == 200 and second.json()["status"] == "duplicate"
    assert len(drain(app.state.job_queue)) == 1, "A redelivery must not queue a second review"
    print("OK - the same delivery twice queues exactly one job")


def test_webhook_same_sha_new_delivery_is_duplicate_but_new_sha_is_queued():
    app = make_app()
    client = TestClient(app)

    post_event(client, make_payload("sha-one"), delivery="d1")
    same_sha = post_event(client, make_payload("sha-one", action="synchronize"), delivery="d2")
    new_sha = post_event(client, make_payload("sha-two", action="synchronize"), delivery="d3")

    assert same_sha.json()["status"] == "duplicate", "Same commit under a new delivery id is still a duplicate"
    assert new_sha.status_code == 202, "A new commit must be reviewed"
    shas = sorted(message.job.head_sha for message in drain(app.state.job_queue))
    assert shas == ["sha-one", "sha-two"]
    print("OK - dedupe is per commit: same sha skipped, new sha queued")


class FailingQueue:
    """A queue whose enqueue always fails, like a storage outage."""

    def enqueue(self, job) -> None:
        raise ConnectionError("storage unavailable")


def test_webhook_enqueue_failure_returns_503_and_releases_claim():
    dedupe = InMemoryDedupeStore()
    client = TestClient(make_app(job_queue=FailingQueue(), dedupe=dedupe))

    response = post_event(client, PR_OPENED_PAYLOAD)

    assert response.status_code == 503, "GitHub must see the delivery as failed"
    # Redelivery after the outage: the claim was released, so it is not a duplicate.
    working_app = make_app(dedupe=dedupe)
    retry = post_event(TestClient(working_app), PR_OPENED_PAYLOAD)
    assert retry.status_code == 202, "The released claim must let the redelivery through"
    print("OK - enqueue failure returns 503 and releases the dedupe claim")


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


def test_webhook_rejects_payload_without_head_sha():
    client = TestClient(make_app())
    payload = {
        "action": "opened",
        "pull_request": {"number": 42},
        "repository": {"full_name": "test-owner/test-repo"},
    }

    response = post_event(client, payload)

    assert response.status_code == 400, "head.sha is required to dedupe by commit"
    print("OK - payload without head.sha returns 400")


if __name__ == "__main__":
    test_webhook_rejects_missing_signature()
    test_webhook_rejects_invalid_signature()
    test_webhook_ignores_non_pull_request_events()
    test_webhook_ignores_unhandled_actions()
    test_webhook_queues_job_for_opened_pr()
    test_webhook_same_delivery_twice_queues_one_job()
    test_webhook_same_sha_new_delivery_is_duplicate_but_new_sha_is_queued()
    test_webhook_enqueue_failure_returns_503_and_releases_claim()
    test_webhook_rejects_malformed_payload()
    test_webhook_rejects_payload_without_head_sha()
    print("\nAll webhook tests passed.")
