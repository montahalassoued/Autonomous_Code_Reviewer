from eval.benchmark.scoring import score_findings
from review.graph import REVIEW_AGENTS


REVIEW_ROW = "review"  # the whole posted review: every agent's findings against every planted issue


# One category's view of a case: its own planted issues; a pattern without an agent applies to every category.
def category_case(case: dict, agent: str) -> dict:
    def mine(entry: dict) -> bool:
        return entry.get("agent") in (None, agent)

    return {
        **case,
        "expected_issues": [issue for issue in case["expected_issues"] if issue["agent"] == agent],
        "allowed_findings": [pattern for pattern in case["allowed_findings"] if mine(pattern)],
        "forbidden_findings": [pattern for pattern in case["forbidden_findings"] if mine(pattern)],
    }


# Records for one review: one per category (comparable with the benchmark) and one for the review as posted.
# A failed agent's category is an error, not "no findings": a spent quota must not look like a clean pass.
def score_review(case: dict, final_state: dict, run: int, label: str, latency_s: float) -> list[dict]:
    findings = final_state.get("findings") or []
    failed = set(final_state.get("failed_agents") or [])
    base = {"model": label, "case_id": case["id"], "run": run, "valid": True, "latency_s": latency_s,
            "input_tokens": 0, "output_tokens": 0}
    records = []
    for agent in REVIEW_AGENTS:
        if agent in failed:
            records.append({**base, "agent": agent, "error": "agent failed", "clean": False, "score": 0.0})
            continue
        own = [finding for finding in findings if finding["agent"] == agent]
        records.append({**base, "agent": agent, "error": None, **score_findings(category_case(case, agent), own, len(own))})

    review = {**base, "agent": REVIEW_ROW, "input_tokens": final_state.get("tokens_in", 0),
              "output_tokens": final_state.get("tokens_out", 0), "llm_calls": final_state.get("llm_calls", 0),
              "failed_agents": sorted(failed), "markdown": final_state.get("final_review_markdown", "")}
    if failed:
        records.append({**review, "error": "partial review", "clean": False, "score": 0.0})
    else:
        records.append({**review, "error": None, **score_findings(case, findings, len(findings))})
    return records


# A review that could not start (every agent failed): an error record per row, so the run stays complete.
def failed_review(case: dict, run: int, label: str, error: str) -> list[dict]:
    return [{"model": label, "case_id": case["id"], "run": run, "agent": agent, "error": error, "clean": False,
             "score": 0.0, "valid": True, "latency_s": 0.0} for agent in (*REVIEW_AGENTS, REVIEW_ROW)]
