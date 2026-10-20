import hashlib
import hmac
import logging
import os

from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.jobs.models import DedupeKey, ReviewJob, pr_target
from app.jobs.trigger_policy import evaluate_pull_request


logger = logging.getLogger(__name__)

router = APIRouter()

# ready_for_review: a draft is skipped, so it is reviewed when it is marked ready.
HANDLED_ACTIONS = {"opened", "synchronize", "ready_for_review"}


# GITHUB_WEBHOOK_SECRET, or a clear error; the API calls it at startup so a missing secret never reaches a request.
def webhook_secret() -> str:
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError(
            "GITHUB_WEBHOOK_SECRET environment variable is not set. "
            "Set it in your .env file to match the secret configured "
            "on the GitHub webhook."
        )
    return secret


# Reject the request unless its HMAC signature proves it came from GitHub.
def _verify_signature(payload_body: bytes, signature_header: str | None) -> None:
    secret = webhook_secret()

    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")

    expected_signature = "sha256=" + hmac.new(
        key=secret.encode(),
        msg=payload_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


# (pr_number, repo, head_sha) from a pull_request payload; 400 if missing.
def _extract_pr_fields(payload: dict) -> tuple[int, str, str]:
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


# Verify, apply the trigger policy, dedupe and queue a review for pull_request events.
@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
):
    payload_body = await request.body()
    _verify_signature(payload_body, x_hub_signature_256)

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"unhandled event type: {x_github_event}"}

    payload = await request.json()
    if payload.get("action") not in HANDLED_ACTIONS:
        return {"status": "ignored", "reason": f"unhandled action: {payload.get('action')}"}

    pr_number, repo_full_name, head_sha = _extract_pr_fields(payload)

    # Before the dedupe, so a refused event leaves no reviews row and costs no LLM call.
    try:
        decision = await run_in_threadpool(evaluate_pull_request, payload, request.app.state.is_collaborator)
    except Exception as exc:  # GitHub unreachable: refuse for now, the delivery can be redelivered
        logger.exception("Collaborator check failed delivery=%s repo=%s", x_github_delivery, repo_full_name)
        raise HTTPException(status_code=503, detail="Could not verify the pull request author") from exc
    if not decision.allowed:
        logger.info("Ignored delivery=%s repo=%s pr=%s: %s", x_github_delivery, repo_full_name, pr_number, decision.reason)
        return {"status": "ignored", "reason": decision.reason}
    dedupe = request.app.state.dedupe
    queue = request.app.state.job_queue
    dedupe_key = DedupeKey(repo_full_name, head_sha, target=pr_target(pr_number))

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
