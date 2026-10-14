"""
security.py - the LangGraph node that analyzes a PR diff for security
vulnerabilities.

Inputs: ReviewState (pr_diff + retrieved_context from agents/retriever.py).
Outputs: a partial state update adding this agent's Finding objects to
ReviewState.findings (merged with the other domain agents' findings via
the operator.add reducer defined in agents/state.py).

Focus areas: OWASP Top 10 and CWE-classified vulnerabilities - SQL
injection, XSS, command injection, insecure deserialization, hardcoded
secrets, SSRF, path traversal, and similar - plus an explicit
authentication/authorization check on every endpoint the PR adds or
modifies (missing authentication CWE-306, missing authorization CWE-862,
IDOR CWE-639). That check gets its own prompt section because missing
authorization on internal endpoints is one of the most common real
findings in code review, and a one-line "broken access control" bullet
was too vague for the model to apply consistently.

Same pattern as agents/performance.py, agents/architecture.py,
agents/testing.py, and agents/documentation.py: Groq call, low
temperature for consistency, robust JSON parsing that never lets a
malformed LLM response crash the pipeline - it just yields fewer (or
zero) findings instead.
"""

import os
import json
from groq import Groq

from agents.state import ReviewState, Finding, RetrievedChunk


SECURITY_MODEL = "llama-3.3-70b-versatile"

SECURITY_PROMPT_TEMPLATE = """You are a senior application security engineer reviewing a pull
request diff.

Look for security vulnerabilities ONLY, classified by OWASP Top 10 /
CWE where applicable, such as:
- SQL injection (string-built queries, unparameterized input)
- Cross-site scripting (XSS) (unescaped output rendered as HTML)
- Command injection (unsanitized input passed to a shell/subprocess)
- Insecure deserialization (pickle, yaml.load, unsafe unmarshalling)
- Hardcoded secrets, API keys, or credentials
- Server-side request forgery (SSRF)
- Path traversal (unsanitized file paths from user input)
- Insecure cryptography (weak hashing, hardcoded salts/IVs, ECB mode)

Authentication and authorization (check this for EVERY API endpoint the
diff adds or modifies - route handlers such as @app.get / @router.post,
Flask @app.route, Django views, webhook handlers):
- Missing authentication (CWE-306): the endpoint never establishes who
  the caller is - no auth dependency (e.g. Depends(get_current_user)),
  no @login_required, no token or signature verification.
- Missing authorization (CWE-862): the caller is authenticated, but the
  endpoint never checks they are allowed to perform this action - e.g.
  an admin or staff-only operation (refunds, deleting users, changing
  roles) with no role or permission check.
- Insecure direct object reference / IDOR (CWE-639): the endpoint loads
  or changes a resource by an id taken from the request without checking
  that the resource belongs to the caller (no owner or tenant check).
Internal or "admin" endpoints are NOT exempt: missing checks there are
among the most common real vulnerabilities. Do NOT report an endpoint
as unprotected when the protection is visible in the diff or the
context - a router-level dependency (APIRouter(dependencies=[...])),
middleware, a decorator, or an explicit owner/role check in the body.

Diff:
{diff}

Related existing code context (may be empty):
{context_summary}

For each issue found, output one JSON object with these exact fields:
- "severity": one of "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"
- "file_path": the file the issue is in
- "line_number": the line number (integer) the issue occurs at, from the diff
- "title": a short title for the issue, including the CWE ID if applicable
- "description": a clear explanation of the vulnerability and its impact
- "evidence": the specific code snippet that shows the issue
- "suggestion": a concrete, secure fix

Output ONLY a JSON array of these objects, nothing else - no markdown,
no explanation, no preamble. If there are no security issues, output
an empty JSON array: []"""


def _build_groq_client() -> Groq:
    """
    Reads GROQ_API_KEY from the environment. Same defensive pattern
    used across the project (rag/query_decomposer.py, agents/retriever.py,
    agents/performance.py, agents/architecture.py, agents/testing.py,
    agents/documentation.py): fail early with a clear message instead
    of a cryptic error mid-call.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com and set it "
            "in your .env file."
        )
    return Groq(api_key=api_key)


def _format_context(retrieved_context: list[RetrievedChunk]) -> str:
    """
    Renders the retrieved context chunks into a short text block for
    the prompt, so the LLM can spot vulnerabilities that only show up
    when the diff is considered together with related code (e.g. a new
    call site that passes unsanitized input into an existing function
    that executes a raw query).

    Args:
        retrieved_context: chunks produced by agents/retriever.py

    Returns:
        a human-readable summary string, or a placeholder line if no
        context was retrieved
    """
    if not retrieved_context:
        return "(no related context retrieved)"

    return "\n".join(
        f"- {chunk['function_name']} ({chunk['file_path']}):\n{chunk['code']}"
        for chunk in retrieved_context
    )


def _parse_findings(raw_output: str) -> list[dict]:
    """
    Robustly parses the LLM's JSON array response into a list of dicts.

    Args:
        raw_output: the raw LLM response text

    Returns:
        a list of parsed dicts, or an empty list if parsing fails at
        every stage - a malformed response should degrade to "no
        findings from this agent", never crash the review pipeline.
    """
    cleaned = raw_output.strip().strip("`").removeprefix("json").strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: the model may have wrapped the array in prose despite
    # instructions. Find the outermost [ ... ] boundaries and retry.
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    return []


def _to_finding(raw: dict) -> Finding | None:
    """
    Converts one parsed dict into a validated Finding, tagging it with
    this agent's name so the synthesizer can group/attribute it.

    Args:
        raw: one dict from the LLM's parsed JSON array

    Returns:
        a Finding, or None if required fields are missing/malformed -
        skipped rather than crashing on one bad entry in an otherwise
        valid array.
    """
    try:
        return Finding(
            agent="security",
            severity=raw["severity"],
            file_path=raw["file_path"],
            line_number=int(raw["line_number"]),
            title=raw["title"],
            description=raw["description"],
            evidence=raw["evidence"],
            suggestion=raw["suggestion"],
        )
    except (KeyError, TypeError, ValueError):
        return None


def security_node(state: ReviewState) -> dict:
    """
    The LangGraph node function for the "security" node defined in
    agents/graph.py. Replaces that file's security_node STUB.

    Args:
        state: the current ReviewState, reads pr_diff and
               retrieved_context

    Returns:
        a partial state update: {"findings": [...]} - merged into
        ReviewState.findings via the operator.add reducer alongside
        the other domain agents' findings
    """
    client = _build_groq_client()
    context_summary = _format_context(state["retrieved_context"])
    prompt = SECURITY_PROMPT_TEMPLATE.format(
        diff=state["pr_diff"],
        context_summary=context_summary,
    )

    response = client.chat.completions.create(
        model=SECURITY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,  # consistency over creativity for analysis
    )

    # content can be None (e.g. a filtered or empty completion); treat
    # that as "no findings" instead of crashing on .strip() later.
    raw_output = response.choices[0].message.content or ""
    raw_findings = _parse_findings(raw_output)
    findings = [f for f in (_to_finding(raw) for raw in raw_findings) if f is not None]

    return {"findings": findings}
