import hashlib
import json
from pathlib import Path

from review.agents.logic.rules import LOGIC_RULES
from review.agents.quality.rules import QUALITY_RULES
from review.agents.security.rules import SECURITY_RULES


AGENT_RULES = {"security": SECURITY_RULES, "logic": LOGIC_RULES, "quality": QUALITY_RULES}
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")


# Dataset text fields are stored as lists of lines for readability.
def join_lines(value) -> str:
    return "\n".join(value) if isinstance(value, list) else value


def _line_range(issue: dict) -> list[int]:
    if "line_range" in issue:
        return list(issue["line_range"])
    if "lines" in issue:  # schema v1
        return list(issue["lines"])
    return [issue["line"], issue["line"]]


# One planted issue in the v2 shape; a v1 issue (file, line, lines, keywords) still loads.
def normalize_issue(issue: dict, index: int) -> dict:
    return {
        "id": issue.get("id", f"issue_{index}"),
        "agent": issue.get("agent"),  # review eval: the category expected to report it
        "file": issue["file"],
        "rule_ids": list(issue.get("rule_ids", [])),
        "severity": list(issue.get("severity", [])),  # acceptable severities; empty = any
        "line_range": _line_range(issue),
        "keywords": [keyword.lower() for keyword in issue.get("keywords", [])],
        "required": issue.get("required", True),
    }


# A finding pattern for allowed_findings / forbidden_findings: file, optional rule ids, lines and severity cap.
def normalize_pattern(pattern: dict) -> dict:
    return {
        "agent": pattern.get("agent"),  # review eval: the category it applies to; None = every category
        "file": pattern["file"],
        "rule_ids": list(pattern.get("rule_ids", [])),
        "line_range": list(pattern["line_range"]) if "line_range" in pattern else None,
        "max_severity": pattern.get("max_severity"),
        "why": pattern.get("why", ""),
    }


def normalize_case(case: dict) -> dict:
    return {
        **case,
        "diff_text": join_lines(case["diff"]),
        "expected_issues": [normalize_issue(issue, i) for i, issue in enumerate(case.get("expected_issues", []))],
        "allowed_findings": [normalize_pattern(p) for p in case.get("allowed_findings", [])],
        "forbidden_findings": [normalize_pattern(p) for p in case.get("forbidden_findings", [])],
        "tags": list(case.get("tags", [])),
    }


# The diff and expected issues of a case, hashed: a replay warns when a case changed since the recorded run.
def case_fingerprint(case: dict) -> str:
    content = json.dumps({"diff": case["diff_text"], "expected": case["expected_issues"]}, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def load_agent_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [normalize_case(case) for case in data.get("agents", [])]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
