"""
embedder.py - converts ContextualChunk text into vectors using Voyage AI.

This module does ONE thing: text in, vector out. It knows nothing about
storage (that's db/chroma.py) and nothing about how the text was built
(that's rag/contextual_chunker.py). If we ever switch embedding
providers, only this file changes.

Why voyage-code-2 specifically: it is trained on code (function names,
identifiers, algorithmic patterns), unlike general-purpose text
embedding models that are trained on natural language prose. Two
functions doing similar things but using different variable names
embed much closer together with a code-specific model.
"""

import os
import voyageai
from rag.contextual_chunker import ContextualChunk


EMBEDDING_MODEL = "voyage-code-2"
EMBEDDING_DIMENSION = 1536  # voyage-code-2's native output size


def _build_client() -> voyageai.Client:
    """
    Reads VOYAGE_API_KEY from the environment. Same defensive pattern
    as contextual_chunker.py's _build_client: fail early with a clear
    message instead of a cryptic error deep inside the API call.
    """
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "VOYAGE_API_KEY environment variable is not set. "
            "Get a free key (50M tokens) at https://www.voyageai.com "
            "and set it in your .env file."
        )
    return voyageai.Client(api_key=api_key)


def embed_chunks(chunks: list[ContextualChunk]) -> list[list[float]]:
    """
    Embeds a batch of contextual chunks in a SINGLE API call.

    Why batch instead of one call per chunk: Voyage's embed() endpoint
    accepts a list of texts natively, and batching is both faster
    (one network round-trip instead of N) and friendlier to API rate
    limits than embedding chunks one at a time.

    Args:
        chunks: ContextualChunk objects from rag/contextual_chunker.py

    Returns:
        a list of embedding vectors, in the SAME ORDER as the input
        chunks - caller is responsible for keeping chunk and vector
        paired together (db/chroma.py does this via zip()).
    """
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


def embed_query(query_text: str) -> list[float]:
    """
    Embeds a single search query string.

    Separate from embed_chunks because Voyage's API distinguishes
    input_type="document" (what we're indexing) from input_type="query"
    (what we're searching with) - using the right one improves
    retrieval quality, since the model was trained knowing which side
    of a search a given text represents.
    """
    client = _build_client()
    result = client.embed(
        [query_text],
        model=EMBEDDING_MODEL,
        input_type="query",
    )
    return result.embeddings[0]