from review.agents.agent_runner import (
    AgentSpec,
    FINDINGS_OUTPUT_INSTRUCTIONS,
    build_groq_client as _build_groq_client,
    run_agent,
)
from review.state import ReviewState


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

Do NOT report logic bugs or code quality issues: other reviewers cover
them. Include the CWE ID in the title when one applies, and make every
suggestion a secure fix.

Diff:
{diff}

Related existing code context (may be empty):
{context_summary}

""" + FINDINGS_OUTPUT_INSTRUCTIONS

SECURITY_AGENT = AgentSpec(name="security", prompt_template=SECURITY_PROMPT_TEMPLATE)


# LangGraph node for the security agent.
def security_node(state: ReviewState) -> dict:
    # _build_groq_client is looked up at call time, so tests can
    # monkeypatch agents.security._build_groq_client with a fake.
    return run_agent(SECURITY_AGENT, state, _build_groq_client())
