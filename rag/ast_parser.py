"""
ast_parser.py - extracts functions from source files as structured chunks.

This is the FIRST step of the ingestion pipeline. It does not call any
LLM and does not touch the network - it is pure, deterministic parsing.

Why tree-sitter instead of naive text splitting (e.g. every 500 chars):
splitting by character count can cut a function in half, producing a
chunk that makes no sense on its own. tree-sitter parses the actual
grammar of the language, so we can extract exactly the nodes we want
(function definitions) as complete, syntactically valid units.

Output of this module feeds directly into:
- rag/contextual_chunker.py (Step 4): needs the raw code + file path
- rag/knowledge_graph.py (Step 5): needs `calls` to build graph edges
- rag/embedder.py (Step 6): needs the final text to embed
"""

from dataclasses import dataclass, field
import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Node


PY_LANGUAGE = Language(tspython.language())


@dataclass
class ParsedFunction:
    """
    A single function extracted from a source file, with everything
    downstream pipeline steps will need about it.
    """
    name: str
    file_path: str
    code: str               # the exact source text of the function
    start_line: int          # 1-indexed, for pointing to exact GitHub lines
    end_line: int
    calls: list[str] = field(default_factory=list)  # names of functions called inside


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


def _extract_calls(function_node: Node, source_bytes: bytes) -> list[str]:
    """
    Walks the function's subtree looking for 'call' nodes (function
    invocations) and collects the name being called.

    Example: inside `def login(): verify_password(p, h)`, this finds
    the call node for verify_password and extracts "verify_password".
    This list becomes the edges of the knowledge graph in Step 5.
    """
    calls: list[str] = []

    def walk(node: Node):
        if node.type == "call":
            # the first child of a call node is what's being called
            # (could be an identifier like `foo()` or an attribute
            # like `self.foo()` - we only handle the simple identifier
            # case here, attribute calls are a future improvement)
            function_part = node.children[0]
            if function_part.type == "identifier":
                calls.append(_get_node_text(function_part, source_bytes))
        for child in node.children:
            walk(child)

    walk(function_node)
    return calls


def parse_file(file_path: str, source_code: str) -> list[ParsedFunction]:
    """
    Parses a single Python source file and returns one ParsedFunction
    per top-level or nested function definition found.

    Args:
        file_path: path to the file, stored as metadata on each chunk
                    (used later for GitHub line-pointing and graph nodes)
        source_code: the raw text content of the file

    Returns:
        list of ParsedFunction, one per `def` found in the file
    """
    parser = Parser(PY_LANGUAGE)
    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    functions: list[ParsedFunction] = []

    def walk(node: Node):
        if node.type == "function_definition":
            name = _extract_function_name(node, source_bytes)
            functions.append(
                ParsedFunction(
                    name=name,
                    file_path=file_path,
                    code=_get_node_text(node, source_bytes),
                    # tree-sitter rows are 0-indexed; GitHub lines are 1-indexed
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    calls=_extract_calls(node, source_bytes),
                )
            )
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return functions