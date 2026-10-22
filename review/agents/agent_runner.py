import json
import logging
import os
from dataclasses import dataclass

from review.agents.prompt_assembler import OTHER_RULE_ID, fill_prompt
from review.agents.tools.tool_definition import Tool, ToolContext, execute_tool_call
from review.finding_locator import locate_findings
from review.finding_merger import merge_duplicate_findings
from review.llm_client import ReviewLlmClient, build_review_llm_client, llm_deadline
from review.output_sanitizer import sanitize_finding
from review.review_tracing import TraceContext, record_llm_response, traced_step
from review.secret_redactor import environment_secret_values, redact_secrets
from review.untrusted_input import CONTEXT_LABEL, DIFF_LABEL, wrap_untrusted
from review.state import ReviewState, Finding


logger = logging.getLogger(__name__)

DEFAULT_REVIEW_AGENT_MODEL = "openai/gpt-oss-120b"
AGENT_TEMPERATURE = 0.1  # consistency over creativity for analysis
# Reasoning models spend output tokens thinking; without a cap Groq's default (2048) cut answers off.
AGENT_MAX_OUTPUT_TOKENS = 4096
# Each extra call resends the whole prompt, so tool use is capped per agent and per review.
DEFAULT_MAX_TOOL_CALLS = 3
FINAL_ANSWER_REQUEST = "Tool call limit reached. Answer now with the JSON array only."
VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
# A flood of findings is noise or an injection; the most severe ones are kept.
MAX_FINDINGS_PER_AGENT = 20
REQUIRED_TEXT_FIELDS = ("file_path", "title", "description", "suggestion")
VALID_CONFIDENCES = {"high", "medium", "low"}
DEFAULT_CONFIDENCE = "medium"
# Until the structural context builder exists, the agents look outside the diff with their tools.
EMPTY_CONTEXT_SUMMARY = "(no context prepared: use your tools to look at the repository when you need it)"

# What makes one review agent different from the others.
@dataclass(frozen=True)
class AgentSpec:
    name: str
    prompt_template: str
    rule_ids: frozenset[str]
    tools: tuple[Tool, ...] = ()
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS


# Returns the Groq model the review agents call.
def review_agent_model() -> str:
    return os.environ.get("REVIEW_AGENT_MODEL", "").strip() or DEFAULT_REVIEW_AGENT_MODEL


# One agent's model: SECURITY_AGENT_MODEL and so on, else REVIEW_AGENT_MODEL. Groq limits each model separately.
def agent_model(agent_name: str) -> str:
    return os.environ.get(f"{agent_name.upper()}_AGENT_MODEL", "").strip() or review_agent_model()


# The model an agent switches to when its own is rate limited; None when unset.
def fallback_agent_model() -> str | None:
    return os.environ.get("FALLBACK_AGENT_MODEL", "").strip() or None


def _is_rate_limited(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 429


# The agents' Groq client: timeout, retries and a concurrency limit live in review/llm_client.py.
def build_groq_client() -> ReviewLlmClient:
    return build_review_llm_client()


# The JSON array in the LLM's answer, or None when there is none (so "no findings" and "no answer" differ).
def parse_findings_array(raw_output: str) -> list[dict] | None:
    cleaned = raw_output.strip().strip("`").removeprefix("json").strip()

    # strict=False keeps raw newlines in strings (multi-line code in a suggestion); the sanitizer drops other controls.
    try:
        parsed = json.loads(cleaned, strict=False)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass  # fall through to the bracket search below

    # The model may have wrapped the array in prose despite instructions.
    # Retry on the outermost [ ... ] boundaries.
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1], strict=False)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass  # unparseable: the caller decides what that means

    return None


# Parses the LLM's JSON array response into a list of dicts; [] when it can't be parsed (the benchmark scores that).
def parse_findings(raw_output: str) -> list[dict]:
    return parse_findings_array(raw_output) or []


# Positive int from an int or numeric string; None otherwise (bool rejected).
def _parse_line_number(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) or None
    return None


