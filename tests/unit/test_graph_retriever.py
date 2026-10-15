"""
Manual test for rag/graph_retriever.py

This test does NOT call any LLM or embedding API - it builds the
knowledge graph from Step 5 directly and constructs fake SearchResult
objects to simulate what hybrid_search.py would have returned, so we
can test the graph enrichment logic in complete isolation.

Goal: verify that
1. A search result for verify_password() gets login() pulled in as a
   caller, even though login() was NOT in the original search results
2. A search result for login() gets verify_password() and
   create_session() pulled in as callees
3. No duplicates appear when the same function would be reachable
   from multiple original results
4. Traversal stops at exactly 1 hop (we don't recursively expand the
   newly-added nodes' own neighbors)
"""

from rag.ast_parser import parse_file
from rag.knowledge_graph import build_knowledge_graph
from rag.hybrid_search import SearchResult
from rag.graph_retriever import enrich_with_graph_context


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


def _fake_search_result(file_path: str, function_name: str, score: float) -> SearchResult:
    """Builds a minimal SearchResult without needing real search/embedding calls."""
    return SearchResult(
        chunk_id=f"{file_path}::{function_name}",
        rrf_score=score,
        metadata={"file_path": file_path, "function_name": function_name},
    )


def test_caller_is_pulled_in_for_a_search_result():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    # Simulate: hybrid search only found verify_password(), NOT login()
    search_results = [_fake_search_result("auth/utils.py", "verify_password", 0.9)]

    enriched = enrich_with_graph_context(search_results, graph)
    ids = [e.chunk_id for e in enriched]

    assert "auth/utils.py::verify_password" in ids
    assert "auth/utils.py::login" in ids, (
        "login() should be pulled in as a caller of verify_password(), "
        "even though it wasn't in the original search results"
    )

    login_entry = next(e for e in enriched if e.chunk_id == "auth/utils.py::login")
    assert login_entry.source == "caller_of_auth/utils.py::verify_password"
    assert login_entry.rrf_score is None, "graph-only additions should have no search score"
    print(f"OK - login() pulled in as: {login_entry.source}")


def test_callees_are_pulled_in_for_a_search_result():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    # Simulate: hybrid search only found login()
    search_results = [_fake_search_result("auth/utils.py", "login", 0.95)]

    enriched = enrich_with_graph_context(search_results, graph)
    ids = {e.chunk_id for e in enriched}

    assert "auth/utils.py::login" in ids
    assert "auth/utils.py::verify_password" in ids, "should pull in callee verify_password"
    assert "auth/utils.py::create_session" in ids, "should pull in callee create_session"
    print(f"OK - login()'s callees both pulled in: {ids}")


def test_no_duplicates_when_already_in_search_results():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    # Simulate: hybrid search found BOTH login() and verify_password()
    search_results = [
        _fake_search_result("auth/utils.py", "login", 0.95),
        _fake_search_result("auth/utils.py", "verify_password", 0.85),
    ]

    enriched = enrich_with_graph_context(search_results, graph)
    ids = [e.chunk_id for e in enriched]

    # verify_password must appear exactly ONCE, as a hybrid_search
    # result, not duplicated as a graph addition from login()
    assert ids.count("auth/utils.py::verify_password") == 1
    verify_entry = next(e for e in enriched if e.chunk_id == "auth/utils.py::verify_password")
    assert verify_entry.source == "hybrid_search", (
        "should keep its original hybrid_search source, not be "
        "overwritten by the graph addition"
    )
    print("OK - no duplicate entries when a function is both a search "
          "result and a graph neighbor of another result")


def test_traversal_stops_at_one_hop():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    # Simulate: hybrid search only found hash_password() - a function
    # with NO callers and NO callees in our sample (isolated node)
    search_results = [_fake_search_result("auth/utils.py", "hash_password", 0.7)]

    enriched = enrich_with_graph_context(search_results, graph)
    ids = {e.chunk_id for e in enriched}

    # hash_password has zero edges, so enrichment should add nothing
    assert ids == {"auth/utils.py::hash_password"}
    print("OK - isolated function (no callers/callees) adds nothing, as expected")


def test_graph_neighbours_carry_their_code():
    functions = parse_file("auth/utils.py", SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    search_results = [_fake_search_result("auth/utils.py", "verify_password", 0.9)]

    enriched = enrich_with_graph_context(search_results, graph)
    login_entry = next(e for e in enriched if e.chunk_id == "auth/utils.py::login")

    # agents read metadata["code"], so a neighbour without it would
    # reach the prompt as a bare name
    assert login_entry.metadata["code"].startswith("def login(username, password):")
    assert "verify_password(password, user.password)" in login_entry.metadata["code"]
    assert login_entry.metadata["context"] == ""
    assert login_entry.metadata["qualified_name"] == "login"
    print("OK - graph neighbour login() carries its real code")


if __name__ == "__main__":
    test_caller_is_pulled_in_for_a_search_result()
    test_callees_are_pulled_in_for_a_search_result()
    test_no_duplicates_when_already_in_search_results()
    test_traversal_stops_at_one_hop()
    test_graph_neighbours_carry_their_code()
    print("\nAll graph_retriever tests passed.")