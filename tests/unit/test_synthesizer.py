from review.synthesizer import synthesizer_node


# Builds a minimal valid Finding dict for testing.
def make_finding(agent: str, severity: str, title: str = "Issue", evidence: str = "some_code_here()") -> dict:
    return {
        "agent": agent,
        "severity": severity,
        "file_path": "some/file.py",
        "line_number": 10,
        "title": title,
        "description": "Some description.",
        "evidence": evidence,
        "suggestion": "Do this instead.",
    }


# Builds a minimal ReviewState-shaped dict for the node under test.
def build_state(findings: list[dict]) -> dict:
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


# Runs the synthesizer and returns the Markdown comment.
def render(findings: list[dict]) -> str:
    return synthesizer_node(build_state(findings))["final_review_markdown"]


def test_synthesizer_renders_categories_in_fixed_order():
    markdown = render([
        make_finding("quality", "CRITICAL", "Quality issue"),
        make_finding("logic", "HIGH", "Logic issue"),
        make_finding("security", "LOW", "Security issue"),
    ])

    security_pos = markdown.find("### 🔒 Security")
    logic_pos = markdown.find("### 🧠 Logic")
    quality_pos = markdown.find("### ✨ Code quality")

    assert -1 < security_pos < logic_pos < quality_pos, "sections must be Security, Logic, Quality"
    assert security_pos < markdown.find("Security issue") < logic_pos
    assert logic_pos < markdown.find("Logic issue") < quality_pos
    assert quality_pos < markdown.find("Quality issue")
    print("OK - findings rendered under their category, in the fixed order")


def test_synthesizer_sorts_by_severity_inside_a_category():
    markdown = render([
        make_finding("security", "LOW", "Low one"),
        make_finding("security", "CRITICAL", "Critical one"),
        make_finding("security", "MEDIUM", "Medium one"),
    ])

    assert markdown.find("Critical one") < markdown.find("Medium one") < markdown.find("Low one")
    print("OK - CRITICAL > MEDIUM > LOW inside the security section")


def test_synthesizer_shows_empty_categories():
    markdown = render([make_finding("security", "HIGH", "Only finding")])

    assert "### 🧠 Logic (0)" in markdown
    assert "### ✨ Code quality (0)" in markdown
    assert markdown.count("No issues found. ✅") == 2, "logic and quality must each say they found nothing"
    print("OK - empty categories are still shown")


def test_synthesizer_summary_table_counts_per_category():
    markdown = render([
        make_finding("security", "CRITICAL"),
        make_finding("security", "CRITICAL"),
        make_finding("quality", "MEDIUM"),
    ])

    assert "Found **3** issue(s)." in markdown
    assert "| 🔒 Security | 2 | - | - | - | - | 2 |" in markdown
    assert "| 🧠 Logic | - | - | - | - | - | 0 |" in markdown
    assert "| ✨ Code quality | - | - | 1 | - | - | 1 |" in markdown
    print("OK - summary table counts findings per category and severity")


def test_synthesizer_includes_finding_fields():
    markdown = render([make_finding("logic", "HIGH", "Off by one")])

    assert "#### 🟠 HIGH - Off by one" in markdown
    assert "`some/file.py:10`" in markdown
    assert "some_code_here()" in markdown
    assert "**Suggestion:** Do this instead." in markdown
    print("OK - title, location, evidence and suggestion are rendered")


def test_synthesizer_skips_empty_evidence_block():
    markdown = render([make_finding("quality", "LOW", "No snippet", evidence="")])

    assert "```" not in markdown, "an empty evidence field must not render an empty code block"
    print("OK - no empty code block for missing evidence")


def test_synthesizer_keeps_unknown_agent_findings():
    markdown = render([make_finding("performance", "MEDIUM", "Legacy agent finding")])

    assert "### 📎 Other (1)" in markdown
    assert "Legacy agent finding" in markdown
    print("OK - a finding from an unknown agent is shown under Other, not dropped")


def test_synthesizer_handles_no_findings():
    markdown = render([])

    assert "No issues found" in markdown
    print("OK - empty findings list produces a clean 'no issues' message")


if __name__ == "__main__":
    test_synthesizer_renders_categories_in_fixed_order()
    test_synthesizer_sorts_by_severity_inside_a_category()
    test_synthesizer_shows_empty_categories()
    test_synthesizer_summary_table_counts_per_category()
    test_synthesizer_includes_finding_fields()
    test_synthesizer_skips_empty_evidence_block()
    test_synthesizer_keeps_unknown_agent_findings()
    test_synthesizer_handles_no_findings()
    print("\nAll synthesizer tests passed.")
