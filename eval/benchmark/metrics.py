import statistics


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _sum(records: list[dict], key: str) -> int:
    return sum(record.get(key, 0) for record in records)


# Micro-averaged metrics over scored records: recall and precision count findings, not cases.
def aggregate(records: list[dict]) -> dict:
    ok = [record for record in records if record.get("error") is None]
    buggy = [record for record in ok if not record["clean"]]
    clean = [record for record in ok if record["clean"]]
    tp = _sum(ok, "tp")
    false_alarms = _sum(ok, "noise") + _sum(ok, "duplicates") + _sum(ok, "forbidden")
    recall = _ratio(tp, _sum(buggy, "required"))
    precision = _ratio(tp, tp + false_alarms)
    latencies = sorted(record["latency_s"] for record in ok) or [0.0]
    tokens = _sum(ok, "input_tokens") + _sum(ok, "output_tokens")
    return {
        "quality": statistics.mean(record["score"] for record in records) if records else 0.0,
        "legacy_quality": statistics.mean(record.get("legacy_score", 0.0) for record in records) if records else 0.0,
        "recall": recall,
        "strict_recall": _ratio(_sum(ok, "tp_strict"), _sum(buggy, "required")),
        "precision": precision,
        "f1": 2 * recall * precision / (recall + precision) if recall and precision else None,
        "clean_pass": _ratio(sum(1 for record in clean if record["passed"]), len(clean)),
        "clean_strict_pass": _ratio(sum(1 for record in clean if record["clean_strict_pass"]), len(clean)),
        "rule_accuracy": _ratio(_sum(ok, "rule_ok"), tp),
        "severity_accuracy": _ratio(_sum(ok, "severity_ok"), tp),
        "line_accuracy": _ratio(_sum(ok, "line_ok"), tp),
        "commentable_rate": _ratio(_sum(ok, "commentable"), _sum(ok, "n_findings")),
        "invalid_rate": _ratio(sum(1 for record in ok if not record["valid"]), len(ok)),
        "dropped_findings": _sum(ok, "n_dropped"),
        "errors": len(records) - len(ok),
        "latency_mean_s": statistics.mean(latencies),
        "latency_p95_s": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))],
        "input_tokens": _sum(ok, "input_tokens"),
        "output_tokens": _sum(ok, "output_tokens"),
        "tokens_per_tp": _ratio(tokens, tp),
        "retries": sum(max(0, record.get("attempts", 1) - 1) for record in ok),
        "cached_calls": sum(1 for record in records if record.get("cached")),
        "calls": len(records),
        "cases": len({record["case_id"] for record in records}),
    }


# One row per model, plus one per (model, agent), best model first.
def summarize(records: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        groups.setdefault((record["model"], "all"), []).append(record)
        groups.setdefault((record["model"], record["agent"]), []).append(record)
    rows = [{"model": model, "agent": agent, **aggregate(group)} for (model, agent), group in groups.items()]
    rows.sort(key=lambda row: (row["agent"] != "all", row["agent"], -row["quality"], row["latency_mean_s"]))
    return rows
