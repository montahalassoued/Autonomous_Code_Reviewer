---
name: run-eval
description: Run or extend the evaluation of this project's LLMs and review quality (eval/llm_benchmark.py for single agent calls, eval/review_eval.py for whole reviews) and compare results before/after a change to prompts, models, retrieval or agents. Use when the user asks to evaluate, benchmark, or compare models or quality.
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

## Whole reviews: `eval/review_eval.py` (Phase 1)

It runs the **production graph** (the 3 agents with their tools, then the synthesizer) on golden PRs, so it measures what a real review posts, tool calls and fallback included. The benchmark above sends one prompt without tools; this is the configuration production runs.
- **Dataset:** `eval/dataset/review_eval_cases.json`. Each case is a list of edits (`find`/`replace` that must match exactly once, or `create`) on `eval/fixtures/review_repo/`, the PRs' `main`; the harness builds the diff in the shape `get_pr_diff` returns. Expected issues name their `agent` and locate their line with `at` (and `at_end`), a fragment of the new code. `tests/unit/test_review_eval.py` checks every case builds and every issue sits on an added line.
- **Scoring:** `eval/review_harness/review_scoring.py`, with the benchmark's `score_findings`: one row per category (its own issues, comparable with the benchmark) and a `review` row for the posted review (cross-agent duplicates count). A failed agent is an error, never a clean pass. Also calls, tokens and latency per review.
- **Calls:** `eval/review_harness/recorded_client.py` records every model answer in `eval/cache/review_eval_calls.jsonl` (gitignored), keyed without the prompt's random nonce. A rerun replays them for free, a run stopped by a spent daily quota resumes where it stopped, and `--offline` refuses any unrecorded call. Per-minute 429s are waited out, so the eval measures quality, not the rate limit.

```bash
venv/Scripts/python.exe -m eval.review_eval --runs 1                       # real calls (user runs it): about 3-10 calls per case
venv/Scripts/python.exe -m eval.review_eval --only sec_sql_injection_search  # one case
venv/Scripts/python.exe -m eval.review_eval --runs 3 --offline             # re-score recorded runs, no API call
```

The models are the production ones (`SECURITY_/LOGIC_/QUALITY_AGENT_MODEL`, `FALLBACK_AGENT_MODEL` from `.env`). Before giving a real run, say roughly what it costs: an agent prompt is 3.5-5k tokens and every tool round resends it, so an estimated ~15k tokens per agent per case (2-3 calls), about 180k per agent per run of 12 cases. Against Groq's ~200k tokens per model per day, one run fits in a day only when the three agents use three different models; with one shared model it takes about three days, resumed from the recorded calls. Planned: `--mode structural` after the Phase 4 context builder, context recall and precision, Langfuse dataset runs.

## The CI eval gate

`eval/recordings/review_eval_calls.jsonl` (committed) holds the recorded answers and `eval/baselines/review_eval_baseline.json` the outcome of each (case, run, row). CI replays them offline and fails on any case that got worse (`eval/review_harness/eval_gate.py`). After a code change that improves a case, or after new live runs, refresh both, in the commit that explains the change:

```bash
cp eval/cache/review_eval_calls.jsonl eval/recordings/review_eval_calls.jsonl     # after a live run: the new answers
venv/Scripts/python.exe -m eval.review_eval --offline --cache eval/recordings/review_eval_calls.jsonl --write-baseline eval/baselines/review_eval_baseline.json
```

Check the recording for secrets before committing it (it holds model answers about the fixture repo only). Never rewrite the baseline to make a regression pass: fix the code, or say plainly why the new outcome is right.

## Comparing before and after a change

1. Keep the dataset fixed between the runs.
2. Run a baseline with `--runs 3`, make the change, then run again.
3. Report a table of each metric: before, after, and the delta, plus the cases that changed. A difference is real only when at least 2 cases move the same way in all 3 runs; anything less is noise.
4. Say so plainly when a metric gets worse. Never present a regression as an improvement.

When `tests/unit/test_prompt_pins.py` fails, a prompt changed: give the user the benchmark command for that agent (`--runs 3`), compare its report with the last one, and only then update `PINNED_PROMPT_HASHES` to the new `prompt_sha256` from the results file, in the same commit as the prompt change.
