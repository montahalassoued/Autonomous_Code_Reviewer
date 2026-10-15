import chromadb
from rag.contextual_chunker import ContextualChunk


COLLECTION_NAME = "code_chunks"


# Persistent ChromaDB client stored on disk.
def get_client(persist_directory: str = "./chroma_data") -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=persist_directory)


# Gets or creates the collection where all chunks live.
def get_collection(client: chromadb.ClientAPI):
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
    )


# Builds the stable id of a chunk: "file_path::qualified_name".
def make_chunk_id(chunk: ContextualChunk) -> str:
    return f"{chunk.function.file_path}::{chunk.function.qualified_name}"


# Stores a batch of chunks together with their pre-computed embeddings and metadata.
def store_chunks(
    collection,
    chunks: list[ContextualChunk],
    embeddings: list[list[float]],
) -> None:
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


# Top_k chunks closest to query_embedding.
def query_collection(collection, query_embedding: list[float], top_k: int = 10) -> dict:
    return collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
    )