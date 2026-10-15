Authentication and authorization - check this for EVERY API endpoint the diff adds or modifies (route handlers such as @app.get / @router.post, Flask @app.route, Django views, webhook handlers):
- Missing authentication (CWE-306): the endpoint never establishes who the caller is - no auth dependency (e.g. Depends(get_current_user)), no @login_required, no token or signature verification.
- Missing authorization (CWE-862): the caller is authenticated, but the endpoint never checks they are allowed to perform this action - e.g. an admin or staff-only operation (refunds, deleting users, changing roles) with no role or permission check.
- Insecure direct object reference / IDOR (CWE-639): the endpoint loads or changes a resource by an id taken from the request without checking that the resource belongs to the caller (no owner or tenant check).
- Mass assignment (CWE-915): request data copied onto a model with **body, setattr in a loop, Model(**data) or obj.update(data), so the caller can set fields like is_admin, role, owner_id, balance or price. A Pydantic schema that lists only the safe fields is the fix, not a finding.

Internal or "admin" endpoints are NOT exempt: missing checks there are among the most common real vulnerabilities.

Tokens, sessions and browsers:
- JWT (CWE-347): jwt.decode with verify_signature False, verify=False, no algorithms list, or "none" allowed; a token whose expiry is not checked.
- CSRF (CWE-352): a state-changing route (POST, PUT, PATCH, DELETE) authenticated by a cookie, where the diff removes CSRF middleware, adds @csrf_exempt or WTF_CSRF_ENABLED = False. APIs authenticated only by an Authorization header are not exposed to CSRF.
- Cookies (CWE-614): set_cookie for a session or auth token without secure=True, httponly=True and a samesite value; SESSION_COOKIE_SECURE or SESSION_COOKIE_HTTPONLY set to False.
- CORS (CWE-942): allow_origins=["*"] or a reflected Origin header together with allow_credentials=True; an origin check done with startswith, endswith or "in" on a string.

Do NOT report an endpoint as unprotected when the protection is visible in the diff or the context - a router-level dependency (APIRouter(dependencies=[...])), middleware, a decorator, or an explicit owner/role check in the body. When the protection could live in code you cannot see (a router or middleware outside the diff), still report it, but say so in the description and set "confidence" to "low".
