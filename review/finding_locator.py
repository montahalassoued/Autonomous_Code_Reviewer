import re

from review.diff_line_map import map_diff_lines
from review.state import Finding


# A quoted fragment shorter than this ("return x", "}") matches too many lines to locate anything.
MIN_EVIDENCE_CHARS = 10
WHITESPACE = re.compile(r"\s+")


def _normalized(code: str) -> str:
    return WHITESPACE.sub(" ", code).strip()


def _quotes(evidence_line: str, code: str) -> bool:
    if len(evidence_line) < MIN_EVIDENCE_CHARS or not code:
        return False
    return evidence_line in code or (len(code) >= MIN_EVIDENCE_CHARS and code in evidence_line)


# The added line the evidence quotes, nearest the reported line; None when the evidence quotes no added line.
def quoted_line(finding: Finding, added_text: dict[int, str]) -> int | None:
    code_by_line = {number: _normalized(code) for number, code in added_text.items()}
    for evidence_line in filter(None, map(_normalized, finding["evidence"].splitlines())):
        matches = [number for number, code in code_by_line.items() if _quotes(evidence_line, code)]
        if matches:
            return min(matches, key=lambda number: (abs(number - finding["line_number"]), number))
    return None


# Models miscount diff lines but quote the code they mean: a finding moves to the added line its evidence quotes.
def locate_findings(findings: list[Finding], diff: str) -> list[Finding]:
    files = map_diff_lines(diff)
    located = []
    for finding in findings:
        lines = files.get(finding["file_path"])
        line = quoted_line(finding, lines.added_text) if lines else None
        located.append({**finding, "line_number": line} if line and line != finding["line_number"] else finding)
    return located
