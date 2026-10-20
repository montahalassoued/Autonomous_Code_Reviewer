import json
from types import SimpleNamespace

import pytest

from eval.benchmark.call_cache import CallCache
from eval.benchmark.dataset_schema import AGENT_RULES
from eval.review_eval import run_eval
from eval.review_harness.case_builder import FIXTURE_REPO, apply_edits, load_review_cases
from eval.review_harness.recorded_client import CallNotRecorded, RecordedGroq
from eval.review_harness.review_report import format_review_markdown, review_costs, summarize_review_records
from review.agents.logic import agent as logic_agent
from review.agents.quality import agent as quality_agent
from review.agents.security import agent as security_agent
from review.diff_line_map import map_diff_lines
from review.graph import REVIEW_AGENTS
from review.untrusted_input import wrap_untrusted

SQL_FINDING = {
    "severity": "CRITICAL", "rule_id": "sql_injection", "confidence": "high", "file_path": "shop/users.py", "line_number": 3,
    "title": "SQL injection in search_users", "description": "The search term is put into the SQL text.",
    "evidence": "return fetch_all(f\"SELECT id, email FROM users WHERE email LIKE '%{term}%'\")",
    "suggestion": "Pass the term as a query parameter.",
}


# Answers like a model: the security prompt gets `security_answer`, the other agents an empty array.
class FakeLiveGroq:

    def __init__(self, security_answer: list[dict]) -> None:
        self.security_answer = security_answer
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request) -> SimpleNamespace:
        self.requests.append(request)
        security = "application security engineer" in request["messages"][0]["content"]
        content = json.dumps(self.security_answer if security else [])
        message = SimpleNamespace(content=content, tool_calls=None)
        usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=100)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage)


def test_every_golden_pr_builds_and_its_issues_sit_on_added_lines():
    cases = load_review_cases()

    assert len(cases) >= 10 and any(not case["expected_issues"] for case in cases), "clean PRs measure false positives"
    assert any("prompt_injection" in case["tags"] for case in cases)
    for case in cases:
        files = map_diff_lines(case["diff_text"])
        for issue in case["expected_issues"]:
            assert issue["agent"] in REVIEW_AGENTS, case["id"]
            assert set(issue["rule_ids"]) <= set(AGENT_RULES[issue["agent"]]), f"{case['id']}: unknown rule id"
            assert issue["line_range"][0] in files[issue["file"]].added, f"{case['id']}: the issue is not on an added line"
    print(f"OK - {len(cases)} golden PRs, every planted issue on a line the diff adds")


def test_an_edit_that_matches_twice_is_refused():
    with pytest.raises(ValueError, match="exactly once"):
        apply_edits("bad_case", [{"file": "shop/users.py", "find": ["    row = fetch_one("], "replace": ["x"]}], FIXTURE_REPO)
    print("OK - an ambiguous edit fails loudly instead of changing the wrong function")


def test_a_recorded_call_replays_without_the_provider_whatever_its_nonce(tmp_path):
    cache = CallCache(tmp_path / "calls.jsonl")
    live = FakeLiveGroq([SQL_FINDING])

    def ask(client: RecordedGroq) -> str:
        prompt = "application security engineer\n" + wrap_untrusted("+ x = 1", "PULL_REQUEST_DIFF")
        request = {"model": "m", "temperature": 0.1, "max_completion_tokens": 10, "messages": [{"role": "user", "content": prompt}]}
        return client.chat.completions.create(**request).choices[0].message.content

    first = ask(RecordedGroq(cache, run=1, live_client=lambda: live))
    replayed = ask(RecordedGroq(CallCache(tmp_path / "calls.jsonl"), run=1, live_client=None))

    assert replayed == first and len(live.requests) == 1, "a new nonce must not turn a recorded call into a new one"
    with pytest.raises(CallNotRecorded):
        ask(RecordedGroq(cache, run=2, live_client=None))
    print("OK - recorded calls replay for free; another run is a new sample")


def test_a_whole_review_is_scored_per_category_and_as_posted(tmp_path, monkeypatch):
    cases = [case for case in load_review_cases() if case["id"] in ("sec_sql_injection_search", "clean_docs_only")]
    live = FakeLiveGroq([SQL_FINDING])
    for module in (security_agent, logic_agent, quality_agent):
        # run_eval swaps the agents' client; monkeypatch puts the real one back after the test.
        monkeypatch.setattr(module, "_build_groq_client", module._build_groq_client)

    records, totals = run_eval(cases, runs=1, label="test", cache=CallCache(tmp_path / "calls.jsonl"),
                               live_client=lambda: live)
    rows = {row["row"]: row for row in summarize_review_records(records)}

    assert rows["security"]["recall"] == 1.0 and rows["review"]["recall"] == 1.0
    assert rows["security"]["line_accuracy"] == 1.0, "the locator moved line 3 to the quoted line 17"
    assert rows["logic"]["clean_pass"] == 1.0, "logic had nothing to find and reported nothing"
    assert rows["security"]["precision"] < 1.0, "the same SQL finding on the docs-only PR is noise"
    assert totals["live_calls"] == len(live.requests) == 6, "2 PRs x 3 agents, no tools called"
    assert review_costs(records)["calls_per_review"] == 3
    report = format_review_markdown(records, {"label": "test", "cases": 2, "runs": 1, "mode": "tools", "models": {},
                                              "recorded_calls": 0, **totals})
    assert "| review |" in report and "clean_docs_only" in report
    print("OK - the production graph runs on the fixture and is scored per category and per review")


def test_an_allowed_finding_with_an_agent_applies_to_that_category_only():
    from eval.review_harness.review_scoring import category_case

    [case] = [case for case in load_review_cases() if case["id"] == "sec_sql_injection_search"]

    assert category_case(case, "quality")["allowed_findings"], "quality may remark on the missing LIMIT"
    assert not category_case(case, "logic")["allowed_findings"], "the label is not a free pass for logic"
    print("OK - an allowed finding labelled for quality does not excuse the other agents")
