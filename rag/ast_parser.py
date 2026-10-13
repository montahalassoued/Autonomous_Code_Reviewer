"""
ast_parser.py - extracts functions from source files as structured chunks.

This is the FIRST step of the ingestion pipeline. It does not call any
LLM and does not touch the network - it is pure, deterministic parsing.

Why tree-sitter instead of naive text splitting (e.g. every 500 chars):
splitting by character count can cut a function in half, producing a
chunk that makes no sense on its own. tree-sitter parses the actual
grammar of the language, so we can extract exactly the nodes we want
(function definitions) as complete, syntactically valid units.

Chunk identity: each function gets a qualified_name ("Class.method",
"Outer.Inner.method", or just "func" at module level). The chunk id
used everywhere downstream is "file_path::qualified_name", so two
classes in one file can each have an __init__ without colliding.
Nested functions (a def inside another def) are NOT emitted as their
own chunks: they stay inside the parent's code, and their calls are
not attributed to the parent, because they only run if the parent
calls them.

Decorators are part of a function's chunk (code and start_line),
since things like @app.route or @retry change what the function does
and a reviewer needs to see them.

Output of this module feeds directly into:
- rag/contextual_chunker.py (Step 4): needs the raw code + file path
- rag/knowledge_graph.py (Step 5): needs `calls` to build graph edges
- rag/embedder.py (Step 6): needs the final text to embed
"""

from dataclasses import dataclass, field
import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Node


PY_LANGUAGE = Language(tspython.language())

# Node types that open a new scope. Calls inside them belong to that
# inner scope, not to the function that contains them.
NESTED_SCOPE_TYPES = {"function_definition", "class_definition", "decorated_definition"}


@dataclass
class ParsedFunction:
    """
    A single function extracted from a source file, with everything
    downstream pipeline steps will need about it.

    `name` is the bare name ("login"), used to resolve calls in
    rag/knowledge_graph.py since a call site only knows the bare name.
    `qualified_name` ("AuthService.login") is the unique identity
    within the file, used to build chunk and graph node ids.
    """
    name: str
    file_path: str
    code: str               # the exact source text, decorators included
    start_line: int          # 1-indexed, for pointing to exact GitHub lines
    end_line: int
    calls: list[str] = field(default_factory=list)  # names of functions called inside
    qualified_name: str = ""

    def __post_init__(self) -> None:
        """Defaults qualified_name to the bare name for module-level functions."""
        if not self.qualified_name:
            self.qualified_name = self.name


def _get_node_text(node: Node, source_bytes: bytes) -> str:
    """
    tree-sitter gives node positions as byte offsets, not the text
    itself - we slice the original source bytes to get the actual code.
    """
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8")


def _extract_function_name(function_node: Node, source_bytes: bytes) -> str:
    """
    A function_definition node's children include 'def', the name
    identifier, parameters, ':', and the body. We look for the child
    of type 'identifier' to get just the name (e.g. 'login').
    """
    for child in function_node.children:
        if child.type == "identifier":
            return _get_node_text(child, source_bytes)
    return "<unknown>"


def _extract_class_name(class_node: Node, source_bytes: bytes) -> str:
    """
    Returns the name of a class_definition node (e.g. 'AuthService'),
    or '<unknown>' if tree-sitter couldn't find one (broken source).
    """
    name_node = class_node.child_by_field_name("name")
    if name_node is None:
        return "<unknown>"
    return _get_node_text(name_node, source_bytes)


