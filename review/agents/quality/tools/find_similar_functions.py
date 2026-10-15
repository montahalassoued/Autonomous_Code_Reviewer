from review.agents.tools.code_graph import MAX_GRAPH_RESULTS, describe_function, require_graph
from review.agents.tools.tool_definition import Tool, ToolContext


MIN_NAME_CHARS = 3


# Functions whose name contains the given one ("parse_date" finds "parse_date_utc"), to spot a re-implemented helper.
def find_similar_functions(context: ToolContext, arguments: dict) -> str:
    graph = require_graph(context)
    name = str(arguments["name"]).strip().lower()
    if len(name) < MIN_NAME_CHARS:
        return f"Name must be at least {MIN_NAME_CHARS} characters."
    excluded_file = str(arguments.get("exclude_file", "")).strip()

    matches = sorted(
        node for node, data in graph.nodes(data=True)
        if name in data["function"].qualified_name.rsplit(".", 1)[-1].lower()
        and data["function"].file_path != excluded_file
    )
    if not matches:
        return f"No other function named like '{name}'."
    shown = matches[:MAX_GRAPH_RESULTS]
    lines = [describe_function(graph, node) for node in shown]
    if len(matches) > len(shown):
        lines.append(f"... {len(matches) - len(shown)} more not shown; use a more specific name")
    return "\n".join(lines)


FIND_SIMILAR_FUNCTIONS = Tool(
    name="find_similar_functions",
    description=(
        "List existing functions whose name contains the given text, with their code. Use it before reporting "
        "duplicated code: a new helper may already exist elsewhere in the repo."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "function name or part of it, e.g. parse_date"},
            "exclude_file": {"type": "string", "description": "optional file to skip, usually the changed file"},
        },
        "required": ["name"],
    },
    run=find_similar_functions,
    requires="graph",
)
