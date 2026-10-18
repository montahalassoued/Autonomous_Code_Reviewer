import json

from eval.benchmark.call_cache import CallCache
from eval.benchmark.dataset_schema import load_agent_cases, normalize_case
from eval.benchmark.metrics import summarize
from eval.benchmark.report import format_markdown
from eval.llm_benchmark import (
    DEFAULT_CASES_PATH, CallResult, _is_retryable, build_agent_prompt, provenance, replay, run_benchmark,
)


SQL_CASE = normalize_case({
    "id": "sql", "agent": "security",
    "diff": ["diff --git a/app/users.py b/app/users.py", "--- a/app/users.py", "+++ b/app/users.py", "@@ -1,1 +1,2 @@",
             " import sqlite3", "+query = f\"SELECT * FROM users WHERE name = '{name}'\""],
    "expected_issues": [{"file": "app/users.py", "rule_ids": ["sql_injection"], "line_range": [2, 2], "keywords": ["sql"]}],
})
GOOD_ANSWER = json.dumps([{
    "severity": "HIGH", "rule_id": "sql_injection", "confidence": "high", "file_path": "app/users.py", "line_number": 2,
    "title": "SQL injection", "description": "d", "evidence": "e", "suggestion": "s",
}])


# A fake model: one model answers well, one always errors; every call is counted.
class FakeModel:

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, model, prompt, temperature):
        self.calls += 1
        if model == "groq:broken":
            return CallResult("", 0.1, error="NotFoundError: model not found")
        if model == "groq:spent":
            return CallResult("", 0.1, error="RateLimitError: Rate limit reached on tokens per day (TPD): Limit 200000")
        return CallResult(GOOD_ANSWER, 0.5, input_tokens=100, output_tokens=5)


def test_run_summarize_and_report_with_a_fake_model():
    records = run_benchmark([SQL_CASE], ["groq:good", "groq:broken"], runs=2, call_fn=FakeModel())
    summary = summarize(records)

    overall = {row["model"]: row for row in summary if row["agent"] == "all"}
    assert overall["groq:good"]["quality"] == 1.0 and overall["groq:good"]["recall"] == 1.0
    assert overall["groq:broken"]["errors"] == 2 and overall["groq:broken"]["quality"] == 0.0
    markdown = format_markdown(summary, records, {"scorer_version": 2, "runs": 2})
    assert "## Overall" in markdown and "## Per agent" in markdown and "error NotFoundError" in markdown
    print("OK - records, summary rows and the Markdown report")


def test_cached_answers_cost_no_call(tmp_path):
    cache, model = CallCache(tmp_path / "calls.jsonl"), FakeModel()

    run_benchmark([SQL_CASE], ["groq:good"], runs=2, call_fn=model, cache=cache)
    second = run_benchmark([SQL_CASE], ["groq:good"], runs=2, call_fn=model, cache=cache)

    assert model.calls == 2, "the second benchmark is served from the cache"
    assert all(record["cached"] for record in second)
    print("OK - a rerun only pays for answers it does not have")


def test_a_spent_daily_quota_stops_the_model_without_fake_scores():
    model = FakeModel()

    records = run_benchmark([SQL_CASE], ["groq:spent", "groq:good"], runs=3, call_fn=model)

    assert [record["model"] for record in records] == ["groq:good"] * 3, "no 0.00 record for calls the quota refused"
    assert model.calls == 4, "one refused call, then the spent model is skipped"
    print("OK - a daily quota ends that model's run instead of scoring zeros")


class QuotaError(Exception):
    status_code = 429


def test_daily_quota_is_not_retried_but_a_minute_limit_is():
    assert not _is_retryable(QuotaError("Rate limit reached on tokens per day (TPD)"))
    assert _is_retryable(QuotaError("Rate limit reached on tokens per minute (TPM)"))
    print("OK - waiting seconds only helps a per-minute limit")


def test_replay_rescores_a_saved_file_without_calls(tmp_path):
    saved = tmp_path / "old.json"
    old_records = run_benchmark([SQL_CASE], ["groq:good"], call_fn=FakeModel())
    saved.write_text(json.dumps({"records": old_records}), encoding="utf-8")

    replayed = replay(saved, [SQL_CASE])

    assert [record["score"] for record in replayed] == [1.0]
    print("OK - saved answers are scored again with the current scorer")


def test_real_dataset_builds_production_prompts_and_provenance():
    cases = load_agent_cases(DEFAULT_CASES_PATH)
    for case in cases:
        prompt = build_agent_prompt(case)
        assert "<<<PULL_REQUEST_DIFF " in prompt and case["diff_text"].splitlines()[0] in prompt

    settings = provenance(DEFAULT_CASES_PATH)
    assert settings["scorer_version"] == 2 and set(settings["prompt_sha256"]) == {"security", "logic", "quality"}
    print(f"OK - {len(cases)} cases build production prompts; results record what produced them")
