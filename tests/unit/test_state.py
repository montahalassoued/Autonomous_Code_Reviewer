import operator
from review.state import ReviewState, Finding


# A well-formed Finding must match the expected structure exactly.
def test_finding_structure():
    finding: Finding = {
        "agent": "security",
        "severity": "CRITICAL",
        "file_path": "auth/login.py",
        "line_number": 2,
        "title": "SQL Injection",
        "description": "Username is concatenated directly into the SQL query.",
        "evidence": "query = \"SELECT * FROM users WHERE username='\" + username",
        "suggestion": "Use a parameterized query instead.",
    }
    assert finding["severity"] == "CRITICAL"
    print("OK - Finding structure is valid")


# operator.add must keep findings from two agents writing in parallel.
def test_operator_add_merge_findings():
    security_findings: list[Finding] = [
        {
            "agent": "security", "severity": "CRITICAL", "file_path": "auth/login.py",
            "line_number": 2, "title": "SQL Injection", "description": "...",
            "evidence": "...", "suggestion": "...",
        }
    ]
    quality_findings: list[Finding] = [
        {
            "agent": "quality", "severity": "MEDIUM", "file_path": "auth/login.py",
            "line_number": 10, "title": "N+1 query", "description": "...",
            "evidence": "...", "suggestion": "...",
        }
    ]

    # This is exactly LangGraph's internal mechanism for a field
    # typed as Annotated[list[Finding], operator.add]
    merged = operator.add(security_findings, quality_findings)

    assert len(merged) == 2, "Both findings must survive, not overwrite each other"
    assert merged[0]["agent"] == "security"
    assert merged[1]["agent"] == "quality"
    print(f"OK - operator.add merges correctly: {len(merged)} findings kept")


# Verify that a complete, consistent ReviewState can be initialized.
def test_full_state_initialization():
    state: ReviewState = {
        "pr_number": 42,
        "repo_full_name": "Montaha/test-repo",
        "pr_diff": "+ def login(username, password): ...",
        "changed_files": ["auth/login.py"],
        "review_context": "",
        "findings": [],
        "final_review_markdown": "",
    }
    assert set(state) == set(ReviewState.__annotations__), "the test state covers every ReviewState field"
    print("OK - ReviewState initializes correctly")


if __name__ == "__main__":
    test_finding_structure()
    test_operator_add_merge_findings()
    test_full_state_initialization()
    print("\nAll tests passed.")
