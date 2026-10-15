from review.agents.tools.repo_files import search_lines, under_prefix
from review.agents.tools.tool_definition import Tool, ToolContext


ERROR_HANDLING_MARKERS = ("except", "raise ", "finally:")


# Where an exception is raised or caught, so a new raise is checked against its callers' handlers.
def find_exception_handlers(context: ToolContext, arguments: dict) -> str:
    exception = str(arguments.get("exception", "")).strip()
    in_prefix = under_prefix(str(arguments.get("path_prefix", "")))
    hits = search_lines(
        context,
        ERROR_HANDLING_MARKERS,
        lambda path: path.endswith(".py") and in_prefix(path),
        (lambda line: exception in line) if exception else None,
    )
    if hits:
        return "\n".join(hits)
    return f"No raise or except of '{exception}' found." if exception else "No error handling found."


FIND_EXCEPTION_HANDLERS = Tool(
    name="find_exception_handlers",
    description=(
        "List the lines that raise or catch exceptions (raise, except, finally). Pass an exception name to see "
        "who raises it and who catches it, before reporting an unhandled error or a swallowed one."
    ),
    parameters={
        "type": "object",
        "properties": {
            "exception": {"type": "string", "description": "optional exception class, e.g. ValueError or NotFoundError"},
            "path_prefix": {"type": "string", "description": "optional folder to limit the search, e.g. app/services"},
        },
    },
    run=find_exception_handlers,
    requires="repo",
)
