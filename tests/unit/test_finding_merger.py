from review.finding_merger import merge_duplicate_findings


def finding(**overrides) -> dict:
    base = {"agent": "security", "severity": "HIGH", "rule_id": "missing_authorization", "confidence": "high",
            "file_path": "app/api/users.py", "line_number": 8, "title": "No authorization on delete_user",
            "description": "Any user can delete any account.", "evidence": "def delete_user(user_id):",
            "suggestion": "Check the caller's role."}
    base.update(overrides)
    return base


def test_one_endpoint_reported_under_two_auth_rules_becomes_one_finding():
    idor = finding(rule_id="idor", line_number=9, confidence="medium", title="IDOR in delete_user")

    merged = merge_duplicate_findings([idor, finding()])

    assert len(merged) == 1
    assert "Also reported as idor at line 9: IDOR in delete_user" in merged[0]["description"]
    print("OK - one bug, one comment, and the second angle is kept in the text")


def test_the_most_severe_and_confident_finding_is_kept():
    weak = finding(severity="MEDIUM", confidence="low", rule_id="idor", title="weak")
    strong = finding(severity="CRITICAL", title="strong")

    merged = merge_duplicate_findings([weak, strong])

    assert merged[0]["title"] == "strong" and merged[0]["severity"] == "CRITICAL"
    print("OK - the merge never lowers the severity a reader sees")


def test_distinct_problems_are_never_merged():
    findings = [
        finding(),
        finding(line_number=40),                           # same rule, another function
        finding(rule_id="sql_injection", line_number=9),   # unrelated rule on a nearby line
        finding(file_path="app/api/orders.py"),            # another file
        finding(agent="quality", rule_id="missing_authorization"),  # another category keeps its own section
    ]

    assert len(merge_duplicate_findings(findings)) == 5
    print("OK - only the same problem in the same place is merged")


def test_two_other_findings_merge_only_on_the_same_line():
    nearby = [finding(rule_id="other", line_number=8), finding(rule_id="other", line_number=10, title="b")]
    same_line = [finding(rule_id="other"), finding(rule_id="other", title="b")]

    assert len(merge_duplicate_findings(nearby)) == 2, "'other' says nothing about what the problem is"
    assert len(merge_duplicate_findings(same_line)) == 1
    print("OK - catch-all findings are merged only when they point at the same line")
