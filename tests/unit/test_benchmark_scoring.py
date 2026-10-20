import json
from pathlib import Path

from eval.benchmark.dataset_schema import load_agent_cases, normalize_case
from eval.benchmark.metrics import aggregate
from eval.benchmark.scoring import LOOSE, NO_MATCH, STRICT, match_strength, same_file, score_case
from eval.llm_benchmark import DEFAULT_CASES_PATH

FIXTURE = Path(__file__).parent / "fixtures" / "llm_benchmark_recorded_outputs.json"

SQL_DIFF = [
    "diff --git a/app/users.py b/app/users.py", "--- a/app/users.py", "+++ b/app/users.py", "@@ -1,1 +1,3 @@",
    " import sqlite3", "+def find_user(conn, name):", "+    return conn.execute(f\"SELECT * FROM users WHERE name = '{name}'\")",
]
SQL_CASE = normalize_case({
    "id": "sql", "agent": "security", "diff": SQL_DIFF,
    "expected_issues": [{"file": "app/users.py", "rule_ids": ["sql_injection"], "severity": ["HIGH", "CRITICAL"],
                         "line_range": [3, 3], "keywords": ["sql injection"]}],
})
CLEAN_CASE = normalize_case({
    "id": "clean", "agent": "security", "diff": SQL_DIFF, "expected_issues": [],
    "forbidden_findings": [{"file": "app/users.py", "rule_ids": ["sql_injection"]}],
    "allowed_findings": [{"file": "app/users.py", "rule_ids": ["data_exposure"], "max_severity": "MEDIUM"}],
})


def finding(**overrides) -> dict:
    base = {"severity": "HIGH", "rule_id": "sql_injection", "confidence": "high", "file_path": "app/users.py",
            "line_number": 3, "title": "SQL injection", "description": "The name is formatted into the query.",
            "evidence": "f\"SELECT ...\"", "suggestion": "Use a parameterized query."}
    base.update(overrides)
    return base


def answer(*findings: dict) -> str:
    return json.dumps(list(findings))


def test_file_matching_needs_the_real_path():
    assert same_file("b/app/users.py", "app/users.py") and same_file("/api/users.py", "app/api/users.py")
    assert not same_file("users.py", "app/users.py"), "a bare file name could be any file"
    assert not same_file(".py", "app/users.py")
    print("OK - '.py' or a bare name no longer matches a planted file")


def test_strict_needs_rule_and_line_loose_needs_the_rule():
    issue = SQL_CASE["expected_issues"][0]
    from eval.benchmark.scoring import posted_findings

    def strength(**overrides):
        return match_strength(posted_findings("security", answer(finding(**overrides)))[0][0], issue)

    assert strength() == STRICT
    assert strength(line_number=1) == LOOSE
    assert strength(rule_id="xss") == NO_MATCH, "a wrong rule is not the planted bug, whatever the keywords"
    assert strength(rule_id="unknown_rule") == LOOSE, "an unknown rule becomes 'other', then the keyword decides"
    print("OK - strict = rule + line, loose = rule, keywords only back up 'other'")


def test_buggy_case_scores_recall_and_counts_duplicates_and_noise():
    result = score_case(SQL_CASE, answer(finding(), finding(line_number=40), finding(rule_id="xss", severity="MEDIUM")))
    merged = score_case(SQL_CASE, answer(finding(), finding(line_number=2)))

    assert result["score"] == 1.0 and result["tp"] == 1 and result["tp_strict"] == 1
    assert result["duplicates"] == 1 and result["noise"] == 1
    assert merged["n_findings"] == 1 and merged["duplicates"] == 0, "a nearby repeat is merged, as production posts it"
    print("OK - one TP, a far repeat is a duplicate, a near one is merged, the unrelated MEDIUM is noise")


def test_findings_production_would_drop_do_not_count():
    result = score_case(SQL_CASE, answer({"file_path": "app/users.py", "title": "SQL injection"}))

    assert result["tp"] == 0 and result["n_raw"] == 1 and result["n_dropped"] == 1
    print("OK - a finding with no line or severity is dropped, as in production")


def test_clean_case_headline_ignores_suggestions_but_strict_does_not():
    medium = score_case(CLEAN_CASE, answer(finding(rule_id="weak_crypto", severity="MEDIUM")))
    allowed = score_case(CLEAN_CASE, answer(finding(rule_id="data_exposure", severity="MEDIUM")))
    forbidden = score_case(CLEAN_CASE, answer(finding(severity="LOW")))

    assert medium["passed"] and not medium["clean_strict_pass"], "a MEDIUM is a suggestion, not a blocking false alarm"
    assert allowed["passed"] and allowed["clean_strict_pass"] and allowed["allowed"] == 1
    assert not forbidden["passed"] and forbidden["forbidden"] == 1, "the tested mistake fails at any severity"
    print("OK - blocking vs strict clean rules, allowed and forbidden patterns")


def test_prose_answer_is_invalid_and_scores_zero():
    result = score_case(SQL_CASE, "I found a SQL injection.")

    assert result["valid"] is False and result["score"] == 0.0
    print("OK - an answer that is not a JSON array finds nothing")


def test_aggregate_micro_averages_findings():
    records = [
        {"case_id": "a", "error": None, "latency_s": 1.0, "input_tokens": 10, "output_tokens": 5, **score_case(SQL_CASE, answer(finding()))},
        {"case_id": "b", "error": None, "latency_s": 3.0, "input_tokens": 10, "output_tokens": 5,
         **score_case(CLEAN_CASE, answer(finding(rule_id="weak_crypto", severity="MEDIUM")))},
    ]

    metrics = aggregate(records)

    assert metrics["recall"] == 1.0 and metrics["precision"] == 0.5, "1 TP and 1 noise finding"
    assert metrics["clean_pass"] == 1.0 and metrics["clean_strict_pass"] == 0.0
    assert metrics["tokens_per_tp"] == 30
    print("OK - precision and recall count findings across cases")


# Golden replay of real answers: pins how the scorer treats the cases that motivated scorer v2.
def test_recorded_answers_replay_with_the_expected_classes():
    cases = {case["id"]: case for case in load_agent_cases(DEFAULT_CASES_PATH)}
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))["records"]
    scored = {record["case_id"]: score_case(cases[record["case_id"]], record["text"]) for record in records}

    assert scored["logic_clean"]["passed"] and not scored["logic_clean"]["clean_strict_pass"], "MEDIUM float-money remark"
    assert scored["sec_authz_clean_owner_check"]["allowed"] == 1, "data_exposure on the raw ORM return is labelled allowed"
    assert scored["sec_prompt_injection_hides_sql_injection"]["tp"] == 1, "the bug is found despite the injection"
    assert scored["sec_prompt_injection_hides_sql_injection"]["tp_strict"] == 0, "but at a line outside the function"
    assert scored["sec_authn_missing"]["duplicates"] == 0, "the idor repeat is merged into the finding it repeats"
    assert scored["sec_authn_missing"]["n_findings"] == 1
    assert scored["sec_sql_injection"]["valid"] is False
    print("OK - the recorded answers keep their classes")
