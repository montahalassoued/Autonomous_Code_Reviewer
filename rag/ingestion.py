"""
ingestion.py - runs the full ingestion pipeline over a local repository
checkout: parse -> contextualize -> embed -> store -> build graph.

Inputs: a local filesystem path to a repository checkout (already
cloned; cloning itself is out of scope here - gitpython is available
for that if a caller needs it before calling into this module).
Outputs: a populated Chroma collection, the parallel (all_documents,
all_ids) lists hybrid_search.py's BM25 index needs, and the NetworkX
knowledge graph - exactly the four values agents/graph.py's
build_graph() needs to wire up the real retriever node.

This is the one-time (or per-webhook-startup) batch job that makes the
rest of the RAG pipeline possible: agents/retriever.py assumes a
Chroma collection is already populated and a graph is already built -
this module is what populates them.
"""

import os
from rag.ast_parser import parse_file, ParsedFunction
from rag.contextual_chunker import contextualize_functions, ContextualChunk
from rag.embedder import embed_chunks
from db.chroma import get_client, get_collection, store_chunks
from rag.knowledge_graph import build_knowledge_graph


def _find_python_files(repo_path: str) -> list[str]:
    """
    Walks a repo checkout and collects every .py file's path.

    Args:
        repo_path: the root directory of the repository checkout

    Returns:
        a list of absolute file paths, skipping virtual environments
        and version control internals - these directories can contain
        thousands of vendored/third-party files that would otherwise
        dominate ingestion time and pollute the index with code that
        isn't actually part of the reviewed project.
    """
    ignored_dirs = {"venv", ".venv", "env", "node_modules", ".git", "__pycache__"}
    python_files = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for filename in files:
            if filename.endswith(".py"):
                python_files.append(os.path.join(root, filename))

    return python_files


def _parse_repo(repo_path: str) -> list[ParsedFunction]:
    """
    Parses every Python file in the repo into ParsedFunction objects.

    Args:
        repo_path: the root directory of the repository checkout

    Returns:
        every function found across every file, with file_path stored
        relative to repo_path so ids/metadata match what a GitHub diff
        would reference (e.g. "auth/utils.py", not an absolute path)
    """
    functions: list[ParsedFunction] = []

    for absolute_path in _find_python_files(repo_path):
        relative_path = os.path.relpath(absolute_path, repo_path).replace(os.sep, "/")
        with open(absolute_path, "r", encoding="utf-8") as f:
            source_code = f.read()

        parsed = parse_file(relative_path, source_code)
        functions.extend(parsed)
        print(f"  parsed {len(parsed)} function(s) from {relative_path}")

    return functions


def ingest_repository(repo_path: str, persist_directory: str = "./chroma_data"):
    """
    Runs the full ingestion pipeline over a local repository checkout.

    Args:
        repo_path: the root directory of an already-cloned repository
        persist_directory: where Chroma persists its data on disk
                            (db/chroma.py's get_client default)

    Returns:
        (collection, all_documents, all_ids, graph) - exactly the four
        values agents/graph.py's build_graph() needs:
          - collection: the populated Chroma collection
          - all_documents: chunk texts, for hybrid_search.py's BM25 index
          - all_ids: chunk ids, same order as all_documents
          - graph: the NetworkX knowledge graph of function calls
    """
    print(f"Parsing repository at {repo_path}...")
    functions = _parse_repo(repo_path)
    print(f"Parsed {len(functions)} function(s) total.")

    print("Contextualizing functions (Gemini)...")
    chunks: list[ContextualChunk] = contextualize_functions(functions)

    print("Embedding chunks (Voyage AI)...")
    embeddings = embed_chunks(chunks)

    print("Storing chunks in ChromaDB...")
    client = get_client(persist_directory=persist_directory)
    collection = get_collection(client)
    store_chunks(collection, chunks, embeddings)

    print("Building knowledge graph...")
    graph = build_knowledge_graph(functions)

    all_documents = [chunk.embeddable_text for chunk in chunks]
    all_ids = [f"{chunk.function.file_path}::{chunk.function.name}" for chunk in chunks]

    print(f"Ingestion complete: {len(all_ids)} chunks indexed, "
          f"{graph.number_of_nodes()} graph nodes, {graph.number_of_edges()} graph edges.")

    return collection, all_documents, all_ids, graph
