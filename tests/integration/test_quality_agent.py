from dotenv import load_dotenv
load_dotenv()

from review.agents.quality.agent import quality_node  # noqa: E402 - must load .env before the agent reads its keys


N_PLUS_ONE_DIFF = """
+ def list_order_totals(session, customer_ids):
+     totals = {}
+     for customer_id in customer_ids:
+         orders = session.query(Order).filter(Order.customer_id == customer_id).all()
+         totals[customer_id] = sum(order.amount for order in orders)
+     return totals
"""


# Builds a minimal ReviewState-shaped dict for the node under test.
def build_state(diff: str) -> dict:
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": diff,
        "changed_files": ["billing/reports.py"],
        "retrieved_context": [],
        "retrieval_quality_score": 1.0,
        "retrieval_attempts": 1,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "parallel_agents",
    }


def test_quality_node_detects_n_plus_one():
    result = quality_node(build_state(N_PLUS_ONE_DIFF))

    findings = result["findings"]
    print(f"\nDetected {len(findings)} quality finding(s):")
    for f in findings:
        print(f"  - [{f['severity']}] {f['title']} ({f['file_path']}:{f['line_number']})")

    text = " ".join(f"{f['title']} {f['description']}".lower() for f in findings)
    assert any(word in text for word in ("n+1", "n + 1", "query inside", "per customer", "in a loop", "each iteration")), (
        "Should report the query executed once per customer"
    )
    for f in findings:
        assert f["agent"] == "quality"
        assert isinstance(f["line_number"], int)
        assert f["suggestion"]
    print("OK - quality agent detected the N+1 query")


# A trivial diff should not crash, even if the LLM returns [].
def test_quality_node_handles_safe_diff():
    result = quality_node(build_state("+ def add(a: int, b: int) -> int:\n+     return a + b\n"))

    assert isinstance(result["findings"], list)
    print(f"\nSafe diff produced {len(result['findings'])} finding(s) (expected: few or none)")


if __name__ == "__main__":
    test_quality_node_detects_n_plus_one()
    test_quality_node_handles_safe_diff()
    print("\nAll quality agent tests passed.")
