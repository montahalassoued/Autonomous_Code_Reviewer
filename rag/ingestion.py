import os
import git
from rag.ast_parser import parse_file, ParsedFunction
from rag.contextual_chunker import contextualize_functions, ContextualChunk
from rag.embedder import embed_chunks
from db.chroma import get_client, get_collection, store_chunks, make_chunk_id
from rag.knowledge_graph import build_knowledge_graph


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
    ignored_dirs = {"venv", ".venv", "env", "node_modules", ".git", "__pycache__"}
    python_files = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for filename in files:
            if filename.endswith(".py"):
                python_files.append(os.path.join(root, filename))

    return python_files


# Parses every Python file in the repo into ParsedFunction objects.
def _parse_repo(repo_path: str) -> list[ParsedFunction]:
    functions: list[ParsedFunction] = []

    for absolute_path in _find_python_files(repo_path):
        relative_path = os.path.relpath(absolute_path, repo_path).replace(os.sep, "/")
        with open(absolute_path, "r", encoding="utf-8") as f:
            source_code = f.read()

        parsed = parse_file(relative_path, source_code)
        functions.extend(parsed)
        print(f"  parsed {len(parsed)} function(s) from {relative_path}")

    return functions


# Removes chunks that are in the collection but not in this run.
def delete_stale_ids(collection, current_ids: list[str]) -> list[str]:
    # include=[] asks Chroma for ids only, not documents or embeddings
    stored_ids = collection.get(include=[])["ids"]
    current = set(current_ids)
    stale_ids = [chunk_id for chunk_id in stored_ids if chunk_id not in current]
    # Chroma rejects delete(ids=[]), so only call it when there's work
    if stale_ids:
        collection.delete(ids=stale_ids)
    return stale_ids


# Runs the full ingestion pipeline over a local repository checkout.
def ingest_repository(repo_path: str, persist_directory: str = "./chroma_data"):
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
