import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from eval.benchmark.call_cache import CallCache
from eval.benchmark.dataset_schema import case_fingerprint, file_sha256, load_agent_cases
from eval.benchmark.metrics import summarize
from eval.benchmark.report import format_markdown
from eval.benchmark.scoring import AGENT_SPECS, SCORER_VERSION, score_case
from review.agents.agent_runner import AGENT_TEMPERATURE, EMPTY_CONTEXT_SUMMARY
from review.agents.prompt_assembler import fill_prompt
from review.untrusted_input import DIFF_LABEL, wrap_untrusted


EVAL_DIR = Path(__file__).parent
DEFAULT_CASES_PATH = EVAL_DIR / "dataset" / "llm_benchmark_cases.json"
DEFAULT_RESULTS_DIR = EVAL_DIR / "results"
DEFAULT_CACHE_PATH = EVAL_DIR / "cache" / "llm_calls.jsonl"

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

AGENT_PROMPTS = {name: spec.prompt_template for name, spec in AGENT_SPECS.items()}

# Same temperature production uses, so the benchmark measures the model, not a different sampling setup.


# What one model call returned, in a provider-neutral shape.
@dataclass
class CallResult:
    text: str
    latency_s: float
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    attempts: int = 1  # 1 + retries after a 429/5xx, so latency can be read honestly


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


# A 429 on a per-day limit (Groq TPD/RPD): no retry or later call can succeed before the window frees up.
def is_daily_quota(error: str | BaseException | None) -> bool:
    return error is not None and "per day" in str(error)


# Rate limits (429) and server errors (5xx) are worth retrying, except a spent daily quota.
def _is_retryable(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status in (429, 500, 502, 503, 504) and not is_daily_quota(exc)


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


def _attempts(call) -> int:
    return int(getattr(call, "retry", None) and call.retry.statistics.get("attempt_number", 1) or 1)


# Call a model and never raise: errors come back in CallResult.error.
def call_model(model_spec: str, prompt: str, temperature: float | None) -> CallResult:
    start = time.perf_counter()
    call = None
    try:
        provider, model_name = _split_model_spec(model_spec)
        call = _call_groq if provider == "groq" else _call_gemini
        text, input_tokens, output_tokens = call(model_name, prompt, temperature)
        return CallResult(text, time.perf_counter() - start, input_tokens, output_tokens, attempts=_attempts(call))
    except Exception as exc:  # noqa: BLE001 - any failure is a benchmark result
        return CallResult("", time.perf_counter() - start, error=f"{type(exc).__name__}: {exc}"[:300],
                          attempts=_attempts(call) if call else 1)


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
# Prompts, runs, replay
# ---------------------------------------------------------------------

# Production agent prompt for a case: the diff wrapped as untrusted data, no prepared context.
def build_agent_prompt(case: dict) -> str:
    diff = wrap_untrusted(case["diff_text"], DIFF_LABEL)
    return fill_prompt(AGENT_PROMPTS[case["agent"]], diff, EMPTY_CONTEXT_SUMMARY)


# The prompt without its random nonce, so the cache key is stable across runs.
def _cache_prompt(case: dict) -> str:
    return fill_prompt(AGENT_PROMPTS[case["agent"]], case["diff_text"], EMPTY_CONTEXT_SUMMARY)


def _record(case: dict, model: str, run: int, scored: dict, result: dict, cached: bool) -> dict:
    return {"task": "agents", "model": model, "agent": case["agent"], "case_id": case["id"], "run": run,
            "case_fingerprint": case_fingerprint(case), **scored, **result, "cached": cached}


def _empty_score(case: dict) -> dict:
    return score_case(case, "[]") | {"score": 0.0, "legacy_score": 0.0, "passed": False, "valid": False,
                                     "clean_strict_pass": False}


# Runs every case against every model, `runs` times; answers already in the cache cost no call.
def run_benchmark(
    cases: list[dict],
    models: list[str],
    runs: int = 1,
    call_fn: CallFn = call_model,
    delay_s: float = 0.0,
    cache: CallCache | None = None,
) -> list[dict]:
    records = []
    for model in models:
        quota_spent = False
        for case in cases:
            for run in range(1, runs + 1):
                key = CallCache.key(model, AGENT_TEMPERATURE, _cache_prompt(case), run)
                cached = cache.get(key) if cache else None
                if quota_spent and not cached:
                    continue
                result = cached or asdict(call_fn(model, build_agent_prompt(case), AGENT_TEMPERATURE))
                if is_daily_quota(result["error"]):
                    print(f"  {model}: daily quota spent, its remaining uncached calls are skipped")
                    quota_spent = True
                    continue
                if cache and not cached:
                    cache.put(key, result)
                scored = score_case(case, result["text"]) if result["error"] is None else _empty_score(case)
                records.append(_record(case, model, run, scored, result, cached is not None))
                status = f"error: {result['error']}" if result["error"] else f"score={scored['score']:.2f}"
                origin = "cache" if cached else f"{result['latency_s']:.1f}s"
                print(f"  {model} {case['id']} run {run}: {status} ({origin})")
                if delay_s and not cached:
                    time.sleep(delay_s)
    return records


# Re-scores a saved results file with the current scorer and dataset: no API call.
def replay(results_path: Path, cases: list[dict]) -> list[dict]:
    by_id = {case["id"]: case for case in cases}
    saved = json.loads(results_path.read_text(encoding="utf-8"))["records"]
    records = []
    for old in saved:
        case = by_id.get(old["case_id"])
        if case is None:
            print(f"  skipped {old['case_id']}: no longer in the dataset")
            continue
        if old.get("case_fingerprint") and old["case_fingerprint"] != case_fingerprint(case):
            print(f"  warning: {old['case_id']} changed since this run; it is scored against the current labels")
        result = {key: old.get(key, default) for key, default in
                  (("text", ""), ("latency_s", 0.0), ("input_tokens", 0), ("output_tokens", 0), ("error", None), ("attempts", 1))}
        scored = score_case(case, result["text"]) if result["error"] is None else _empty_score(case)
        records.append(_record(case, old["model"], old["run"], scored, result, cached=False))
    return records


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# What produced a result: a difference between two files can be traced to the prompt, the dataset, the scorer or the model.
def provenance(cases_path: Path) -> dict:
    return {
        "scorer_version": SCORER_VERSION,
        "dataset_sha256": file_sha256(cases_path),
        "prompt_sha256": {name: hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16] for name, prompt in AGENT_PROMPTS.items()},
        "git_commit": _git_commit(),
    }


