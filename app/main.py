"""
main.py - FastAPI application entrypoint.

Inputs: environment variables - either TARGET_REPO_URL (a remote Git
URL to clone, e.g. for a deployed server with no pre-existing
checkout) or REPO_PATH (a local checkout already on disk, e.g. for
local development), plus everything the RAG pipeline and
Groq/Voyage/Gemini clients need - see the other modules'
_build_*_client() functions.
Outputs: a FastAPI app instance with the webhook router mounted and a
GET /health endpoint, ready to run via `uvicorn app.main:app`.

Startup does the expensive one-time work the review pipeline depends
on: obtaining a checkout of the target repo, ingesting it into a
Chroma collection + BM25 corpus + knowledge graph (rag/ingestion.py),
then compiling the LangGraph (agents/graph.py) bound to that data and
storing it on app.state so app/api/webhook.py can invoke it per
incoming PR.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhook import router as webhook_router
from rag.ingestion import ingest_repository, clone_repository
from agents.graph import build_graph


CLONE_DIR = "./target_repo"


def _resolve_repo_path() -> str:
    """
    Determines the local path to ingest, cloning a remote repo first
    if TARGET_REPO_URL is set.

    Returns:
        a local filesystem path ready for ingest_repository()

    TARGET_REPO_URL takes priority: it's the real deployment path
    (e.g. on Render, where no checkout exists yet). REPO_PATH is the
    fallback for local development against a repo you've already
    cloned yourself.
    """
    repo_url = os.environ.get("TARGET_REPO_URL")
    if repo_url:
        return clone_repository(repo_url, CLONE_DIR)

    repo_path = os.environ.get("REPO_PATH")
    if repo_path:
        return repo_path

    raise RuntimeError(
        "Neither TARGET_REPO_URL nor REPO_PATH is set. Set one in "
        "your .env file: TARGET_REPO_URL for a remote repo to clone "
        "(e.g. on a deployed server), or REPO_PATH for a repo already "
        "checked out locally."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs ingestion once at startup and compiles the review graph,
    storing it on app.state so route handlers can reach it without
    reaching into globals.
    """
    repo_path = _resolve_repo_path()
    collection, all_documents, all_ids, graph_index = ingest_repository(repo_path)
    app.state.compiled_graph = build_graph(collection, all_documents, all_ids, graph_index)

    yield


app = FastAPI(title="Autonomous Code Reviewer", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/health")
async def health() -> dict:
    """
    Liveness/readiness check.

    Returns:
        a small JSON status dict - "ok" once ingestion has finished
        and the compiled graph is ready, "starting" while the app is
        still ingesting the repo. Distinguishing these matters because
        webhook deliveries that arrive before ingestion finishes would
        otherwise hit an AttributeError on app.state.compiled_graph.
    """
    graph_ready = hasattr(app.state, "compiled_graph")
    return {"status": "ok" if graph_ready else "starting"}
