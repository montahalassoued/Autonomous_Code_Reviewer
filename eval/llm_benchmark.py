"""
llm_benchmark.py - compares free LLMs on the four LLM jobs in this
project, so we can pick the right model for each one.

The four jobs, and the production code each one benchmarks:
  agents      -> the domain review agents (agents/security.py, agents/performance.py, ...)
  decomposer  -> diff -> search queries (rag/query_decomposer.py)
  judge       -> retrieval quality score 0-1 (agents/retriever.py)
  chunker     -> one context sentence per function (rag/contextual_chunker.py)

Inputs:
  - eval/dataset/llm_benchmark_cases.json: labelled cases with a known
    right answer (a planted bug, the concerns a diff touches, whether
    some context is relevant, the words a good summary should contain).
  - GROQ_API_KEY and GOOGLE_API_KEY from .env (free tiers).
  - CLI flags: which tasks, which models, how many runs.

Outputs:
  - eval/results/llm_benchmark_<timestamp>.json: every call (raw model
    output, score, latency, tokens) plus the per-model summary.
  - eval/results/llm_benchmark_<timestamp>.md: the summary tables.
  - The same tables printed to the terminal.

Why it's designed this way:
  - It imports the REAL prompt templates from the production modules,
    so a model is judged on exactly the prompt the pipeline sends, and
    a prompt change is picked up automatically on the next run.
  - Scoring is deterministic (keyword / threshold checks, no LLM
    grader), so two runs of the benchmark are comparable and grading
    itself costs no quota.
  - Each model is called through one small `call_model` function that
    hides the Groq/Gemini differences; tests swap it for a fake.
  - A model that errors (retired, rate-limited, wrong name) is
    recorded as an error and scored 0 - it never stops the run.

Usage:
  venv/Scripts/python.exe -m eval.llm_benchmark --list-models
  venv/Scripts/python.exe -m eval.llm_benchmark
  venv/Scripts/python.exe -m eval.llm_benchmark --tasks agents,judge \\
      --models groq:llama-3.3-70b-versatile,gemini:gemini-2.5-flash --runs 3

Every run makes REAL API calls and uses free-tier quota.
"""

import argparse
import json
import os
import re
import statistics
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from agents.security import SECURITY_PROMPT_TEMPLATE
from agents.performance import PERFORMANCE_PROMPT_TEMPLATE
from agents.architecture import ARCHITECTURE_PROMPT_TEMPLATE
from agents.testing import TESTING_PROMPT_TEMPLATE
from agents.documentation import DOCUMENTATION_PROMPT_TEMPLATE
from agents.retriever import JUDGE_PROMPT_TEMPLATE, QUALITY_THRESHOLD
from rag.query_decomposer import DECOMPOSE_PROMPT_TEMPLATE
from rag.contextual_chunker import CONTEXT_PROMPT_TEMPLATE


EVAL_DIR = Path(__file__).parent
DEFAULT_CASES_PATH = EVAL_DIR / "dataset" / "llm_benchmark_cases.json"
DEFAULT_RESULTS_DIR = EVAL_DIR / "results"

ALL_TASKS = ["agents", "decomposer", "judge", "chunker"]

# Free-tier candidates. Provider model lists change over time; run with
# --list-models to see what your keys can call today, and pass --models
# to override this list. (Groq retired llama-3.3-70b-versatile and
# llama-3.1-8b-instant, which production still uses - see --list-models.)
DEFAULT_MODELS = [
    "groq:openai/gpt-oss-120b",
    "groq:openai/gpt-oss-20b",
    "groq:qwen/qwen3.8-27b",
    "gemini:gemini-2.5-flash-lite",
    "gemini:gemini-2.5-flash",
]

AGENT_PROMPTS = {
    "security": SECURITY_PROMPT_TEMPLATE,
    "performance": PERFORMANCE_PROMPT_TEMPLATE,
    "architecture": ARCHITECTURE_PROMPT_TEMPLATE,
    "testing": TESTING_PROMPT_TEMPLATE,
    "documentation": DOCUMENTATION_PROMPT_TEMPLATE,
}

# Same temperatures production uses for each job, so the benchmark
# measures the model, not a different sampling setup. None = provider
# default (contextual_chunker.py doesn't set one).
TASK_TEMPERATURE = {"agents": 0.1, "decomposer": 0.2, "judge": 0.0, "chunker": None}

# A finding at one of these severities on a clean diff counts as a
# false positive. LOW/INFO nitpicks are tolerated.
FALSE_POSITIVE_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM"}

