import re
from typing import Callable, Iterator

from review.agents.quality.tools.python_source_scan import code_lines, scan_python_files
from review.agents.tools.tool_definition import Tool, ToolContext


MIN_CALL_CHARS = 3
LOOP_KEYWORDS = ("for ", "async for ", "while ")
# Calls that usually reach a database or the network; dict.get and lazy ORM attributes are not matched.
LOOP_COST_PATTERN = re.compile(
    r"\.(execute|query|filter|filter_by|get_or_create|scalar|scalars|fetchone|fetchall|first|one|one_or_none|refresh)\("
    r"|\.objects\.|\bsession\.get\("
    r"|(?<![\w.])(requests|httpx)\.\w+\(|\burlopen\("
)


# What repeats on a line outside a loop body: a one-line loop body or a comprehension's expression.
def _repeated_part(text: str) -> str:
    if text.startswith(LOOP_KEYWORDS):
        return text.rpartition(":")[2]
    if " for " in text:
        return text.partition(" for ")[0]
    return ""


# Lines that run a matching call once per loop iteration, with the line of the innermost loop.
def find_loop_calls(source: str, is_costly: Callable[[str], bool]) -> Iterator[tuple[int, str]]:
    open_loops: list[tuple[int, int]] = []  # (indentation, line number) of the enclosing loops
    for number, indent, text in code_lines(source):
        while open_loops and indent <= open_loops[-1][0]:
            open_loops.pop()
        # A loop header's iterable runs once, so outside a loop only the repeated part is checked.
        checked = text if open_loops else _repeated_part(text)
        if checked and is_costly(checked):
            loop_line = open_loops[-1][1] if open_loops else number
            yield number, f"{text}  (loop at line {loop_line})"
        if text.startswith(LOOP_KEYWORDS):
            open_loops.append((indent, number))


def _call_matcher(call: str) -> Callable[[str], bool]:
    if not call:
        return lambda text: LOOP_COST_PATTERN.search(text) is not None
    # Escaped, so the model's text is literal; the boundary stops "get_price" matching "budget_price".
    pattern = re.compile(rf"(?<!\w){re.escape(call)}\(")
    return lambda text: pattern.search(text) is not None


# Queries and HTTP calls made per loop iteration, to confirm an N+1 whose loop is outside the diff hunk.
def find_loop_queries(context: ToolContext, arguments: dict) -> str:
    call = str(arguments.get("call", "")).strip()
    if call and len(call) < MIN_CALL_CHARS:
        return f"call must be at least {MIN_CALL_CHARS} characters."
    is_costly = _call_matcher(call)
    hits = scan_python_files(
        context, str(arguments.get("path_prefix", "")), lambda source: find_loop_calls(source, is_costly)
    )
    if hits:
        return "\n".join(hits)
    return f"No call to '{call}' inside a loop." if call else "No query or HTTP call found inside a loop."


FIND_LOOP_QUERIES = Tool(
    name="find_loop_queries",
    description=(
        "List production lines that run a query, ORM lookup or HTTP call inside a for/while loop or a comprehension, "
        "with the loop's line. Pass a file to see whether a changed line sits in a loop that starts outside the hunk, "
        "or pass call=<helper name> to find loops that call a changed helper. Text and indentation matching only."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path_prefix": {"type": "string", "description": "optional file or folder, e.g. app/services/orders.py"},
            "call": {"type": "string", "description": "optional function name to look for instead, e.g. get_price"},
        },
    },
    run=find_loop_queries,
    requires="repo",
)
