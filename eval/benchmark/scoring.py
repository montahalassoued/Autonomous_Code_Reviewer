import json

from review.agents.agent_runner import SEVERITY_RANK, postprocess_findings
from review.agents.logic.agent import LOGIC_AGENT
from review.agents.quality.agent import QUALITY_AGENT
from review.agents.security.agent import SECURITY_AGENT
from review.diff_line_map import map_diff_lines
from review.synthesizer import review_label
from review.state import Finding


# Bump when a scoring rule changes, so results scored differently are never compared as equals.
SCORER_VERSION = 2
AGENT_SPECS = {"security": SECURITY_AGENT, "logic": LOGIC_AGENT, "quality": QUALITY_AGENT}
STRICT, LOOSE, NO_MATCH = 2, 1, 0
# Legacy (scorer v1) rules, kept so old and new numbers can be read side by side.
LEGACY_FALSE_POSITIVE_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM"}


# Same cleanup as parse_findings: True when the output really is a JSON array, not prose that degraded to [].
def is_json_array(text: str) -> bool:
    cleaned = text.strip().strip("`").removeprefix("json").strip()
    candidates = [cleaned]
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end > start:
        candidates.append(cleaned[start:end + 1])
    for candidate in candidates:
        try:
            if isinstance(json.loads(candidate, strict=False), list):
                return True
        except json.JSONDecodeError:
            continue
    return False


# What production would post: the same postprocess_findings run_agent uses.
def posted_findings(agent: str, text: str, diff: str = "") -> tuple[list[Finding], int]:
    return postprocess_findings(text, AGENT_SPECS[agent], diff)


def _normalized_path(path: str) -> str:
    return path.strip().removeprefix("a/").removeprefix("b/").lstrip("/")


# The reported path names the expected file: equal, or a longer repo path ending with it ("users.py" alone is not enough).
def same_file(reported: str, expected: str) -> bool:
    path = _normalized_path(reported)
    return bool(path) and (path == expected or ("/" in path and expected.endswith("/" + path)))


def _in_range(line: int, line_range: list[int] | None) -> bool:
    return line_range is None or line_range[0] <= line <= line_range[1]


def _finding_text(finding: Finding) -> str:
    return " ".join(finding[field] for field in ("title", "description", "evidence", "suggestion")).lower()


# STRICT: right file, rule and line. LOOSE: right file and rule (or rule "other" with a keyword). Keywords only decide when the case lists no rule.
def match_strength(finding: Finding, issue: dict) -> int:
    if not same_file(finding["file_path"], issue["file"]):
        return NO_MATCH
    keyword_ok = any(keyword in _finding_text(finding) for keyword in issue["keywords"])
    if issue["rule_ids"]:
        rule_ok = finding["rule_id"] in issue["rule_ids"]
        if not (rule_ok or (finding["rule_id"] == "other" and keyword_ok)):
            return NO_MATCH
        exact = rule_ok
    else:
        if not keyword_ok:
            return NO_MATCH
        exact = True
    return STRICT if exact and _in_range(finding["line_number"], issue["line_range"]) else LOOSE


# A finding fits an allowed or forbidden pattern: same file, listed rule, line and severity cap if given.
def matches_pattern(finding: Finding, pattern: dict) -> bool:
    if not same_file(finding["file_path"], pattern["file"]):
        return False
    if pattern["rule_ids"] and finding["rule_id"] not in pattern["rule_ids"]:
        return False
    if not _in_range(finding["line_number"], pattern["line_range"]):
        return False
    cap = pattern["max_severity"]
    return cap is None or SEVERITY_RANK[finding["severity"]] >= SEVERITY_RANK[cap]


# Greedy one-to-one assignment, strict matches first: one finding counts for one issue at most.
def _assign(findings: list[Finding], issues: list[dict]) -> dict[int, tuple[int, int]]:
    assigned: dict[int, tuple[int, int]] = {}
    used: set[int] = set()
    for wanted in (STRICT, LOOSE):
        for issue_index, issue in enumerate(issues):
            if issue_index in assigned:
                continue
            for finding_index, finding in enumerate(findings):
                strength = match_strength(finding, issue)
                if finding_index not in used and strength >= wanted:
                    assigned[issue_index] = (finding_index, strength)
                    used.add(finding_index)
                    break
    return assigned


# A finding left after the assignment: forbidden, duplicate, allowed, noise (MEDIUM or above) or nit.
def _classify_unmatched(finding: Finding, case: dict) -> str:
    if any(matches_pattern(finding, pattern) for pattern in case["forbidden_findings"]):
        return "forbidden"
    if any(match_strength(finding, issue) for issue in case["expected_issues"]):
        return "duplicate"
    if any(matches_pattern(finding, pattern) for pattern in case["allowed_findings"]):
        return "allowed"
    return "noise" if SEVERITY_RANK[finding["severity"]] <= SEVERITY_RANK["MEDIUM"] else "nit"


