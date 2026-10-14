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
def build_state(findings: list[dict], diff: str = "+ def f(): pass") -> dict:
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": diff,
        "changed_files": ["some/file.py"],
        "retrieved_context": [],
        "retrieval_quality_score": 1.0,
        "retrieval_attempts": 1,
        "findings": findings,
        "final_review_markdown": "",
        "next_step": "synthesizer",
    }


# Runs the synthesizer and returns the Markdown comment.
def render(findings: list[dict], diff: str = "+ def f(): pass") -> str:
    return synthesizer_node(build_state(findings, diff))["final_review_markdown"]


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

    assert "⛔ **2 blocking issue(s)** to fix before merging, out of 3 finding(s)." in markdown
    assert "| 🔒 Security | 2 | - | - | - | - | 2 |" in markdown
    assert "| 🧠 Logic | - | - | - | - | - | 0 |" in markdown
    assert "| ✨ Code quality | - | - | 1 | - | - | 1 |" in markdown
    print("OK - summary table counts findings per category and severity")


def test_synthesizer_includes_finding_fields():
    markdown = render([make_finding("logic", "HIGH", "Off by one")])

    assert "#### 🟠 [blocking] HIGH - Off by one" in markdown
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


def test_synthesizer_labels_each_severity():
    markdown = render([
        make_finding("security", "CRITICAL", "Crit"),
        make_finding("logic", "MEDIUM", "Med"),
        make_finding("quality", "INFO", "Info"),
    ])

    assert "[blocking] CRITICAL - Crit" in markdown
    assert "[suggestion] MEDIUM - Med" in markdown
    assert "[nit] INFO - Info" in markdown
    print("OK - CRITICAL/HIGH are blocking, MEDIUM a suggestion, LOW/INFO nits")


def test_synthesizer_low_confidence_never_blocks():
    finding = {**make_finding("security", "CRITICAL", "Maybe unprotected"), "confidence": "low"}
    markdown = render([finding])

    assert "[suggestion] CRITICAL - Maybe unprotected" in markdown
    assert "**Confidence:** low" in markdown
    assert "Nothing blocking" in markdown
    print("OK - a low-confidence CRITICAL is a suggestion, not a blocker")


def test_synthesizer_verdict_when_nothing_blocks():
    markdown = render([make_finding("quality", "MEDIUM"), make_finding("quality", "LOW")])

    assert "✅ **Nothing blocking.** 2 suggestion(s) or nit(s) below." in markdown
    assert "⛔" not in markdown
    print("OK - no CRITICAL/HIGH finding gives a 'nothing blocking' verdict")


def test_synthesizer_folds_nits_under_details():
    markdown = render([make_finding("quality", "MEDIUM", "Real issue"), make_finding("quality", "LOW", "Tiny nit")])

    details_pos = markdown.find("<details>\n<summary>1 nit(s)</summary>\n\n")
    assert details_pos != -1, "nits must be folded in a <details> block with a blank line after <summary>"
    assert markdown.find("Real issue") < details_pos < markdown.find("Tiny nit")
    assert markdown.rstrip().endswith("</details>")
    print("OK - nits are folded below the real issues")


def test_synthesizer_flags_large_prs_only():
    large_diff = "\n".join(["+++ b/big.py"] + [f"+line {i}" for i in range(450)])
    small_diff = "\n".join(["--- a/small.py", "+++ b/small.py"] + [f"-old {i}" for i in range(200)] + [f"+new {i}" for i in range(200)])

    assert "📏 **Large PR:** 450 changed lines." in render([make_finding("security", "HIGH")], large_diff)
    assert "📏 **Large PR:** 450 changed lines." in render([], large_diff), "the note also shows on a clean review"
    assert "Large PR" not in render([make_finding("security", "HIGH")], small_diff), "400 changed lines is not large"
    print("OK - only diffs above 400 changed lines get the size note; file headers don't count")


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
    test_synthesizer_labels_each_severity()
    test_synthesizer_low_confidence_never_blocks()
    test_synthesizer_verdict_when_nothing_blocks()
    test_synthesizer_folds_nits_under_details()
    test_synthesizer_flags_large_prs_only()
    test_synthesizer_handles_no_findings()
    print("\nAll synthesizer tests passed.")
