"""
main.py - FastAPI application entrypoint (the webhook receiver).

Inputs: environment variables for the job backend - JOBS_BACKEND
("memory" or "azure") and the Azure Storage settings read by
app/jobs/factory.py - plus GITHUB_WEBHOOK_SECRET for signature checks.
Outputs: a FastAPI app instance with the webhook router mounted and a
GET /health endpoint, ready to run via `uvicorn app.main:app`.

Startup only creates the job queue and the dedupe store and puts them on
app.state. It does NOT clone or ingest the repo and does not build the
graph: that heavy work moved to the review worker (app/worker.py), so
the API starts in seconds and /health is "ok" immediately.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhook import router as webhook_router
from app.jobs.factory import build_dedupe, build_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Creates the queue and dedupe store once and stores them on app.state."""
    app.state.job_queue = build_queue()
    app.state.dedupe = build_dedupe()

    yield


app = FastAPI(title="Autonomous Code Reviewer", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/health")
async def health() -> dict:
    """
    Liveness/readiness check.

    Returns:
        {"status": "ok"} - the API has no slow startup anymore, so it
        is ready as soon as it answers.
    """
    return {"status": "ok"}
