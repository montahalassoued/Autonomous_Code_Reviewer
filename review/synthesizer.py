from review.diff_line_map import map_diff_lines
from review.finding_merger import merge_cross_agent_findings
from review.injection_detector import detect_injection
from review.output_sanitizer import (COMMENT_BUDGET_CHARS, code_block, escape_markdown_text,
                                     escape_markdown_with_code_blocks, inline_code)
from review.state import ReviewState, Finding


# Each changed file and the new-side lines a comment may point at (empty when the diff shows none, e.g. a binary file).
DiffLines = dict[str, frozenset[int]]


def commentable_lines(diff: str, changed_files) -> DiffLines:
    lines = {path: frozenset(file_lines.commentable) for path, file_lines in map_diff_lines(diff).items()}
    return {**{path: frozenset() for path in changed_files}, **lines}


# Marks findings on a line the change did not add, in a file it did change: older code, shown but never blocking.
# A finding in another file is left as is, since a change can break its callers there. The "unchanged_code" key is
# a display marker recomputed from the diff, not a Finding field: the findings table does not store it.
def mark_unchanged_code(findings: list[Finding], diff: str) -> list[Finding]:
    added = {path: file_lines.added for path, file_lines in map_diff_lines(diff).items() if file_lines.added}
    return [{**finding, "unchanged_code": True}
            if finding["file_path"] in added and finding["line_number"] not in added[finding["file_path"]] else finding
            for finding in findings]


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
MAX_UNREVIEWED_LISTED = 10

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

LABEL_ORDER = {"blocking": 0, "suggestion": 1, "nit": 2}
SECTION_OVERHEAD_CHARS = 300  # category heading, separators and the nit fold


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


# A low-confidence finding depends on code the agent could not see, and one on unchanged code predates the change,
# so neither blocks a merge.
def review_label(finding: Finding) -> str:
    label = SEVERITY_LABEL.get(finding["severity"], "nit")
    if label == "blocking" and (finding.get("confidence") == "low" or finding.get("unchanged_code")):
        return "suggestion"
    return label


