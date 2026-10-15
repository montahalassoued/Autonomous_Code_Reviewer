import pytest

from rag.ast_parser import parse_file
from rag.knowledge_graph import build_knowledge_graph
from review.agents.logic.agent import LOGIC_AGENT
from review.agents.logic.tools.find_call_sites import find_call_sites
from review.agents.logic.tools.find_return_values import find_return_values
from review.agents.tools.tool_definition import ToolContext


USERS_SOURCE = """class UserRepository:
    @staticmethod
    def find(users, name):
        for user in users:
            if user.name == name:
                return user
        return None

    def load(self, path):
        if path:
            return open(path).read()


def first_name(user):
    def fallback():
        return "anonymous"
    if user.name:
        return user.name.split()[0]
    else:
        return fallback()


def names(users):
    for user in users:
        yield user.name


def log(message):
    print(message)
"""

ORDERS_SOURCE = "def create_order(user, items, discount=0):\n    return Order(user, items, discount)\n"

API_SOURCE = (
    "from app.orders import create_order\n\n\ndef checkout(cart):\n"
    "    order = create_order(cart.user, cart.items)\n"
    "    service.create_order(cart.user)\n"
    "    return recreate_order(cart)\n"
)


@pytest.fixture
def graph():
    return ToolContext(code_graph=build_knowledge_graph(parse_file("app/users.py", USERS_SOURCE)))


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "orders.py").write_text(ORDERS_SOURCE, encoding="utf-8")
    (tmp_path / "app" / "api.py").write_text(API_SOURCE, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_orders.py").write_text("def test_create():\n    create_order(user, [])\n", encoding="utf-8")
    return ToolContext(repo_root=tmp_path)


def test_find_return_values_marks_explicit_none_in_a_decorated_method(graph):
    output = find_return_values(graph, {"name": "find"})

    assert output.splitlines() == [
        "- UserRepository.find (app/users.py:2)",
        "  6: return user",
        "  7: return None  <- returns None",
        "  every path ends in a return or a raise",
    ]
    print("OK - return lines keep their file line numbers under a decorator, and None is marked")


def test_find_return_values_flags_an_implicit_none(graph):
    load = find_return_values(graph, {"name": "UserRepository.load"})
    log = find_return_values(graph, {"name": "log"})

    assert load.splitlines() == [
        "- UserRepository.load (app/users.py:9)",
        "  11: return open(path).read()",
        "  can reach its end without a return, so it returns None there",
    ]
    assert log.endswith("no return statement, so it always returns None")
    print("OK - falling off the end of a function is reported as a None path")


def test_find_return_values_skips_nested_functions_and_generators(graph):
    first_name = find_return_values(graph, {"name": "first_name"})
    names = find_return_values(graph, {"name": "names"})

    assert "16:" not in first_name, "the nested fallback's return is not first_name's"
    assert "  18: return user.name.split()[0]" in first_name
    assert first_name.endswith("every path ends in a return or a raise")
    assert names.endswith("a generator: it yields values, and a return only ends the iteration")
    print("OK - nested functions do not count, and a generator is not reported as returning None")


def test_find_return_values_reports_an_unknown_function(graph):
    assert find_return_values(graph, {"name": "missing"}) == "No function named 'missing' in the call graph."
    assert "No function named" in find_return_values(graph, {"name": "find", "file_path": "app/other.py"})
    print("OK - an unknown name or file gives a clear message, not an error")


def test_find_call_sites_lists_calls_with_their_arguments(repo):
    output = find_call_sites(repo, {"name": "create_order"})

    assert output.splitlines() == [
        "app/api.py:5: order = create_order(cart.user, cart.items)",
        "app/api.py:6: service.create_order(cart.user)",
    ], "no import line, no def line, no recreate_order( and no test file"
    print("OK - the logic agent sees every production call of a function, method calls included")


def test_find_call_sites_takes_qualified_names_and_rejects_short_ones(repo):
    assert find_call_sites(repo, {"name": "OrderService.create_order"}) == find_call_sites(repo, {"name": "create_order"})
    assert find_call_sites(repo, {"name": "go"}) == "Name must be at least 3 characters."
    no_hits = find_call_sites(repo, {"name": "create_order", "path_prefix": "tests"})
    assert no_hits == "No call to 'create_order(' found outside tests."
    print("OK - Class.method is searched as method(, and a too-short name is refused")


def test_logic_toolset_offers_the_new_tools_with_their_resource():
    tools = {tool.name: tool for tool in LOGIC_AGENT.tools}

    assert tools["find_return_values"].requires == "graph"
    assert tools["find_call_sites"].requires == "repo"
    print("OK - find_return_values needs the call graph, find_call_sites only the checkout")


if __name__ == "__main__":
    print("Run under pytest: these tests use fixtures.")
