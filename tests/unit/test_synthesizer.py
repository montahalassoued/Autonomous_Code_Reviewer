"""
Unit test for agents/synthesizer.py

No LLM calls needed - synthesizer_node is pure formatting logic over
findings already present in state, so this test is fast and fully
deterministic.
"""

from agents.synthesizer import synthesizer_node


def make_finding(agent: str, severity: str, title: str = "Issue") -> dict:
    """Builds a minimal valid Finding dict for testing."""
    return {
        "agent": agent,
        "severity": severity,
        "file_path": "some/file.py",
        "line_number": 10,
        "title": title,
        "description": "Some description.",
        "evidence": "some_code_here()",
        "suggestion": "Do this instead.",
    }


def build_state(findings: list[dict]) -> dict:
    """Builds a minimal ReviewState-shaped dict for the node under test."""
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": "+ def f(): pass",
        "changed_files": ["some/file.py"],
        "retrieved_context": [],
        "retrieval_quality_score": 1.0,
        "retrieval_attempts": 1,
        "findings": findings,
        "final_review_markdown": "",
        "next_step": "synthesizer",
    }


def test_synthesizer_sorts_by_severity():
    findings = [
        make_finding("documentation", "LOW", "Missing docstring"),
        make_finding("security", "CRITICAL", "SQL Injection"),
        make_finding("performance", "MEDIUM", "N+1 query"),
        make_finding("architecture", "HIGH", "God class"),
    ]
    state = build_state(findings)

    result = synthesizer_node(state)
    markdown = result["final_review_markdown"]

    # CRITICAL must appear before HIGH, HIGH before MEDIUM, MEDIUM before LOW
    critical_pos = markdown.find("SQL Injection")
    high_pos = markdown.find("God class")
    medium_pos = markdown.find("N+1 query")
    low_pos = markdown.find("Missing docstring")

    assert critical_pos < high_pos < medium_pos < low_pos
    print("OK - findings sorted CRITICAL > HIGH > MEDIUM > LOW")


def test_synthesizer_includes_all_findings():
    findings = [
        make_finding("security", "CRITICAL", "Issue A"),
        make_finding("performance", "LOW", "Issue B"),
    ]
    state = build_state(findings)

    result = synthesizer_node(state)
    markdown = result["final_review_markdown"]

    assert "Issue A" in markdown
    assert "Issue B" in markdown
    assert "some/file.py:10" in markdown
    assert "Do this instead." in markdown
    print("OK - all findings and their fields present in output")


def test_synthesizer_handles_no_findings():
    state = build_state([])

    result = synthesizer_node(state)
    markdown = result["final_review_markdown"]

    assert "No issues found" in markdown
    print("OK - empty findings list produces a clean 'no issues' message")


def test_synthesizer_summary_counts():
    findings = [
        make_finding("security", "CRITICAL"),
        make_finding("security", "CRITICAL"),
        make_finding("performance", "MEDIUM"),
    ]
    state = build_state(findings)

    result = synthesizer_node(state)
    markdown = result["final_review_markdown"]

    assert "**CRITICAL:** 2" in markdown
    assert "**MEDIUM:** 1" in markdown
    assert "**HIGH:**" not in markdown  # zero-count severities omitted
    print("OK - summary counts correct and zero-count severities omitted")


if __name__ == "__main__":
    test_synthesizer_sorts_by_severity()
    test_synthesizer_includes_all_findings()
    test_synthesizer_handles_no_findings()
    test_synthesizer_summary_counts()
    print("\nAll synthesizer tests passed.")
