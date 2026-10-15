from review.agents.tools.repo_files import search_lines
from review.agents.tools.tool_definition import Tool, ToolContext


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{path}")


# Test lines that mention a name, so "missing test" is only claimed after looking.
def find_tests(context: ToolContext, arguments: dict) -> str:
    name = str(arguments["name"])
    hits = search_lines(context, (name,), is_test_file)
    return "\n".join(hits) if hits else f"No test file mentions '{name}'."


FIND_TESTS = Tool(
    name="find_tests",
    description="List the lines in test files that mention a function or class name. Use it before reporting a missing test.",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "function or class name, e.g. apply_discount"}},
        "required": ["name"],
    },
    run=find_tests,
    requires="repo",
)
