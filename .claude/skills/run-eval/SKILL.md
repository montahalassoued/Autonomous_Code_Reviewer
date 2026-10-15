---
name: run-eval
description: Run or extend the evaluation of this project's LLMs and review quality (eval/llm_benchmark.py today, the Phase 1 review eval harness later) and compare results before/after a change to prompts, models, retrieval or agents. Use when the user asks to evaluate, benchmark, or compare models or quality.
---

# Evaluation

## What exists today: `eval/llm_benchmark.py`

It compares free LLMs on the 4 LLM jobs (agents, decomposer, judge, chunker) using the **real production prompts** and deterministic scoring (no LLM grader).
- **Dataset:** `eval/dataset/llm_benchmark_cases.json`. Each case has a known right answer.
- **Results:** `eval/results/llm_benchmark_<timestamp>.json` and `.md`
- **Unit tests:** `tests/unit/test_llm_benchmark.py` (offline)

```bash
venv/Scripts/python.exe -m eval.llm_benchmark --list-models   # what your keys can call
venv/Scripts/python.exe -m eval.llm_benchmark --tasks agents,judge --models groq:<m>,gemini:<m> --runs 3
```

Every run makes **real API calls**, so the user runs it. Before giving the command, tell the user roughly how many calls it makes (cases × models × runs).

## What's planned: the review eval harness (Phase 1 of `docs/IMPLEMENTATION_PLAN.md`)

Golden PRs with planted bugs, measuring:
- context recall and precision
- findings per category
- false positives on clean PRs
- calls per review

The metrics are custom and deterministic, not RAGAS. Scores are pushed to **Langfuse** as dataset runs.

## Comparing before and after a change

1. Keep the dataset fixed between the runs.
2. Run a baseline with `--runs 3`, make the change, then run again.
3. Report a table of each metric: before, after, and the delta. Treat differences of about 0.02 or less as noise.
4. Say so plainly when a metric gets worse. Never present a regression as an improvement.
