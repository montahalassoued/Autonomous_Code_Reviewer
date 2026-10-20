from review.output_sanitizer import FIELD_LIMITS_CHARS, truncate_text
from review.state import Finding


# Rules that name one problem from different angles: one unprotected endpoint, one loop doing work per item.
RELATED_RULE_GROUPS = (
    frozenset({"missing_authentication", "missing_authorization", "idor"}),
    frozenset({"n_plus_one", "io_in_loop", "repeated_work_in_loop"}),
)
# Findings this close are about the same statement or function (decorator, def and body lines).
MAX_LINE_GAP = 5
CATCH_ALL_RULE = "other"
SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def _related_rules(a: str, b: str) -> bool:
    return a == b or any(a in group and b in group for group in RELATED_RULE_GROUPS)


def _same_evidence(a: Finding, b: Finding) -> bool:
    return bool(a["evidence"].strip()) and " ".join(a["evidence"].split()) == " ".join(b["evidence"].split())


def same_problem(a: Finding, b: Finding) -> bool:
    if a["agent"] != b["agent"] or a["file_path"] != b["file_path"]:
        return False
    gap = abs(a["line_number"] - b["line_number"])
    # "other" names no problem, so only the same line makes two of them one.
    if CATCH_ALL_RULE in (a["rule_id"], b["rule_id"]):
        return a["rule_id"] == b["rule_id"] and gap == 0
    # One rule twice is one problem only at one place: two SQL injections a few lines apart are two bugs.
    if a["rule_id"] == b["rule_id"]:
        return gap == 0 or (gap <= MAX_LINE_GAP and _same_evidence(a, b))
    # Related rules near each other are one problem seen from two angles.
    return gap <= MAX_LINE_GAP and _related_rules(a["rule_id"], b["rule_id"])


# One finding per problem: the most severe and confident is kept and names the others, so one bug gets one comment.
def merge_duplicate_findings(findings: list[Finding]) -> list[Finding]:
    ranked = sorted(findings, key=lambda finding: (SEVERITY_RANK[finding["severity"]], CONFIDENCE_RANK[finding["confidence"]]))
    kept: list[Finding] = []
    for finding in ranked:
        primary = next((other for other in kept if same_problem(other, finding)), None)
        if primary is None:
            kept.append(dict(finding))
            continue
        note = f" Also reported as {finding['rule_id']} at line {finding['line_number']}: {finding['title']}"
        primary["description"] = truncate_text(primary["description"] + note, FIELD_LIMITS_CHARS["description"])
    return kept
