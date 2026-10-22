import json

from eval.benchmark.dataset_schema import normalize_case
from eval.benchmark.model_selection import beats, recommend_models, top_models
from eval.benchmark.report import format_markdown
from eval.benchmark.scoring import score_case

DIFF = ["diff --git a/app/users.py b/app/users.py", "--- a/app/users.py", "+++ b/app/users.py", "@@ -1,1 +1,2 @@",
        " import sqlite3", "+query = f\"SELECT * FROM users WHERE name = '{name}'\""]
FOUND = json.dumps([{"severity": "HIGH", "rule_id": "sql_injection", "confidence": "high", "file_path": "app/users.py",
                     "line_number": 2, "title": "SQL injection", "description": "d", "evidence": "e", "suggestion": "s"}])


def case(case_id: str, agent: str) -> dict:
    return normalize_case({"id": case_id, "agent": agent, "diff": DIFF, "expected_issues": [
        {"file": "app/users.py", "rule_ids": ["sql_injection"], "line_range": [2, 2], "keywords": ["sql"]}]})


# Scored records for `model`: it finds the bug on the first `found` cases, in all 3 runs.
def records_for(model: str, agent: str, found: int, total: int = 4, tokens: int = 100) -> list[dict]:
    records = []
    for index in range(total):
        planted = case(f"{agent}_{index}", agent)
        for run in (1, 2, 3):
            scored = score_case(planted, FOUND if index < found else "[]")
            records.append({"model": model, "agent": agent, "case_id": planted["id"], "run": run, "error": None,
                            "latency_s": 1.0, "input_tokens": tokens, "output_tokens": 0, **scored})
    return records


def test_one_case_is_a_tie_two_cases_are_a_win():
    one = records_for("groq:a", "security", found=4) + records_for("groq:b", "security", found=3)
    two = records_for("groq:a", "security", found=4) + records_for("groq:b", "security", found=2)

    assert not beats(one, "groq:a", "groq:b") and top_models(one) == ["groq:a", "groq:b"]
    assert beats(two, "groq:a", "groq:b") and top_models(two) == ["groq:a"]
    print("OK - one case apart is noise, two cases in every run decide")


def test_tied_agents_get_different_models_so_quotas_spread():
    records = []
    for agent in ("security", "logic", "quality"):
        records += records_for("groq:big", agent, found=4, tokens=300) + records_for("groq:small", agent, found=4)
    records += records_for("groq:third", "quality", found=4, tokens=200)

    picks = {agent: recommendation["choice"] for agent, recommendation in recommend_models(records).items()}

    assert picks["logic"] != picks["security"], "a tie never puts one model on two agents while another is free"
    assert len(set(picks.values())) == 3
    print(f"OK - tied models are spread over the agents: {picks}")


def test_a_better_model_outside_production_is_not_picked():
    records = records_for("gemini:g", "logic", found=4) + records_for("groq:a", "logic", found=2)

    recommendation = recommend_models(records)["logic"]

    assert recommendation["top"] == ["gemini:g"] and recommendation["choice"] == "groq:a"
    print("OK - production calls Groq only, so the best Groq model is picked")


def test_report_shows_the_picks_as_env_lines():
    records = records_for("groq:a", "security", found=4) + records_for("groq:b", "security", found=1, total=2)

    markdown = format_markdown([], records, {})

    assert "## Best model per agent" in markdown and "SECURITY_AGENT_MODEL=a" in markdown
    assert "groq:b has 6 of 12 security calls" in markdown, "a partial run is flagged"
    print("OK - the report names each agent's model and flags incomplete runs")


TOO_LARGE = ("APIStatusError: Error code: 413 - Request too large for model `openai/gpt-oss-20b` on tokens per minute "
             "(TPM): Limit 8000, Requested 9300")


# Turns the scored record of one run into a 413: the model never saw the prompt.
def make_too_large(record: dict) -> dict:
    return {**record, "error": TOO_LARGE, "score": 0.0}


def test_a_model_too_small_for_an_agents_prompt_is_not_eligible_for_it():
    small = records_for("groq:small", "security", found=4, tokens=10)
    small[0] = make_too_large(small[0])
    records = records_for("groq:big", "security", found=4, tokens=900) + small

    recommendation = recommend_models(records)["security"]

    assert recommendation["choice"] == "groq:big", "the cheaper model would fail on real prompts this size"
    assert recommendation["too_small"] == ["groq:small"] and "groq:small" not in recommendation["top"]
    print("OK - a single 413 on an agent's prompt rules the model out for that agent")


def test_a_413_is_left_out_of_the_scores_instead_of_counting_as_a_wrong_answer():
    from eval.benchmark.metrics import aggregate

    records = records_for("groq:small", "security", found=4)
    records[0] = make_too_large(records[0])

    metrics = aggregate(records)

    assert metrics["quality"] == 1.0 and metrics["errors"] == 0, "the call the model never saw is not scored"
    assert metrics["too_large"] == 1 and metrics["calls"] == 12
    print("OK - too-large calls are counted apart from errors and wrong answers")


def test_cut_off_answers_are_counted_and_the_report_shows_both_columns():
    from eval.benchmark.metrics import aggregate, summarize

    records = records_for("groq:a", "security", found=4) + records_for("groq:b", "security", found=4)
    records[0] = {**records[0], "finish_reason": "length"}
    records[-1] = make_too_large(records[-1])

    assert aggregate(records[:12])["cut_off"] == 1
    markdown = format_markdown(summarize(records), records, {"runs": 3})
    assert "| cut off |" in markdown and "| too large |" in markdown
    assert "too large for the model's per-minute token limit" in markdown
    assert "not eligible (too large)" in markdown
    print("OK - the report shows cut-off answers and models too small for a prompt")
