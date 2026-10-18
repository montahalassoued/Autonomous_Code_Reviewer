import json
from pathlib import Path

from eval.benchmark.dataset_schema import AGENT_RULES, SEVERITIES, load_agent_cases
from eval.llm_benchmark import DEFAULT_CASES_PATH
from review.diff_line_map import map_diff_lines

AGENTS_DIR = Path(__file__).parents[2] / "review" / "agents"
MAX_SHARED_WITH_EXAMPLES = 0.5
MIN_CLEAN_CASES_PER_AGENT = 1

CASES = load_agent_cases(DEFAULT_CASES_PATH)


def _added_lines(diff_lines: list[str]) -> set[str]:
    return {line[1:].strip() for line in diff_lines if line.startswith("+") and not line.startswith("+++") and line[1:].strip()}


def test_case_ids_are_unique_and_agents_known():
    ids = [case["id"] for case in CASES]

    assert len(ids) == len(set(ids)), "duplicate case id"
    assert {case["agent"] for case in CASES} <= set(AGENT_RULES)
    print(f"OK - {len(ids)} cases with unique ids")


def test_labels_use_real_rules_severities_and_files():
    for case in CASES:
        rules, files = AGENT_RULES[case["agent"]], map_diff_lines(case["diff_text"])
        for issue in case["expected_issues"]:
            assert issue["rule_ids"], f"{case['id']}: every planted issue lists its rule_ids"
            assert set(issue["rule_ids"]) <= set(rules), f"{case['id']}: unknown rule in {issue['rule_ids']}"
            assert set(issue["severity"]) <= set(SEVERITIES), case["id"]
            assert issue["file"] in files, f"{case['id']}: {issue['file']} is not in the diff"
            low, high = issue["line_range"]
            assert set(range(low, high + 1)) & files[issue["file"]].added, f"{case['id']}: line_range misses the added lines"
        for pattern in case["allowed_findings"] + case["forbidden_findings"]:
            assert set(pattern["rule_ids"]) <= set(rules), f"{case['id']}: unknown rule in a pattern"
            assert pattern["file"] in files, f"{case['id']}: pattern file not in the diff"
    print("OK - rule ids, severities, files and lines match the diffs")


def test_clean_cases_say_what_mistake_they_test():
    for case in CASES:
        if not case["expected_issues"]:
            assert case["forbidden_findings"], f"{case['id']}: a clean case lists the forbidden finding it tests"
    for agent in AGENT_RULES:
        clean = [case for case in CASES if case["agent"] == agent and not case["expected_issues"]]
        assert len(clean) >= MIN_CLEAN_CASES_PER_AGENT, f"{agent} has no clean case"
    print("OK - every clean case names its trap, every agent has one")


def test_cases_do_not_copy_the_prompt_examples():
    for case in CASES:
        examples = json.loads((AGENTS_DIR / case["agent"] / "examples.json").read_text(encoding="utf-8"))
        added = _added_lines(case["diff"])
        for example in examples:
            shared = len(added & _added_lines(example["diff"])) / max(len(added), 1)
            assert shared <= MAX_SHARED_WITH_EXAMPLES, f"{case['id']} repeats a few-shot example ({shared:.0%})"
    print("OK - no case is a copy of what the prompt already shows")