# Added + removed lines in a unified diff (file headers excluded).
def _count_changed_lines(diff: str) -> int:
    return sum(
        1 for line in diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


# A PR that talks to the AI reviewer may have talked it out of a finding, so it never reads as clean.
NEEDS_HUMAN_REVIEW = ("⚠️ **Needs human review.** This pull request contains text aimed at the AI reviewer "
                      "(listed below), so an issue may have been missed on purpose.")


# One line telling the author whether anything must be fixed before merging.
def _format_verdict(findings: list[Finding], injection_suspected: bool) -> str:
    blocking = sum(1 for finding in findings if review_label(finding) == "blocking")
    if blocking:
        return f"⛔ **{blocking} blocking issue(s)** to fix before merging, out of {len(findings)} finding(s)."
    if injection_suspected:
        return f"{NEEDS_HUMAN_REVIEW} Nothing blocking was found; {len(findings)} suggestion(s) or nit(s) below."
    return f"✅ **Nothing blocking.** {len(findings)} suggestion(s) or nit(s) below."


def _format_size_note(diff: str) -> str | None:
    changed_lines = _count_changed_lines(diff)
    if changed_lines <= LARGE_PR_CHANGED_LINES:
        return None
    return (
        f"📏 **Large PR:** {changed_lines:,} changed lines. Reviews miss more above "
        f"~{LARGE_PR_CHANGED_LINES} lines; consider splitting it."
    )


# Every LLM-written field is escaped for its place: heading, code span, prose or fenced block.
def _format_finding(finding: Finding, diff_lines: DiffLines | None = None) -> str:
    emoji = SEVERITY_EMOJI.get(finding["severity"], "⚪")
    location = inline_code(f"{finding['file_path']}:{finding['line_number']}")
    if diff_lines and finding["file_path"] not in diff_lines:
        location += " (file not in this PR)"
    elif diff_lines and diff_lines[finding["file_path"]] and finding["line_number"] not in diff_lines[finding["file_path"]]:
        location += " (line not in the diff)"
    elif finding.get("unchanged_code"):
        location += " (unchanged line)"
    lines = [
        f"#### {emoji} [{review_label(finding)}] {finding['severity']} - {escape_markdown_text(finding['title'], single_line=True)}",
        f"**Location:** {location} · **Confidence:** {finding.get('confidence', 'medium')}",
        "",
        escape_markdown_text(finding["description"]),
    ]
    if finding["evidence"].strip():
        lines += ["", code_block(finding["evidence"])]
    suggestion = escape_markdown_with_code_blocks(finding["suggestion"])
    # A fenced block must start on its own line; escaped prose never starts with three backticks.
    separator = "\n\n" if suggestion.startswith("```") else " "
    lines += ["", f"**Suggestion:**{separator}{suggestion}"]
    return "\n".join(lines)


def _join_findings(findings: list[Finding], diff_lines: DiffLines) -> str:
    return "\n\n---\n\n".join(_format_finding(finding, diff_lines) for finding in findings)


# Category section: blocking issues and suggestions shown, nits folded so they don't bury them.
def _format_category_section(category: str, findings: list[Finding], diff_lines: DiffLines, total: int) -> str:
    heading = f"### {_category_title(category)} ({total})"
    if not findings:
        return f"{heading}\n\nNo issues found. ✅" if not total else f"{heading}\n\nNot shown: see the note below."

    main = [finding for finding in findings if review_label(finding) != "nit"]
    nits = [finding for finding in findings if review_label(finding) == "nit"]
    parts = [heading]
    if main:
        parts.append(_join_findings(main, diff_lines))
    if nits:
        # GitHub only renders Markdown inside <details> after a blank line.
        parts.append(f"<details>\n<summary>{len(nits)} nit(s)</summary>\n\n{_join_findings(nits, diff_lines)}\n\n</details>")
    return "\n\n".join(parts)


# The findings that fit in the comment, blocking first: whatever is left out is counted, never cut mid-Markdown.
def _findings_within_budget(findings: list[Finding], diff_lines: DiffLines, budget_chars: int) -> list[Finding]:
    ranked = sorted(findings, key=lambda f: (LABEL_ORDER[review_label(f)], SEVERITY_ORDER.get(f["severity"], len(SEVERITY_ORDER))))
    kept, used = [], 0
    for finding in ranked:
        size = len(_format_finding(finding, diff_lines)) + 10
        if used + size > budget_chars:
            break
        kept.append(finding)
        used += size
    return kept


# Names the categories whose agent failed, so a partial review is never read as a clean one.
def _format_partial_note(failed_agents: list[str]) -> str | None:
    if not failed_agents:
        return None
    names = ", ".join(_category_title(agent) for agent in sorted(set(failed_agents), key=_category_order))
    return (
        f"⚠️ **Partial review:** {names} could not run (model or API error), so its findings are missing. "
        # A redelivery would not help: a partial review is stored as posted, so the dedupe drops it.
        "Push a new commit to retry."
    )


def _category_order(agent: str) -> int:
    return list(CATEGORY_TITLES).index(agent) if agent in CATEGORY_TITLES else len(CATEGORY_TITLES)


# Files the agents never saw, so "no issues" is never read as covering them.
def _format_unreviewed_note(unreviewed_files: list[str]) -> str | None:
    if not unreviewed_files:
        return None
    names = ", ".join(inline_code(path) for path in unreviewed_files[:MAX_UNREVIEWED_LISTED])
    more = len(unreviewed_files) - MAX_UNREVIEWED_LISTED
    return (
        f"⚠️ **Not reviewed:** {len(unreviewed_files)} file(s) were too large for one review or had no patch from GitHub: "
        f"{names}{f' and {more} more' if more > 0 else ''}. Split the pull request to have them reviewed."
    )


# The comment when no file fits in a review: no agent runs, and the reader is told why.
def unreviewable_markdown(unreviewed_files: list[str]) -> str:
    return "\n\n".join([REVIEW_HEADING, "Nothing in this pull request could be reviewed.",
                        _format_unreviewed_note(unreviewed_files) or ""]).rstrip() + "\n"


# Deterministic warning: lines of the diff that look written for the AI reviewer, so a human checks them.
def _format_injection_note(signals: list) -> str | None:
    if not signals:
        return None
    lines = [
        "⚠️ **Possible prompt injection.** These added lines look written for an AI reviewer. "
        "They were treated as data, but check them and read this review with care:"
    ]
    lines += [
        f"- {inline_code(f'{signal.file_path}:{signal.line_number}')} ({signal.kind.replace('_', ' ')}): {inline_code(signal.excerpt)}"
        for signal in signals
    ]
    return "\n".join(lines)


# LangGraph node: findings -> final review comment.
def synthesizer_node(state: ReviewState) -> dict:
    findings = mark_unchanged_code(merge_cross_agent_findings(state["findings"]), state["pr_diff"])
    failed_agents = state.get("failed_agents") or []
    unreviewed = state.get("unreviewed_files") or []
    injection_signals = detect_injection(state["pr_diff"])
    notes = [note for note in (
        _format_partial_note(failed_agents), _format_unreviewed_note(unreviewed), _format_injection_note(injection_signals),
        _format_size_note(state["pr_diff"]),
    ) if note]

    if not findings:
        reviewed = [CATEGORY_TITLES[agent].split(" ", 1)[1].lower() for agent in CATEGORY_TITLES if agent not in failed_agents]
        clean = f"No issues found by the {', '.join(reviewed)} review(s)."
        if injection_signals:
            clean = f"{NEEDS_HUMAN_REVIEW} {clean}"
        elif not (failed_agents or unreviewed):
            clean += " Nice work! ✅"
        sections = [REVIEW_HEADING, clean, *notes]
        return {"final_review_markdown": "\n\n".join(sections) + "\n", "posted_findings": []}

    diff_lines = commentable_lines(state["pr_diff"], state.get("changed_files") or ())
    groups = _group_by_category(findings)
    sections = [REVIEW_HEADING, _format_verdict(findings, bool(injection_signals)), *notes, _format_summary_table(groups)]

    # The verdict and the table count every finding; only the detailed sections are budgeted.
    budget = COMMENT_BUDGET_CHARS - sum(len(section) for section in sections) - SECTION_OVERHEAD_CHARS * len(groups)
    shown = {id(finding) for finding in _findings_within_budget(findings, diff_lines, budget)}
    for category, group in groups.items():
        visible = [finding for finding in group if id(finding) in shown]
        sections.append(_format_category_section(category, visible, diff_lines, len(group)))
    hidden = len(findings) - len(shown)
    if hidden:
        sections.append(f"ℹ️ {hidden} more finding(s) not shown (GitHub comment size limit).")

    return {"final_review_markdown": "\n\n".join(sections) + "\n", "posted_findings": findings}
