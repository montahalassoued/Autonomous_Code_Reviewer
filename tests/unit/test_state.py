"""
Manual test for agents/state.py

Goal: verify that operator.add correctly merges findings from multiple
agents instead of overwriting them. This is the critical behavior that
the whole parallel execution of the project depends on.
"""

import operator
from agents.state import ReviewState, Finding


def test_finding_structure():
    """A well-formed Finding must match the expected structure exactly."""
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


def test_operator_add_merge_findings():
    """
    Simulates what LangGraph does when 2 agents write to findings
    in parallel: it applies operator.add on both updates.
    """
    security_findings: list[Finding] = [
        {
            "agent": "security", "severity": "CRITICAL", "file_path": "auth/login.py",
            "line_number": 2, "title": "SQL Injection", "description": "...",
            "evidence": "...", "suggestion": "...",
        }
    ]
    performance_findings: list[Finding] = [
        {
            "agent": "performance", "severity": "MEDIUM", "file_path": "auth/login.py",
            "line_number": 10, "title": "N+1 query", "description": "...",
            "evidence": "...", "suggestion": "...",
        }
    ]

    # This is exactly LangGraph's internal mechanism for a field
    # typed as Annotated[list[Finding], operator.add]
    merged = operator.add(security_findings, performance_findings)

    assert len(merged) == 2, "Both findings must survive, not overwrite each other"
    assert merged[0]["agent"] == "security"
    assert merged[1]["agent"] == "performance"
    print(f"OK - operator.add merges correctly: {len(merged)} findings kept")


def test_full_state_initialization():
    """Verify that a complete, consistent ReviewState can be initialized."""
    state: ReviewState = {
        "pr_number": 42,
        "repo_full_name": "Montaha/test-repo",
        "pr_diff": "+ def login(username, password): ...",
        "changed_files": ["auth/login.py"],
        "retrieved_context": [],
        "retrieval_quality_score": 0.0,
        "retrieval_attempts": 0,
        "findings": [],
        "final_review_markdown": "",
        "next_step": "retriever",
    }
    assert state["next_step"] == "retriever"
    print("OK - ReviewState initializes correctly")


if __name__ == "__main__":
    test_finding_structure()
    test_operator_add_merge_findings()
    test_full_state_initialization()
    print("\nAll tests passed.")
