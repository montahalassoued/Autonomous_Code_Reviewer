"""
Manual test for rag/knowledge_graph.py

Goal: build a graph from the same auth/utils.py snippet used in
previous steps, and verify:
1. Every function becomes a node
2. Edges are created for internal calls (login -> verify_password)
3. External calls (bcrypt.hashpw, db.execute) are correctly SKIPPED
   (not turned into broken edges to non-existent nodes)
4. get_callers / get_callees correctly traverse the graph
5. save_graph / load_graph round-trip works
"""

import os
from rag.ast_parser import parse_file
from rag.knowledge_graph import (
    build_knowledge_graph,
    get_callers,
    get_callees,
    save_graph,
    load_graph,
)


SAMPLE_CODE = '''def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt())


def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())


def login(username, password):
    query = "SELECT * FROM users WHERE username='" + username + "'"
    user = db.execute(query).fetchone()
    if user and verify_password(password, user.password):
        return create_session(user)
    return None


def create_session(user):
    return {"user_id": user.id}
'''


def test_every_function_becomes_a_node():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    assert graph.number_of_nodes() == 4, f"Expected 4 nodes, got {graph.number_of_nodes()}"
    assert "auth/utils.py::login" in graph
    assert "auth/utils.py::verify_password" in graph
    assert "auth/utils.py::hash_password" in graph
    assert "auth/utils.py::create_session" in graph
    print(f"OK - {graph.number_of_nodes()} nodes created: {list(graph.nodes)}")


def test_internal_calls_become_edges():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    # login() calls verify_password() and create_session() - both are
    # known functions in our parsed set, so both should be real edges.
    assert graph.has_edge("auth/utils.py::login", "auth/utils.py::verify_password")
    assert graph.has_edge("auth/utils.py::login", "auth/utils.py::create_session")
    print(f"OK - {graph.number_of_edges()} edges created: {list(graph.edges)}")


def test_external_calls_are_skipped_not_broken():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    # bcrypt.hashpw / bcrypt.checkpw are attribute calls anyway (Step 3
    # limitation - not even detected as calls). But even if they were
    # detected as a plain "bcrypt" call, there is no node named
    # "bcrypt" in our graph, so no edge should ever point to it.
    node_names = list(graph.nodes)
    assert not any("bcrypt" in n for n in node_names), (
        "External library call should never become a phantom node"
    )
    print("OK - no phantom nodes created for external library calls")


def test_get_callers_finds_who_calls_a_function():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    callers = get_callers(graph, "auth/utils.py", "verify_password")
    assert callers == ["auth/utils.py::login"], f"Got: {callers}"
    print(f"OK - callers of verify_password(): {callers}")


def test_get_callees_finds_what_a_function_calls():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    callees = get_callees(graph, "auth/utils.py", "login")
    assert set(callees) == {"auth/utils.py::verify_password", "auth/utils.py::create_session"}
    print(f"OK - login() calls: {callees}")


def test_save_and_load_roundtrip():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    test_path = "test_graph.pkl"
    save_graph(graph, test_path)
    loaded = load_graph(test_path)

    assert loaded.number_of_nodes() == graph.number_of_nodes()
    assert loaded.number_of_edges() == graph.number_of_edges()
    os.remove(test_path)
    print("OK - graph survives save/load round-trip via pickle")


if __name__ == "__main__":
    test_every_function_becomes_a_node()
    test_internal_calls_become_edges()
    test_external_calls_are_skipped_not_broken()
    test_get_callers_finds_who_calls_a_function()
    test_get_callees_finds_what_a_function_calls()
    test_save_and_load_roundtrip()
    print("\nAll knowledge_graph tests passed.")