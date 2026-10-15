import json

from eval.llm_benchmark import (
    ALL_TASKS,
    DEFAULT_CASES_PATH,
    TASKS,
    CallResult,
    format_markdown,
    load_cases,
    run_benchmark,
    score_agent,
    summarize,
)


SQL_CASE = {
    "id": "sql",
    "agent": "security",
    "diff": "+ query = f\"SELECT ... {name}\"",
    "expected_issues": [{"file": "app/users.py", "line": 15, "keywords": ["sql injection"]}],
}
CLEAN_CASE = {"id": "clean", "agent": "security", "diff": "+ x = 1", "expected_issues": []}


# Builds a complete finding dict, with any field overridden.
def make_finding(**overrides) -> dict:
    finding = {
        "severity": "HIGH",
        "file_path": "app/users.py",
        "line_number": 15,
        "title": "SQL Injection (CWE-89)",
        "description": "User input is formatted into the query.",
        "evidence": "f\"SELECT ...\"",
        "suggestion": "Use a parameterized query.",
    }
    finding.update(overrides)
    return finding


def test_agent_finds_planted_bug():
    result = score_agent(SQL_CASE, json.dumps([make_finding()]))
    assert result["score"] == 1.0
    assert result["valid"] is True
    assert result["line_hits"] == 1


def test_agent_accepts_diff_prefix_and_fenced_output():
    output = "```json\n" + json.dumps([make_finding(file_path="b/app/users.py", line_number=40)]) + "\n```"
    result = score_agent(SQL_CASE, output)
    assert result["score"] == 1.0
    assert result["line_hits"] == 0  # 40 is too far from 15


def test_agent_wrong_file_is_a_miss():
    result = score_agent(SQL_CASE, json.dumps([make_finding(file_path="other.py")]))
    assert result["score"] == 0.0


def test_agent_unparseable_output_is_invalid():
    result = score_agent(SQL_CASE, "I could not find anything.")
    assert result == {"score": 0.0, "valid": False, "n_findings": 0}


def test_agent_missing_fields_found_but_invalid():
    result = score_agent(SQL_CASE, json.dumps([{"file_path": "app/users.py", "title": "SQL injection"}]))
    assert result["score"] == 1.0
    assert result["valid"] is False


def test_agent_clean_diff_tolerates_low_but_not_medium():
    assert score_agent(CLEAN_CASE, json.dumps([make_finding(severity="LOW")]))["score"] == 1.0
    assert score_agent(CLEAN_CASE, "[]")["score"] == 1.0
    result = score_agent(CLEAN_CASE, json.dumps([make_finding(severity="MEDIUM")]))
    assert result["score"] == 0.0
    assert result["false_positives"] == 1


# A fake model: one model answers well, one always errors.
def fake_call(model, prompt, temperature):
    if model == "groq:broken":
        return CallResult("", 0.1, error="NotFoundError: model not found")
    return CallResult(json.dumps([make_finding()]), 0.5, input_tokens=100, output_tokens=5)


def test_run_and_summarize_with_fake_model():
    cases = {"agents": [SQL_CASE]}
    records = run_benchmark(cases, ["groq:good", "groq:broken"], ["agents"], runs=2, call_fn=fake_call)
    assert len(records) == 4

    summary = summarize(records)
    assert [row["model"] for row in summary] == ["groq:good", "groq:broken"]  # best first
    good, broken = summary
    assert good["quality"] == 1.0 and good["errors"] == 0 and good["input_tokens_mean"] == 100
    assert broken["quality"] == 0.0 and broken["errors"] == 2

    markdown = format_markdown(summary)
    assert "## agents" in markdown and "**agents**: groq:good" in markdown


def test_real_dataset_loads_and_builds_prompts():
    cases = load_cases(DEFAULT_CASES_PATH)
    assert set(cases) == set(ALL_TASKS)
    for task, task_cases in cases.items():
        build_prompt, _ = TASKS[task]
        assert task_cases, f"no cases for {task}"
        for case in task_cases:
            assert build_prompt(case).strip()
