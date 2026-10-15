import pytest

from review.agents.quality.agent import QUALITY_AGENT
from review.agents.quality.tools.find_blocking_calls_in_async import find_blocking_calls_in_async
from review.agents.quality.tools.find_loop_queries import find_loop_queries
from review.agents.tools.repo_files import MAX_SEARCH_HITS
from review.agents.tools.tool_definition import ToolContext, execute_tool_call


ORDERS_SOURCE = """from app.db import Customer, Order, session


def order_rows(order_ids):
    rows = []
    for order in session.query(Order).filter(Order.id.in_(order_ids)):
        customer = session.get(Customer, order.customer_id)
        rows.append((order.id, customer.name))
    return rows


def customer_names(ids):
    names = [session.get(Customer, i).name for i in ids]
    return names + [c.name for c in session.query(Customer).all()]
"""

CART_SOURCE = """def cart_total(items):
    total = 0
    for item in items:
        total += budget_price(item)
    return total + get_price(items[0])
"""

PROFILES_SOURCE = """import asyncio
import time

import requests


async def load_profile(session, user_id):
    response = requests.get(f"https://api.example.com/users/{user_id}", timeout=5)
    rows = await session.execute(QUERY, {"id": user_id})
    avatar = await asyncio.to_thread(requests.get, response.json()["avatar"], timeout=5)

    def parse(payload):
        time.sleep(0.1)
        return payload

    time.sleep(1)
    return parse(rows), avatar


def sync_report():
    return requests.get("https://api.example.com/report", timeout=5).json()
"""

SYNC_DRIVER_SOURCE = """async def count_users(connection):
    cursor = connection.cursor()
    cursor.execute("SELECT count(*) FROM users")
    return cursor.fetchone()[0]
"""


@pytest.fixture
def repo(tmp_path):
    for relative, source in (
        ("app/services/orders.py", ORDERS_SOURCE),
        ("app/cart.py", CART_SOURCE),
        ("app/api/profiles.py", PROFILES_SOURCE),
        ("app/db_async.py", SYNC_DRIVER_SOURCE),
        ("tests/test_orders.py", ORDERS_SOURCE),
        ("venv/lib/orders.py", ORDERS_SOURCE),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ToolContext(repo_root=tmp_path)


def test_find_loop_queries_flags_per_item_queries_not_the_loop_iterable(repo):
    output = find_loop_queries(repo, {})

    assert output.splitlines() == [
        "app/services/orders.py:7: customer = session.get(Customer, order.customer_id)  (loop at line 6)",
        "app/services/orders.py:13: names = [session.get(Customer, i).name for i in ids]  (loop at line 13)",
    ], "the query in a loop header or a comprehension's iterable runs once; tests/ and venv/ are skipped"
    print("OK - find_loop_queries shows queries run once per item, with the loop's line")


def test_find_loop_queries_finds_loops_that_call_a_helper(repo):
    output = find_loop_queries(repo, {"call": "budget_price"})

    assert output == "app/cart.py:4: total += budget_price(item)  (loop at line 3)"
    assert find_loop_queries(repo, {"call": "get_price"}) == "No call to 'get_price' inside a loop.", (
        "get_price is called outside the loop, and budget_price( must not match get_price("
    )
    assert "at least" in find_loop_queries(repo, {"call": "gp"})
    print("OK - find_loop_queries finds an N+1 hidden behind a helper called in a loop")


def test_find_loop_queries_respects_the_path_prefix(repo):
    assert find_loop_queries(repo, {"path_prefix": "app/cart.py"}) == "No query or HTTP call found inside a loop."
    assert find_loop_queries(repo, {"path_prefix": "tests"}) == "No query or HTTP call found inside a loop."
    print("OK - find_loop_queries scans only the given file or folder, never tests")


def test_find_blocking_calls_in_async_skips_awaited_offloaded_and_sync_code(repo):
    output = find_blocking_calls_in_async(repo, {"path_prefix": "app/api"})

    assert output.splitlines() == [
        'app/api/profiles.py:8: response = requests.get(f"https://api.example.com/users/{user_id}", timeout=5)'
        "  (in async def at line 7)",
        "app/api/profiles.py:16: time.sleep(1)  (in async def at line 7)",
    ], "awaited execute, to_thread, a nested plain def and a sync def are not blocking the event loop"
    print("OK - find_blocking_calls_in_async shows only calls that stall the event loop")


def test_find_blocking_calls_in_async_flags_a_sync_database_driver(repo):
    output = find_blocking_calls_in_async(repo, {"path_prefix": "app/db_async.py"})

    assert output.splitlines() == [
        'app/db_async.py:3: cursor.execute("SELECT count(*) FROM users")  (in async def at line 1)',
        "app/db_async.py:4: return cursor.fetchone()[0]  (in async def at line 1)",
    ]
    assert find_blocking_calls_in_async(repo, {"path_prefix": "app/cart.py"}) == "No blocking call found inside an async def."
    print("OK - a non-awaited execute or fetch inside async def is reported")


def test_loop_scan_is_capped(tmp_path):
    (tmp_path / "bulk.py").write_text("def f(ids):\n    for i in ids:\n" + "        session.get(X, i)\n" * 50, encoding="utf-8")

    lines = find_loop_queries(ToolContext(repo_root=tmp_path), {}).splitlines()

    assert len(lines) == MAX_SEARCH_HITS + 1 and lines[-1].startswith(f"... stopped after {MAX_SEARCH_HITS} hits")
    print(f"OK - at most {MAX_SEARCH_HITS} hits, so the tool output stays small")


def test_quality_tools_are_registered_and_fail_safely_without_a_checkout():
    tools = {tool.name: tool for tool in QUALITY_AGENT.tools}

    for name in ("find_loop_queries", "find_blocking_calls_in_async"):
        assert name in tools, f"quality is missing {name}"
        output = execute_tool_call(tools, name, "{}", ToolContext())
        assert "no repository checkout" in output and output.startswith("<<<TOOL_OUTPUT ")
    print("OK - both tools are offered to the quality agent and never raise without a repo")
