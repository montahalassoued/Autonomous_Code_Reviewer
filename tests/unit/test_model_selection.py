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