# How far a reported line may be from the planted bug and still count
# as "right line" (models often point at the def or the next line).
LINE_TOLERANCE = 3

MAX_CONTEXT_WORDS = 30  # the chunker prompt asks for "max 30 words"
EMPTY_CONTEXT_SUMMARY = "(no related context retrieved)"


@dataclass
class CallResult:
    """
    What one model call returned, in a provider-neutral shape.

    Attributes:
        text: the model's output text ("" on error or empty output)
        latency_s: wall-clock seconds for the call, retries included
        input_tokens: prompt tokens reported by the provider (0 if unknown)
        output_tokens: completion tokens reported by the provider (0 if unknown)
        error: the error message if the call failed after retries, else None
    """
    text: str
    latency_s: float
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


# Signature of the function that talks to a model:
# (model_spec, prompt, temperature) -> CallResult. Tests pass a fake.
CallFn = Callable[[str, str, float | None], CallResult]


# ---------------------------------------------------------------------
# Model calls
# ---------------------------------------------------------------------

def _build_groq_client():
    """
    Reads GROQ_API_KEY from the environment.

    Returns:
        a groq.Groq client

    Raises:
        RuntimeError: if GROQ_API_KEY is not set
    """
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com and set it "
            "in your .env file."
        )
    return Groq(api_key=api_key)


def _build_gemini_client():
    """
    Reads GOOGLE_API_KEY from the environment.

    Returns:
        a google.genai.Client

    Raises:
        RuntimeError: if GOOGLE_API_KEY is not set
    """
    from google import genai

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is not set. "
            "Get a free key at https://aistudio.google.com and set it "
            "in your .env file."
        )
    return genai.Client(api_key=api_key)


def _split_model_spec(model_spec: str) -> tuple[str, str]:
    """
    Splits "provider:model" into its two parts. The model part may
    itself contain "/" (e.g. "groq:openai/gpt-oss-120b").

    Args:
        model_spec: e.g. "groq:llama-3.3-70b-versatile"

    Returns:
        (provider, model_name)

    Raises:
        ValueError: if the provider is not "groq" or "gemini"
    """
    provider, _, model_name = model_spec.partition(":")
    if provider not in ("groq", "gemini") or not model_name:
        raise ValueError(
            f"Bad model spec {model_spec!r}: use 'groq:<model>' or 'gemini:<model>'."
        )
    return provider, model_name


def _is_retryable(exc: BaseException) -> bool:
    """
    True for rate limits (429) and server errors (5xx), which usually
    pass on their own. Client errors (bad model name, bad key) are not
    retried because they never will.

    Args:
        exc: the exception raised by the provider SDK

    Returns:
        whether tenacity should retry the call
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status in (429, 500, 502, 503, 504)


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _call_groq(model_name: str, prompt: str, temperature: float | None) -> tuple[str, int, int]:
    """
    One Groq chat completion, retried on 429/5xx.

    Args:
        model_name: Groq model id
        prompt: the full user prompt
        temperature: sampling temperature, or None for the default

    Returns:
        (text, input_tokens, output_tokens)
    """
    client = _build_groq_client()
    kwargs = {"temperature": temperature} if temperature is not None else {}
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
    usage = response.usage
    return (
        response.choices[0].message.content or "",
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
    )


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _call_gemini(model_name: str, prompt: str, temperature: float | None) -> tuple[str, int, int]:
    """
    One Gemini generate_content call, retried on 429/5xx.

    Args:
        model_name: Gemini model id
        prompt: the full prompt
        temperature: sampling temperature, or None for the default

    Returns:
        (text, input_tokens, output_tokens)
    """
    from google.genai import types

    client = _build_gemini_client()
    config = types.GenerateContentConfig(temperature=temperature) if temperature is not None else None
    response = client.models.generate_content(model=model_name, contents=prompt, config=config)
    usage = response.usage_metadata
    return (
        response.text or "",
        getattr(usage, "prompt_token_count", 0) or 0,
        getattr(usage, "candidates_token_count", 0) or 0,
    )


def call_model(model_spec: str, prompt: str, temperature: float | None) -> CallResult:
    """
    Calls a model and never raises: a failure becomes a CallResult with
    `error` set, so one broken model can't stop the whole benchmark.

    Args:
        model_spec: "groq:<model>" or "gemini:<model>"
        prompt: the full prompt
        temperature: sampling temperature, or None for the default

    Returns:
        a CallResult
    """
    start = time.perf_counter()
    try:
        provider, model_name = _split_model_spec(model_spec)
        call = _call_groq if provider == "groq" else _call_gemini
        text, input_tokens, output_tokens = call(model_name, prompt, temperature)
        return CallResult(text, time.perf_counter() - start, input_tokens, output_tokens)
    except Exception as exc:  # noqa: BLE001 - any failure is a benchmark result
        return CallResult("", time.perf_counter() - start, error=f"{type(exc).__name__}: {exc}"[:300])


def list_available_models() -> None:
    """
    Prints the model ids each provider key can call right now, so the
    candidate list can be checked against what the free tier offers.
    """
    def list_groq() -> list[str]:
        return sorted(m.id for m in _build_groq_client().models.list().data)

    def list_gemini() -> list[str]:
        # Keep the client in a variable: the model list is fetched page
        # by page while we iterate, and a client that was only a
        # temporary gets garbage-collected (and closed) before that.
        client = _build_gemini_client()
        return sorted(
            m.name.removeprefix("models/")
            for m in client.models.list()
            # Hide embedding/TTS models; keep any model that doesn't say.
            if m.supported_actions is None or "generateContent" in m.supported_actions
        )

    for name, lister in (("groq", list_groq), ("gemini", list_gemini)):
        try:
            print(f"\n{name}:")
            for model_id in lister():
                print(f"  {name}:{model_id}")
        except Exception as exc:  # noqa: BLE001 - show the problem, keep going
            print(f"  could not list models: {exc}")


# ---------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------

def _clean_output(text: str) -> str:
    """
    Removes the wrappers models add despite instructions: reasoning
    blocks (<think>...</think>, from qwen and similar), markdown code
    fences, and a leading "json" language tag.

    Args:
        text: raw model output

    Returns:
        the cleaned text
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip().strip("`").removeprefix("json").strip()


