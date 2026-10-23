import json
from pathlib import Path


# What a case's review must not lose: found bugs, and must not gain: noise, duplicates, forbidden findings, errors.
HIGHER_IS_BETTER = ("score", "tp", "tp_strict")
LOWER_IS_BETTER = ("noise", "blocking_noise", "duplicates", "forbidden")
OUTCOME_FIELDS = (*HIGHER_IS_BETTER, *LOWER_IS_BETTER)


def _key(record: dict) -> str:
    return f"{record['case_id']}#run{record['run']}#{record['agent']}"


# One outcome per (case, run, row); a replay is deterministic, so any change between two of them is the code's.
def case_outcomes(records: list[dict]) -> dict[str, dict]:
    outcomes = {}
    for record in records:
        if record.get("stale"):
            continue
        error = record.get("error")
        outcomes[_key(record)] = {"error": error} if error else {field: record[field] for field in OUTCOME_FIELDS}
    return outcomes


def write_baseline(path: Path, records: list[dict], settings: dict) -> None:
    provenance = {key: settings.get(key) for key in ("dataset_sha256", "prompt_sha256", "git_commit", "models")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provenance": provenance, "outcomes": case_outcomes(records)}, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _changes(before: dict, after: dict) -> tuple[list[str], list[str]]:
    if before.get("error") or after.get("error"):
        if before.get("error") == after.get("error"):
            return [], []
        return ([f"error {after['error']!r}"], []) if after.get("error") else ([], [f"error {before['error']!r} is gone"])
    worse = [f"{field} {before[field]} -> {after[field]}" for field in HIGHER_IS_BETTER if after[field] < before[field]]
    worse += [f"{field} {before[field]} -> {after[field]}" for field in LOWER_IS_BETTER if after[field] > before[field]]
    better = [f"{field} {before[field]} -> {after[field]}" for field in HIGHER_IS_BETTER if after[field] > before[field]]
    better += [f"{field} {before[field]} -> {after[field]}" for field in LOWER_IS_BETTER if after[field] < before[field]]
    return worse, better


# Regressions fail the gate; stale cases (requests no longer recorded) and cases new to the baseline are only listed.
def compare_to_baseline(baseline: dict, records: list[dict]) -> dict[str, list[str]]:
    expected, current = baseline["outcomes"], case_outcomes(records)
    stale = sorted({_key(record) for record in records if record.get("stale")} & set(expected))
    result: dict[str, list[str]] = {"regressions": [], "improvements": [], "stale": stale, "new": []}
    for key, after in sorted(current.items()):
        if key not in expected:
            result["new"].append(key)
            continue
        worse, better = _changes(expected[key], after)
        result["regressions"] += [f"{key}: {change}" for change in worse]
        result["improvements"] += [f"{key}: {change}" for change in better]
    return result


def format_gate_report(result: dict[str, list[str]]) -> str:
    lines = ["## Eval gate", ""]
    for title, key in (("Regressions (the gate fails)", "regressions"), ("Improvements (update the baseline)", "improvements"),
                       ("Stale: requests no longer recorded, re-record with a live run", "stale"),
                       ("Not in the baseline", "new")):
        if result[key]:
            lines += [f"**{title}:**", *[f"- {entry}" for entry in result[key]], ""]
    if not any(result.values()):
        lines.append("Every gated case matches the baseline.")
    return "\n".join(lines)
