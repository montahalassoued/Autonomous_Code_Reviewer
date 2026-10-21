from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhook import router as webhook_router, webhook_secret
from app.jobs.factory import build_dedupe, build_queue
from tools.github_tools import is_collaborator


# Checks the webhook secret, then creates the queue, the dedupe store and the collaborator check once on app.state.
@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_secret()
    app.state.job_queue = build_queue()
    app.state.dedupe = build_dedupe()
    app.state.is_collaborator = is_collaborator

    yield

    app.state.dedupe.close()


# No /docs, /redoc or /openapi.json: GitHub is the only client, so the API's shape stays private.
app = FastAPI(title="Autonomous Code Reviewer", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(webhook_router)


# Liveness/readiness check.
@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
