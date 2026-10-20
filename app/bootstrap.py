import os
import re

from app.jobs.trigger_policy import allowed_repos


CLONE_DIR = "./target_repo"
# https://github.com/owner/repo(.git) or git@github.com:owner/repo(.git)
GITHUB_REPO_URL = re.compile(r"github\.com[/:]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


# "owner/repo" from a GitHub clone URL, lowercased like ALLOWED_REPOS; None for another host.
def repo_full_name_from_url(url: str) -> str | None:
    match = GITHUB_REPO_URL.search(url.strip())
    return f"{match.group(1)}/{match.group(2)}".lower() if match else None


# One worker reads one checkout: an allowed repo other than TARGET_REPO_URL's would be reviewed with the wrong code.
def check_allowed_repos_match_checkout() -> None:
    repo_url = os.environ.get("TARGET_REPO_URL", "").strip()
    checkout = repo_full_name_from_url(repo_url) if repo_url else None
    if checkout is None:
        return  # REPO_PATH or a non-GitHub URL: the checkout's name is unknown
    others = sorted(allowed_repos() - {checkout})
    if others:
        raise RuntimeError(
            f"ALLOWED_REPOS lists {', '.join(others)}, but this worker's checkout is {checkout} "
            f"(TARGET_REPO_URL), so its tools would read the wrong code. Allow only {checkout}, "
            "or run one worker per repository."
        )


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


# Clones/locates the repo, builds its call graph, and compiles the review graph.
def build_review_graph():
    from review.graph import build_graph
    from rag.ingestion import ingest_repository

    check_allowed_repos_match_checkout()
    repo_path = resolve_repo_path()
    return build_graph(ingest_repository(repo_path), repo_path)
