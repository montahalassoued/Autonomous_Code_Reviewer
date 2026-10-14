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
