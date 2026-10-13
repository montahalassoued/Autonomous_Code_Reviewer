"""
webhook.py - GitHub webhook route handler.

Inputs: GitHub webhook HTTP requests (pull_request event, "opened" or
"synchronize" action), including the raw request body, the
X-Hub-Signature-256 header GitHub signs every payload with, and the
X-GitHub-Delivery header (a unique id per delivery).
Outputs: 202 {"status": "queued"} after the review job is stored in the
durable queue; 200 {"status": "duplicate"} if that commit was already
queued; 503 if the queue is unavailable so the delivery shows as failed
in GitHub and can be redelivered.

The handler never runs a review and never needs the compiled graph or
the repo index: app/worker.py does that. The queue and dedupe store live
on `request.app.state` (set in app/main.py, replaced in tests).

Why dedupe by (repo, head sha, PR): GitHub delivers at least once and
users can redeliver by hand. The same commit must not be reviewed twice.
The claim is made BEFORE enqueueing and released if enqueueing fails,
so a failed enqueue never blocks the redelivery.
"""

import hashlib
import hmac
import logging
import os

from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.jobs.models import DedupeKey, ReviewJob


logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_signature(payload_body: bytes, signature_header: str | None) -> None:
    """
    Validates the X-Hub-Signature-256 header GitHub attaches to every
    webhook delivery, proving the request actually came from GitHub
    (or whoever holds GITHUB_WEBHOOK_SECRET) and wasn't forged or
    tampered with in transit.

    Args:
        payload_body: the raw, unparsed request body bytes - the
                       signature is computed over these exact bytes,
                       so it must never be reserialized JSON
        signature_header: the value of the X-Hub-Signature-256 header,
                           or None if missing

    Raises:
        HTTPException: 401 if the header is missing or doesn't match
    """
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError(
            "GITHUB_WEBHOOK_SECRET environment variable is not set. "
            "Set it in your .env file to match the secret configured "
            "on the GitHub webhook."
        )

    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    expected_signature = "sha256=" + hmac.new(
        key=secret.encode(),
        msg=payload_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def _extract_pr_fields(payload: dict) -> tuple[int, str, str]:
    """
    Pulls the fields the review job needs out of a GitHub
    pull_request webhook payload.

    Args:
        payload: the parsed JSON webhook body

    Returns:
        (pr_number, repo_full_name, head_sha) - the list of changed
        files is NOT in the payload; the worker fetches it via
        get_pr_diff()

    Raises:
        HTTPException: 400 if required fields are missing, so a
        malformed or unexpected payload doesn't crash the server
    """
    try:
        pr_number = payload["pull_request"]["number"]
        repo_full_name = payload["repository"]["full_name"]
        head_sha = payload["pull_request"]["head"]["sha"]
    except (KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Malformed webhook payload, missing field: {exc}",
        ) from exc

    return pr_number, repo_full_name, head_sha


@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
):
    """
    Receives a GitHub webhook delivery, validates it, and queues a
    review job for "opened" or "synchronize" pull_request events.

    Args:
        request: the raw FastAPI request, used to read the exact body
                 bytes needed for signature verification
        x_hub_signature_256: GitHub's HMAC signature header
        x_github_event: GitHub's event-type header (e.g. "pull_request")
        x_github_delivery: GitHub's unique delivery id (logging only;
                           dedupe uses the commit, which also catches
                           separate deliveries of the same commit)

    Returns:
        200 {"status": "ignored"} for events/actions not acted on,
        200 {"status": "duplicate"} if the commit was already queued,
        202 {"status": "queued", "job_id": ...} once the job is stored.

    Raises:
        HTTPException: 401 bad signature, 400 malformed payload,
        503 if the dedupe store or queue is unavailable
    """
    payload_body = await request.body()
    _verify_signature(payload_body, x_hub_signature_256)

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"unhandled event type: {x_github_event}"}

    payload = await request.json()
    if payload.get("action") not in {"opened", "synchronize"}:
        return {"status": "ignored", "reason": f"unhandled action: {payload.get('action')}"}

    pr_number, repo_full_name, head_sha = _extract_pr_fields(payload)
    dedupe = request.app.state.dedupe
    queue = request.app.state.job_queue
    dedupe_key = DedupeKey(repo_full_name, head_sha, target=f"pr:{pr_number}")

    # The Azure SDK is blocking, so run it off the event loop.
    try:
        is_new = await run_in_threadpool(dedupe.claim, dedupe_key)
    except Exception as exc:  # storage outage: fail the delivery so it can be redelivered
        logger.exception("Dedupe claim failed delivery=%s", x_github_delivery)
        raise HTTPException(status_code=503, detail="Dedupe store unavailable") from exc

    if not is_new:
        logger.info("Duplicate delivery=%s repo=%s pr=%s sha=%s", x_github_delivery, repo_full_name, pr_number, head_sha)
        return {"status": "duplicate", "pr_number": pr_number, "repo": repo_full_name}

    job = ReviewJob(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        head_sha=head_sha,
        delivery_id=x_github_delivery or "",
    )
    try:
        await run_in_threadpool(queue.enqueue, job)
    except Exception as exc:
        # Release the claim, otherwise the manual redelivery would be called a duplicate.
        logger.exception("Enqueue failed job_id=%s delivery=%s", job.job_id, x_github_delivery)
        await run_in_threadpool(dedupe.release, dedupe_key)
        raise HTTPException(status_code=503, detail="Review queue unavailable") from exc

    logger.info("Queued job_id=%s delivery=%s repo=%s pr=%s sha=%s", job.job_id, x_github_delivery, repo_full_name, pr_number, head_sha)
    return JSONResponse(
        status_code=202,
        content={"status": "queued", "job_id": job.job_id, "pr_number": pr_number, "repo": repo_full_name},
    )
