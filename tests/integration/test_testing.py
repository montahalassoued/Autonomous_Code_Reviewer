"""
Integration test for agents/testing.py

Makes a REAL call to Groq (llama-3.3-70b-versatile) against a diff that
introduces a new function with an untested edge case (division by
zero) and no accompanying test, to check the agent actually detects
coverage gaps rather than just returning valid-but-empty output.
"""

from dotenv import load_dotenv
load_dotenv()

from agents.testing import testing_node


UNTESTED_DIFF = """
+ def calculate_average(values):
+     total = sum(values)
+     return total / len(values)
"""


def build_state(diff: str) -> dict:
    """Builds a minimal ReviewState-shaped dict for the node under test."""
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": diff,
        "changed_files": ["stats/average.py"],
        "retrieved_context": [],
        "retrieval_quality_score": 1.0,
        "retrieval_attempts": 1,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "parallel_agents",
    }


def test_testing_node_detects_missing_coverage():
    state = build_state(UNTESTED_DIFF)

    result = testing_node(state)

    assert "findings" in result
    findings = result["findings"]
    print(f"\nDetected {len(findings)} testing finding(s):")
    for f in findings:
        print(f"  - [{f['severity']}] {f['title']} ({f['file_path']}:{f['line_number']})")

    assert len(findings) > 0, "Should detect missing test coverage / untested edge case"
    for f in findings:
        assert f["agent"] == "testing"
        assert f["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
        assert isinstance(f["line_number"], int)
        assert f["title"]
        assert f["description"]
        assert f["suggestion"]

    print("OK - testing agent detected the missing coverage issue with valid Finding shape")


def test_testing_node_handles_empty_context():
    """A trivial diff with no real testing issue should not crash,
    even if the LLM returns an empty findings array."""
    state = build_state("+ def add(a, b):\n+     return a + b\n")

    result = testing_node(state)

    assert "findings" in result
    assert isinstance(result["findings"], list)
    print(f"\nTrivial diff produced {len(result['findings'])} finding(s) (expected: few or none)")


if __name__ == "__main__":
    test_testing_node_detects_missing_coverage()
    test_testing_node_handles_empty_context()
    print("\nAll testing agent tests passed.")
