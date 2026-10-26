import base64
import os
import re

import git
import networkx as nx

from rag.ast_parser import parse_file, ParsedFunction
from rag.file_classifier import SKIPPED_DIRS
from rag.code_indexer import build_index
from rag.knowledge_graph import build_resolved_graph


GITHUB_HTTPS = "https://github.com/"
REPO_FULL_NAME = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")


# A GitHub token as an HTTP header for github.com, passed in the environment: it never lands in the URL, .git/config,
# the process list or an error message. Empty for another host or without a token (a public repo needs none).
def git_auth_environment(repo_url: str, token: str | None = None) -> dict[str, str]:
    token = (token if token is not None else os.environ.get("GITHUB_TOKEN", "")).strip()
    if not token or not repo_url.startswith(GITHUB_HTTPS):
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"http.{GITHUB_HTTPS}.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"}


# owner/repo at one commit under checkouts_root/owner/repo/sha: only that commit is fetched, and each commit has its own
# folder, so a cached review of an older commit never reads a newer one's files. The working tree is the reviewed code.
# Both come from a webhook: a name like ../.. would leave checkouts_root, a "sha" starting with - would be a git option.
def checkout_commit(repo_full_name: str, sha: str, checkouts_root: str, token: str | None = None,
                    remote_url: str | None = None) -> str:
    if not REPO_FULL_NAME.fullmatch(repo_full_name) or ".." in repo_full_name:
        raise ValueError(f"not a GitHub owner/repo name: {repo_full_name!r}")
    if not COMMIT_SHA.fullmatch(sha):
        raise ValueError(f"not a commit sha: {sha!r}")
    owner, name = repo_full_name.split("/", 1)
    path = os.path.join(checkouts_root, owner, name, sha)
    repo_url = remote_url or f"{GITHUB_HTTPS}{repo_full_name}.git"
    if os.path.isdir(os.path.join(path, ".git")):
        repo = git.Repo(path)
    else:
        repo = git.Repo.init(path)
        repo.create_remote("origin", repo_url)
    with repo.git.custom_environment(**git_auth_environment(repo_url, token)):
        repo.git.fetch("--depth", "1", "--no-tags", "origin", sha)
    repo.git.checkout("--force", "--detach", sha)
    return path


# Walks a repo checkout and collects every .py file's path.
def _find_python_files(repo_path: str) -> list[str]:
    python_files = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
        for filename in files:
            if filename.endswith(".py"):
                python_files.append(os.path.join(root, filename))

    return python_files


# Parses every Python file in the repo into ParsedFunction objects.
def _parse_repo(repo_path: str) -> list[ParsedFunction]:
    functions: list[ParsedFunction] = []

    for absolute_path in _find_python_files(repo_path):
        relative_path = os.path.relpath(absolute_path, repo_path).replace(os.sep, "/")
        # One file in another encoding must not stop the worker: bad bytes become U+FFFD.
        with open(absolute_path, "r", encoding="utf-8", errors="replace") as f:
            source_code = f.read()

        parsed = parse_file(relative_path, source_code)
        functions.extend(parsed)
        print(f"  parsed {len(parsed)} function(s) from {relative_path}")

    return functions


# Parses the checkout and builds the call graph the agents' graph tools read; no LLM or embedding call.
def ingest_repository(repo_path: str) -> nx.DiGraph:
    print(f"Parsing repository at {repo_path}...")
    functions = _parse_repo(repo_path)
    graph = build_resolved_graph(functions, build_index(repo_path).resolution.edges)
    print(f"Ingestion complete: {len(functions)} function(s), "
          f"{graph.number_of_nodes()} graph nodes, {graph.number_of_edges()} graph edges.")
    return graph
