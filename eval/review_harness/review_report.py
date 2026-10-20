import statistics

from eval.benchmark.metrics import aggregate
from eval.benchmark.report import _cell, _failures, _table
from eval.review_harness.review_scoring import REVIEW_ROW
from review.graph import REVIEW_AGENTS


QUALITY_COLUMNS = (
    ("quality", "quality", "ratio"), ("recall", "recall", "ratio"), ("strict recall", "strict_recall", "ratio"),
    ("precision", "precision", "ratio"), ("F1", "f1", "ratio"), ("clean pass", "clean_pass", "ratio"),
    ("clean strict", "clean_strict_pass", "ratio"), ("rule acc", "rule_accuracy", "ratio"),
    ("severity acc", "severity_accuracy", "ratio"), ("line acc", "line_accuracy", "ratio"),
    ("commentable", "commentable_rate", "ratio"), ("errors", "errors", "count"),
)


# The review row first (what the developer sees), then one row per category.
def summarize_review_records(records: list[dict]) -> list[dict]:
    return [{"row": row, **aggregate([record for record in records if record["agent"] == row])}
            for row in (REVIEW_ROW, *REVIEW_AGENTS)]


# What one review costs: calls, tokens and latency per complete review, and how many came back partial.
def review_costs(records: list[dict]) -> dict:
    reviews = [record for record in records if record["agent"] == REVIEW_ROW and record.get("llm_calls") is not None]
    latencies = sorted(record["latency_s"] for record in reviews) or [0.0]
    return {
        "reviews": len(reviews),
        "partial_reviews": sum(1 for record in reviews if record.get("failed_agents")),
        "calls_per_review": statistics.mean(record["llm_calls"] for record in reviews) if reviews else None,
        "tokens_per_review": statistics.mean(record["input_tokens"] + record["output_tokens"] for record in reviews) if reviews else None,
        "latency_mean_s": statistics.mean(latencies),
        "latency_p95_s": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))],
    }


def format_review_markdown(records: list[dict], settings: dict) -> str:
    costs = review_costs(records)
    lines = [
        f"# Review eval: {settings['label']}", "",
        f"{settings['cases']} case(s) x {settings['runs']} run(s), mode `{settings['mode']}`, models {settings['models']}. "
        f"Recorded calls replayed: {settings['recorded_calls']}, live calls: {settings['live_calls']}.", "",
        "## Quality", "",
        "The `review` row scores the posted review as a whole (cross-agent duplicates count as noise); "
        "the category rows score each agent on its own planted issues, like `eval/llm_benchmark.py`.", "",
        *_table(summarize_review_records(records), QUALITY_COLUMNS, ("row", "row")), "",
        "## Cost per review", "",
        "| reviews | partial | calls / review | tokens / review | latency avg (s) | latency p95 (s) |",
        "|---|---|---|---|---|---|",
        f"| {costs['reviews']} | {costs['partial_reviews']} | {_cell(costs['calls_per_review'], 'seconds')} | "
        f"{_cell(costs['tokens_per_review'], 'count')} | {_cell(costs['latency_mean_s'], 'seconds')} | "
        f"{_cell(costs['latency_p95_s'], 'seconds')} |", "",
        "## Failures and noise (review row)", "",
        *(_failures([record for record in records if record["agent"] == REVIEW_ROW]) or ["None."]), "",
    ]
    if settings.get("stopped"):
        lines += [f"**Incomplete:** {settings['stopped']}", ""]
    return "\n".join(lines)
