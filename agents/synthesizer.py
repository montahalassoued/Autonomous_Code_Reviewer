"""
synthesizer.py - the LangGraph node that combines all domain agent
findings into the final GitHub review comment.

Inputs: ReviewState.findings (populated by agents/security.py,
agents/performance.py, agents/architecture.py, agents/testing.py,
agents/documentation.py, merged via the operator.add reducer in
agents/state.py after join_agents_node).
Outputs: a partial state update setting ReviewState.final_review_markdown
to a single Markdown string ready to post as a GitHub PR comment via
tools/github_tools.py.

Purely a formatting step - no LLM call needed, since every finding
already contains everything the comment needs (severity, location,
description, suggestion). Deterministic and instant.
"""

from agents.state import ReviewState, Finding


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


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    """
    Sorts findings by severity (CRITICAL first, INFO last), so the
    most important issues appear at the top of the review comment.

    Args:
        findings: the raw, unordered findings from all domain agents

    Returns:
        findings sorted by severity, most severe first. Ties keep
        their original relative order (Python's sort is stable), which
        naturally groups findings from the same agent together when
        agents are appended in a consistent order upstream.
    """
    return sorted(findings, key=lambda f: SEVERITY_ORDER.get(f["severity"], len(SEVERITY_ORDER)))


def _format_finding(finding: Finding) -> str:
    """
    Renders one Finding as a Markdown block.

    Args:
        finding: a single Finding to render

    Returns:
        a Markdown string for this finding, including severity emoji,
        file:line reference, description, evidence, and suggestion
    """
    emoji = SEVERITY_EMOJI.get(finding["severity"], "⚪")
    location = f"`{finding['file_path']}:{finding['line_number']}`"

    lines = [
        f"### {emoji} {finding['severity']} - {finding['title']}",
        f"**Location:** {location}  ",
        f"**Agent:** {finding['agent']}",
        "",
        finding["description"],
        "",
        "```",
        finding["evidence"],
        "```",
        "",
        f"**Suggestion:** {finding['suggestion']}",
    ]
    return "\n".join(lines)


def _format_summary(findings: list[Finding]) -> str:
    """
    Builds a one-line-per-severity summary count, so reviewers can
    gauge PR health at a glance before reading individual findings.

    Args:
        findings: all findings (any order)

    Returns:
        a Markdown bullet list summarizing counts per severity level,
        omitting severities with zero findings
    """
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for finding in findings:
        if finding["severity"] in counts:
            counts[finding["severity"]] += 1

    lines = []
    for severity in SEVERITY_ORDER:
        if counts[severity] > 0:
            emoji = SEVERITY_EMOJI[severity]
            lines.append(f"- {emoji} **{severity}:** {counts[severity]}")
    return "\n".join(lines)


def synthesizer_node(state: ReviewState) -> dict:
    """
    The LangGraph node function for the "synthesizer" node defined in
    agents/graph.py. Replaces that file's synthesizer_node STUB.

    Args:
        state: the current ReviewState, reads findings (all 5 domain
               agents' output, merged)

    Returns:
        a partial state update: {"final_review_markdown": "..."} - the
        complete Markdown comment ready to post to the PR
    """
    findings = state["findings"]

    if not findings:
        return {
            "final_review_markdown": (
                "## 🤖 Autonomous Code Review\n\n"
                "No issues found. Nice work! ✅"
            )
        }

    sorted_findings = _sort_findings(findings)

    sections = [
        "## 🤖 Autonomous Code Review",
        "",
        f"Found **{len(findings)}** issue(s) across security, performance, "
        "architecture, testing, and documentation.",
        "",
        _format_summary(sorted_findings),
        "",
        "---",
        "",
    ]
    sections.extend(_format_finding(f) + "\n\n---\n" for f in sorted_findings)

    return {"final_review_markdown": "\n".join(sections).rstrip() + "\n"}
