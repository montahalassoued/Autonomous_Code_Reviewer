"""
main.py - FastAPI application entrypoint.

Inputs: environment variables (REPO_PATH pointing at a local checkout
to index, plus everything the RAG pipeline and Groq/Voyage/Gemini
clients need - see the other modules' _build_*_client() functions).
Outputs: a FastAPI app instance with the webhook router mounted and a
GET /health endpoint, ready to run via `uvicorn app.main:app`.

Startup does the expensive one-time work the review pipeline depends
on: ingesting REPO_PATH into a Chroma collection + BM25 corpus +
knowledge graph (rag/ingestion.py), then compiling the LangGraph
(agents/graph.py) bound to that data and storing it on app.state so
app/api/webhook.py can invoke it per incoming PR.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhook import router as webhook_router
from rag.ingestion import ingest_repository
from agents.graph import build_graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs ingestion once at startup and compiles the review graph,
    storing it on app.state so route handlers can reach it without
    reaching into globals.

    REPO_PATH must point at a local checkout of the repository this
    service will review PRs for - cloning it there is out of scope for
    this module (a deploy step, or a separate ingestion trigger, is
    responsible for making sure the checkout exists and is up to date
    before the app starts).
    """
    repo_path = os.environ.get("REPO_PATH")
    if not repo_path:
        raise RuntimeError(
            "REPO_PATH environment variable is not set. "
            "Set it to a local checkout of the repository to index "
            "and review, in your .env file."
        )

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
