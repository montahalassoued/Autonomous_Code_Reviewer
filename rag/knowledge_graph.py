import networkx as nx
from rag.ast_parser import ParsedFunction


# Builds a unique node identifier for a function.
def _node_id(file_path: str, qualified_name: str) -> str:
    return f"{file_path}::{qualified_name}"


# Builds a directed graph from a list of parsed functions.
def build_knowledge_graph(functions: list[ParsedFunction]) -> nx.DiGraph:
    graph = nx.DiGraph()

    # First pass: register every function as a node. We do this before
    # adding any edges so that when we check "is this called function
    # known?" in the second pass, ALL nodes already exist regardless
    # of which function appears first in the input list.
    for fn in functions:
        node_id = _node_id(fn.file_path, fn.qualified_name)
        graph.add_node(node_id, function=fn)

    # Build a lookup from plain function name -> list of node_ids that
    # have that name (handles the case of same name in different files).
    # We need this because ParsedFunction.calls only stores the called
    # function's NAME (e.g. "verify_password"), not its file - the
    # parser has no way to know which file a call resolves to without
    # this kind of cross-referencing.
    name_to_node_ids: dict[str, list[str]] = {}
    for fn in functions:
        name_to_node_ids.setdefault(fn.name, []).append(
            _node_id(fn.file_path, fn.qualified_name)
        )

    # Second pass: add edges for every detected call, but only to
    # functions we actually know about (skip calls to external
    # libraries like bcrypt.hashpw, db.execute, etc. - those aren't
    # nodes in our graph since we never parsed bcrypt's source).
    for fn in functions:
        caller_id = _node_id(fn.file_path, fn.qualified_name)
        for called_name in fn.calls:
            if called_name not in name_to_node_ids:
                continue  # external call, e.g. a library function - skip
            for callee_id in name_to_node_ids[called_name]:
                if callee_id == caller_id:
                    continue  # skip self-recursive calls for now
                graph.add_edge(caller_id, callee_id)

    return graph
