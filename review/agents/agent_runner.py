import json
import logging
import os
from dataclasses import dataclass

from groq import Groq

from review.agents.prompt_assembler import OTHER_RULE_ID, fill_prompt
from review.agents.tools.tool_definition import Tool, ToolContext, execute_tool_call
from review.output_sanitizer import sanitize_finding
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


# Builds a Groq client from GROQ_API_KEY.
def build_groq_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com and set it "
            "in your .env file."
        )
    return Groq(api_key=api_key)


# Parses the LLM's JSON array response into a list of dicts.
def parse_findings(raw_output: str) -> list[dict]:
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
            pass  # unparseable: degrade to no findings

    return []


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


def _ask_model(client: Groq, messages: list[dict], tool_schemas: list[dict], tool_choice: str):
    request = {"model": review_agent_model(), "messages": messages, "temperature": AGENT_TEMPERATURE,
               "max_completion_tokens": AGENT_MAX_OUTPUT_TOKENS}
    if tool_schemas:
        request["tools"] = tool_schemas
        request["tool_choice"] = tool_choice
    choice = client.chat.completions.create(**request).choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        logger.warning("Agent answer cut off at %d output tokens: its findings may be lost", AGENT_MAX_OUTPUT_TOKENS)
    return choice.message


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
def _converse(spec: AgentSpec, prompt: str, client: Groq, tool_context: ToolContext | None) -> str:
    tools = {tool.name: tool for tool in spec.tools if tool_context is not None and tool.is_available(tool_context)}
    tool_schemas = [tool.to_groq_schema() for tool in tools.values()]
    messages = [{"role": "user", "content": prompt}]
    calls_left = spec.max_tool_calls if tools else 0

    while True:
        message = _ask_model(client, messages, tool_schemas, "auto" if calls_left > 0 else "none")
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls or calls_left == 0:
            if tools and not message.content:
                logger.warning("%s returned no answer after using its tools", spec.name)
            # content can be None (a filtered or empty completion): no findings.
            return message.content or ""

        messages.append(_assistant_turn(message, tool_calls))
        for call in tool_calls:
            # Every tool_call_id needs a reply, even the calls made past the limit.
            if calls_left > 0:
                output = execute_tool_call(tools, call.function.name, call.function.arguments, tool_context)
                calls_left -= 1
            else:
                output = "Not run: tool call limit reached."
            messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
        if calls_left == 0:
            messages.append({"role": "user", "content": FINAL_ANSWER_REQUEST})


# Run one agent: prompt -> Groq (with bounded tool calls) -> validated findings.
def run_agent(spec: AgentSpec, state: ReviewState, client: Groq, tool_context: ToolContext | None = None) -> dict:
    raw_output = _converse(spec, build_prompt(spec, state), client, tool_context)
    raw_findings = parse_findings(raw_output)

    known_secrets = environment_secret_values()
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

    if len(findings) > MAX_FINDINGS_PER_AGENT:
        logger.warning("%s returned %d findings, keeping the %d most severe", spec.name, len(findings), MAX_FINDINGS_PER_AGENT)
        findings = sorted(findings, key=lambda finding: SEVERITY_RANK[finding["severity"]])[:MAX_FINDINGS_PER_AGENT]
    return {"findings": findings}