def _extract_json(text: str, opener: str, closer: str):
    """
    Parses a JSON value from model output, first as-is, then from the
    outermost opener..closer span (for output wrapped in prose). Same
    two-step approach as agents/security.py's _parse_findings, but it
    reports failure instead of hiding it, because the valid-output rate
    is one of the things we're measuring.

    Args:
        text: raw model output
        opener: "[" for an array, "{" for an object
        closer: "]" or "}"

    Returns:
        the parsed value, or None if nothing parses
    """
    cleaned = _clean_output(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start, end = cleaned.find(opener), cleaned.rfind(closer)
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _contains_any(text: str, keywords: list[str]) -> bool:
    """
    Case-insensitive "does any keyword appear in the text".

    Args:
        text: text to search
        keywords: lowercase keywords

    Returns:
        True if at least one keyword is a substring of the text
    """
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


# ---------------------------------------------------------------------
# Prompt builders (one per task) - use the production templates
# ---------------------------------------------------------------------

def _join_lines(value) -> str:
    """
    Dataset text fields are stored as lists of lines for readability.

    Args:
        value: a string or a list of lines

    Returns:
        the text as one string
    """
    return "\n".join(value) if isinstance(value, list) else value


def build_agent_prompt(case: dict) -> str:
    """
    Args:
        case: an "agents" case (agent, diff)

    Returns:
        the agent's production prompt, with no retrieved context (we're
        measuring the model, not the RAG pipeline)
    """
    return AGENT_PROMPTS[case["agent"]].format(
        diff=_join_lines(case["diff"]), context_summary=EMPTY_CONTEXT_SUMMARY
    )


def build_decomposer_prompt(case: dict) -> str:
    """
    Args:
        case: a "decomposer" case (diff)

    Returns:
        the production query-decomposition prompt
    """
    return DECOMPOSE_PROMPT_TEMPLATE.format(diff=_join_lines(case["diff"]))


def build_judge_prompt(case: dict) -> str:
    """
    Args:
        case: a "judge" case (diff, context list of {name, file})

    Returns:
        the production retrieval-judge prompt, with the context rendered
        the way agents/retriever.py renders it
    """
    context_summary = "\n".join(f"- {c['name']} ({c['file']})" for c in case["context"])
    return JUDGE_PROMPT_TEMPLATE.format(diff=_join_lines(case["diff"]), context_summary=context_summary)


def build_chunker_prompt(case: dict) -> str:
    """
    Args:
        case: a "chunker" case (file_path, calls, code)

    Returns:
        the production contextual-chunking prompt
    """
    return CONTEXT_PROMPT_TEMPLATE.format(
        file_path=case["file_path"],
        calls=", ".join(case["calls"]) if case["calls"] else "none",
        code=_join_lines(case["code"]),
    )


# ---------------------------------------------------------------------
# Scorers (one per task) - each returns {"score": 0..1, "valid": bool, ...}
# ---------------------------------------------------------------------

def _finding_matches(finding: dict, expected: dict) -> bool:
    """
    A finding matches a planted issue when it is in the same file and
    its text uses one of the issue's keywords.

    Args:
        finding: one dict from the model's JSON array
        expected: one expected_issues entry (file, line, keywords)

    Returns:
        whether the finding reports that issue
    """
    # Models sometimes keep the diff's "a/" / "b/" prefix or report a
    # shorter path, so compare by suffix in both directions.
    file_path = str(finding.get("file_path", "")).removeprefix("a/").removeprefix("b/").lstrip("/")
    same_file = bool(file_path) and (
        file_path.endswith(expected["file"]) or expected["file"].endswith(file_path)
    )
    text = " ".join(str(finding.get(k, "")) for k in ("title", "description", "evidence", "suggestion"))
    return same_file and _contains_any(text, expected["keywords"])


def score_agent(case: dict, output: str) -> dict:
    """
    Buggy diff: score = share of planted issues found (recall).
    Clean diff: score = 1 if there is no MEDIUM+ finding, else 0.

    Args:
        case: an "agents" case
        output: the model's raw output

    Returns:
        score, valid (output is a JSON array of well-formed findings),
        and details: n_findings, found, line_hits, false_positives
    """
    findings = _extract_json(output, "[", "]")
    if not isinstance(findings, list):
        return {"score": 0.0, "valid": False, "n_findings": 0}

    findings = [f for f in findings if isinstance(f, dict)]
    required = {"severity", "file_path", "line_number", "title", "description", "evidence", "suggestion"}
    valid = all(required <= f.keys() for f in findings)

    expected = case["expected_issues"]
    if not expected:
        false_positives = sum(
            1 for f in findings if str(f.get("severity", "")).upper() in FALSE_POSITIVE_SEVERITIES
        )
        return {
            "score": 1.0 if false_positives == 0 else 0.0,
            "valid": valid,
            "n_findings": len(findings),
            "false_positives": false_positives,
        }

    found = line_hits = 0
    for issue in expected:
        matches = [f for f in findings if _finding_matches(f, issue)]
        if matches:
            found += 1
            if any(_line_close(f.get("line_number"), issue["line"]) for f in matches):
                line_hits += 1

    return {
        "score": found / len(expected),
        "valid": valid,
        "n_findings": len(findings),
        "found": found,
        "line_hits": line_hits,
    }


def _line_close(reported, expected_line: int) -> bool:
    """
    Args:
        reported: the line_number the model gave (any type)
        expected_line: the planted bug's line

    Returns:
        True if reported is an int within LINE_TOLERANCE of expected_line
    """
    try:
        return abs(int(reported) - expected_line) <= LINE_TOLERANCE
    except (TypeError, ValueError):
        return False


def score_decomposer(case: dict, output: str) -> dict:
    """
    score = share of the diff's concerns covered by at least one query.
    Only the first 3 queries count, like production's [:3] cap.

    Args:
        case: a "decomposer" case (expected_concerns: list of keyword lists)
        output: the model's raw output

    Returns:
        score, valid (a non-empty JSON array of non-blank strings,
        3 or fewer), n_queries, queries
    """
    queries = _extract_json(output, "[", "]")
    if not (isinstance(queries, list) and queries and all(isinstance(q, str) and q.strip() for q in queries)):
        return {"score": 0.0, "valid": False, "n_queries": 0}

    kept = queries[:3]
    concerns = case["expected_concerns"]
    covered = sum(1 for keywords in concerns if any(_contains_any(q, keywords) for q in kept))
    return {
        "score": covered / len(concerns),
        "valid": len(queries) <= 3,
        "n_queries": len(queries),
        "queries": kept,
    }


def score_judge(case: dict, output: str) -> dict:
    """
    score = 1 if the judge lands on the right side of production's
    QUALITY_THRESHOLD (>= for relevant context, < for irrelevant), else 0.

    Args:
        case: a "judge" case (expected: "high" or "low")
        output: the model's raw output

    Returns:
        score, valid (a JSON object with a numeric "score" in [0, 1]),
        judge_score (the raw number)
    """
    parsed = _extract_json(output, "{", "}")
    try:
        judge_score = float(parsed["score"])
    except (TypeError, KeyError, ValueError):
        return {"score": 0.0, "valid": False, "judge_score": None}

    correct = judge_score >= QUALITY_THRESHOLD if case["expected"] == "high" else judge_score < QUALITY_THRESHOLD
    return {
        "score": 1.0 if correct else 0.0,
        "valid": 0.0 <= judge_score <= 1.0,
        "judge_score": judge_score,
    }


def score_chunker(case: dict, output: str) -> dict:
    """
    Four equal parts (0.25 each): at most 30 words, one sentence,
    names the file, and uses at least one expected keyword.

    Args:
        case: a "chunker" case (file_path, keywords)
        output: the model's raw output

    Returns:
        score, valid (non-empty output), words, sentence
    """
    # Same post-processing as rag/contextual_chunker.generate_context.
    sentence = _clean_output(output).strip('"').strip("'")
    if not sentence:
        return {"score": 0.0, "valid": False, "words": 0, "sentence": ""}

    words = len(sentence.split())
    # Count sentence ends that are followed by more text; a trailing
    # period is fine. Dots inside names like "gateway.py" aren't followed
    # by a space, so they don't count.
    extra_sentences = len(re.findall(r"[.!?]\s+\S", sentence))
    file_name = Path(case["file_path"]).name
    checks = [
        words <= MAX_CONTEXT_WORDS,
        extra_sentences == 0 and "\n" not in sentence,
        case["file_path"] in sentence or file_name in sentence,
        _contains_any(sentence, case["keywords"]),
    ]
    return {"score": sum(checks) / len(checks), "valid": True, "words": words, "sentence": sentence}


TASKS = {
    "agents": (build_agent_prompt, score_agent),
    "decomposer": (build_decomposer_prompt, score_decomposer),
    "judge": (build_judge_prompt, score_judge),
    "chunker": (build_chunker_prompt, score_chunker),
}


# ---------------------------------------------------------------------
# Running and summarizing
# ---------------------------------------------------------------------

def load_cases(path: Path) -> dict:
    """
    Args:
        path: the dataset JSON file

    Returns:
        {task_name: [case, ...]} for the tasks present in the file
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {task: data[task] for task in ALL_TASKS if task in data}


def run_benchmark(
    cases: dict,
    models: list[str],
    tasks: list[str],
    runs: int = 1,
    call_fn: CallFn = call_model,
    delay_s: float = 0.0,
) -> list[dict]:
    """
    Runs every case of every task against every model, `runs` times.

    Args:
        cases: output of load_cases
        models: model specs ("provider:model")
        tasks: task names to run (subset of ALL_TASKS)
        runs: repetitions per case, to see how stable a model is
        call_fn: the model-calling function (a fake in tests)
        delay_s: pause between calls, to stay under free-tier rate limits

    Returns:
        one record per call: task, model, case_id, run, the score dict
        fields, latency, tokens, error, and the raw output
    """
    records = []
    for task in tasks:
        build_prompt, score = TASKS[task]
        for model in models:
            for case in cases.get(task, []):
                for run in range(1, runs + 1):
                    result = call_fn(model, build_prompt(case), TASK_TEMPERATURE[task])
                    scored = score(case, result.text) if result.error is None else {"score": 0.0, "valid": False}
                    records.append({
                        "task": task,
                        "model": model,
                        "case_id": case["id"],
                        "run": run,
                        **scored,
                        **asdict(result),
                    })
                    status = f"error: {result.error}" if result.error else f"score={scored['score']:.2f}"
                    print(f"  [{task}] {model} {case['id']} run {run}: {status} ({result.latency_s:.1f}s)")
                    if delay_s:
                        time.sleep(delay_s)
    return records


def summarize(records: list[dict]) -> list[dict]:
    """
    Aggregates records per (task, model).

    Args:
        records: output of run_benchmark

    Returns:
        one row per (task, model): quality (mean score), valid_rate,
        error count, mean/p95 latency, mean input/output tokens, calls.
        Sorted by task, then quality (best first), then latency.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        groups.setdefault((record["task"], record["model"]), []).append(record)

    rows = []
    for (task, model), group in groups.items():
        ok = [r for r in group if r["error"] is None]
        latencies = sorted(r["latency_s"] for r in ok) or [0.0]
        rows.append({
            "task": task,
            "model": model,
            "quality": statistics.mean(r["score"] for r in group),
            "valid_rate": statistics.mean(1.0 if r["valid"] else 0.0 for r in group),
            "errors": len(group) - len(ok),
            "latency_mean_s": statistics.mean(latencies),
            "latency_p95_s": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))],
            "input_tokens_mean": statistics.mean(r["input_tokens"] for r in ok) if ok else 0,
            "output_tokens_mean": statistics.mean(r["output_tokens"] for r in ok) if ok else 0,
            "calls": len(group),
        })

    rows.sort(key=lambda r: (ALL_TASKS.index(r["task"]), -r["quality"], r["latency_mean_s"]))
    return rows


