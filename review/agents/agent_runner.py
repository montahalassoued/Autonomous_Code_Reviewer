"""
agent_runner.py - the executor shared by the three review agents
(security_agent.py, logic_agent.py and quality_agent.py, in this
folder). It runs ONE agent at a time and knows nothing about the others:
the order and parallelism are decided by review/graph.py, the orchestrator.

Inputs: an AgentSpec (the agent's category and prompt template), the
current ReviewState (pr_diff + retrieved_context) and a Groq client.
Outputs: a list of validated Finding objects tagged with the agent's
name, which review/synthesizer.py groups by category.

Why one shared runner: the old five agents each copied the same ~150
lines (client, context formatting, JSON parsing, validation). A fix in
one copy never reached the others. Now an agent is just a prompt plus a
name, and the robustness rules live in one place:
  - the LLM output is parsed defensively and never crashes the review:
    a malformed response degrades to zero findings from that agent;
  - every finding is validated (known severity, integer line, non-empty
    text) and invalid entries are dropped and logged, so the synthesizer
    never renders a half-empty finding.

The model is read from REVIEW_AGENT_MODEL so it can change without a
code change - Groq retires models (llama-3.3-70b-versatile was retired
while production still used it). The default is provisional until
eval/llm_benchmark.py has compared the candidates on the 3 agents.
"""

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
- "description": a clear explanation of the issue and its impact
- "evidence": the specific code snippet that shows the issue
- "suggestion": a concrete fix

Output ONLY a JSON array of these objects, nothing else - no markdown,
no explanation, no preamble. If there are no issues, output an empty
JSON array: []"""


@dataclass(frozen=True)
class AgentSpec:
    """
    What makes one review agent different from the others.

    Attributes:
        name: the agent's category, also its LangGraph node name and the
              `agent` field of its findings ("security", "logic", "quality")
        prompt_template: the prompt, with {diff} and {context_summary}
                         placeholders
    """
    name: str
    prompt_template: str


def review_agent_model() -> str:
    """
    Returns the Groq model the review agents call.

    Returns:
        REVIEW_AGENT_MODEL from the environment, or the provisional default
    """
    return os.environ.get("REVIEW_AGENT_MODEL", "").strip() or DEFAULT_REVIEW_AGENT_MODEL


def build_groq_client() -> Groq:
    """
    Builds a Groq client from GROQ_API_KEY.

    Returns:
        a ready Groq client

    Raises:
        RuntimeError: if GROQ_API_KEY is not set, so the failure is clear
                      instead of a cryptic error mid-call
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com and set it "
            "in your .env file."
        )
    return Groq(api_key=api_key)


def format_context(retrieved_context: list[RetrievedChunk]) -> str:
    """
    Renders the retrieved context chunks into a text block for the prompt,
    so an agent can judge the diff together with the code around it
    (e.g. an existing caller that breaks when a signature changes).

    Args:
        retrieved_context: chunks produced by review/retriever.py

    Returns:
        one "- name (file):\\ncode" block per chunk, or a placeholder line
        if no context was retrieved
    """
    if not retrieved_context:
        return EMPTY_CONTEXT_SUMMARY

    return "\n".join(
        f"- {chunk['function_name']} ({chunk['file_path']}):\n{chunk['code']}"
        for chunk in retrieved_context
    )


def parse_findings(raw_output: str) -> list[dict]:
    """
    Parses the LLM's JSON array response into a list of dicts.

    Args:
        raw_output: the raw LLM response text

    Returns:
        the parsed dicts, or [] if the output can't be parsed - a
        malformed response means "no findings from this agent", never a
        crashed review
    """
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


def _parse_line_number(value) -> int | None:
    """
    Reads a line number the LLM may have sent as an int or a numeric string.

    Args:
        value: the raw "line_number" value

    Returns:
        a positive int, or None if the value isn't a usable line number.
        bool is rejected explicitly because Python treats True as 1.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) or None
    return None


def to_finding(raw: dict, agent_name: str) -> Finding | None:
    """
    Validates one parsed dict and converts it into a Finding.

    Args:
        raw: one dict from the LLM's parsed JSON array
        agent_name: the agent's category, stored in Finding.agent

    Returns:
        a Finding, or None if a field is missing or invalid (unknown
        severity, non-integer line, empty title/description/suggestion).
        One bad entry is skipped; the rest of the array still counts.
    """
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


def build_prompt(spec: AgentSpec, state: ReviewState) -> str:
    """
    Fills the agent's prompt template from the review state.

    Args:
        spec: the agent being run
        state: the current ReviewState (reads pr_diff, retrieved_context)

    Returns:
        the complete prompt string
    """
    return spec.prompt_template.format(
        diff=state["pr_diff"],
        context_summary=format_context(state["retrieved_context"]),
    )


def run_agent(spec: AgentSpec, state: ReviewState, client: Groq) -> dict:
    """
    Runs one review agent: builds its prompt, calls the model, and
    returns its validated findings as a LangGraph partial state update.

    Args:
        spec: the agent to run
        state: the current ReviewState
        client: a Groq client (tests pass a fake)

    Returns:
        {"findings": [...]}, merged into ReviewState.findings by the
        operator.add reducer alongside the other agents' findings
    """
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
