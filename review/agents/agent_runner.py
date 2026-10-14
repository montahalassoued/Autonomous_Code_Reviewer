import json
import logging
import os
from dataclasses import dataclass

from groq import Groq

from review.state import ReviewState, Finding, RetrievedChunk


logger = logging.getLogger(__name__)

DEFAULT_REVIEW_AGENT_MODEL = "openai/gpt-oss-120b"
AGENT_TEMPERATURE = 0.1  # consistency over creativity for analysis
VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
REQUIRED_TEXT_FIELDS = ("file_path", "title", "description", "suggestion")
EMPTY_CONTEXT_SUMMARY = "(no related context retrieved)"

# The output contract every agent prompt ends with. Shared so the three
# agents can never drift apart on the JSON shape the runner parses.
FINDINGS_OUTPUT_INSTRUCTIONS = """For each issue found, output one JSON object with these exact fields:
- "severity": one of "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"
- "file_path": the file the issue is in
- "line_number": the line number (integer) the issue occurs at, from the diff
- "title": a short title for the issue
- "description": what goes wrong, for which input or scenario, and its impact
- "evidence": the specific code snippet that shows the issue
- "suggestion": a concrete fix and why it helps, with a short code example
  when that makes it clearer

Output ONLY a JSON array of these objects, nothing else - no markdown,
no explanation, no preamble. If there are no issues, output an empty
JSON array: []"""


# What makes one review agent different from the others.
@dataclass(frozen=True)
class AgentSpec:
    name: str
    prompt_template: str


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


# Render the retrieved chunks as '- name (file)' + code blocks for the prompt.
def format_context(retrieved_context: list[RetrievedChunk]) -> str:
    if not retrieved_context:
        return EMPTY_CONTEXT_SUMMARY

    return "\n".join(
        f"- {chunk['function_name']} ({chunk['file_path']}):\n{chunk['code']}"
        for chunk in retrieved_context
    )


# Parses the LLM's JSON array response into a list of dicts.
def parse_findings(raw_output: str) -> list[dict]:
    cleaned = raw_output.strip().strip("`").removeprefix("json").strip()

    try:
        parsed = json.loads(cleaned)
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
            parsed = json.loads(cleaned[start:end + 1])
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
def to_finding(raw: dict, agent_name: str) -> Finding | None:
    severity = str(raw.get("severity", "")).strip().upper()
    if severity not in VALID_SEVERITIES:
        return None

    line_number = _parse_line_number(raw.get("line_number"))
    if line_number is None:
        return None

    for field in REQUIRED_TEXT_FIELDS:
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            return None

    evidence = raw.get("evidence")
    return Finding(
        agent=agent_name,
        severity=severity,
        file_path=raw["file_path"].strip(),
        line_number=line_number,
        title=raw["title"].strip(),
        description=raw["description"].strip(),
        evidence=evidence if isinstance(evidence, str) else "",
        suggestion=raw["suggestion"].strip(),
    )


# Fills the agent's prompt template from the review state.
def build_prompt(spec: AgentSpec, state: ReviewState) -> str:
    return spec.prompt_template.format(
        diff=state["pr_diff"],
        context_summary=format_context(state["retrieved_context"]),
    )


# Run one agent: prompt -> Groq -> validated findings.
def run_agent(spec: AgentSpec, state: ReviewState, client: Groq) -> dict:
    response = client.chat.completions.create(
        model=review_agent_model(),
        messages=[{"role": "user", "content": build_prompt(spec, state)}],
        temperature=AGENT_TEMPERATURE,
    )

    # content can be None (a filtered or empty completion): no findings.
    raw_output = response.choices[0].message.content or ""
    raw_findings = parse_findings(raw_output)

    findings = []
    for raw in raw_findings:
        finding = to_finding(raw, spec.name)
        if finding is None:
            logger.warning("Dropped invalid %s finding: %.200s", spec.name, json.dumps(raw, default=str))
            continue
        findings.append(finding)

    return {"findings": findings}
