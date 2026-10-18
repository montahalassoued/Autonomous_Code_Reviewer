from rag.ast_parser import parse_file
from rag.knowledge_graph import build_knowledge_graph


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

    callers = list(graph.predecessors("auth/utils.py::verify_password"))
    assert callers == ["auth/utils.py::login"], f"Got: {callers}"
    print(f"OK - callers of verify_password(): {callers}")


def test_get_callees_finds_what_a_function_calls():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    callees = list(graph.successors("auth/utils.py::login"))
    assert set(callees) == {"auth/utils.py::verify_password", "auth/utils.py::create_session"}
    print(f"OK - login() calls: {callees}")


METHOD_CODE = '''class UserService:
    def __init__(self, db):
        self.db = db

    def register(self, email):
        return normalize_email(email)


class AdminService:
    def __init__(self, db):
        self.db = db


def normalize_email(email):
    return email.strip().lower()
'''


def test_methods_get_class_qualified_node_ids():
    functions = parse_file("services.py", METHOD_CODE)
    graph = build_knowledge_graph(functions)

    # two __init__ methods in one file must be two distinct nodes
    assert "services.py::UserService.__init__" in graph
    assert "services.py::AdminService.__init__" in graph
    assert graph.number_of_nodes() == 4, f"Expected 4 nodes, got {list(graph.nodes)}"

    # calls still resolve by bare name, and the edge uses qualified ids
    assert graph.has_edge("services.py::UserService.register", "services.py::normalize_email")
    assert list(graph.predecessors("services.py::normalize_email")) == ["services.py::UserService.register"]
    assert list(graph.successors("services.py::UserService.register")) == ["services.py::normalize_email"]
    print(f"OK - qualified node ids: {list(graph.nodes)}")


if __name__ == "__main__":
    test_every_function_becomes_a_node()
    test_internal_calls_become_edges()
    test_external_calls_are_skipped_not_broken()
    test_get_callers_finds_who_calls_a_function()
    test_get_callees_finds_what_a_function_calls()
    test_methods_get_class_qualified_node_ids()
    print("\nAll knowledge_graph tests passed.")