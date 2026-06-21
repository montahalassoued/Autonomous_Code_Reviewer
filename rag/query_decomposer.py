"""
query_decomposer.py - transforms a raw PR diff into targeted search
queries for the retrieval pipeline.

This is the missing piece connecting "a PR was opened" to "here's what
to search for": a diff is raw code, not a search query. A single
generic query built from the whole diff is often too vague - a diff
touching authentication AND email AND database access needs queries
covering each concern, not one blurry average of all three.

Why Groq here, not Gemini (used in contextual_chunker.py): this runs
ONCE PER REVIEW (a handful of times per PR), not once per function in
an entire repo. Volume is low, so we use Groq - the model we reserve
for actual reasoning tasks the agents depend on, as opposed to Gemini
which we use for high-volume, low-stakes ingestion-time chunking.
"""

import os
import json
from groq import Groq


DECOMPOSE_MODEL = "llama-3.3-70b-versatile"

DECOMPOSE_PROMPT_TEMPLATE = """You are analyzing a pull request diff to figure out what existing
code in the repository would be useful context for reviewing it.

Generate 1 to 3 short search queries (each a plain sentence, not code)
that describe DIFFERENT concerns touched by this diff. Each query
should target one specific concern - if the diff touches
authentication, database access, and email sending, write one query
per concern rather than a single combined query.

Output ONLY a JSON array of strings, nothing else - no markdown, no
explanation, no preamble.

Diff:
{diff}

Queries:"""


def _build_client() -> Groq:
    """
    Reads GROQ_API_KEY from the environment. Same defensive pattern
    used in rag/contextual_chunker.py and rag/embedder.py: fail early
    with a clear message instead of a cryptic error mid-call.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com and set it "
            "in your .env file."
        )
    return Groq(api_key=api_key)


def decompose_diff(diff: str) -> list[str]:
    """
    Generates 1-3 search queries from a raw PR diff.

    Args:
        diff: the raw diff text, as it would arrive from a GitHub
              webhook payload

    Returns:
        a list of 1-3 query strings, each targeting a different
        concern found in the diff. Falls back to a single query built
        from the raw diff if the LLM output can't be parsed as JSON,
        rather than crashing the whole retrieval pipeline over a
        formatting hiccup.
    """
    client = _build_client()
    prompt = DECOMPOSE_PROMPT_TEMPLATE.format(diff=diff)

    response = client.chat.completions.create(
        model=DECOMPOSE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,  # low temperature: we want consistent,
                          # focused queries, not creative variation
    )

    raw_output = response.choices[0].message.content.strip()
    # Defensive stripping: models sometimes wrap JSON in markdown
    # code fences even when explicitly told not to.
    raw_output = raw_output.strip("`").removeprefix("json").strip()

    try:
        queries = json.loads(raw_output)
        if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
            return queries[:3]  # defensive cap, even if the model ignores the limit
    except json.JSONDecodeError:
        pass

    # Fallback: if parsing failed for any reason, don't crash the
    # pipeline - degrade to a single naive query built from the diff
    # itself, which still lets hybrid_search.py run (just less
    # precisely targeted than a clean decomposition would be).
    return [diff[:200]]