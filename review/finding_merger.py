from review.output_sanitizer import FIELD_LIMITS_CHARS, truncate_text
from review.state import Finding


# Rules that name one problem from different angles: one unprotected endpoint, one loop doing work per item.
RELATED_RULE_GROUPS = (
    frozenset({"missing_authentication", "missing_authorization", "idor"}),
    frozenset({"n_plus_one", "io_in_loop", "repeated_work_in_loop"}),
    frozenset({"unbounded_query", "whole_data_in_memory"}),
)
INJECTION_RULES = frozenset({"sql_injection", "nosql_injection", "command_injection", "code_injection", "template_injection"})
# Logic rules that name what breaks, not why: on an injection's line, "fails with a quote" is the injection itself.
SYMPTOM_RULES = frozenset({"unhandled_edge_case", "wrong_api_usage", "missed_state"})
# Rules of two agents that describe one bug from each side: an inverted check or an unchecked role is also a missing authorization.
CROSS_AGENT_RULE_GROUPS = (
    frozenset({"wrong_condition", "missed_state", "missing_authorization", "missing_authentication"}),
    INJECTION_RULES | {"unhandled_edge_case", "wrong_api_usage"},
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


# Two agents on one line: the same bug only when one says "other" (it names no problem) or their rules are two sides of one bug.
# Same line and same code is not enough: a SQL injection and an unbounded query can quote the same statement.
def same_problem_across_agents(a: Finding, b: Finding) -> bool:
    if a["agent"] == b["agent"] or a["file_path"] != b["file_path"] or a["line_number"] != b["line_number"]:
        return False
    rules = {a["rule_id"], b["rule_id"]}
    return CATCH_ALL_RULE in rules or any(rules <= group for group in CROSS_AGENT_RULE_GROUPS)


def _merge(findings: list[Finding], is_duplicate, note) -> list[Finding]:
    # A named rule beats "other", then the most severe, then the cause over a symptom, then the most confident.
    ranked = sorted(findings, key=lambda finding: (finding["rule_id"] == CATCH_ALL_RULE, SEVERITY_RANK[finding["severity"]],
                                                   finding["rule_id"] in SYMPTOM_RULES, CONFIDENCE_RANK[finding["confidence"]]))
    kept: list[Finding] = []
    for finding in ranked:
        primary = next((other for other in kept if is_duplicate(other, finding)), None)
        if primary is None:
            kept.append(dict(finding))
            continue
        primary["description"] = truncate_text(primary["description"] + note(finding), FIELD_LIMITS_CHARS["description"])
    return kept


# One finding per problem within one agent: the kept finding names the others, so one bug gets one comment.
def merge_duplicate_findings(findings: list[Finding]) -> list[Finding]:
    return _merge(findings, same_problem,
                  lambda finding: f" Also reported as {finding['rule_id']} at line {finding['line_number']}: {finding['title']}")


# One finding per problem across agents, run by the synthesizer on the whole review.
def merge_cross_agent_findings(findings: list[Finding]) -> list[Finding]:
    return _merge(findings, same_problem_across_agents,
                  lambda finding: f" Also reported by the {finding['agent']} review as {finding['rule_id']}: {finding['title']}")