# Validates one parsed dict and converts it into a Finding.
def to_finding(raw: dict, spec: AgentSpec) -> Finding | None:
    severity = str(raw.get("severity", "")).strip().upper()
    if severity not in VALID_SEVERITIES:
        return None

    line_number = _parse_line_number(raw.get("line_number"))
    if line_number is None:
        return None

    for field in REQUIRED_TEXT_FIELDS:
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            return None

    # An unknown rule id or confidence is normalized, not a reason to drop the finding.
    rule_id = str(raw.get("rule_id", "")).strip().lower()
    confidence = str(raw.get("confidence", "")).strip().lower()
    evidence = raw.get("evidence")
    return Finding(
        agent=spec.name,
        severity=severity,
        rule_id=rule_id if rule_id in spec.rule_ids else OTHER_RULE_ID,
        confidence=confidence if confidence in VALID_CONFIDENCES else DEFAULT_CONFIDENCE,
        file_path=raw["file_path"].strip(),
        line_number=line_number,
        title=raw["title"].strip(),
        description=raw["description"].strip(),
        evidence=evidence if isinstance(evidence, str) else "",
        suggestion=raw["suggestion"].strip(),
    )


# Fills the agent's prompt template; the diff and the context are wrapped as untrusted data, our own note is not.
def build_prompt(spec: AgentSpec, state: ReviewState) -> str:
    context = state.get("review_context")
    return fill_prompt(
        spec.prompt_template,
        wrap_untrusted(state["pr_diff"], DIFF_LABEL),
        wrap_untrusted(context, CONTEXT_LABEL) if context else EMPTY_CONTEXT_SUMMARY,
    )


# Model responses and tokens of one agent's conversation, tool rounds included.
@dataclass
class LlmUsage:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0

    def record(self, response) -> None:
        usage = getattr(response, "usage", None)
        self.calls += 1
        self.tokens_in += getattr(usage, "prompt_tokens", 0) or 0
        self.tokens_out += getattr(usage, "completion_tokens", 0) or 0


def _ask_model(client: ReviewLlmClient, model: str, trace_context: TraceContext | None, usage: LlmUsage,
               messages: list[dict], tool_schemas: list[dict], tool_choice: str):
    request = {"model": model, "messages": messages, "temperature": AGENT_TEMPERATURE,
               "max_completion_tokens": AGENT_MAX_OUTPUT_TOKENS}
    if tool_schemas:
        request["tools"] = tool_schemas
        request["tool_choice"] = tool_choice
    parameters = {"temperature": AGENT_TEMPERATURE, "max_completion_tokens": AGENT_MAX_OUTPUT_TOKENS,
                  "tool_choice": request.get("tool_choice")}
    # list(): the conversation grows after this call, the trace keeps what was actually sent.
    with traced_step(trace_context, "llm call", "generation", model=model, input=list(messages),
                     model_parameters=parameters) as step:
        response = client.chat.completions.create(**request)
        record_llm_response(step, response)
    usage.record(response)
    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise UnusableAgentAnswer(f"answer cut off at {AGENT_MAX_OUTPUT_TOKENS} output tokens: findings may be lost")
    return choice.message


# Asks models[0]; a 429 the client gave up on (spent quota, retries used) moves the conversation to the next model.
def _ask_with_fallback(client: ReviewLlmClient, models: list[str], agent_name: str,
                       trace_context: TraceContext | None, *request_args):
    while True:
        try:
            return _ask_model(client, models[0], trace_context, *request_args)
        except Exception as exc:
            if not _is_rate_limited(exc) or len(models) == 1:
                raise
            logger.warning("%s: %s is rate limited, switching to %s", agent_name, models[0], models[1])
            models.pop(0)


def _assistant_turn(message, tool_calls) -> dict:
    return {
        "role": "assistant",
        "content": message.content or "",
        "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}
            for call in tool_calls
        ],
    }


