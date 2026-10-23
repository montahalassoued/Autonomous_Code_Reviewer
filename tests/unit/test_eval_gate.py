import json
from pathlib import Path

from eval.benchmark.call_cache import CallCache
from eval.review_eval import run_eval, run_gate
from eval.review_harness.case_builder import load_review_cases
from eval.review_harness.eval_gate import compare_to_baseline
from review.agents.logic import agent as logic_agent
from review.agents.quality import agent as quality_agent
from review.agents.security import agent as security_agent

RECORDING = Path("eval/recordings/review_eval_calls.jsonl")
BASELINE = Path("eval/baselines/review_eval_baseline.json")


def outcome(**overrides) -> dict:
    return {"score": 1.0, "tp": 1, "tp_strict": 1, "noise": 0, "blocking_noise": 0, "duplicates": 0, "forbidden": 0, **overrides}


def record(case_id: str = "case_a", agent: str = "review", stale: bool = False, error: str | None = None, **fields) -> dict:
    return {"case_id": case_id, "run": 1, "agent": agent, "stale": stale, "error": error, **outcome(**fields)}


def test_a_lost_bug_or_new_noise_is_a_regression_and_the_reverse_an_improvement():
    baseline = {"outcomes": {"case_a#run1#review": outcome(), "case_b#run1#review": outcome(noise=2)}}

    result = compare_to_baseline(baseline, [record("case_a", tp=0, score=0.0), record("case_b", noise=0)])

    assert result["regressions"] == ["case_a#run1#review: score 1.0 -> 0.0", "case_a#run1#review: tp 1 -> 0"]
    assert result["improvements"] == ["case_b#run1#review: noise 2 -> 0"]
    print("OK - fewer bugs found fails the gate; less noise is reported as an improvement")


def test_a_stale_case_is_listed_not_failed_and_a_gate_that_replays_nothing_fails():
    baseline = {"outcomes": {"case_a#run1#review": outcome()}}

    result = compare_to_baseline(baseline, [record("case_a", stale=True, error="partial review")])

    assert result["stale"] == ["case_a#run1#review"] and not result["regressions"]
    assert run_gate(baseline, [record("case_a", stale=True, error="partial review")]) == 1, "nothing was checked"
    assert run_gate(baseline, [record("case_a")]) == 0
    print("OK - changed requests are re-recorded, never reported as a regression, and never pass an empty gate")


def test_a_new_error_on_a_replayed_case_is_a_regression():
    baseline = {"outcomes": {"case_a#run1#review": outcome()}}

    result = compare_to_baseline(baseline, [record("case_a", error="partial review")])

    assert result["regressions"] == ["case_a#run1#review: error 'partial review'"]
    print("OK - an agent that now fails on recorded answers fails the gate")


# The real recording through the real graph: undoing the cross-agent merge brings back logic's duplicate of the SQL bug.
def test_the_gate_catches_a_real_regression_on_the_committed_recording(monkeypatch):
    for module in (security_agent, logic_agent, quality_agent):
        monkeypatch.setattr(module, "_build_groq_client", module._build_groq_client)
    cases = [case for case in load_review_cases() if case["id"] == "sec_sql_injection_search"]
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    records, _ = run_eval(cases, runs=1, label="gate", cache=CallCache(RECORDING), live_client=None)
    assert run_gate(baseline, records) == 0, "the committed baseline matches the committed code"

    monkeypatch.setattr("review.synthesizer.merge_cross_agent_findings", lambda findings: findings)
    records, _ = run_eval(cases, runs=1, label="gate", cache=CallCache(RECORDING), live_client=None)
    assert run_gate(baseline, records) == 1
    assert compare_to_baseline(baseline, records)["regressions"] == ["sec_sql_injection_search#run1#review: duplicates 0 -> 1"]
    print("OK - removing the cross-agent merge fails the gate on recorded answers, with no API call")
