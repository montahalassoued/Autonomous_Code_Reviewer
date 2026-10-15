import ast

from review.agents.tools.code_graph import FUNCTION_NAME_PARAMETERS, MAX_GRAPH_RESULTS, matching_nodes, require_graph
from review.agents.tools.tool_definition import Tool, ToolContext


# Enough return lines per function to see its None paths while 10 functions stay under the output cap.
MAX_RETURNS_SHOWN = 8
FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
NESTED_SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
DEF_PREFIXES = ("def ", "async def ")
RETURNS_NONE_MARK = "  <- returns None"


# A decorated method's code starts at "@" but its def line keeps the class indent, so wrap it to parse.
def _parse_function(code: str) -> tuple[ast.AST, int] | None:
    def_line = next((line for line in code.splitlines() if line.lstrip().startswith(DEF_PREFIXES)), "")
    indent = def_line[: len(def_line) - len(def_line.lstrip())]
    source, offset = (f"if True:\n{indent}{code}", 1) if indent else (code, 0)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    definition = next((node for node in ast.walk(tree) if isinstance(node, FUNCTION_TYPES)), None)
    return (definition, offset) if definition else None


# The function's own return statements and whether it yields; nested functions and classes are skipped.
def _own_returns(definition: ast.AST) -> tuple[list[ast.Return], bool]:
    returns, is_generator = [], False
    pending = list(definition.body)
    while pending:
        node = pending.pop()
        if isinstance(node, NESTED_SCOPE_TYPES):
            continue
        if isinstance(node, ast.Return):
            returns.append(node)
        is_generator = is_generator or isinstance(node, (ast.Yield, ast.YieldFrom))
        pending.extend(ast.iter_child_nodes(node))
    return sorted(returns, key=lambda node: node.lineno), is_generator


def _returns_none(statement: ast.Return) -> bool:
    return statement.value is None or (isinstance(statement.value, ast.Constant) and statement.value.value is None)


# True when the block always returns or raises, so the function cannot fall off its end.
def _always_exits(statements: list[ast.stmt]) -> bool:
    if not statements:
        return False
    last = statements[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    if isinstance(last, ast.If):
        return _always_exits(last.body) and _always_exits(last.orelse)
    if isinstance(last, (ast.With, ast.AsyncWith)):
        return _always_exits(last.body)
    if isinstance(last, ast.Try):
        handlers_exit = all(_always_exits(handler.body) for handler in last.handlers)
        body_exits = _always_exits(last.body) or _always_exits(last.orelse)
        return _always_exits(last.finalbody) or (handlers_exit and body_exits)
    # "while True" without a break can only be left by a return or a raise.
    if isinstance(last, ast.While) and isinstance(last.test, ast.Constant) and last.test.value is True:
        return not any(isinstance(node, ast.Break) for node in ast.walk(last))
    return False


def _ending(definition: ast.AST, returns: list[ast.Return], is_generator: bool) -> str:
    if is_generator:
        return "  a generator: it yields values, and a return only ends the iteration"
    if _always_exits(definition.body):
        return "  every path ends in a return or a raise"
    if not returns:
        return "  no return statement, so it always returns None"
    return "  can reach its end without a return, so it returns None there"


def _describe_returns(graph, node: str) -> str:
    function = graph.nodes[node]["function"]
    header = f"- {function.qualified_name} ({function.file_path}:{function.start_line})"
    parsed = _parse_function(function.code)
    if parsed is None:
        return f"{header}\n  could not be parsed; read it with read_file"
    definition, offset = parsed
    returns, is_generator = _own_returns(definition)
    code_lines = function.code.splitlines()
    lines = [header]
    for statement in returns[:MAX_RETURNS_SHOWN]:
        index = statement.lineno - 1 - offset
        marker = RETURNS_NONE_MARK if _returns_none(statement) else ""
        lines.append(f"  {function.start_line + index}: {code_lines[index].strip()}{marker}")
    if len(returns) > MAX_RETURNS_SHOWN:
        lines.append(f"  ... {len(returns) - MAX_RETURNS_SHOWN} more return statements")
    lines.append(_ending(definition, returns, is_generator))
    return "\n".join(lines)


# What a function can return, so an unhandled None is only reported when a None path exists.
def find_return_values(context: ToolContext, arguments: dict) -> str:
    graph = require_graph(context)
    name = str(arguments["name"])
    nodes = sorted(matching_nodes(graph, name, str(arguments.get("file_path", "")).strip()))
    if not nodes:
        return f"No function named '{name}' in the call graph."
    lines = [_describe_returns(graph, node) for node in nodes[:MAX_GRAPH_RESULTS]]
    if len(nodes) > MAX_GRAPH_RESULTS:
        lines.append(f"... {len(nodes) - MAX_GRAPH_RESULTS} more functions not shown; pass file_path to narrow")
    return "\n".join(lines)


FIND_RETURN_VALUES = Tool(
    name="find_return_values",
    description=(
        "List a function's own return statements with their file lines, mark the ones that return None, and say "
        "whether it can reach its end without a return (an implicit None). Use it before reporting, or dropping, "
        "an unhandled None from a call."
    ),
    parameters=FUNCTION_NAME_PARAMETERS,
    run=find_return_values,
    requires="graph",
)
