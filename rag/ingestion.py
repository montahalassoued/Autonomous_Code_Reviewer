import os

import git
import networkx as nx

from rag.ast_parser import parse_file, ParsedFunction
from rag.file_classifier import SKIPPED_DIRS
from rag.code_indexer import build_index
from rag.knowledge_graph import build_resolved_graph


# Clone a remote repo into target_dir.
def clone_repository(repo_url: str, target_dir: str) -> str:
    if os.path.isdir(os.path.join(target_dir, ".git")):
        print(f"Existing checkout found at {target_dir}, pulling latest changes...")
        repo = git.Repo(target_dir)
        repo.remotes.origin.pull()
    else:
        print(f"Cloning {repo_url} into {target_dir}...")
        git.Repo.clone_from(repo_url, target_dir, depth=1)

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
