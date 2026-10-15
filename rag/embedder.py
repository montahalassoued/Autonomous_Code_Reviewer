import os
import voyageai
from rag.contextual_chunker import ContextualChunk


EMBEDDING_MODEL = "voyage-code-2"
EMBEDDING_DIMENSION = 1536  # voyage-code-2's native output size


# Reads VOYAGE_API_KEY from the environment.
def _build_client() -> voyageai.Client:
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "VOYAGE_API_KEY environment variable is not set. "
            "Get a free key (50M tokens) at https://www.voyageai.com "
            "and set it in your .env file."
        )
    return voyageai.Client(api_key=api_key)


# Embeds a batch of contextual chunks in a SINGLE API call.
def embed_chunks(chunks: list[ContextualChunk]) -> list[list[float]]:
    if not chunks:
        return []

    client = _build_client()
    texts = [chunk.embeddable_text for chunk in chunks]

    result = client.embed(
        texts,
        model=EMBEDDING_MODEL,
        input_type="document",  # "document" for indexing, "query" for searching later
    )

    return result.embeddings


# Embeds a single search query string.
def embed_query(query_text: str) -> list[float]:
    client = _build_client()
    result = client.embed(
        [query_text],
        model=EMBEDDING_MODEL,
        input_type="query",
    )
    return result.embeddings[0]