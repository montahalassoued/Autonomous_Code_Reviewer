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


# Sorts findings by severity, CRITICAL first and INFO last.
def _sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: SEVERITY_ORDER.get(f["severity"], len(SEVERITY_ORDER)))


# Findings grouped by category, in CATEGORY_TITLES order; unknown agents go to 'other'.
def _group_by_category(findings: list[Finding]) -> dict[str, list[Finding]]:
    groups: dict[str, list[Finding]] = {category: [] for category in CATEGORY_TITLES}
    for finding in findings:
        category = finding["agent"] if finding["agent"] in CATEGORY_TITLES else "other"
        groups.setdefault(category, []).append(finding)
    return {category: _sort_findings(group) for category, group in groups.items()}


# Returns the section heading text for a category.
def _category_title(category: str) -> str:
    return CATEGORY_TITLES.get(category, OTHER_CATEGORY_TITLE)


# Summary table: one row per category, one column per severity.
def _format_summary_table(groups: dict[str, list[Finding]]) -> str:
    header = "| Category | " + " | ".join(f"{SEVERITY_EMOJI[s]} {s.title()}" for s in SEVERITY_ORDER) + " | Total |"
    separator = "| --- |" + " --- |" * (len(SEVERITY_ORDER) + 1)
    rows = [header, separator]
    for category, group in groups.items():
        counts = [sum(1 for f in group if f["severity"] == severity) for severity in SEVERITY_ORDER]
        cells = [str(count) if count else "-" for count in counts]
        rows.append(f"| {_category_title(category)} | " + " | ".join(cells) + f" | {len(group)} |")
    return "\n".join(rows)


# Renders one Finding as a Markdown block.
def _format_finding(finding: Finding) -> str:
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


# One category section: heading with count, then findings or 'No issues found'.
def _format_category_section(category: str, findings: list[Finding]) -> str:
    heading = f"### {_category_title(category)} ({len(findings)})"
    if not findings:
        return f"{heading}\n\nNo issues found. ✅"
    blocks = "\n\n---\n\n".join(_format_finding(finding) for finding in findings)
    return f"{heading}\n\n{blocks}"


# LangGraph node: findings -> final review comment.
def synthesizer_node(state: ReviewState) -> dict:
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
