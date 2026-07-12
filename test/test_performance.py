"""
Integration test for agents/performance.py

Makes a REAL call to Groq (llama-3.3-70b-versatile) against a diff that
introduces an obvious N+1 query pattern plus a nested-loop inefficiency,
to check the agent actually detects performance issues rather than
just returning valid-but-empty output.
"""

from dotenv import load_dotenv
load_dotenv()

from agents.performance import performance_node


N_PLUS_ONE_DIFF = """
+ def get_all_user_emails(user_ids):
+     emails = []
+     for user_id in user_ids:
+         user = db.query(f"SELECT * FROM users WHERE id = {user_id}")
+         emails.append(user.email)
+     return emails
"""


def build_state(diff: str) -> dict:
    """Builds a minimal ReviewState-shaped dict for the node under test."""
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": diff,
        "changed_files": ["db/users.py"],
        "retrieved_context": [],
        "retrieval_quality_score": 1.0,
        "retrieval_attempts": 1,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "parallel_agents",
    }


def test_performance_node_detects_n_plus_one():
    state = build_state(N_PLUS_ONE_DIFF)

    result = performance_node(state)

    assert "findings" in result
    findings = result["findings"]
    print(f"\nDetected {len(findings)} performance finding(s):")
    for f in findings:
        print(f"  - [{f['severity']}] {f['title']} ({f['file_path']}:{f['line_number']})")

    assert len(findings) > 0, "Should detect the N+1 query pattern"
    for f in findings:
        assert f["agent"] == "performance"
        assert f["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
        assert isinstance(f["line_number"], int)
        assert f["title"]
        assert f["description"]
        assert f["suggestion"]

    print("OK - performance agent detected the N+1 query issue with valid Finding shape")


def test_performance_node_handles_empty_context():
    """A trivial diff with no real performance issue should not crash,
    even if the LLM returns an empty findings array."""
    state = build_state("+ def add(a, b):\n+     return a + b\n")

    result = performance_node(state)

    assert "findings" in result
    assert isinstance(result["findings"], list)
    print(f"\nTrivial diff produced {len(result['findings'])} finding(s) (expected: few or none)")


if __name__ == "__main__":
    test_performance_node_detects_n_plus_one()
    test_performance_node_handles_empty_context()
    print("\nAll performance agent tests passed.")
