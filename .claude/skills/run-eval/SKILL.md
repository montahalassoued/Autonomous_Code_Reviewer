---
name: run-eval
description: Build or run the RAGAS evaluation of the RAG pipeline (eval/ragas_eval.py) and compare results before/after a change to retrieval, chunking, reranking, or agent prompts. Use when the user asks to evaluate, benchmark, or measure the quality of retrieval/reviews.
---

# RAG evaluation

The entrypoint is `eval/ragas_eval.py` (Step 15 in `docs/PROGRESS.md`). **It's currently a stub**, so the first time you use this skill you'll need to build it with the user.

## If the eval isn't built yet

Confirm the plan with the user first, then implement it (following the `clean-code` skill):

1. **Dataset:** `eval/dataset.json`, a small hand-written set (start with 10-20 items). Each item has:
   - `question`: a retrieval query, e.g. "where is the webhook signature verified?"
   - `ground_truth`: the expected answer in one or two sentences
   - `expected_files` (optional): the files that should be retrieved
2. **Run the retrieval pipeline** for each question: ingest the target repo once with `rag/ingestion.py`, then run the same path as `agents/retriever.py` (hybrid_search → graph_retriever → reranker).
3. **Score with RAGAS** (`ragas` and `datasets` are in `requirements.txt`). Use `context_precision`, `context_recall`, plus `faithfulness` and `answer_relevancy` if an answer is generated. Configure RAGAS to use the project's Groq or Gemini LLM rather than OpenAI's default.
4. **Output:** print a metric table, and save `eval/results/<YYYY-MM-DD>_<label>.json` with the per-item scores and the averages.
5. Add a CLI entry: `venv/Scripts/python.exe -m eval.ragas_eval --label baseline`.

## Running a before/after comparison

1. Tell the user that the eval makes many real LLM and embedding calls (quota and cost).
2. Run a baseline on the current code: `--label before-<change>`.
3. Apply the change, then run it again: `--label after-<change>`.
4. Report a table of each metric (before, after, delta), plus the items that got noticeably worse.
5. If a metric drops, say so plainly. Don't present a regression as a win.

## Tips

- Keep the dataset fixed between the two runs, or the comparison means nothing.
- Results vary a little between runs. Treat differences under ~0.02 as noise.
- Commit `eval/dataset.json`, and decide with the user whether to commit `eval/results/`.