def _summarize_finding(finding: Finding, finding_class: str) -> dict:
    return {
        "class": finding_class, "rule_id": finding["rule_id"], "severity": finding["severity"],
        "confidence": finding["confidence"], "file_path": finding["file_path"],
        "line_number": finding["line_number"], "title": finding["title"],
    }


# Scores one model answer against one case; every count needed by aggregate() is in the result.
def score_case(case: dict, text: str) -> dict:
    findings, n_raw = posted_findings(case["agent"], text, case["diff_text"])
    return {**score_findings(case, findings, n_raw), "legacy_score": legacy_score(case, text), "valid": is_json_array(text)}


# Scores posted findings against a case's labels; the review eval scores a whole review's findings with it too.
def score_findings(case: dict, findings: list[Finding], n_raw: int) -> dict:
    issues = case["expected_issues"]
    assigned = _assign(findings, issues)
    required = [index for index, issue in enumerate(issues) if issue["required"]]
    matched_required = [index for index in required if index in assigned]

    classes = [""] * len(findings)
    for issue_index, (finding_index, _strength) in assigned.items():
        classes[finding_index] = "tp" if issues[issue_index]["required"] else "optional"
    for index, finding in enumerate(findings):
        if not classes[index]:
            classes[index] = _classify_unmatched(finding, case)

    def counted(name: str) -> int:
        return classes.count(name)

    blocking_noise = sum(
        1 for finding, finding_class in zip(findings, classes) if finding_class == "noise" and review_label(finding) == "blocking"
    )
    lines = map_diff_lines(case["diff_text"])
    commentable = sum(
        1 for finding in findings
        if any(same_file(finding["file_path"], path) and finding["line_number"] in file_lines.commentable
               for path, file_lines in lines.items())
    )
    tp = len(matched_required)
    forbidden = counted("forbidden")
    clean = not required
    if forbidden:
        score = 0.0
    elif clean:
        score = 1.0 if blocking_noise == 0 else 0.0  # what the author experiences: no blocking false alarm
    else:
        score = tp / len(required)

    matched_findings = [(issues[index], findings[assigned[index][0]], assigned[index][1]) for index in matched_required]
    return {
        "score": score,
        "clean": clean,
        "passed": score == 1.0,
        "clean_strict_pass": clean and forbidden == 0 and counted("noise") == 0,
        "required": len(required),
        "tp": tp,
        "tp_strict": sum(1 for _issue, _finding, strength in matched_findings if strength == STRICT),
        "rule_ok": sum(1 for issue, finding, _s in matched_findings if not issue["rule_ids"] or finding["rule_id"] in issue["rule_ids"]),
        "severity_ok": sum(1 for issue, finding, _s in matched_findings if not issue["severity"] or finding["severity"] in issue["severity"]),
        "line_ok": sum(1 for issue, finding, _s in matched_findings if _in_range(finding["line_number"], issue["line_range"])),
        "noise": counted("noise"),
        "blocking_noise": blocking_noise,
        "duplicates": counted("duplicate"),
        "forbidden": forbidden,
        "allowed": counted("allowed"),
        "nits": counted("nit"),
        "n_raw": n_raw,
        "n_findings": len(findings),
        "n_dropped": n_raw - len(findings),
        "commentable": commentable,
        "findings": [_summarize_finding(finding, finding_class) for finding, finding_class in zip(findings, classes)],
    }


# Scorer v1: raw JSON, same file and any keyword, MEDIUM or above fails a clean case.
def legacy_score(case: dict, text: str) -> float:
    cleaned = text.strip().strip("`").removeprefix("json").strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    try:
        raw = json.loads(cleaned if cleaned.startswith("[") else cleaned[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return 0.0
    findings = [finding for finding in raw if isinstance(finding, dict)] if isinstance(raw, list) else []
    issues = case["expected_issues"]
    if not issues:
        return 0.0 if any(str(f.get("severity", "")).upper() in LEGACY_FALSE_POSITIVE_SEVERITIES for f in findings) else 1.0

    def legacy_match(finding: dict, issue: dict) -> bool:
        path = _normalized_path(str(finding.get("file_path", "")))
        text_blob = " ".join(str(finding.get(k, "")) for k in ("title", "description", "evidence", "suggestion")).lower()
        same = bool(path) and (path.endswith(issue["file"]) or issue["file"].endswith(path))
        return same and any(keyword in text_blob for keyword in issue["keywords"])

    return sum(1 for issue in issues if any(legacy_match(f, issue) for f in findings)) / len(issues)
