import hashlib
import hmac
import json
import os

# Force (not setdefault): earlier tests may have already load_dotenv()'d a
# real .env value for this key, which would silently desync the sign()
# helper below from what _verify_signature() actually checks against.
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"
os.environ["ALLOWED_REPOS"] = "test-owner/test-repo"

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.webhook import router
from app.jobs.dedupe import InMemoryDedupeStore
from app.jobs.models import DedupeKey
from app.jobs.queue import InMemoryJobQueue


COLLABORATORS = {"alice"}


# Stands in for the GitHub API: alice is a collaborator, nobody else is.
def fake_is_collaborator(repo_full_name: str, login: str) -> bool:
    return login in COLLABORATORS


# Builds a minimal FastAPI app with the webhook router, in-memory job backends and a fake collaborator check.
def make_app(job_queue=None, dedupe=None, is_collaborator=fake_is_collaborator):
    app = FastAPI()
    app.include_router(router)
    app.state.job_queue = job_queue or InMemoryJobQueue()
    app.state.dedupe = dedupe or InMemoryDedupeStore()
    app.state.is_collaborator = is_collaborator
    return app


# Computes the X-Hub-Signature-256 header value GitHub would send.
def sign(body: bytes, secret: str = "test-secret") -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# Builds a minimal pull_request payload for the given head sha.
def make_payload(sha: str = "sha-one", action: str = "opened", author: str = "alice", draft: bool = False,
                 repo: str = "test-owner/test-repo") -> dict:
    return {
        "action": action,
        "pull_request": {"number": 42, "head": {"sha": sha}, "user": {"login": author}, "draft": draft},
        "repository": {"full_name": repo},
    }


PR_OPENED_PAYLOAD = make_payload()


# Posts a correctly signed webhook delivery.
def post_event(client, payload: dict, event: str = "pull_request", delivery: str = "delivery-1"):
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


# Receives every visible job from the in-memory queue.
def drain(job_queue: InMemoryJobQueue) -> list:
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


# A queue whose enqueue always fails, like a storage outage.
class FailingQueue:

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


def test_webhook_ignores_pr_from_a_non_collaborator():
    app = make_app()
    client = TestClient(app)

    response = post_event(client, make_payload(author="mallory"))

    assert response.status_code == 200 and response.json()["status"] == "ignored"
    assert "not a collaborator" in response.json()["reason"]
    assert drain(app.state.job_queue) == []
    assert app.state.dedupe.claim(DedupeKey("test-owner/test-repo", "sha-one", "pr:42")), "no dedupe claim was taken"
    print("OK - an outsider's PR is ignored before the dedupe and the queue")


def test_webhook_ignores_repo_not_in_allowed_repos(monkeypatch):
    monkeypatch.delenv("ALLOWED_OWNERS", raising=False)
    app = make_app()

    response = post_event(TestClient(app), make_payload(repo="someone/else"))

    assert response.json() == {"status": "ignored", "reason": "repo someone/else is not in ALLOWED_OWNERS or ALLOWED_REPOS"}
    assert drain(app.state.job_queue) == []
    print("OK - only allowed repos are reviewed")


def test_an_allowed_owner_covers_every_one_of_their_repos(monkeypatch):
    monkeypatch.setenv("ALLOWED_OWNERS", "Someone")
    app = make_app()

    response = post_event(TestClient(app), make_payload(repo="someone/brand-new-repo"))

    assert response.status_code == 202, "a repo created today needs no setting"
    assert drain(app.state.job_queue)[0].job.repo_full_name == "someone/brand-new-repo"
    print("OK - ALLOWED_OWNERS reviews any repo of that owner, any case")


def test_webhook_ignores_drafts_and_reviews_them_when_ready():
    app = make_app()
    client = TestClient(app)

    draft = post_event(client, make_payload(draft=True), delivery="d1")
    ready = post_event(client, make_payload(action="ready_for_review"), delivery="d2")

    assert draft.json()["reason"] == "draft pull request"
    assert ready.status_code == 202, "the draft left no dedupe row, so ready_for_review is queued"
    assert len(drain(app.state.job_queue)) == 1
    print("OK - drafts are skipped, ready_for_review queues the review")


def test_webhook_returns_503_when_the_collaborator_check_fails():
    def github_down(repo_full_name, login):
        raise ConnectionError("GitHub unreachable")

    app = make_app(is_collaborator=github_down)

    response = post_event(TestClient(app), PR_OPENED_PAYLOAD)

    assert response.status_code == 503, "fail closed: no review without a verified author"
    assert drain(app.state.job_queue) == []
    print("OK - an unverifiable author is refused with 503 so GitHub can redeliver")


def test_webhook_reviews_nothing_when_allowed_repos_is_unset(monkeypatch):
    monkeypatch.delenv("ALLOWED_REPOS")
    monkeypatch.delenv("ALLOWED_OWNERS", raising=False)

    response = post_event(TestClient(make_app()), PR_OPENED_PAYLOAD)

    assert response.json()["status"] == "ignored" and "neither ALLOWED_OWNERS nor ALLOWED_REPOS" in response.json()["reason"]
    print("OK - a missing allow-list fails closed")


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
    test_webhook_ignores_pr_from_a_non_collaborator()
    test_webhook_ignores_repo_not_in_allowed_repos()
    test_webhook_ignores_drafts_and_reviews_them_when_ready()
    test_webhook_returns_503_when_the_collaborator_check_fails()
    print("\nAll webhook tests passed.")


def test_an_oversized_body_is_refused_before_the_signature_check(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_MAX_BODY_BYTES", "100")
    job_queue = InMemoryJobQueue()
    client = TestClient(make_app(job_queue=job_queue))

    response = client.post("/webhook/github", content=b"x" * 101, headers={"X-GitHub-Event": "pull_request"})

    assert response.status_code == 413, "no signature was sent, so a 401 would mean the body was read and checked first"
    assert drain(job_queue) == []
    print("OK - a body over the cap gets 413 without a signature check")


def test_a_streamed_body_without_content_length_is_still_capped(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_MAX_BODY_BYTES", "100")
    client = TestClient(make_app())

    def chunks():
        for _ in range(5):
            yield b"x" * 40  # 200 bytes in total, sent chunked with no Content-Length

    response = client.post("/webhook/github", content=chunks(), headers={"X-GitHub-Event": "pull_request"})

    assert response.status_code == 413
    print("OK - a chunked body is capped while it is read")


def test_a_body_under_the_cap_is_still_reviewed(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_MAX_BODY_BYTES", "100000")
    job_queue = InMemoryJobQueue()
    client = TestClient(make_app(job_queue=job_queue))

    assert post_event(client, PR_OPENED_PAYLOAD).status_code == 202
    assert len(drain(job_queue)) == 1
    print("OK - a normal delivery passes the size cap")


def test_a_signed_body_that_is_not_json_gets_400():
    client = TestClient(make_app())
    body = b"not json"

    response = client.post("/webhook/github", content=body,
                           headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(body)})

    assert response.status_code == 400
    print("OK - a signed non-JSON body is refused with 400, not a 500")
