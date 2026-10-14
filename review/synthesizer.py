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

# Must the author act before merging? Derived from severity, so it is deterministic.
SEVERITY_LABEL = {
    "CRITICAL": "blocking",
    "HIGH": "blocking",
    "MEDIUM": "suggestion",
    "LOW": "nit",
    "INFO": "nit",
}

# Reviews get less thorough above ~400 changed lines (SmartBear / Google data).
LARGE_PR_CHANGED_LINES = 400


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


def _label(finding: Finding) -> str:
    return SEVERITY_LABEL.get(finding["severity"], "nit")


# Added + removed lines in a unified diff (file headers excluded).
def _count_changed_lines(diff: str) -> int:
    return sum(
        1 for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


# One line telling the author whether anything must be fixed before merging.
def _format_verdict(findings: list[Finding]) -> str:
    blocking = sum(1 for finding in findings if _label(finding) == "blocking")
    if blocking:
        return f"⛔ **{blocking} blocking issue(s)** to fix before merging, out of {len(findings)} finding(s)."
    return f"✅ **Nothing blocking.** {len(findings)} suggestion(s) or nit(s) below."


def _format_size_note(diff: str) -> str | None:
    changed_lines = _count_changed_lines(diff)
    if changed_lines <= LARGE_PR_CHANGED_LINES:
        return None
    return (
        f"📏 **Large PR:** {changed_lines:,} changed lines. Reviews miss more above "
        f"~{LARGE_PR_CHANGED_LINES} lines; consider splitting it."
    )


def _format_finding(finding: Finding) -> str:
    emoji = SEVERITY_EMOJI.get(finding["severity"], "⚪")
    lines = [
        f"#### {emoji} [{_label(finding)}] {finding['severity']} - {finding['title']}",
        f"**Location:** `{finding['file_path']}:{finding['line_number']}`",
        "",
        finding["description"],
    ]
    if finding["evidence"].strip():
        lines += ["", "```", finding["evidence"], "```"]
    lines += ["", f"**Suggestion:** {finding['suggestion']}"]
    return "\n".join(lines)


def _join_findings(findings: list[Finding]) -> str:
    return "\n\n---\n\n".join(_format_finding(finding) for finding in findings)


# Category section: blocking issues and suggestions shown, nits folded so they don't bury them.
def _format_category_section(category: str, findings: list[Finding]) -> str:
    heading = f"### {_category_title(category)} ({len(findings)})"
    if not findings:
        return f"{heading}\n\nNo issues found. ✅"

    main = [finding for finding in findings if _label(finding) != "nit"]
    nits = [finding for finding in findings if _label(finding) == "nit"]
    parts = [heading]
    if main:
        parts.append(_join_findings(main))
    if nits:
        # GitHub only renders Markdown inside <details> after a blank line.
        parts.append(f"<details>\n<summary>{len(nits)} nit(s)</summary>\n\n{_join_findings(nits)}\n\n</details>")
    return "\n\n".join(parts)


# LangGraph node: findings -> final review comment.
def synthesizer_node(state: ReviewState) -> dict:
    findings = state["findings"]
    size_note = _format_size_note(state["pr_diff"])

    if not findings:
        sections = [REVIEW_HEADING, "No issues found by the security, logic and code quality reviews. Nice work! ✅"]
        if size_note:
            sections.append(size_note)
        return {"final_review_markdown": "\n\n".join(sections) + "\n"}

    groups = _group_by_category(findings)
    sections = [REVIEW_HEADING, _format_verdict(findings)]
    if size_note:
        sections.append(size_note)
    sections.append(_format_summary_table(groups))
    sections += [_format_category_section(category, group) for category, group in groups.items()]

    return {"final_review_markdown": "\n\n".join(sections) + "\n"}
