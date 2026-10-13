"""
ingestion.py - runs the full ingestion pipeline over a repository:
clone (if given a URL) -> parse -> contextualize -> embed -> store ->
delete stale ids -> build graph.

Inputs: either a local filesystem path to an existing repository
checkout, or a remote Git URL to clone first (clone_repository()).
Outputs: a populated Chroma collection, the parallel (all_documents,
all_ids) lists hybrid_search.py's BM25 index needs, and the NetworkX
knowledge graph - exactly the four values agents/graph.py's
build_graph() needs to wire up the real retriever node.

This is the one-time (or per-deploy-startup) batch job that makes the
rest of the RAG pipeline possible: agents/retriever.py assumes a
Chroma collection is already populated and a graph is already built -
this module is what populates them.
"""

import os
import git
from rag.ast_parser import parse_file, ParsedFunction
from rag.contextual_chunker import contextualize_functions, ContextualChunk
from rag.embedder import embed_chunks
from db.chroma import get_client, get_collection, store_chunks, make_chunk_id
from rag.knowledge_graph import build_knowledge_graph


def clone_repository(repo_url: str, target_dir: str) -> str:
    """
    Clones a remote Git repository to target_dir, so ingestion can run
    against a repo that only exists as a URL (e.g. on app/main.py's
    deployed server, which has no pre-existing local checkout of
    whatever repository is being reviewed).

    Args:
        repo_url: an HTTPS Git URL, e.g. "https://github.com/owner/repo.git"
        target_dir: where to clone it to

    Returns:
        target_dir, for convenient chaining into ingest_repository()

    A shallow clone (depth=1) is used since ingestion only needs the
    current state of the files, not history - this keeps startup fast
    and avoids downloading a repo's entire commit history.

    If target_dir already contains a checkout, this pulls the latest
    changes instead of re-cloning from scratch, so repeated app
    restarts don't redundantly re-download the whole repo every time.
    """
    if os.path.isdir(os.path.join(target_dir, ".git")):
        print(f"Existing checkout found at {target_dir}, pulling latest changes...")
        repo = git.Repo(target_dir)
        repo.remotes.origin.pull()
    else:
        print(f"Cloning {repo_url} into {target_dir}...")
        git.Repo.clone_from(repo_url, target_dir, depth=1)

    return target_dir


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


def delete_stale_ids(collection, current_ids: list[str]) -> list[str]:
    """
    Removes chunks that are in the collection but not in this run.

    The Chroma collection persists on disk across restarts, and upsert
    only adds or overwrites. Without this, deleted or renamed
    functions (and ids in an older format, like "file::name" before
    methods were class-qualified) would stay searchable forever and
    reach the agents as context that no longer exists.

    Args:
        collection: the Chroma collection that was just upserted into
        current_ids: every chunk id produced by this ingestion run

    Returns:
        the ids that were deleted (empty if nothing was stale)
    """
    # include=[] asks Chroma for ids only, not documents or embeddings
    stored_ids = collection.get(include=[])["ids"]
    current = set(current_ids)
    stale_ids = [chunk_id for chunk_id in stored_ids if chunk_id not in current]
    # Chroma rejects delete(ids=[]), so only call it when there's work
    if stale_ids:
        collection.delete(ids=stale_ids)
    return stale_ids


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

    all_documents = [chunk.embeddable_text for chunk in chunks]
    all_ids = [make_chunk_id(chunk) for chunk in chunks]

    stale_ids = delete_stale_ids(collection, all_ids)
    print(f"Removed {len(stale_ids)} stale chunk(s) from ChromaDB.")

    print("Building knowledge graph...")
    graph = build_knowledge_graph(functions)

    print(f"Ingestion complete: {len(all_ids)} chunks indexed, "
          f"{graph.number_of_nodes()} graph nodes, {graph.number_of_edges()} graph edges.")

    return collection, all_documents, all_ids, graph
