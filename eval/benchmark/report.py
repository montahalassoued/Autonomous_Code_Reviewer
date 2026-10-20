from eval.benchmark.model_selection import MIN_CASE_GAP, PRODUCTION_PROVIDER, recommend_models


def _cell(value, kind: str = "ratio") -> str:
    if value is None:
        return "-"
    if kind == "ratio":
        return f"{value:.2f}"
    if kind == "percent":
        return f"{value:.0%}"
    if kind == "seconds":
        return f"{value:.1f}"
    return f"{value:,.0f}"


MAIN_COLUMNS = (
    ("quality", "quality", "ratio"), ("recall", "recall", "ratio"), ("strict recall", "strict_recall", "ratio"),
    ("precision", "precision", "ratio"), ("F1", "f1", "ratio"), ("clean pass", "clean_pass", "ratio"),
    ("clean strict", "clean_strict_pass", "ratio"), ("rule acc", "rule_accuracy", "ratio"),
    ("line acc", "line_accuracy", "ratio"), ("invalid", "invalid_rate", "percent"), ("errors", "errors", "count"),
    ("legacy", "legacy_quality", "ratio"),
)
COST_COLUMNS = (
    ("latency avg (s)", "latency_mean_s", "seconds"), ("latency p95 (s)", "latency_p95_s", "seconds"),
    ("tokens in", "input_tokens", "count"), ("tokens out", "output_tokens", "count"),
    ("tokens / TP", "tokens_per_tp", "count"), ("retries", "retries", "count"), ("cached", "cached_calls", "count"),
    ("calls", "calls", "count"),
)


def _table(rows: list[dict], columns, first: tuple[str, str]) -> list[str]:
    lines = [f"| {first[0]} | " + " | ".join(title for title, _key, _kind in columns) + " |",
             "|---|" + "---|" * len(columns)]
    for row in rows:
        lines.append(f"| {row[first[1]]} | " + " | ".join(_cell(row[key], kind) for _title, key, kind in columns) + " |")
    return lines


# Cases that did not pass, with the findings that made them fail: what to fix in a prompt or to label in the dataset.
def _failures(records: list[dict]) -> list[str]:
    lines = []
    for record in records:
        if record.get("error"):
            lines.append(f"- `{record['case_id']}` run {record['run']} ({record['model']}): error {record['error']}")
            continue
        if record["passed"] and not (record["noise"] or record["duplicates"] or record["forbidden"]):
            continue
        missed = record["required"] - record["tp"]
        summary = f"missed {missed} of {record['required']}" if record["required"] else "clean case"
        lines.append(f"- `{record['case_id']}` run {record['run']} ({record['model']}): score {record['score']:.2f}, {summary}")
        for finding in record["findings"]:
            if finding["class"] in ("noise", "forbidden", "duplicate"):
                lines.append(
                    f"  - {finding['class']}: {finding['severity']}/{finding['confidence']} `{finding['rule_id']}` "
                    f"line {finding['line_number']}: {finding['title']}"
                )
    return lines


# Which model to give each agent; shown only when the run compares several models.
def _model_choice(records: list[dict]) -> list[str]:
    if len({record["model"] for record in records}) < 2:
        return []
    recommendations = recommend_models(records)
    lines = [
        "## Best model per agent", "",
        f"A model beats another when it wins at least {MIN_CASE_GAP} more cases, each one in every run both models have; "
        "models nobody beats are tied. The pick is a Groq model (production calls Groq only), a different one per agent "
        "when a tie allows it so each model's daily quota serves one agent, then the fewest tokens per true positive.", "",
        "| agent | cases | tied best | pick |", "|---|---|---|---|",
    ]
    env_lines, partial = [], []
    for agent, recommendation in sorted(recommendations.items()):
        choice = recommendation["choice"] or "-"
        lines.append(f"| {agent} | {recommendation['cases']} | {', '.join(recommendation['top'])} | {choice} |")
        if recommendation["choice"]:
            env_lines.append(f"{agent.upper()}_AGENT_MODEL={recommendation['choice'].removeprefix(PRODUCTION_PROVIDER)}")
        calls = {}
        for record in records:
            if record["agent"] == agent:
                calls[record["model"]] = calls.get(record["model"], 0) + 1
        most = max(calls.values())
        partial += [f"- {model} has {count} of {most} {agent} calls: compare again once its run is complete."
                    for model, count in sorted(calls.items()) if count < most]
    lines += ["", "```", *env_lines, "```", ""]
    if partial:
        lines += ["Incomplete comparisons:", "", *partial, ""]
    return lines


def format_markdown(summary: list[dict], records: list[dict], settings: dict) -> str:
    overall = [row for row in summary if row["agent"] == "all"]
    per_agent = [row for row in summary if row["agent"] != "all"]
    lines = [
        "# LLM benchmark results", "",
        f"Scorer v{settings.get('scorer_version')}, dataset `{settings.get('dataset_sha256')}`, "
        f"commit `{settings.get('git_commit') or 'unknown'}`, {settings.get('runs')} run(s).", "",
        "quality: mean case score (buggy: share of required issues found; clean: no blocking false alarm). "
        "recall/precision are micro-averaged over findings; clean strict: no MEDIUM+ noise at all; "
        "legacy: scorer v1 (file + any keyword).", "",
        "## Overall", "", *_table(overall, MAIN_COLUMNS, ("model", "model")), "",
        "## Cost", "", *_table(overall, COST_COLUMNS, ("model", "model")), "",
        "## Per agent", "", *_table([{**row, "label": f"{row['model']} / {row['agent']}"} for row in per_agent],
                                    MAIN_COLUMNS, ("model / agent", "label")), "",
        *_model_choice(records),
    ]
    failures = _failures(records)
    lines += ["## Failures and noise (triage)", "", *(failures or ["None."]), ""]
    return "\n".join(lines)
