from eval.benchmark.metrics import aggregate

# A model beats another only on at least this many more cases won in every common run (see IMPLEMENTATION_PLAN).
MIN_CASE_GAP = 2
PRODUCTION_PROVIDER = "groq:"
ERROR_OUTCOME = (-1.0, 0, 0, 0)


# One run of one case, best first: bugs found, then no blocking false alarm, then fewer extra findings, then exact lines.
def _outcome(record: dict) -> tuple:
    if record.get("error"):
        return ERROR_OUTCOME
    return (record["score"], -(record["blocking_noise"] + record["forbidden"]),
            -(record["noise"] + record["duplicates"]), record["tp_strict"])


def _outcomes(records: list[dict]) -> dict[tuple[str, str], dict[int, tuple]]:
    by_model_case: dict[tuple[str, str], dict[int, tuple]] = {}
    for record in records:
        by_model_case.setdefault((record["model"], record["case_id"]), {})[record["run"]] = _outcome(record)
    return by_model_case


# Cases `a` wins and cases `b` wins: a case is won only when it is better in every run both models have.
def case_wins(records: list[dict], a: str, b: str) -> tuple[int, int]:
    outcomes = _outcomes(records)
    wins_a = wins_b = 0
    for case_id in {record["case_id"] for record in records}:
        runs_a, runs_b = outcomes.get((a, case_id), {}), outcomes.get((b, case_id), {})
        common = runs_a.keys() & runs_b.keys()
        if not common:
            continue
        wins_a += all(runs_a[run] > runs_b[run] for run in common)
        wins_b += all(runs_a[run] < runs_b[run] for run in common)
    return wins_a, wins_b


def beats(records: list[dict], a: str, b: str) -> bool:
    wins_a, wins_b = case_wins(records, a, b)
    return wins_a - wins_b >= MIN_CASE_GAP


# Models no other model beats on this agent's cases: a tie under the decision rule.
def top_models(records: list[dict]) -> list[str]:
    models = sorted({record["model"] for record in records})
    return [model for model in models if not any(beats(records, other, model) for other in models if other != model)]


def _tokens_per_tp(records: list[dict], model: str) -> float:
    value = aggregate([record for record in records if record["model"] == model])["tokens_per_tp"]
    return value if value is not None else float("inf")


# Per agent: the tied best models, and one production (Groq) model each, spread so no model serves two agents when a tie allows.
def recommend_models(records: list[dict]) -> dict[str, dict]:
    by_agent: dict[str, list[dict]] = {}
    for record in records:
        by_agent.setdefault(record["agent"], []).append(record)
    tops = {agent: top_models(agent_records) for agent, agent_records in by_agent.items()}

    recommendations, used = {}, set()
    for agent in sorted(tops, key=lambda name: (len(tops[name]), name)):
        agent_records = by_agent[agent]
        groq_models = sorted({record["model"] for record in agent_records if record["model"].startswith(PRODUCTION_PROVIDER)})
        candidates = [model for model in tops[agent] if model.startswith(PRODUCTION_PROVIDER)]
        if not candidates:
            # Every tied best model is outside production: take the Groq model with the best mean case score.
            candidates = sorted(groq_models, key=lambda model: -aggregate(
                [record for record in agent_records if record["model"] == model])["quality"])[:1]
        candidates.sort(key=lambda model: (model in used, _tokens_per_tp(agent_records, model), model))
        choice = candidates[0] if candidates else None
        if choice:
            used.add(choice)
        recommendations[agent] = {"top": tops[agent], "choice": choice, "cases": len({r["case_id"] for r in agent_records})}
    return recommendations
