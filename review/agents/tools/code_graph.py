from review.agents.tools.tool_definition import Tool, ToolContext


# 10 results x 15 lines stays under the tool output cap, so a result is never cut mid-function.
MAX_GRAPH_RESULTS = 10
MAX_CODE_LINES_PER_RESULT = 15


def require_graph(context: ToolContext):
    if context.code_graph is None:
        raise RuntimeError("no call graph is available in this review")
    return context.code_graph


# Nodes named `name` ("login" also matches "AuthService.login"), optionally only in one file.
def _matching_nodes(graph, name: str, file_path: str) -> list[str]:
    matches = []
    for node, data in graph.nodes(data=True):
        function = data["function"]
        is_named = function.qualified_name == name or function.qualified_name.endswith("." + name)
        if is_named and (not file_path or function.file_path == file_path):
            matches.append(node)
    return matches


def describe_function(graph, node: str) -> str:
    function = graph.nodes[node]["function"]
    code = "\n".join(function.code.splitlines()[:MAX_CODE_LINES_PER_RESULT])
    return f"- {function.qualified_name} ({function.file_path}:{function.start_line})\n{code}"


def _neighbours(context: ToolContext, arguments: dict, direction: str) -> str:
    graph = require_graph(context)
    name = str(arguments["name"])
    nodes = _matching_nodes(graph, name, str(arguments.get("file_path", "")).strip())
    if not nodes:
        return f"No function named '{name}' in the call graph."
    related = set()
    for node in nodes:
        related.update(graph.predecessors(node) if direction == "callers" else graph.successors(node))
    if not related:
        return f"No {direction} of '{name}' found (calls are resolved by name only, so some may be missing)."

    shown = sorted(related)[:MAX_GRAPH_RESULTS]
    lines = [describe_function(graph, node) for node in shown]
    if len(related) > len(shown):
        lines.append(f"... {len(related) - len(shown)} more {direction} not shown; pass file_path to narrow")
    return "\n".join(lines)


def find_callers(context: ToolContext, arguments: dict) -> str:
    return _neighbours(context, arguments, "callers")


def find_callees(context: ToolContext, arguments: dict) -> str:
    return _neighbours(context, arguments, "callees")


_NAME_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "function or Class.method name, e.g. login or AuthService.login"},
        "file_path": {"type": "string", "description": "optional file of that function, when the name is ambiguous"},
    },
    "required": ["name"],
}

FIND_CALLERS = Tool(
    name="find_callers",
    description="List the functions that call the given function, with their code. Use it to check who breaks when a signature or return value changes.",
    parameters=_NAME_PARAMETERS,
    run=find_callers,
    requires="graph",
)

FIND_CALLEES = Tool(
    name="find_callees",
    description="List the functions the given function calls, with their code. Use it to check what can return None or raise.",
    parameters=_NAME_PARAMETERS,
    run=find_callees,
    requires="graph",
)