def _extract_calls(function_node: Node, source_bytes: bytes) -> list[str]:
    """
    Walks the function's subtree looking for 'call' nodes (function
    invocations) and collects the name being called.

    Example: inside `def login(): verify_password(p, h)`, this finds
    the call node for verify_password and extracts "verify_password".
    This list becomes the edges of the knowledge graph in Step 5.

    Nested defs and classes are skipped: their calls only happen if
    the nested function is itself invoked, so counting them as the
    parent's calls would create misleading graph edges.
    """
    calls: list[str] = []

    def walk(node: Node) -> None:
        if node.type == "call":
            # the first child of a call node is what's being called
            # (could be an identifier like `foo()` or an attribute
            # like `self.foo()` - we only handle the simple identifier
            # case here, attribute calls are a future improvement)
            function_part = node.children[0]
            if function_part.type == "identifier":
                calls.append(_get_node_text(function_part, source_bytes))
        for child in node.children:
            if child.type in NESTED_SCOPE_TYPES:
                continue
            walk(child)

    walk(function_node)
    return calls


def _unique_qualified_name(qualified_name: str, seen_counts: dict[str, int]) -> str:
    """
    Makes a qualified name unique within one file.

    The same qualified name can legitimately appear twice in a file
    (a @property and its @x.setter, or a function redefined under an
    `if`). Chroma rejects duplicate ids in one upsert, so the second
    occurrence becomes "name#2", the third "name#3", and so on. The
    first occurrence keeps the plain name, so ids stay stable for the
    common case.

    Args:
        qualified_name: the name built from the class stack
        seen_counts: how many times each name was seen so far in this
                     file (updated in place)

    Returns:
        the qualified name, suffixed if it was already used
    """
    count = seen_counts.get(qualified_name, 0) + 1
    seen_counts[qualified_name] = count
    if count == 1:
        return qualified_name
    return f"{qualified_name}#{count}"


def parse_file(file_path: str, source_code: str) -> list[ParsedFunction]:
    """
    Parses a single Python source file and returns one ParsedFunction
    per module-level function or method (at any class nesting depth).
    Functions defined inside other functions are not emitted.

    Args:
        file_path: path to the file, stored as metadata on each chunk
                    (used later for GitHub line-pointing and graph nodes)
        source_code: the raw text content of the file

    Returns:
        list of ParsedFunction, in source order
    """
    parser = Parser(PY_LANGUAGE)
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    functions: list[ParsedFunction] = []
    seen_counts: dict[str, int] = {}

    def emit_function(function_node: Node, span_node: Node, class_stack: list[str]) -> None:
        # span_node is the decorated_definition when there are
        # decorators, so they're included in code and start_line;
        # calls still come from the function itself, since decorator
        # expressions run at import time, not when the function runs.
        name = _extract_function_name(function_node, source_bytes)
        qualified_name = _unique_qualified_name(".".join(class_stack + [name]), seen_counts)
        functions.append(
            ParsedFunction(
                name=name,
                file_path=file_path,
                code=_get_node_text(span_node, source_bytes),
                # tree-sitter rows are 0-indexed; GitHub lines are 1-indexed
                start_line=span_node.start_point[0] + 1,
                end_line=span_node.end_point[0] + 1,
                calls=_extract_calls(function_node, source_bytes),
                qualified_name=qualified_name,
            )
        )

    def walk_definition(node: Node, span_node: Node, class_stack: list[str]) -> None:
        if node.type == "function_definition":
            # Don't recurse into the body: nested defs stay part of
            # this function's chunk instead of becoming their own.
            emit_function(node, span_node, class_stack)
            return
        if node.type == "class_definition":
            inner_stack = class_stack + [_extract_class_name(node, source_bytes)]
            for child in node.children:
                walk(child, inner_stack)
            return
        for child in node.children:
            walk(child, class_stack)

    def walk(node: Node, class_stack: list[str]) -> None:
        if node.type == "decorated_definition":
            definition = node.child_by_field_name("definition")
            if definition is not None:
                walk_definition(definition, node, class_stack)
            return
        # Everything else (module, class bodies, if/try blocks) is
        # walked generically so functions under `if TYPE_CHECKING:`
        # or `try:` are still found.
        walk_definition(node, node, class_stack)

    walk(tree.root_node, [])
    return functions
