from code_index.file_classifier import is_test_file
from review.agents.tools.repo_files import search_lines
from review.agents.tools.tool_definition import Tool, ToolContext


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
