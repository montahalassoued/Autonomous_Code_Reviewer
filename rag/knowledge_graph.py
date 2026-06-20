"""
knowledge_graph.py - builds a directed graph of function call relationships.

This is what GraphRAG adds on top of vector search: vector search finds
functions that are SEMANTICALLY similar to a query, but it cannot answer
structural questions like "what calls this function?" or "what does this
function depend on?". A graph captures exactly that relationship, and we
can traverse it to enrich whatever the
vector search already found.

This module is pure, deterministic graph construction - no LLM calls,
no network access. It directly reuses ParsedFunction.calls from
rag/ast_parser.py , so the call-detection limitation documented
there (plain identifiers only, not attribute calls) also applies here:
the graph will under-count edges involving method calls.
"""

import pickle
import networkx as nx
from rag.ast_parser import ParsedFunction


def _node_id(file_path: str, function_name: str) -> str:
    """
    Builds a unique node identifier for a function.

    Using file_path + function_name (not just the name) avoids
    collisions: two different files can each have a function called
    `get()`, and they must be distinct nodes in the graph.
    """
    return f"{file_path}::{function_name}"


def build_knowledge_graph(functions: list[ParsedFunction]) -> nx.DiGraph:
    """
    Builds a directed graph from a list of parsed functions.

    Each function becomes a node (with its metadata attached). Each
    entry in a function's `calls` list becomes a directed edge from
    that function to the called one - but ONLY if the called function
    is also a known node in this same set (we can't draw an edge to a
    function we never parsed, e.g. an external library call like
    bcrypt.hashpw).

    Args:
        functions: the full list of ParsedFunction for a repo (or a
                    subset, e.g. one file), typically the direct output
                    of rag/ast_parser.py's parse_file()

    Returns:
        a networkx.DiGraph where node IDs are "file_path::function_name"
        and each node carries the original ParsedFunction as data
    """
    graph = nx.DiGraph()

    # First pass: register every function as a node. We do this before
    # adding any edges so that when we check "is this called function
    # known?" in the second pass, ALL nodes already exist regardless
    # of which function appears first in the input list.
    for fn in functions:
        node_id = _node_id(fn.file_path, fn.name)
        graph.add_node(node_id, function=fn)

    # Build a lookup from plain function name -> list of node_ids that
    # have that name (handles the case of same name in different files).
    # We need this because ParsedFunction.calls only stores the called
    # function's NAME (e.g. "verify_password"), not its file - the
    # parser has no way to know which file a call resolves to without
    # this kind of cross-referencing.
    name_to_node_ids: dict[str, list[str]] = {}
    for fn in functions:
        name_to_node_ids.setdefault(fn.name, []).append(_node_id(fn.file_path, fn.name))

    # Second pass: add edges for every detected call, but only to
    # functions we actually know about (skip calls to external
    # libraries like bcrypt.hashpw, db.execute, etc. - those aren't
    # nodes in our graph since we never parsed bcrypt's source).
    for fn in functions:
        caller_id = _node_id(fn.file_path, fn.name)
        for called_name in fn.calls:
            if called_name not in name_to_node_ids:
                continue  # external call, e.g. a library function - skip
            for callee_id in name_to_node_ids[called_name]:
                if callee_id == caller_id:
                    continue  # skip self-recursive calls for now
                graph.add_edge(caller_id, callee_id)

    return graph


def get_callers(graph: nx.DiGraph, file_path: str, function_name: str) -> list[str]:
    """
    Returns the node IDs of every function that calls the given function.

    In graph terms: predecessors = nodes with an edge POINTING TO this
    node. This answers "who calls this?" - useful for an agent to see
    the blast radius of changing a function.
    """
    node_id = _node_id(file_path, function_name)
    if node_id not in graph:
        return []
    return list(graph.predecessors(node_id))


def get_callees(graph: nx.DiGraph, file_path: str, function_name: str) -> list[str]:
    """
    Returns the node IDs of every function that the given function calls.

    In graph terms: successors = nodes this node has an edge POINTING TO.
    Answers "what does this depend on?".
    """
    node_id = _node_id(file_path, function_name)
    if node_id not in graph:
        return []
    return list(graph.successors(node_id))


def save_graph(graph: nx.DiGraph, path: str) -> None:
    """
    Persists the graph to disk via pickle, so ingestion only needs to
    run once and the review pipeline (Step 8) can load it back without
    re-parsing the whole repo on every PR.
    """
    with open(path, "wb") as f:
        pickle.dump(graph, f)


def load_graph(path: str) -> nx.DiGraph:
    """Loads a previously saved graph back from disk."""
    with open(path, "rb") as f:
        return pickle.load(f)