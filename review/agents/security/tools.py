from review.agents.tools.repo_files import READ_FILE, SEARCH_CODE, search_lines, under_prefix
from review.agents.tools.tool_definition import Tool, ToolContext


# Code that usually means "this endpoint is protected": dependencies, decorators, middleware.
AUTH_GUARD_MARKERS = (
    "Depends(",
    "dependencies=[",
    "login_required",
    "permission_required",
    "require_",
    "add_middleware(",
    "@requires(",
    "verify_signature",
    "HTTPBearer",
    "OAuth2PasswordBearer",
)


# Where authentication and authorization live in the repo, so a router-level guard is not missed.
def find_auth_guards(context: ToolContext, arguments: dict) -> str:
    in_prefix = under_prefix(str(arguments.get("path_prefix", "")))
    hits = search_lines(context, AUTH_GUARD_MARKERS, lambda path: path.endswith(".py") and in_prefix(path))
    return "\n".join(hits) if hits else "No authentication or authorization code found."


FIND_AUTH_GUARDS = Tool(
    name="find_auth_guards",
    description=(
        "List the lines that set up authentication or authorization (Depends(...), router dependencies, "
        "login_required, middleware). Use it before reporting an endpoint as unprotected."
    ),
    parameters={
        "type": "object",
        "properties": {"path_prefix": {"type": "string", "description": "optional folder to limit the search, e.g. app/api"}},
    },
    run=find_auth_guards,
    requires="repo",
)

SECURITY_TOOLS = (READ_FILE, SEARCH_CODE, FIND_AUTH_GUARDS)
