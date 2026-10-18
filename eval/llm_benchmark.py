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

from review.agents.agent_runner import EMPTY_CONTEXT_SUMMARY
from review.agents.prompt_assembler import fill_prompt
from review.agents.security.agent import SECURITY_AGENT
from review.untrusted_input import DIFF_LABEL, wrap_untrusted
from review.agents.logic.agent import LOGIC_AGENT
from review.agents.quality.agent import QUALITY_AGENT


EVAL_DIR = Path(__file__).parent
DEFAULT_CASES_PATH = EVAL_DIR / "dataset" / "llm_benchmark_cases.json"
DEFAULT_RESULTS_DIR = EVAL_DIR / "results"

# The semantic-retrieval tasks (decomposer, judge, chunker) were removed with that pipeline.
ALL_TASKS = ["agents"]

# Free-tier candidates. Provider model lists change over time; run with
# --list-models to see what your keys can call today, and pass --models
# to override this list. (Groq retired llama-3.3-70b-versatile and
# llama-3.1-8b-instant; the review agents read REVIEW_AGENT_MODEL -
# see review/agents/agent_runner.py.)
DEFAULT_MODELS = [
    "groq:openai/gpt-oss-120b",
    "groq:openai/gpt-oss-20b",
    "groq:qwen/qwen3.8-27b",
    "gemini:gemini-2.5-flash-lite",
    "gemini:gemini-2.5-flash",
]

AGENT_PROMPTS = {
    "security": SECURITY_AGENT.prompt_template,
    "logic": LOGIC_AGENT.prompt_template,
    "quality": QUALITY_AGENT.prompt_template,
}

# Same temperature production uses, so the benchmark measures the model, not a different sampling setup.
TASK_TEMPERATURE = {"agents": 0.1}

# A finding at one of these severities on a clean diff counts as a
# false positive. LOW/INFO nitpicks are tolerated.
FALSE_POSITIVE_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM"}

# How far a reported line may be from the planted bug and still count
# as "right line" (models often point at the def or the next line).
LINE_TOLERANCE = 3


# What one model call returned, in a provider-neutral shape.
@dataclass
class CallResult:
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

# Reads GROQ_API_KEY from the environment.
def _build_groq_client():
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com and set it "
            "in your .env file."
        )
    return Groq(api_key=api_key)


# Reads GOOGLE_API_KEY from the environment.
def _build_gemini_client():
    from google import genai

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is not set. "
            "Get a free key at https://aistudio.google.com and set it "
            "in your .env file."
        )
    return genai.Client(api_key=api_key)


# Splits "provider:model" into its two parts.
def _split_model_spec(model_spec: str) -> tuple[str, str]:
    provider, _, model_name = model_spec.partition(":")
    if provider not in ("groq", "gemini") or not model_name:
        raise ValueError(
            f"Bad model spec {model_spec!r}: use 'groq:<model>' or 'gemini:<model>'."
        )
    return provider, model_name


# Rate limits (429) and server errors (5xx) are worth retrying.
def _is_retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status in (429, 500, 502, 503, 504)


# One Groq chat completion, retried on 429/5xx.
@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _call_groq(model_name: str, prompt: str, temperature: float | None) -> tuple[str, int, int]:
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


# One Gemini generate_content call, retried on 429/5xx.
@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _call_gemini(model_name: str, prompt: str, temperature: float | None) -> tuple[str, int, int]:
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


# Call a model and never raise: errors come back in CallResult.error.
def call_model(model_spec: str, prompt: str, temperature: float | None) -> CallResult:
    start = time.perf_counter()
    try:
        provider, model_name = _split_model_spec(model_spec)
        call = _call_groq if provider == "groq" else _call_gemini
        text, input_tokens, output_tokens = call(model_name, prompt, temperature)
        return CallResult(text, time.perf_counter() - start, input_tokens, output_tokens)
    except Exception as exc:  # noqa: BLE001 - any failure is a benchmark result
        return CallResult("", time.perf_counter() - start, error=f"{type(exc).__name__}: {exc}"[:300])


