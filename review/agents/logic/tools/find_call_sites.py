from review.agents.tools.related_tests import is_test_file
from review.agents.tools.repo_files import search_lines, under_prefix
from review.agents.tools.tool_definition import Tool, ToolContext


MIN_NAME_CHARS = 3


# True when the line calls `name(` itself, not a longer name ending with it ("recreate_order(" for "create_order").
def _calls_name(line: str, name: str) -> bool:
    call = name + "("
    start = line.find(call)
    while start != -1:
        before = line[start - 1] if start else " "
        if not (before.isalnum() or before == "_"):
            return True
        start = line.find(call, start + 1)
    return False


# Every production line that calls a function, with its arguments, to check callers against a changed signature.
def find_call_sites(context: ToolContext, arguments: dict) -> str:
    # "OrderService.create" is called as "service.create(" or "self.create(", so search the last part.
    name = str(arguments["name"]).strip().rstrip("(").rsplit(".", 1)[-1]
    if len(name) < MIN_NAME_CHARS:
        return f"Name must be at least {MIN_NAME_CHARS} characters."
    in_prefix = under_prefix(str(arguments.get("path_prefix", "")))
    # Tests are left out: CI catches their breakage, and they would fill the hit limit.
    hits = search_lines(
        context,
        (name + "(",),
        lambda path: path.endswith(".py") and in_prefix(path) and not is_test_file(path),
        lambda line: f"def {name}(" not in line and _calls_name(line, name),
    )
    return "\n".join(hits) if hits else f"No call to '{name}(' found outside tests."


FIND_CALL_SITES = Tool(
    name="find_call_sites",
    description=(
        "List the lines outside tests that call a function, with their arguments, including method calls "
        "(obj.name(...)) that find_callers misses. Use it when the diff changes a signature, a return value or a "
        "raised exception, to check every caller. Only the first line of a multi-line call is shown, and other "
        "functions with the same name are included."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "function or Class.method name, e.g. create_order"},
            "path_prefix": {"type": "string", "description": "optional folder to limit the search, e.g. app/services"},
        },
        "required": ["name"],
    },
    run=find_call_sites,
    requires="repo",
)
