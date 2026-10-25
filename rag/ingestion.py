import base64
import os

import git
import networkx as nx

from rag.ast_parser import parse_file, ParsedFunction
from rag.file_classifier import SKIPPED_DIRS
from rag.code_indexer import build_index
from rag.knowledge_graph import build_resolved_graph


GITHUB_HTTPS = "https://github.com/"


# GITHUB_TOKEN as an HTTP header for github.com, passed in the environment: it never lands in the URL, .git/config,
# the process list or an error message. Empty for another host or without a token (a public repo needs none).
def git_auth_environment(repo_url: str) -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token or not repo_url.startswith(GITHUB_HTTPS):
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"http.{GITHUB_HTTPS}.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"}


# Clone a remote repo into target_dir; a private GitHub repo is read with GITHUB_TOKEN.
def clone_repository(repo_url: str, target_dir: str) -> str:
    auth = git_auth_environment(repo_url)
    if os.path.isdir(os.path.join(target_dir, ".git")):
        print(f"Existing checkout found at {target_dir}, pulling latest changes...")
        repo = git.Repo(target_dir)
        with repo.git.custom_environment(**auth):
            repo.remotes.origin.pull()
    else:
        print(f"Cloning {repo_url} into {target_dir}...")
        git.Repo.clone_from(repo_url, target_dir, depth=1, env={**os.environ, **auth})

    return target_dir


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