# Print the model ids each provider key can call right now.
def list_available_models() -> None:
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

# Strip <think> blocks, code fences and a leading 'json' tag from model output.
def _clean_output(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip().strip("`").removeprefix("json").strip()


# Parse JSON as-is, else from the outermost opener..closer span; None on failure.
def _extract_json(text: str, opener: str, closer: str):
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


# Case-insensitive "does any keyword appear in the text".
def _contains_any(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


# ---------------------------------------------------------------------
# Prompt builders (one per task) - use the production templates
# ---------------------------------------------------------------------

# Dataset text fields are stored as lists of lines for readability.
def _join_lines(value) -> str:
    return "\n".join(value) if isinstance(value, list) else value


# Production agent prompt for an 'agents' case: the diff wrapped as untrusted data, no prepared context.
def build_agent_prompt(case: dict) -> str:
    diff = wrap_untrusted(_join_lines(case["diff"]), DIFF_LABEL)
    return fill_prompt(AGENT_PROMPTS[case["agent"]], diff, EMPTY_CONTEXT_SUMMARY)


# ---------------------------------------------------------------------
# Scorers (one per task) - each returns {"score": 0..1, "valid": bool, ...}
# ---------------------------------------------------------------------

# A finding matches a planted issue: same file and one of its keywords.
def _finding_matches(finding: dict, expected: dict) -> bool:
    # Models sometimes keep the diff's "a/" / "b/" prefix or report a
    # shorter path, so compare by suffix in both directions.
    file_path = str(finding.get("file_path", "")).removeprefix("a/").removeprefix("b/").lstrip("/")
    same_file = bool(file_path) and (
        file_path.endswith(expected["file"]) or expected["file"].endswith(file_path)
    )
    text = " ".join(str(finding.get(k, "")) for k in ("title", "description", "evidence", "suggestion"))
    return same_file and _contains_any(text, expected["keywords"])


# Buggy diff: score = share of planted issues found (recall).
def score_agent(case: dict, output: str) -> dict:
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


# True if the reported line is within LINE_TOLERANCE of the planted bug.
def _line_close(reported, expected_line: int) -> bool:
    try:
        return abs(int(reported) - expected_line) <= LINE_TOLERANCE
    except (TypeError, ValueError):
        return False


TASKS = {
    "agents": (build_agent_prompt, score_agent),
}


# ---------------------------------------------------------------------
# Running and summarizing
# ---------------------------------------------------------------------

# Load the benchmark cases from the dataset JSON.
def load_cases(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {task: data[task] for task in ALL_TASKS if task in data}


# Runs every case of every task against every model, `runs` times.
def run_benchmark(
    cases: dict,
    models: list[str],
    tasks: list[str],
    runs: int = 1,
    call_fn: CallFn = call_model,
    delay_s: float = 0.0,
) -> list[dict]:
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


# Aggregates records per (task, model).
def summarize(records: list[dict]) -> list[dict]:
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


# Summary tables as Markdown.
def format_markdown(summary: list[dict]) -> str:
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


# Writes the full JSON (every call) and the Markdown summary.
def save_results(records: list[dict], summary: list[dict], results_dir: Path, settings: dict) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = f"llm_benchmark_{datetime.now():%Y%m%d_%H%M%S}"
    json_path = results_dir / f"{stem}.json"
    json_path.write_text(
        json.dumps({"settings": settings, "summary": summary, "records": records}, indent=2),
        encoding="utf-8",
    )
    (results_dir / f"{stem}.md").write_text(format_markdown(summary), encoding="utf-8")
    return json_path


# Command-line arguments.
def _parse_args() -> argparse.Namespace:
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


# CLI entry point: load .env, run the benchmark, print and save results.
def main() -> None:
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
