"""
contextual_chunker.py - enriches each parsed function with LLM-generated
context before it gets embedded.

This implements Anthropic's "Contextual Retrieval" technique: a bare code
chunk (just the function body) often loses meaning when isolated from its
file and surrounding codebase - two functions named `get()` in different
classes can look nearly identical to a vector embedding. By prepending a
short, LLM-generated description of what the function does and where it
lives, the resulting embedding captures MEANING, not just syntax.

Why Gemini Flash specifically: this step runs once per function in the
ENTIRE repo during ingestion - potentially hundreds of calls in one run.
We want a fast, free, "good enough" model here, keeping the more
expensive/precious Groq calls for the actual agent analysis later.
"""

import os
from dataclasses import dataclass
from google import genai
from google.genai.errors import ServerError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from rag.ast_parser import ParsedFunction


CONTEXT_PROMPT_TEMPLATE = """You are documenting a codebase for a search system.
Given a function, write ONE short sentence (max 30 words) describing:
- what this function does
- which file it belongs to
- what it calls, if relevant for understanding its role

Be factual and concise. Do not repeat the code. Output ONLY the sentence,
nothing else - no markdown, no quotes, no preamble.

File: {file_path}
Function calls made inside it: {calls}

Code:
{code}

Context sentence:"""


@dataclass
class ContextualChunk:
    """
    A ParsedFunction enriched with an LLM-generated context sentence,
    ready to be embedded in Step 6.
    """
    function: ParsedFunction
    context: str

    @property
    def embeddable_text(self) -> str:
        """
        The actual text that will be sent to the embedding model:
        context sentence prepended to the raw code, exactly as
        Anthropic's technique prescribes.
        """
        return f"{self.context}\n\n{self.function.code}"


def _build_client() -> genai.Client:
    """
    Reads GOOGLE_API_KEY from the environment. Raises a clear error
    early if it's missing, instead of failing deep inside an API call
    with a confusing message.
    """
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is not set. "
            "Get a free key at https://aistudio.google.com and set it "
            "in your .env file."
        )
    return genai.Client(api_key=api_key)


@retry(
    retry=retry_if_exception_type(ServerError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def generate_context(client: genai.Client, function: ParsedFunction) -> str:
    """
    Calls Gemini Flash to generate a one-sentence context for a single
    function. This is the core LLM call of this module.

    Retries on ServerError (5xx - transient outages/overload on
    Google's side) with exponential backoff, up to 4 attempts total.
    Does NOT retry on 4xx client errors (bad API key, invalid request)
    since those won't resolve themselves on retry. Ingestion runs one
    of these calls per function in a repo - without this, a single
    transient 503 kills the entire run.

    Args:
        client: an initialized genai.Client
        function: a ParsedFunction from rag/ast_parser.py

    Returns:
        a short context sentence (stripped of whitespace/quotes)
    """
    prompt = CONTEXT_PROMPT_TEMPLATE.format(
        file_path=function.file_path,
        calls=", ".join(function.calls) if function.calls else "none",
        code=function.code,
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
    )

    # Defensive stripping: LLMs sometimes wrap output in quotes or
    # markdown even when explicitly told not to.
    return response.text.strip().strip('"').strip("'")


def contextualize_functions(
    functions: list[ParsedFunction],
) -> list[ContextualChunk]:
    """
    Runs generate_context on every function in the list.

    This is the function that ingestion.py (Step 7+) will call after
    ast_parser.py has produced the raw functions for a whole repo.

    Note: this makes one LLM call PER function, sequentially. For a
    large repo this could be slow - parallelizing this with asyncio is
    a reasonable future optimization, noted in docs/DECISIONS.md.
    """
    client = _build_client()
    results: list[ContextualChunk] = []

    for fn in functions:
        context = generate_context(client, fn)
        results.append(ContextualChunk(function=fn, context=context))
        print(f"  contextualized {fn.qualified_name} ({fn.file_path}:{fn.start_line})")

    return results