"""
chroma.py - stores and retrieves embedded chunks using ChromaDB.

This is the local development vector store. In production, the same
role is played by Qdrant Cloud (db/qdrant.py, written later) - both
modules expose a similar shape so rag/hybrid_search.py can swap
between them without caring which one is actually running.

ChromaDB can generate its own embeddings via a built-in
embedding_function, but we explicitly do NOT use that here: our
vectors already come from Voyage AI (rag/embedder.py), which is
code-specific and chosen deliberately. We pass our own embeddings in
directly and disable Chroma's default behavior.
"""

import chromadb
from rag.contextual_chunker import ContextualChunk


COLLECTION_NAME = "code_chunks"


def get_client(persist_directory: str = "./chroma_data") -> chromadb.ClientAPI:
    """
    Returns a persistent ChromaDB client - data survives between runs,
    written to disk at persist_directory. This matters because
    ingestion (Step 3-6) runs once, and the review pipeline (Step 8+)
    needs to read this data back later, potentially in a completely
    separate process.
    """
    return chromadb.PersistentClient(path=persist_directory)


def get_collection(client: chromadb.ClientAPI):
    """
    Gets or creates the collection where all chunks live.

    embedding_function=None tells Chroma "do not compute embeddings
    yourself - I will always provide pre-computed vectors". Without
    this, Chroma would silently use its own default model whenever we
    forget to pass embeddings explicitly, producing vectors that are
    NOT compatible with Voyage's vector space.
    """
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
    )


def make_chunk_id(chunk: ContextualChunk) -> str:
    """
    Builds the stable id of a chunk: "file_path::qualified_name".

    This is the same id rag/knowledge_graph.py uses for graph nodes,
    so a search hit can be looked up in the graph directly. It is
    defined once here so ingestion and storage can't drift apart.

    Args:
        chunk: a ContextualChunk from rag/contextual_chunker.py

    Returns:
        the chunk id, e.g. "auth/service.py::AuthService.login"
    """
    return f"{chunk.function.file_path}::{chunk.function.qualified_name}"


def store_chunks(
    collection,
    chunks: list[ContextualChunk],
    embeddings: list[list[float]],
) -> None:
    """
    Stores a batch of chunks together with their pre-computed
    embeddings and metadata.

    Args:
        collection: a Chroma collection from get_collection()
        chunks: ContextualChunk objects (same order as embeddings)
        embeddings: vectors from rag/embedder.py's embed_chunks()
                    (same order as chunks - this pairing is the
                    caller's responsibility, enforced here by zip())

    Each chunk's metadata (file_path, function_name, qualified_name,
    line numbers, context, code) is stored alongside its vector, so a
    search result can be turned directly into a RetrievedChunk
    (agents/state.py) without a second lookup anywhere else. The code
    is stored in metadata (not only in `documents`) because every
    downstream consumer (graph_retriever, reranker, retriever) reads
    chunk metadata, and documents also carry the context sentence.
    """
    if not chunks:
        return

    ids = [make_chunk_id(chunk) for chunk in chunks]
    documents = [chunk.embeddable_text for chunk in chunks]
    metadatas = [
        {
            "file_path": chunk.function.file_path,
            "function_name": chunk.function.name,
            "qualified_name": chunk.function.qualified_name,
            "start_line": chunk.function.start_line,
            "end_line": chunk.function.end_line,
            "context": chunk.context,
            "code": chunk.function.code,
            # ChromaDB metadata values must be str/int/float/bool, not
            # lists - we join calls into a comma-separated string and
            # split it back when reading results out.
            "calls": ",".join(chunk.function.calls),
        }
        for chunk in chunks
    ]

    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )


def query_collection(collection, query_embedding: list[float], top_k: int = 10) -> dict:
    """
    Runs a dense similarity search: finds the top_k chunks whose
    embeddings are closest to query_embedding.

    Returns Chroma's raw result dict - rag/hybrid_search.py (Step 7)
    will be responsible for combining this with BM25 results, so we
    keep this function a thin, unopinionated wrapper rather than
    reshaping the output here.
    """
    return collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
    )