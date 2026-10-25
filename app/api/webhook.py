import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import JSONResponse
from prometheus_client import Counter
from starlette.concurrency import run_in_threadpool

from app.jobs.models import ReviewJob
from app.jobs.trigger_policy import evaluate_pull_request, evaluate_push


logger = logging.getLogger(__name__)

router = APIRouter()

WEBHOOK_DELIVERIES = Counter("reviewer_webhook_deliveries_total",
                             "GitHub deliveries: queued, duplicate, ignored, or http_<status> when refused", ["result"])

# ready_for_review: a draft is skipped, so it is reviewed when it is marked ready.
HANDLED_ACTIONS = {"opened", "synchronize", "ready_for_review"}
# GitHub caps deliveries at 25 MB, but a pull_request event is usually well under 1 MB.
DEFAULT_WEBHOOK_MAX_BODY_BYTES = 5 * 1024 * 1024


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


def webhook_max_body_bytes() -> int:
    try:
        value = int(os.environ.get("GITHUB_WEBHOOK_MAX_BODY_BYTES", "").strip() or DEFAULT_WEBHOOK_MAX_BODY_BYTES)
    except ValueError:
        return DEFAULT_WEBHOOK_MAX_BODY_BYTES
    return value if value > 0 else DEFAULT_WEBHOOK_MAX_BODY_BYTES


# The body, read in chunks and refused with 413 past the cap, so an unsigned flood can't fill memory.
async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    too_large = HTTPException(status_code=413, detail=f"Webhook payload larger than {max_bytes} bytes")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():  # a missing or false Content-Length is still capped here
        body.extend(chunk)
        if len(body) > max_bytes:
            raise too_large
    return bytes(body)


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


# Counts every delivery by how it ended: queued, duplicate, ignored, or the HTTP status that refused it.
@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
):
    try:
        response = await _handle_delivery(request, x_hub_signature_256, x_github_event, x_github_delivery)
    except HTTPException as exc:
        WEBHOOK_DELIVERIES.labels(f"http_{exc.status_code}").inc()
        raise
    WEBHOOK_DELIVERIES.labels("queued" if isinstance(response, JSONResponse) else response["status"]).inc()
    return response


# Verify, decide whether the event is reviewed (a pull request or a push), then dedupe and queue it.
async def _handle_delivery(request: Request, x_hub_signature_256: str | None, x_github_event: str | None,
                           x_github_delivery: str | None):
    payload_body = await _read_limited_body(request, webhook_max_body_bytes())
    _verify_signature(payload_body, x_hub_signature_256)

    if x_github_event not in ("pull_request", "push"):
        return {"status": "ignored", "reason": f"unhandled event type: {x_github_event}"}
    try:
        payload = json.loads(payload_body)
    except ValueError as exc:  # signed but not JSON: GitHub never sends that, so refuse it cleanly
        raise HTTPException(status_code=400, detail="Webhook payload is not valid JSON") from exc

    if x_github_event == "push":
        job_or_ignored = _push_job(payload, x_github_delivery or "")
    else:
        job_or_ignored = await _pull_request_job(request, payload, x_github_delivery or "")
    if isinstance(job_or_ignored, dict):
        return job_or_ignored
    return await _claim_and_queue(request, job_or_ignored)


# The installation of the GitHub App that sent the event; None when a repo webhook sent it (personal token).
def _installation_id(payload: dict) -> int | None:
    installation = payload.get("installation") or {}
    return installation.get("id")


async def _pull_request_job(request: Request, payload: dict, delivery_id: str) -> ReviewJob | dict:
    if payload.get("action") not in HANDLED_ACTIONS:
        return {"status": "ignored", "reason": f"unhandled action: {payload.get('action')}"}
    pr_number, repo_full_name, head_sha = _extract_pr_fields(payload)

    # Before the dedupe, so a refused event leaves no reviews row and costs no LLM call.
    try:
        decision = await run_in_threadpool(evaluate_pull_request, payload, request.app.state.is_collaborator)
    except Exception as exc:  # GitHub unreachable: refuse for now, the delivery can be redelivered
        logger.exception("Collaborator check failed delivery=%s repo=%s", delivery_id, repo_full_name)
        raise HTTPException(status_code=503, detail="Could not verify the pull request author") from exc
    if not decision.allowed:
        logger.info("Ignored delivery=%s repo=%s pr=%s: %s", delivery_id, repo_full_name, pr_number, decision.reason)
        return {"status": "ignored", "reason": decision.reason}
    return ReviewJob(repo_full_name=repo_full_name, pr_number=pr_number, head_sha=head_sha, delivery_id=delivery_id,
                     installation_id=_installation_id(payload))


# A push to a branch: the review covers before..after and is posted on the pushed commit.
def _push_job(payload: dict, delivery_id: str) -> ReviewJob | dict:
    try:
        repo_full_name, head_sha = payload["repository"]["full_name"], payload["after"]
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Malformed push payload, missing field: {exc}") from exc
    decision = evaluate_push(payload)
    if not decision.allowed:
        logger.info("Ignored push delivery=%s repo=%s ref=%s: %s", delivery_id, repo_full_name, payload.get("ref"), decision.reason)
        return {"status": "ignored", "reason": decision.reason}
    return ReviewJob(repo_full_name=repo_full_name, pr_number=0, head_sha=head_sha, delivery_id=delivery_id,
                     event="push", ref=payload["ref"], base_sha=payload.get("before", ""),
                     installation_id=_installation_id(payload))


def _where(job: ReviewJob) -> dict:
    return {"ref": job.ref} if job.is_push else {"pr_number": job.pr_number}


async def _claim_and_queue(request: Request, job: ReviewJob):
    dedupe = request.app.state.dedupe
    queue = request.app.state.job_queue
    dedupe_key = job.dedupe_key

    # The Azure SDK is blocking, so run it off the event loop.
    try:
        is_new = await run_in_threadpool(dedupe.claim, dedupe_key)
    except Exception as exc:  # storage outage: fail the delivery so it can be redelivered
        logger.exception("Dedupe claim failed delivery=%s", job.delivery_id)
        raise HTTPException(status_code=503, detail="Dedupe store unavailable") from exc

    if not is_new:
        logger.info("Duplicate delivery=%s repo=%s target=%s sha=%s", job.delivery_id, job.repo_full_name,
                    dedupe_key.target, job.head_sha)
        return {"status": "duplicate", **_where(job), "repo": job.repo_full_name}

    try:
        await run_in_threadpool(queue.enqueue, job)
    except Exception as exc:
        # Release the claim, otherwise the manual redelivery would be called a duplicate.
        logger.exception("Enqueue failed job_id=%s delivery=%s", job.job_id, job.delivery_id)
        await run_in_threadpool(dedupe.release, dedupe_key)
        raise HTTPException(status_code=503, detail="Review queue unavailable") from exc

    logger.info("Queued job_id=%s delivery=%s repo=%s target=%s sha=%s", job.job_id, job.delivery_id, job.repo_full_name,
                dedupe_key.target, job.head_sha)
    return JSONResponse(
        status_code=202,
        content={"status": "queued", "job_id": job.job_id, **_where(job), "repo": job.repo_full_name},
    )
