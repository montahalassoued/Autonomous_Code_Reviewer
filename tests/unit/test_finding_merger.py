from review.finding_merger import merge_cross_agent_findings, merge_duplicate_findings


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


def test_one_rule_twice_on_different_code_is_two_bugs():
    first = finding(rule_id="sql_injection", line_number=3, evidence="cursor.execute(f\"... {name}\")")
    second = finding(rule_id="sql_injection", line_number=5, evidence="cursor.execute(f\"... {email}\")")
    repeat = finding(rule_id="sql_injection", line_number=4, evidence="cursor.execute(f\"... {name}\")")

    assert len(merge_duplicate_findings([first, second])) == 2, "two nearby SQL injections are two comments"
    assert len(merge_duplicate_findings([first, repeat])) == 1, "the same quoted code is the same bug"
    print("OK - the same rule merges only at the same line or on the same quoted code")


def test_two_other_findings_merge_only_on_the_same_line():
    nearby = [finding(rule_id="other", line_number=8), finding(rule_id="other", line_number=10, title="b")]
    same_line = [finding(rule_id="other"), finding(rule_id="other", title="b")]

    assert len(merge_duplicate_findings(nearby)) == 2, "'other' says nothing about what the problem is"
    assert len(merge_duplicate_findings(same_line)) == 1
    print("OK - catch-all findings are merged only when they point at the same line")


def test_one_unbounded_search_said_two_ways_becomes_one_finding():
    unbounded = finding(agent="quality", rule_id="unbounded_query", line_number=17, title="No LIMIT on the search")
    in_memory = finding(agent="quality", rule_id="whole_data_in_memory", line_number=17, title="fetch_all loads every row")

    assert len(merge_duplicate_findings([unbounded, in_memory])) == 1
    print("OK - unbounded_query and whole_data_in_memory on one statement are one remark")


# The real review of sec_sql_injection_search: security found the bug, logic repeated it as "other", quality made its own point.
def test_a_vague_finding_from_another_agent_folds_into_the_named_one():
    injection = finding(rule_id="sql_injection", line_number=17, title="SQL injection in search_users")
    logic_echo = finding(agent="logic", rule_id="other", line_number=17, title="Unescaped search term can cause SQL syntax error")
    unbounded = finding(agent="quality", rule_id="unbounded_query", severity="MEDIUM", line_number=17, title="No LIMIT")

    merged = merge_cross_agent_findings([logic_echo, injection, unbounded])

    assert [(f["agent"], f["rule_id"]) for f in merged] == [("security", "sql_injection"), ("quality", "unbounded_query")]
    assert "Also reported by the logic review as other: Unescaped search term" in merged[0]["description"]
    print("OK - one bug, one comment; a different problem on the same line stays its own finding")


def test_an_inverted_check_and_the_bypass_it_opens_are_one_bug():
    bypass = finding(rule_id="missing_authorization", line_number=11)
    inverted = finding(agent="logic", rule_id="wrong_condition", severity="CRITICAL", line_number=11, title="Admin check inverted")

    merged = merge_cross_agent_findings([bypass, inverted])

    assert len(merged) == 1 and merged[0]["rule_id"] == "wrong_condition", "the most severe side is kept"
    print("OK - logic's wrong_condition and security's missing_authorization on one line merge")


def test_agents_on_different_lines_or_with_unrelated_rules_are_never_merged():
    injection = finding(rule_id="sql_injection", line_number=17)
    assert len(merge_cross_agent_findings([injection, finding(agent="logic", rule_id="other", line_number=18)])) == 2
    assert len(merge_cross_agent_findings([injection, finding(agent="quality", rule_id="n_plus_one", line_number=17)])) == 2
    same_agent = finding(rule_id="other", line_number=17)
    assert len(merge_cross_agent_findings([injection, same_agent])) == 2, "one agent's own duplicates are merge_duplicate_findings' job"
    print("OK - only one line and a vague or paired rule make two agents' findings one")



# The real review of sec_command_injection_export: logic described the injection's symptom on the same line.
def test_a_logic_symptom_on_an_injection_line_folds_into_the_injection():
    injection = finding(rule_id="command_injection", severity="HIGH", line_number=5, title="Command injection in export")
    unquoted = finding(agent="logic", rule_id="wrong_api_usage", line_number=5, title="Filename not quoted in shell command")
    quote = finding(agent="logic", rule_id="unhandled_edge_case", line_number=5, title="Fails when the name has a quote")

    merged = merge_cross_agent_findings([unquoted, quote, injection])

    assert [(f["agent"], f["rule_id"]) for f in merged] == [("security", "command_injection")], "at equal severity the cause wins"
    assert "Also reported by the logic review as wrong_api_usage" in merged[0]["description"]
    print("OK - one injection, one comment, named by its cause")


# The real review of sec_missing_authorization_delete: logic called the missing admin check an unhandled role.
def test_an_unchecked_role_and_a_missing_authentication_are_one_bug():
    missing = finding(rule_id="missing_authentication", severity="CRITICAL", line_number=20)
    role = finding(agent="logic", rule_id="missed_state", line_number=20, title="admin_delete_order does not enforce admin")

    assert len(merge_cross_agent_findings([role, missing])) == 1
    print("OK - logic's missed_state on an endpoint's auth line is the same bug")


def test_a_symptom_rule_away_from_the_injection_or_without_one_stays():
    injection = finding(rule_id="sql_injection", line_number=19)
    elsewhere = finding(agent="logic", rule_id="unhandled_edge_case", line_number=23, title="Missing key raises KeyError")
    beside_a_leak = finding(agent="logic", rule_id="unhandled_edge_case", line_number=19, title="Empty list")

    assert len(merge_cross_agent_findings([injection, elsewhere])) == 2, "another line is another bug"
    assert len(merge_cross_agent_findings([finding(rule_id="data_exposure", line_number=19), beside_a_leak])) == 2
    print("OK - a symptom folds only into an injection on its own line")
