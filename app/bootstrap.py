"""
bootstrap.py - one-time startup work shared by the review worker.

Inputs: TARGET_REPO_URL (remote Git URL to clone) or REPO_PATH (local checkout).
Outputs: a compiled LangGraph bound to the ingested repo index.

Why a shared module: resolving the repo and building the graph used to live in
app/main.py. The API no longer ingests anything (it only enqueues), so this
logic now has one owner and one place to change. Heavy imports (chromadb,
tree-sitter, sentence-transformers) are inside the functions so importing
this module is cheap.
"""

import os


CLONE_DIR = "./target_repo"


def resolve_repo_path() -> str:
    """
    Determines the local path to ingest, cloning a remote repo first
    if TARGET_REPO_URL is set.

    Returns:
        a local filesystem path ready for ingest_repository()

    TARGET_REPO_URL takes priority: it's the real deployment path
    (e.g. on Azure Container Apps, where no checkout exists yet). REPO_PATH is the
    fallback for local development against a repo you've already
    cloned yourself.
    """
    from rag.ingestion import clone_repository

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


def build_review_graph():
    """
    Clones/locates the repo, ingests it, and compiles the review graph.

    Returns:
        the compiled LangGraph from review/graph.py's build_graph()
    """
    from review.graph import build_graph
    from rag.ingestion import ingest_repository

    repo_path = resolve_repo_path()
    collection, all_documents, all_ids, graph_index = ingest_repository(repo_path)
    return build_graph(collection, all_documents, all_ids, graph_index)
