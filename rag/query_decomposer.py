import json
import logging
import os

from groq import APIError, Groq


logger = logging.getLogger(__name__)

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


# Reads GROQ_API_KEY from the environment.
def _build_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com and set it "
            "in your .env file."
        )
    return Groq(api_key=api_key)


# Generates 1-3 search queries from a raw PR diff; the caller picks the Groq model.
def decompose_diff(diff: str, model: str) -> list[str]:
    client = _build_client()
    prompt = DECOMPOSE_PROMPT_TEMPLATE.format(diff=diff)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,  # consistent, focused queries, not creative variation
        )
    except APIError as error:
        # A Groq outage or rate limit degrades retrieval, never the whole review.
        logger.warning("Query decomposition failed (%s), searching with the diff itself", type(error).__name__)
        return [diff[:200]]

    # content can be None on an empty or filtered completion - treat it
    # like unparseable output so we reach the fallback below.
    raw_output = (response.choices[0].message.content or "").strip()
    # Defensive stripping: models sometimes wrap JSON in markdown
    # code fences even when explicitly told not to.
    raw_output = raw_output.strip("`").removeprefix("json").strip()

    try:
        queries = json.loads(raw_output)
        # An empty list (or a list of blank strings) parses fine but
        # would make retrieval search for nothing, so it counts as a
        # failure and falls through to the fallback too.
        if (
            isinstance(queries, list)
            and queries
            and all(isinstance(q, str) and q.strip() for q in queries)
        ):
            return queries[:3]  # defensive cap, even if the model ignores the limit
    except json.JSONDecodeError:
        pass

    # Fallback: if parsing failed for any reason, don't crash the
    # pipeline - degrade to a single naive query built from the diff
    # itself, which still lets hybrid_search.py run (just less
    # precisely targeted than a clean decomposition would be).
    return [diff[:200]]