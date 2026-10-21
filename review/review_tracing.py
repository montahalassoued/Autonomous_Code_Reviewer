import logging
import os
import threading
from contextlib import ExitStack, contextmanager
from typing import Any, Callable, Iterator

from review.secret_redactor import environment_secret_values, redact_secrets


logger = logging.getLogger(__name__)

DEFAULT_LANGFUSE_URL = "https://cloud.langfuse.com"
# The longest prompt (security, ~21k) plus a full diff (12k) and tool output fits; anything longer is cut.
MAX_TRACED_TEXT_CHARS = 60_000

# {"trace_id": ..., "parent_span_id": ...}: where a new observation hangs in the review's trace tree.
TraceContext = dict[str, str]

_client = None
_client_built = False
_client_lock = threading.Lock()


# Every string Langfuse receives is redacted (all formats, our own secrets) and capped.
def mask_secrets(*, data: Any, **_kwargs: Any) -> Any:
    if isinstance(data, str):
        return redact_secrets(data, environment_secret_values())[:MAX_TRACED_TEXT_CHARS]
    if isinstance(data, dict):
        return {key: mask_secrets(data=value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask_secrets(data=value) for value in data]
    return data


def is_tracing_configured() -> bool:
    if os.environ.get("LANGFUSE_TRACING_ENABLED", "").strip().lower() == "false":
        return False
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip() and os.environ.get("LANGFUSE_SECRET_KEY", "").strip())


# The process's Langfuse client, or None when tracing is off or the client can't be built.
def tracing_client():
    global _client, _client_built
    with _client_lock:
        if not _client_built:
            _client_built = True
            _client = _build_langfuse_client() if is_tracing_configured() else None
        return _client


def _build_langfuse_client():
    try:
        from langfuse import Langfuse

        base_url = (os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or DEFAULT_LANGFUSE_URL).strip()
        client = Langfuse(public_key=os.environ["LANGFUSE_PUBLIC_KEY"].strip(),
                          secret_key=os.environ["LANGFUSE_SECRET_KEY"].strip(), base_url=base_url, mask=mask_secrets)
        logger.info("Langfuse tracing on, sending to %s", base_url)
        return client
    except Exception:  # tracing is optional: a broken setup must never stop reviews
        logger.exception("Langfuse tracing is off: the client could not be built")
        return None


# Calls a Langfuse method and logs instead of raising: tracing must never fail a review.
def _safely(action: Callable, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except Exception:
        logger.warning("Langfuse tracing call failed", exc_info=True)
        return None


def _context_of(observation) -> TraceContext:
    return {"trace_id": observation.trace_id, "parent_span_id": observation.id}


# One review's root span; yields a ReviewTrace, or None when tracing is off.
@contextmanager
def review_trace(repo_full_name: str, pr_number: int, head_sha: str, job_id: str) -> Iterator["ReviewTrace | None"]:
    client = tracing_client()
    stack = ExitStack()
    trace = None
    if client is not None:
        try:
            from langfuse import propagate_attributes

            stack.enter_context(propagate_attributes(
                trace_name=f"review {repo_full_name}#{pr_number}", session_id=f"{repo_full_name}#{pr_number}",
                tags=[repo_full_name], metadata={"head_sha": head_sha, "job_id": job_id}))
            span = stack.enter_context(client.start_as_current_observation(
                name="review", as_type="chain", input={"repo": repo_full_name, "pr": pr_number, "head_sha": head_sha}))
            trace = ReviewTrace(span)
        except Exception:
            logger.warning("Langfuse review trace could not start", exc_info=True)
            _safely(stack.close)
            stack = ExitStack()
    try:
        yield trace
    except BaseException as error:
        if trace is not None:
            _safely(trace.span.update, level="ERROR", status_message=f"{type(error).__name__}: {error}")
        raise
    finally:
        _safely(stack.close)


# The root span of one review, so the worker can attach the outcome before it ends.
class ReviewTrace:

    def __init__(self, span) -> None:
        self.span = span
        self.context = _context_of(span)

    def record_outcome(self, final_state: dict) -> None:
        findings = final_state.get("posted_findings") or []
        _safely(self.span.update, output={
            "findings": len(findings),
            "blocking": sum(1 for finding in findings if finding.get("severity") in ("CRITICAL", "HIGH")),
            "failed_agents": final_state.get("failed_agents") or [],
            "llm_calls": final_state.get("llm_calls", 0),
            "tokens_in": final_state.get("tokens_in", 0),
            "tokens_out": final_state.get("tokens_out", 0),
        })


# A child observation (an agent, an LLM call, a tool call) of a traced review; a no-op without a trace.
class TracedStep:

    def __init__(self, observation) -> None:
        self.observation = observation

    @property
    def context(self) -> TraceContext | None:
        return _context_of(self.observation) if self.observation is not None else None

    def update(self, **fields: Any) -> None:
        if self.observation is not None:
            _safely(self.observation.update, **fields)


@contextmanager
def traced_step(trace_context: TraceContext | None, name: str, as_type: str, **fields: Any) -> Iterator[TracedStep]:
    client = tracing_client() if trace_context else None
    observation = None
    if client is not None:
        observation = _safely(client.start_observation, trace_context=trace_context, name=name, as_type=as_type, **fields)
    step = TracedStep(observation)
    try:
        yield step
    except BaseException as error:
        step.update(level="ERROR", status_message=f"{type(error).__name__}: {error}")
        raise
    finally:
        if observation is not None:
            _safely(observation.end)


# What one Groq response is worth keeping in a generation: the answer, its tool calls and the token counts.
def record_llm_response(step: TracedStep, response) -> None:
    choice = response.choices[0]
    usage = getattr(response, "usage", None)
    tool_calls = getattr(choice.message, "tool_calls", None) or []
    step.update(
        output={"content": choice.message.content or "",
                "tool_calls": [{"name": call.function.name, "arguments": call.function.arguments} for call in tool_calls]},
        usage_details={"input": getattr(usage, "prompt_tokens", 0) or 0, "output": getattr(usage, "completion_tokens", 0) or 0},
        metadata={"finish_reason": str(getattr(choice, "finish_reason", None))},
    )


# Sends what is still buffered; the worker calls it on shutdown so the last reviews' traces aren't lost.
def flush_tracing() -> None:
    client = _client
    if client is not None:
        _safely(client.flush)
