Authentication and authorization - check this for EVERY API endpoint the diff adds or modifies (route handlers such as @app.get / @router.post, Flask @app.route, Django views, webhook handlers):
- Missing authentication (CWE-306): the endpoint never establishes who the caller is - no auth dependency (e.g. Depends(get_current_user)), no @login_required, no token or signature verification.
- Missing authorization (CWE-862): the caller is authenticated, but the endpoint never checks they are allowed to perform this action - e.g. an admin or staff-only operation (refunds, deleting users, changing roles) with no role or permission check.
- Insecure direct object reference / IDOR (CWE-639): the endpoint loads or changes a resource by an id taken from the request without checking that the resource belongs to the caller (no owner or tenant check).

Internal or "admin" endpoints are NOT exempt: missing checks there are among the most common real vulnerabilities.

Do NOT report an endpoint as unprotected when the protection is visible in the diff or the context - a router-level dependency (APIRouter(dependencies=[...])), middleware, a decorator, or an explicit owner/role check in the body. When the protection could live in code you cannot see (a router or middleware outside the diff), still report it, but say so in the description and set "confidence" to "low".
