import os


CLONE_DIR = "./target_repo"


# Local repo path: clone TARGET_REPO_URL if set, else use REPO_PATH.
def resolve_repo_path() -> str:
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


# Clones/locates the repo, ingests it, and compiles the review graph.
def build_review_graph():
    from review.graph import build_graph
    from rag.ingestion import ingest_repository

    repo_path = resolve_repo_path()
    collection, all_documents, all_ids, graph_index = ingest_repository(repo_path)
    return build_graph(collection, all_documents, all_ids, graph_index, repo_path)