# Let the model call its tools up to the limit, then make it answer with tool_choice="none".
def _converse(spec: AgentSpec, prompt: str, client: ReviewLlmClient, tool_context: ToolContext | None, usage: LlmUsage,
              trace_context: TraceContext | None = None) -> str:
    tools = {tool.name: tool for tool in spec.tools if tool_context is not None and tool.is_available(tool_context)}
    tool_schemas = [tool.to_groq_schema() for tool in tools.values()]
    messages = [{"role": "user", "content": prompt}]
    calls_left = spec.max_tool_calls if tools else 0
    models = list(dict.fromkeys(model for model in (agent_model(spec.name), fallback_agent_model()) if model))

    while True:
        message = _ask_with_fallback(client, models, spec.name, trace_context, usage, messages, tool_schemas,
                                     "auto" if calls_left > 0 else "none")
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls or calls_left == 0:
            # content can be None (a filtered or empty completion): that is no answer, not "no findings".
            if not (message.content or "").strip():
                raise UnusableAgentAnswer("empty answer" + (" after using its tools" if tools else ""))
            return message.content

        messages.append(_assistant_turn(message, tool_calls))
        for call in tool_calls:
            # Every tool_call_id needs a reply, even the calls made past the limit.
            if calls_left > 0:
                with traced_step(trace_context, call.function.name, "tool", input=call.function.arguments) as step:
                    output = execute_tool_call(tools, call.function.name, call.function.arguments, tool_context)
                    step.update(output=output)
                calls_left -= 1
            else:
                output = "Not run: tool call limit reached."
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
        if calls_left == 0:
            messages.append({"role": "user", "content": FINAL_ANSWER_REQUEST})


# An answer that can't be trusted to hold every finding: posting it would read as "no issues" for that category.
class UnusableAgentAnswer(RuntimeError):
    pass


# An agent that fails mid-conversation still spent quota: the graph adds this usage to the partial review's cost.
class AgentFailed(Exception):
    def __init__(self, agent_name: str, usage: LlmUsage) -> None:
        super().__init__(f"{agent_name} agent failed after {usage.calls} LLM call(s)")
        self.usage = usage


# Run one agent: prompt -> Groq (with bounded tool calls) -> validated findings.
def run_agent(spec: AgentSpec, state: ReviewState, client: ReviewLlmClient, tool_context: ToolContext | None = None) -> dict:
    usage = LlmUsage()
    with traced_step(state.get("trace_context"), spec.name, "agent") as agent_step:
        try:
            with llm_deadline(state.get("review_deadline")):
                raw_output = _converse(spec, build_prompt(spec, state), client, tool_context, usage, agent_step.context)
            if parse_findings_array(raw_output) is None:
                raise UnusableAgentAnswer(f"answer is not a JSON array: {redact_secrets(raw_output[:120])!r}")
        except Exception as exc:
            raise AgentFailed(spec.name, usage) from exc
        findings, raw_count = postprocess_findings(raw_output, spec, state["pr_diff"], environment_secret_values())
        agent_step.update(output={"findings": len(findings), "raw_findings": raw_count,
                                  "llm_calls": usage.calls, "tokens_in": usage.tokens_in, "tokens_out": usage.tokens_out})
    return {"findings": findings, "llm_calls": usage.calls, "tokens_in": usage.tokens_in, "tokens_out": usage.tokens_out}


# What gets posted from a model answer: parse, validate, sanitize, move to the quoted line, merge, cap.
# The benchmark scores through this same function, so it measures exactly what production posts.
def postprocess_findings(raw_output: str, spec: AgentSpec, diff: str,
                         known_secrets: tuple[str, ...] = ()) -> tuple[list[Finding], int]:
    raw_findings = parse_findings(raw_output)
    findings = []
    for raw in raw_findings:
        finding = to_finding(raw, spec)
        if finding is not None:
            finding = sanitize_finding(finding, known_secrets)
        if finding is None:
            # The raw output may quote a secret from the diff, so the log line is redacted too.
            logger.warning("Dropped invalid %s finding: %.200s", spec.name, redact_secrets(json.dumps(raw, default=str), known_secrets))
            continue
        findings.append(finding)

    findings = merge_duplicate_findings(locate_findings(findings, diff))
    if len(findings) > MAX_FINDINGS_PER_AGENT:
        logger.warning("%s returned %d findings, keeping the %d most severe", spec.name, len(findings), MAX_FINDINGS_PER_AGENT)
    findings = sorted(findings, key=lambda finding: SEVERITY_RANK[finding["severity"]])[:MAX_FINDINGS_PER_AGENT]
    return findings, len(raw_findings)
