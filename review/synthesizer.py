from review.state import ReviewState, Finding


SEVERITY_ORDER = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "INFO": 4,
}

SEVERITY_EMOJI = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🔵",
    "INFO": "⚪",
}

# Render order and headings of the review categories. The keys are the
# agent names in review/graph.py's REVIEW_AGENTS.
CATEGORY_TITLES = {
    "security": "🔒 Security",
    "logic": "🧠 Logic",
    "quality": "✨ Code quality",
}
OTHER_CATEGORY_TITLE = "📎 Other"

REVIEW_HEADING = "## 🤖 Autonomous Code Review"


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    """
    Sorts findings by severity, CRITICAL first and INFO last.

    Args:
        findings: findings in any order

    Returns:
        a new list sorted by severity; ties keep their original order
        (Python's sort is stable)
    """
    return sorted(findings, key=lambda f: SEVERITY_ORDER.get(f["severity"], len(SEVERITY_ORDER)))


def _group_by_category(findings: list[Finding]) -> dict[str, list[Finding]]:
    """
    Groups findings by agent, in CATEGORY_TITLES order, with any unknown
    agent's findings collected under "other".

    Args:
        findings: all findings from every agent

    Returns:
        {category: findings sorted by severity}, with every known
        category present (possibly empty) and "other" only if needed
    """
    groups: dict[str, list[Finding]] = {category: [] for category in CATEGORY_TITLES}
    for finding in findings:
        category = finding["agent"] if finding["agent"] in CATEGORY_TITLES else "other"
        groups.setdefault(category, []).append(finding)
    return {category: _sort_findings(group) for category, group in groups.items()}


def _category_title(category: str) -> str:
    """
    Returns the section heading text for a category.

    Args:
        category: a category key ("security", "logic", "quality" or "other")

    Returns:
        the heading from CATEGORY_TITLES, or the "Other" heading for an unknown key
    """
    return CATEGORY_TITLES.get(category, OTHER_CATEGORY_TITLE)


def _format_summary_table(groups: dict[str, list[Finding]]) -> str:
    """
    Builds the at-a-glance table: one row per category, one column per
    severity, plus a total.

    Args:
        groups: the output of _group_by_category

    Returns:
        a Markdown table; zero counts are shown as "-" so the real
        numbers stand out
    """
    header = "| Category | " + " | ".join(f"{SEVERITY_EMOJI[s]} {s.title()}" for s in SEVERITY_ORDER) + " | Total |"
    separator = "| --- |" + " --- |" * (len(SEVERITY_ORDER) + 1)
    rows = [header, separator]
    for category, group in groups.items():
        counts = [sum(1 for f in group if f["severity"] == severity) for severity in SEVERITY_ORDER]
        cells = [str(count) if count else "-" for count in counts]
        rows.append(f"| {_category_title(category)} | " + " | ".join(cells) + f" | {len(group)} |")
    return "\n".join(rows)


def _format_finding(finding: Finding) -> str:
    """
    Renders one Finding as a Markdown block.

    Args:
        finding: a single Finding to render

    Returns:
        the finding's heading (severity + title), location, description,
        evidence (if any) and suggestion
    """
    emoji = SEVERITY_EMOJI.get(finding["severity"], "⚪")
    lines = [
        f"#### {emoji} {finding['severity']} - {finding['title']}",
        f"**Location:** `{finding['file_path']}:{finding['line_number']}`",
        "",
        finding["description"],
    ]
    if finding["evidence"].strip():
        lines += ["", "```", finding["evidence"], "```"]
    lines += ["", f"**Suggestion:** {finding['suggestion']}"]
    return "\n".join(lines)


def _format_category_section(category: str, findings: list[Finding]) -> str:
    """
    Renders one category's section: its heading with a count, then its
    findings, or a "No issues found" line when it has none.

    Args:
        category: the category key ("security", "logic", "quality", "other")
        findings: that category's findings, already sorted by severity

    Returns:
        the section's Markdown
    """
    heading = f"### {_category_title(category)} ({len(findings)})"
    if not findings:
        return f"{heading}\n\nNo issues found. ✅"
    blocks = "\n\n---\n\n".join(_format_finding(finding) for finding in findings)
    return f"{heading}\n\n{blocks}"


def synthesizer_node(state: ReviewState) -> dict:
    """
    The LangGraph node for the "synthesizer" (see review/graph.py).

    Args:
        state: the current ReviewState, reads findings (all agents' output, merged)

    Returns:
        {"final_review_markdown": "..."}: the complete comment for the PR
    """
    findings = state["findings"]

    if not findings:
        return {
            "final_review_markdown": (
                f"{REVIEW_HEADING}\n\n"
                "No issues found by the security, logic and code quality reviews. Nice work! ✅\n"
            )
        }

    groups = _group_by_category(findings)
    sections = [
        REVIEW_HEADING,
        f"Found **{len(findings)}** issue(s).",
        _format_summary_table(groups),
    ]
    sections += [_format_category_section(category, group) for category, group in groups.items()]

    return {"final_review_markdown": "\n\n".join(sections) + "\n"}
