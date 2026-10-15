from dataclasses import dataclass, field
import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Node


PY_LANGUAGE = Language(tspython.language())

# Node types that open a new scope. Calls inside them belong to that
# inner scope, not to the function that contains them.
NESTED_SCOPE_TYPES = {"function_definition", "class_definition", "decorated_definition"}


# One function from a source file, with what later steps need.
@dataclass
class ParsedFunction:
    name: str
    file_path: str
    code: str               # the exact source text, decorators included
    start_line: int          # 1-indexed, for pointing to exact GitHub lines
    end_line: int
    calls: list[str] = field(default_factory=list)  # names of functions called inside
    qualified_name: str = ""

    # Defaults qualified_name to the bare name for module-level functions.
    def __post_init__(self) -> None:
        if not self.qualified_name:
            self.qualified_name = self.name


# tree-sitter gives byte offsets, so slice the source bytes to get the text.
def _get_node_text(node: Node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8")


# The identifier child of a function_definition node.
def _extract_function_name(function_node: Node, source_bytes: bytes) -> str:
    for child in function_node.children:
        if child.type == "identifier":
            return _get_node_text(child, source_bytes)
    return "<unknown>"


# The name of a class_definition node.
def _extract_class_name(class_node: Node, source_bytes: bytes) -> str:
    name_node = class_node.child_by_field_name("name")
    if name_node is None:
        return "<unknown>"
    return _get_node_text(name_node, source_bytes)


# Names of the plain function calls inside a function.
def _extract_calls(function_node: Node, source_bytes: bytes) -> list[str]:
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


# Makes a qualified name unique within one file.
def _unique_qualified_name(qualified_name: str, seen_counts: dict[str, int]) -> str:
    count = seen_counts.get(qualified_name, 0) + 1
    seen_counts[qualified_name] = count
    if count == 1:
        return qualified_name
    return f"{qualified_name}#{count}"


# One ParsedFunction per function or method in the file.
def parse_file(file_path: str, source_code: str) -> list[ParsedFunction]:
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
