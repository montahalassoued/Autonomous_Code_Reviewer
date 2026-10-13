"""
Integration test for agents/security.py

Makes a REAL call to Groq (llama-3.3-70b-versatile) against a diff that
introduces a classic SQL injection vulnerability (string-concatenated
query), to check the agent actually detects security issues rather
than just returning valid-but-empty output.
"""

from dotenv import load_dotenv
load_dotenv()

from agents.security import security_node


SQL_INJECTION_DIFF = """
+ def get_user(username):
+     query = "SELECT * FROM users WHERE username = '" + username + "'"
+     return db.execute(query)
"""


def build_state(diff: str) -> dict:
    """Builds a minimal ReviewState-shaped dict for the node under test."""
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": diff,
        "changed_files": ["auth/login.py"],
        "retrieved_context": [],
        "retrieval_quality_score": 1.0,
        "retrieval_attempts": 1,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "parallel_agents",
    }


def test_security_node_detects_sql_injection():
    state = build_state(SQL_INJECTION_DIFF)

    result = security_node(state)

    assert "findings" in result
    findings = result["findings"]
    print(f"\nDetected {len(findings)} security finding(s):")
    for f in findings:
        print(f"  - [{f['severity']}] {f['title']} ({f['file_path']}:{f['line_number']})")

    assert len(findings) > 0, "Should detect the SQL injection vulnerability"
    assert any(f["severity"] in {"CRITICAL", "HIGH"} for f in findings), (
        "SQL injection should be flagged as CRITICAL or HIGH severity"
    )
    for f in findings:
        assert f["agent"] == "security"
        assert f["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
        assert isinstance(f["line_number"], int)
        assert f["title"]
        assert f["description"]
        assert f["suggestion"]

    print("OK - security agent detected the SQL injection issue with valid Finding shape")


def test_security_node_handles_empty_context():
    """A trivial, safe diff should not crash, even if the LLM returns
    an empty findings array."""
    state = build_state("+ def add(a, b):\n+     return a + b\n")

    result = security_node(state)

    assert "findings" in result
    assert isinstance(result["findings"], list)
    print(f"\nSafe diff produced {len(result['findings'])} finding(s) (expected: few or none)")


if __name__ == "__main__":
    test_security_node_detects_sql_injection()
    test_security_node_handles_empty_context()
    print("\nAll security agent tests passed.")
