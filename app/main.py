from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhook import router as webhook_router
from app.jobs.factory import build_dedupe, build_queue
from tools.github_tools import is_collaborator


# Creates the queue, the dedupe store and the collaborator check once and stores them on app.state.
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.job_queue = build_queue()
    app.state.dedupe = build_dedupe()
    app.state.is_collaborator = is_collaborator

    yield

    app.state.dedupe.close()


app = FastAPI(title="Autonomous Code Reviewer", lifespan=lifespan)
app.include_router(webhook_router)


# Liveness/readiness check.
@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