# Writes the full JSON (every call, with the raw text for later replays) and the Markdown report.
def save_results(records: list[dict], summary: list[dict], results_dir: Path, settings: dict) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = f"llm_benchmark_{datetime.now():%Y%m%d_%H%M%S}"
    json_path = results_dir / f"{stem}.json"
    json_path.write_text(json.dumps({"settings": settings, "summary": summary, "records": records}, indent=2), encoding="utf-8")
    (results_dir / f"{stem}.md").write_text(format_markdown(summary, records, settings), encoding="utf-8")
    return json_path


# Command-line arguments.
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark LLMs on the review agents' production prompts.")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                        help="comma-separated 'groq:<model>' / 'gemini:<model>' specs")
    parser.add_argument("--runs", type=int, default=1, help="repetitions per case (3 before deciding anything)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="seconds to wait between real calls, for free-tier rate limits (default 2)")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="dataset JSON file")
    parser.add_argument("--only", default="", help="comma-separated case ids to run (default: all)")
    parser.add_argument("--out", type=Path, default=DEFAULT_RESULTS_DIR, help="results directory")
    parser.add_argument("--fresh", action="store_true", help="ignore the call cache and call every model again")
    parser.add_argument("--replay", type=Path, help="re-score a saved results JSON with the current scorer, no API call")
    parser.add_argument("--list-models", action="store_true", help="print the models your keys can call, then exit")
    return parser.parse_args()


# CLI entry point: load .env, run (or replay) the benchmark, print and save results.
def main() -> None:
    load_dotenv()
    # Model titles can hold characters a Windows console cannot encode; never crash after a paid run.
    sys.stdout.reconfigure(errors="replace")
    args = _parse_args()
    if args.list_models:
        list_available_models()
        return

    cases = load_agent_cases(args.cases)
    only = {case_id.strip() for case_id in args.only.split(",") if case_id.strip()}
    if only:
        unknown = only - {case["id"] for case in cases}
        if unknown:
            raise SystemExit(f"Unknown case id(s): {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in only]
    settings = {"cases": str(args.cases), **provenance(args.cases)}

    if args.replay:
        print(f"Re-scoring {args.replay} with scorer v{SCORER_VERSION} (no API call).")
        records = replay(args.replay, cases)
        settings |= {"replay_of": str(args.replay), "runs": max((r["run"] for r in records), default=0),
                     "models": sorted({r["model"] for r in records})}
    else:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        for model in models:
            _split_model_spec(model)  # fail fast on a typo, before any API call
        cache = None if args.fresh else CallCache(DEFAULT_CACHE_PATH)
        print(f"Up to {len(cases) * len(models) * args.runs} calls ({len(models)} model(s) x {len(cases)} cases x "
              f"{args.runs} run(s)); cached answers are reused{' (disabled: --fresh)' if args.fresh else ''}.")
        records = run_benchmark(cases, models, args.runs, call_model, args.delay, cache)
        missing = len(cases) * len(models) * args.runs - len(records)
        settings |= {"models": models, "runs": args.runs, "missing_calls": missing}
        if missing:
            print(f"\nIncomplete: {missing} call(s) skipped on a daily quota. Rerun the same command once it resets; "
                  "cached answers cost nothing.")

    summary = summarize(records)
    print("\n" + format_markdown(summary, records, settings))
    json_path = save_results(records, summary, args.out, settings)
    print(f"Saved {json_path} and {json_path.with_suffix('.md')}")


if __name__ == "__main__":
    main()
