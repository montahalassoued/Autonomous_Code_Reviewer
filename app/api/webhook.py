"""
webhook.py - GitHub webhook route handler.

Inputs: GitHub webhook HTTP requests (pull_request event, "opened" or
"synchronize" action), including the raw request body and the
X-Hub-Signature-256 header GitHub signs every payload with.
Outputs: a 200 response once the payload is validated and the review
pipeline has been scheduled to run in the background; triggers
agents/graph.py's compiled LangGraph asynchronously via
tools/github_tools.py for the diff fetch and the final comment post.

The compiled graph itself is not built here - app/main.py owns
ingestion/startup and stores the compiled graph on `request.app.state`,
since building it requires the Chroma collection, BM25 corpus, and
knowledge graph, none of which this module should know how to
construct.
"""

import hashlib
import hmac
import os

from fastapi import APIRouter, Request, Header, HTTPException, BackgroundTasks

from tools.github_tools import get_pr_diff, post_review_comment


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


def _extract_pr_fields(payload: dict) -> tuple[int, str]:
    """
    Pulls the fields the review pipeline needs out of a GitHub
    pull_request webhook payload.

    Args:
        payload: the parsed JSON webhook body

    Returns:
        (pr_number, repo_full_name) - the list of changed files is NOT
        in the webhook payload itself; it's fetched separately via the
        GitHub API inside get_pr_diff()

    Raises:
        HTTPException: 400 if required fields are missing, so a
        malformed or unexpected payload doesn't crash the server
    """
    try:
        pr_number = payload["pull_request"]["number"]
        repo_full_name = payload["repository"]["full_name"]
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Malformed webhook payload, missing field: {exc}",
        ) from exc

    return pr_number, repo_full_name


def _run_review_pipeline(compiled_graph, repo_full_name: str, pr_number: int) -> None:
    """
    Runs the full review pipeline for one PR and posts the result back
    to GitHub as a comment. Runs as a FastAPI BackgroundTask so the
    webhook HTTP response returns immediately - GitHub expects a fast
    ack and will retry deliveries that time out.

    Args:
        compiled_graph: the LangGraph graph from agents/graph.py's
                        build_graph(), already bound to the RAG
                        dependencies via closure
        repo_full_name: the repository in "owner/repo" form
        pr_number: the pull request number
    """
    diff = get_pr_diff(repo_full_name, pr_number)

    initial_state = {
        "pr_number": pr_number,
        "repo_full_name": repo_full_name,
        "pr_diff": diff,
        "changed_files": [],
        "retrieved_context": [],
        "retrieval_quality_score": 0.0,
        "retrieval_attempts": 0,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "retriever",
    }

    final_state = compiled_graph.invoke(initial_state)
    post_review_comment(repo_full_name, pr_number, final_state["final_review_markdown"])


@router.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict:
    """
    Receives a GitHub webhook delivery, validates it, and schedules a
    review run for "opened" or "synchronize" pull_request events.

    Args:
        request: the raw FastAPI request, used to read the exact body
                 bytes needed for signature verification
        background_tasks: FastAPI's background task runner, used to
                           run the (slow, multi-LLM-call) review
                           pipeline after this handler returns
        x_hub_signature_256: GitHub's HMAC signature header
        x_github_event: GitHub's event-type header (e.g. "pull_request")

    Returns:
        a small JSON status dict - "ignored" for event types/actions
        this endpoint doesn't act on, "scheduled" once the review has
        been queued as a background task
    """
    payload_body = await request.body()
    _verify_signature(payload_body, x_hub_signature_256)

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"unhandled event type: {x_github_event}"}

    payload = await request.json()
    if payload.get("action") not in {"opened", "synchronize"}:
        return {"status": "ignored", "reason": f"unhandled action: {payload.get('action')}"}

    pr_number, repo_full_name = _extract_pr_fields(payload)

    compiled_graph = request.app.state.compiled_graph
    background_tasks.add_task(_run_review_pipeline, compiled_graph, repo_full_name, pr_number)

    return {"status": "scheduled", "pr_number": pr_number, "repo": repo_full_name}
