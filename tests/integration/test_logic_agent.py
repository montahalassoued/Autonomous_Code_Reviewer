"""
Integration test for review/agents/logic_agent.py

Makes a REAL call to Groq (REVIEW_AGENT_MODEL, see review/agents/agent_runner.py)
against a diff with a classic off-by-one bug, to check the agent actually
detects logic bugs rather than just returning valid-but-empty output.
Uses Groq quota: run it by hand, not in CI.
"""

from dotenv import load_dotenv
load_dotenv()

from review.agents.logic_agent import logic_node  # noqa: E402 - must load .env before the agent reads its keys


OFF_BY_ONE_DIFF = """
+ def last_items(items, count):
+     \"\"\"Returns the last `count` items of the list.\"\"\"
+     result = []
+     for index in range(len(items) - count + 1, len(items)):
+         result.append(items[index])
+     return result
"""


def build_state(diff: str) -> dict:
    """Builds a minimal ReviewState-shaped dict for the node under test."""
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": diff,
        "changed_files": ["lists/slicing.py"],
        "retrieved_context": [],
        "retrieval_quality_score": 1.0,
        "retrieval_attempts": 1,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "parallel_agents",
    }


def test_logic_node_detects_off_by_one():
    result = logic_node(build_state(OFF_BY_ONE_DIFF))

    findings = result["findings"]
    print(f"\nDetected {len(findings)} logic finding(s):")
    for f in findings:
        print(f"  - [{f['severity']}] {f['title']} ({f['file_path']}:{f['line_number']})")

    assert len(findings) > 0, "Should detect the off-by-one in the range start"
    text = " ".join(f"{f['title']} {f['description']}".lower() for f in findings)
    assert any(word in text for word in ("off-by-one", "off by one", "range", "skip", "count - 1", "one fewer")), (
        "Should explain that the loop returns one item too few"
    )
    for f in findings:
        assert f["agent"] == "logic"
        assert isinstance(f["line_number"], int)
        assert f["suggestion"]
    print("OK - logic agent detected the off-by-one bug")


def test_logic_node_handles_safe_diff():
    """A trivial, correct diff should not crash, even if the LLM returns []."""
    result = logic_node(build_state("+ def add(a, b):\n+     return a + b\n"))

    assert isinstance(result["findings"], list)
    print(f"\nSafe diff produced {len(result['findings'])} finding(s) (expected: few or none)")


if __name__ == "__main__":
    test_logic_node_detects_off_by_one()
    test_logic_node_handles_safe_diff()
    print("\nAll logic agent tests passed.")
