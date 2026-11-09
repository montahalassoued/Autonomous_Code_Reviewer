from code_index.file_classifier import is_test_file
from review.agents.tools.repo_files import search_lines, under_prefix
from review.agents.tools.tool_definition import Tool, ToolContext


# App-wide protections that usually live outside the diff: CORS, CSRF, cookies, debug, TLS, JWT.
SECURITY_SETTING_MARKERS = (
    "CORSMiddleware",
    "allow_origins",
    "allow_credentials",
    "CORS(",
    "CSRF",
    "csrf",
    "SESSION_COOKIE_",
    "set_cookie(",
    "samesite",
    "DEBUG =",
    "DEBUG=",
    "debug=True",
    "ALLOWED_HOSTS",
    "TrustedHostMiddleware",
    "HTTPSRedirectMiddleware",
    "SECURE_",
    "verify=False",
    "jwt.decode(",
    "algorithms=",
)
SETTING_FILE_SUFFIXES = (".py", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json")


# Test settings often disable CSRF or TLS on purpose, so they would look like real gaps.
def _is_production_setting_file(path: str) -> bool:
    return path.endswith(SETTING_FILE_SUFFIXES) and not is_test_file(path) and "/tests/" not in f"/{path}"


def find_security_settings(context: ToolContext, arguments: dict) -> str:
    in_prefix = under_prefix(str(arguments.get("path_prefix", "")))
    hits = search_lines(context, SECURITY_SETTING_MARKERS, lambda path: _is_production_setting_file(path) and in_prefix(path))
    return "\n".join(hits) if hits else "No CORS, CSRF, cookie, debug, TLS or JWT setting found."


FIND_SECURITY_SETTINGS = Tool(
    name="find_security_settings",
    description=(
        "List the app-wide security settings outside tests: CORS middleware, CSRF protection, cookie flags, "
        "DEBUG, allowed hosts, verify=False and jwt.decode options. Use it before reporting CSRF, CORS, "
        "cookie, debug or JWT issues, since these are usually configured once outside the diff."
    ),
    parameters={
        "type": "object",
        "properties": {"path_prefix": {"type": "string", "description": "optional folder to limit the search, e.g. app"}},
    },
    run=find_security_settings,
    requires="repo",
)
