---
name: run-eval
description: Run or extend the evaluation of this project's LLMs and review quality (eval/llm_benchmark.py today, the Phase 1 review eval harness later) and compare results before/after a change to prompts, models, retrieval or agents. Use when the user asks to evaluate, benchmark, or compare models or quality.
---

# Evaluation

## What exists today: `eval/llm_benchmark.py`

It compares LLMs (and prompt versions) on the 3 review agents using the **real production prompts**, and scores what production would post (`to_finding`, `sanitize_finding`, the 20-finding cap) with deterministic rules (scorer v2, no LLM grader).
- **Dataset:** `eval/dataset/llm_benchmark_cases.json` (schema v2): each planted issue lists its `rule_ids`, `severity` band and `line_range`; clean cases list `forbidden_findings` (the mistake tested) and `allowed_findings` (defensible remarks, neutral). `tests/unit/test_benchmark_dataset.py` validates every label offline.
- **Metrics:** quality (buggy: share of required issues found; clean: no blocking false alarm), recall and strict recall (right rule and line), precision (TP vs noise, duplicates, forbidden), F1, clean strict pass (no MEDIUM+ noise), rule and line accuracy, tokens per TP, retries. Per agent, plus a triage list of every failure and noise finding.
- **Results:** `eval/results/llm_benchmark_<timestamp>.json` (every call, with the raw text and the scorer, dataset and prompt hashes) and `.md`.
- **Code:** `eval/llm_benchmark.py` (CLI, model calls) and `eval/benchmark/` (`dataset_schema`, `scoring`, `metrics`, `report`, `call_cache`, `model_selection`: the best model per agent). Unit tests: `tests/unit/test_llm_benchmark.py`, `test_benchmark_*.py` (offline, including a golden replay of real answers).

```bash
venv/Scripts/python.exe -m eval.llm_benchmark --list-models                                   # what your keys can call
venv/Scripts/python.exe -m eval.llm_benchmark --models groq:<m> --runs 3                       # real calls (user runs it)
venv/Scripts/python.exe -m eval.llm_benchmark --models groq:<m> --only case_a,case_b --runs 3  # a subset
venv/Scripts/python.exe -m eval.llm_benchmark --replay eval/results/<file>.json                # re-score, no API call
venv/Scripts/python.exe -m eval.llm_benchmark --replay <model_a>.json <model_b>.json           # compare runs, pick a model per agent
```

Real runs make **API calls**, so the user runs them. Before giving the command, say how many calls it makes (cases × models × runs) minus what the cache (`eval/cache/`, gitignored) already holds; `--fresh` ignores the cache. A replay costs nothing and is how a scoring or label change is checked.

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
3. Report a table of each metric: before, after, and the delta, plus the cases that changed. A difference is real only when at least 2 cases move the same way in all 3 runs; anything less is noise.
4. Say so plainly when a metric gets worse. Never present a regression as an improvement.

When `tests/unit/test_prompt_pins.py` fails, a prompt changed: give the user the benchmark command for that agent (`--runs 3`), compare its report with the last one, and only then update `PINNED_PROMPT_HASHES` to the new `prompt_sha256` from the results file, in the same commit as the prompt change.
