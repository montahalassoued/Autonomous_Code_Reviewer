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


# A ParsedFunction plus its LLM-written context sentence.
@dataclass
class ContextualChunk:
    function: ParsedFunction
    context: str

    # Text sent to the embedder: context sentence + code.
    @property
    def embeddable_text(self) -> str:
        return f"{self.context}\n\n{self.function.code}"


# Reads GOOGLE_API_KEY from the environment.
def _build_client() -> genai.Client:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is not set. "
            "Get a free key at https://aistudio.google.com and set it "
            "in your .env file."
        )
    return genai.Client(api_key=api_key)


# Calls Gemini Flash to generate a one-sentence context for a single function.
@retry(
    retry=retry_if_exception_type(ServerError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def generate_context(client: genai.Client, function: ParsedFunction) -> str:
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


# Runs generate_context on every function in the list.
def contextualize_functions(
    functions: list[ParsedFunction],
) -> list[ContextualChunk]:
    client = _build_client()
    results: list[ContextualChunk] = []

    for fn in functions:
        context = generate_context(client, fn)
        results.append(ContextualChunk(function=fn, context=context))
        print(f"  contextualized {fn.qualified_name} ({fn.file_path}:{fn.start_line})")

    return results