def format_markdown(summary: list[dict]) -> str:
    """
    Args:
        summary: output of summarize

    Returns:
        one Markdown table per task, best model first, followed by a
        "best per task" line
    """
    lines = ["# LLM benchmark results", ""]
    best = []
    for task in ALL_TASKS:
        rows = [r for r in summary if r["task"] == task]
        if not rows:
            continue
        lines += [
            f"## {task}",
            "",
            "| model | quality | valid | errors | latency avg (s) | latency p95 (s) | tokens in | tokens out | calls |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            lines.append(
                f"| {r['model']} | {r['quality']:.2f} | {r['valid_rate']:.0%} | {r['errors']} "
                f"| {r['latency_mean_s']:.2f} | {r['latency_p95_s']:.2f} "
                f"| {r['input_tokens_mean']:.0f} | {r['output_tokens_mean']:.0f} | {r['calls']} |"
            )
        lines.append("")
        best.append(f"- **{task}**: {rows[0]['model']} (quality {rows[0]['quality']:.2f})")

    lines += ["## Best per task (quality, then speed)", "", *best, ""]
    return "\n".join(lines)


def save_results(records: list[dict], summary: list[dict], results_dir: Path, settings: dict) -> Path:
    """
    Writes the full JSON (every call) and the Markdown summary.

    Args:
        records: output of run_benchmark
        summary: output of summarize
        results_dir: where to write
        settings: the run settings, stored so results can be reproduced

    Returns:
        the path of the JSON file (the .md sits next to it)
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = f"llm_benchmark_{datetime.now():%Y%m%d_%H%M%S}"
    json_path = results_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps({"settings": settings, "summary": summary, "records": records}, indent=2),
        encoding="utf-8",
    )
    (results_dir / f"{stem}.md").write_text(format_markdown(summary), encoding="utf-8")
    return json_path


def _parse_args() -> argparse.Namespace:
    """
    Returns:
        the parsed command-line arguments
    """
    parser = argparse.ArgumentParser(description="Benchmark free LLMs on this project's LLM jobs.")
    parser.add_argument("--tasks", default=",".join(ALL_TASKS),
                        help=f"comma-separated subset of {ALL_TASKS}")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                        help="comma-separated 'groq:<model>' / 'gemini:<model>' specs")
    parser.add_argument("--runs", type=int, default=1, help="repetitions per case (default 1)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="seconds to wait between calls, for free-tier rate limits (default 2)")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="dataset JSON file")
    parser.add_argument("--out", type=Path, default=DEFAULT_RESULTS_DIR, help="results directory")
    parser.add_argument("--list-models", action="store_true",
                        help="print the models your keys can call, then exit")
    return parser.parse_args()


def main() -> None:
    """
    CLI entry point: load .env, run the benchmark, print and save results.
    """
    load_dotenv()
    args = _parse_args()

    if args.list_models:
        list_available_models()
        return

    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    unknown = set(tasks) - set(ALL_TASKS)
    if unknown:
        raise SystemExit(f"Unknown task(s) {sorted(unknown)}; choose from {ALL_TASKS}.")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    for model in models:
        _split_model_spec(model)  # fail fast on a typo, before any API call

    cases = load_cases(args.cases)
    n_calls = sum(len(cases.get(t, [])) for t in tasks) * len(models) * args.runs
    print(f"Running {n_calls} calls ({len(models)} models x {tasks}, {args.runs} run(s) each).")

    records = run_benchmark(cases, models, tasks, args.runs, call_model, args.delay)
    summary = summarize(records)
    print("\n" + format_markdown(summary))

    settings = {"tasks": tasks, "models": models, "runs": args.runs, "cases": str(args.cases)}
    json_path = save_results(records, summary, args.out, settings)
    print(f"Saved {json_path} and {json_path.with_suffix('.md')}")


if __name__ == "__main__":
    main()
