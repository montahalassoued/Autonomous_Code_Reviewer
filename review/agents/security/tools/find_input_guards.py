from code_index.file_classifier import is_test_file
from review.agents.tools.repo_files import search_lines, under_prefix
from review.agents.tools.tool_definition import Tool, ToolContext


# Code that validates or neutralizes untrusted input, grouped by the sink it protects.
INPUT_GUARD_MARKERS = {
    "path": ("secure_filename(", "safe_join(", "is_relative_to(", "commonpath(", "realpath(", "filter=\"data\""),
    "html": ("escape(", "bleach.clean(", "markupsafe", "autoescape", "nh3.clean("),
    "shell": ("shlex.quote(", "shlex.split("),
    "url": ("urlparse(", "urlsplit(", "ALLOWED_", "allowlist", "is_private", "is_loopback", "url_has_allowed_host_and_scheme("),
    "xml": ("defusedxml", "resolve_entities=False"),
    "schema": ("@validator", "@field_validator", "@model_validator", "Field(pattern", "constr(", "Literal[", "re.fullmatch("),
}


def _markers_for(kind: str) -> tuple[str, ...]:
    if not kind:
        return tuple(marker for markers in INPUT_GUARD_MARKERS.values() for marker in markers)
    return INPUT_GUARD_MARKERS[kind]


# Where the repo validates input, so a sink is not reported when a guard exists outside the diff.
def find_input_guards(context: ToolContext, arguments: dict) -> str:
    kind = str(arguments.get("kind", "")).strip().lower()
    if kind and kind not in INPUT_GUARD_MARKERS:
        return f"Unknown kind '{kind}'. Use one of: {', '.join(INPUT_GUARD_MARKERS)}."
    in_prefix = under_prefix(str(arguments.get("path_prefix", "")))
    # A validator that only exists in a test file protects nothing in production.
    hits = search_lines(
        context,
        _markers_for(kind),
        lambda path: path.endswith(".py") and in_prefix(path) and not is_test_file(path),
    )
    return "\n".join(hits) if hits else "No input validation or escaping code found."


FIND_INPUT_GUARDS = Tool(
    name="find_input_guards",
    description=(
        "List the lines that validate or escape untrusted input outside tests: path checks (secure_filename, "
        "is_relative_to), HTML escaping, shlex.quote, URL allowlists, defusedxml, Pydantic validators. "
        "Use it before reporting injection, SSRF, path traversal or XSS when the guard could be outside the diff."
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(INPUT_GUARD_MARKERS), "description": "optional: only guards for this sink"},
            "path_prefix": {"type": "string", "description": "optional folder to limit the search, e.g. app/api"},
        },
    },
    run=find_input_guards,
    requires="repo",
)
