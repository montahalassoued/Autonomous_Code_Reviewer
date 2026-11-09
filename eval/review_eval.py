import argparse
import contextlib
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from eval.benchmark.call_cache import CallCache
from eval.llm_benchmark import provenance
from eval.review_harness.eval_gate import compare_to_baseline, format_gate_report, write_baseline
from eval.review_harness.case_builder import FIXTURE_REPO, REVIEW_CASES, load_review_cases
from eval.review_harness.recorded_client import RecordedGroq
from eval.review_harness.review_report import format_review_markdown, summarize_review_records
from eval.review_harness.review_scoring import failed_review, score_review
from code_index.ingestion import ingest_repository
from review.agents.agent_runner import agent_model, build_groq_client, fallback_agent_model
from review.agents.logic import agent as logic_agent
from review.agents.quality import agent as quality_agent
from review.agents.security import agent as security_agent
from review.diff_budget import fit_diff, max_diff_chars
from review.graph import REVIEW_AGENTS, ReviewAgentsUnavailable, build_graph
from review.state import initial_state
from tools.github_tools import changed_files_in_diff


EVAL_DIR = Path(__file__).parent
DEFAULT_RESULTS_DIR = EVAL_DIR / "results"
DEFAULT_CACHE_PATH = EVAL_DIR / "cache" / "review_eval_calls.jsonl"
FIXTURE_REPO_NAME = "eval/review-fixture"


# The agents look their client up at call time, so the eval swaps in the recording one without touching review/.
def use_client(client: RecordedGroq) -> None:
    for module in (security_agent, logic_agent, quality_agent):
        module._build_groq_client = lambda: client


def run_case(compiled_graph, case: dict, run: int, label: str) -> list[dict]:
    diff, over_budget = fit_diff(case["diff_text"], max_diff_chars())
    state = initial_state(0, FIXTURE_REPO_NAME, diff, changed_files_in_diff(diff), over_budget)
    started = time.monotonic()
    try:
        final_state = compiled_graph.invoke(state)
    except ReviewAgentsUnavailable as exc:
        return failed_review(case, run, label, str(exc))
    return score_review(case, final_state, run, label, time.monotonic() - started)


# Every case through the production graph, run after run; a spent daily quota stops it with what is done saved.
# live_client=None replays recorded calls only (--offline).
def run_eval(cases: list[dict], runs: int, label: str, cache: CallCache,
             live_client: Callable[[], object] | None) -> tuple[list[dict], dict]:
    with contextlib.redirect_stdout(io.StringIO()):  # ingestion prints one line per parsed file
        compiled_graph = build_graph(ingest_repository(str(FIXTURE_REPO)), str(FIXTURE_REPO))
    records, totals, stopped = [], {"recorded_calls": 0, "live_calls": 0}, None
    for run in range(1, runs + 1):
        client = RecordedGroq(cache, run, live_client)
        use_client(client)
        for case in cases:
            print(f"run {run}/{runs} {case['id']}...", flush=True)
            missing_before = client.missing_calls
            case_records = run_case(compiled_graph, case, run, label)
            # An unrecorded call in --offline mode means the requests changed: the case cannot be replayed, not a regression.
            stale = client.missing_calls > missing_before
            records += [{**record, "stale": stale} for record in case_records]
            if client.exhausted_models:
                stopped = f"daily quota spent for {', '.join(sorted(client.exhausted_models))} at run {run}, case {case['id']}"
                break
        totals["recorded_calls"] += client.recorded_calls
        totals["live_calls"] += client.live_calls
        if stopped:
            break
    return records, {**totals, "stopped": stopped}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run whole reviews (agents with tools + synthesizer) on the golden PRs.")
    parser.add_argument("--label", default="tools", help="name of this configuration in the report")
    parser.add_argument("--mode", choices=["tools"], default="tools", help="tools: the diff plus the agents' tools (today's production)")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--offline", action="store_true", help="replay recorded calls only; an unrecorded call fails its agent")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--baseline", type=Path, help="compare with this baseline; exit 1 if any case got worse (the CI gate)")
    parser.add_argument("--write-baseline", type=Path, help="save this run's outcomes as the new baseline")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    arguments = _parse_args()
    cases = load_review_cases()
    if arguments.only:
        wanted = set(arguments.only.split(","))
        cases = [case for case in cases if case["id"] in wanted]
    records, totals = run_eval(cases, arguments.runs, arguments.label, CallCache(arguments.cache),
                               None if arguments.offline else build_groq_client)

    settings = {
        "label": arguments.label, "mode": arguments.mode, "runs": arguments.runs, "cases": len(cases),
        "models": {agent: agent_model(agent) for agent in REVIEW_AGENTS}, "fallback_model": fallback_agent_model(),
        **totals, **provenance(REVIEW_CASES),
    }
    arguments.results_dir.mkdir(parents=True, exist_ok=True)
    stem = arguments.results_dir / f"review_eval_{datetime.now():%Y%m%d_%H%M%S}"
    summary = summarize_review_records(records)
    stem.with_suffix(".json").write_text(json.dumps({"settings": settings, "summary": summary, "records": records}, indent=2),
                                         encoding="utf-8")
    markdown = format_review_markdown(records, settings)
    stem.with_suffix(".md").write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"Saved {stem}.json and .md")
    if arguments.write_baseline:
        write_baseline(arguments.write_baseline, records, settings)
        print(f"Baseline written to {arguments.write_baseline}")
    if arguments.baseline:
        sys.exit(run_gate(json.loads(arguments.baseline.read_text(encoding="utf-8")), records))


# Prints the gate report; 1 on a regression, or when no gated case could be replayed (a gate that checks nothing must not pass).
def run_gate(baseline: dict, records: list[dict]) -> int:
    result = compare_to_baseline(baseline, records)
    print(format_gate_report(result))
    compared = len(baseline["outcomes"]) - len(result["stale"])
    if not compared:
        print("No baseline case could be replayed: re-record the calls with a live run.")
    return 1 if result["regressions"] or not compared else 0


if __name__ == "__main__":
    main()
