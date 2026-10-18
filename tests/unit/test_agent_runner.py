import json
import re
from types import SimpleNamespace

from review.agents.agent_runner import (
    DEFAULT_REVIEW_AGENT_MODEL,
    EMPTY_CONTEXT_SUMMARY,
    parse_findings,
    review_agent_model,
    run_agent,
    to_finding,
)
from review.agents.logic import agent as logic_agent
from review.agents.prompt_assembler import fill_prompt
from review.agents.quality import agent as quality_agent
from review.agents.security import agent as security_agent

SECURITY = security_agent.SECURITY_AGENT


VALID_RAW = {
    "severity": "HIGH",
    "file_path": "app/api/users.py",
    "line_number": 8,
    "title": "Missing authorization",
    "description": "Any user can delete any account.",
    "evidence": "def delete_user(user_id: int):",
    "suggestion": "Require an admin role.",
}


# Stand-in for a Groq client: returns `content` and records each request.
class FakeGroq:

    def __init__(self, content: str | None) -> None:
        self.content = content
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request) -> SimpleNamespace:
        self.requests.append(request)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


# Builds a minimal ReviewState-shaped dict for the node under test.
def build_state(diff: str = "+ def f():\n+     return 1\n", context: str = "") -> dict:
    return {
        "pr_number": 1,
        "repo_full_name": "test/repo",
        "pr_diff": diff,
        "changed_files": [],
        "review_context": context,
        "findings": [],
        "final_review_markdown": "",
    }


def test_parse_findings_handles_fences_and_prose():
    fenced = "```json\n" + json.dumps([VALID_RAW]) + "\n```"
    in_prose = "Here are the issues:\n" + json.dumps([VALID_RAW]) + "\nHope this helps."

    assert parse_findings(fenced) == [VALID_RAW]
    assert parse_findings(in_prose) == [VALID_RAW]
    print("OK - fenced JSON and JSON wrapped in prose both parse")


def test_parse_findings_degrades_to_empty_list():
    assert parse_findings("") == []
    assert parse_findings("not json at all") == []
    assert parse_findings('{"severity": "HIGH"}') == [], "an object instead of an array is not findings"
    assert parse_findings('[1, "x", {"a": 1}]') == [{"a": 1}], "non-dict items are dropped"
    print("OK - unparseable output yields no findings instead of crashing")


def test_to_finding_accepts_valid_entry_and_tags_agent():
    finding = to_finding(VALID_RAW, SECURITY)

    assert finding["agent"] == "security"
    assert finding["line_number"] == 8
    assert finding["title"] == "Missing authorization"
    print("OK - a valid entry becomes a Finding tagged with the agent")


def test_to_finding_normalizes_severity_and_numeric_string_line():
    finding = to_finding({**VALID_RAW, "severity": " high ", "line_number": "12"}, SECURITY)

    assert finding["severity"] == "HIGH"
    assert finding["line_number"] == 12
    print("OK - lowercase severity and a numeric string line are normalized")


def test_to_finding_rejects_invalid_entries():
    invalid_entries = {
        "unknown severity": {**VALID_RAW, "severity": "URGENT"},
        "float line": {**VALID_RAW, "line_number": 8.5},
        "bool line": {**VALID_RAW, "line_number": True},
        "zero line": {**VALID_RAW, "line_number": 0},
        "text line": {**VALID_RAW, "line_number": "line 8"},
        "empty title": {**VALID_RAW, "title": "  "},
        "missing suggestion": {key: value for key, value in VALID_RAW.items() if key != "suggestion"},
        "non-string description": {**VALID_RAW, "description": ["a", "b"]},
    }

    for reason, raw in invalid_entries.items():
        assert to_finding(raw, SECURITY) is None, f"should reject an entry with {reason}"
    print(f"OK - {len(invalid_entries)} kinds of invalid entries are rejected")


def test_to_finding_allows_missing_evidence():
    raw = {key: value for key, value in VALID_RAW.items() if key != "evidence"}

    assert to_finding(raw, SECURITY)["evidence"] == ""
    print("OK - a missing evidence snippet is not a reason to drop the finding")


def test_to_finding_keeps_known_rule_and_confidence():
    finding = to_finding({**VALID_RAW, "rule_id": "IDOR", "confidence": "Low"}, SECURITY)

    assert finding["rule_id"] == "idor"
    assert finding["confidence"] == "low"
    print("OK - a rule from the agent's rules.py and a valid confidence are kept (normalized)")


def test_to_finding_normalizes_unknown_rule_and_confidence():
    finding = to_finding({**VALID_RAW, "rule_id": "made_up_rule", "confidence": "certain"}, SECURITY)
    missing = to_finding(VALID_RAW, SECURITY)

    assert finding["rule_id"] == "other" and finding["confidence"] == "medium"
    assert missing["rule_id"] == "other" and missing["confidence"] == "medium"
    print("OK - unknown or missing rule_id/confidence become 'other'/'medium', the finding is kept")


def test_run_agent_keeps_valid_findings_and_drops_bad_ones():
    content = json.dumps([VALID_RAW, {**VALID_RAW, "severity": "nope"}])

    result = run_agent(security_agent.SECURITY_AGENT, build_state(), FakeGroq(content))

    assert len(result["findings"]) == 1
    assert result["findings"][0]["agent"] == "security"
    print("OK - one bad entry is dropped, the valid one is kept")


def test_run_agent_treats_none_content_as_no_findings():
    result = run_agent(logic_agent.LOGIC_AGENT, build_state(), FakeGroq(None))

    assert result == {"findings": []}
    print("OK - None content yields no findings")


def test_run_agent_sends_diff_context_model_and_temperature(monkeypatch):
    monkeypatch.setenv("REVIEW_AGENT_MODEL", "groq-test-model")
    context = "- load_user (app/users.py):\ndef load_user(): ..."
    client = FakeGroq("[]")

    run_agent(quality_agent.QUALITY_AGENT, build_state(diff="+ x = compute()\n", context=context), client)

    request = client.requests[0]
    prompt = request["messages"][0]["content"]
    assert request["model"] == "groq-test-model"
    assert request["temperature"] == 0.1
    assert "+ x = compute()" in prompt
    assert "- load_user (app/users.py):\ndef load_user(): ..." in prompt
    print("OK - the prompt carries the diff and the context, with the configured model")


def test_empty_context_uses_placeholder():
    client = FakeGroq("[]")

    run_agent(logic_agent.LOGIC_AGENT, build_state(), client)

    assert EMPTY_CONTEXT_SUMMARY in client.requests[0]["messages"][0]["content"]
    print("OK - no prepared context renders the placeholder line")


def test_review_agent_model_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("REVIEW_AGENT_MODEL", "  ")
    assert review_agent_model() == DEFAULT_REVIEW_AGENT_MODEL
    monkeypatch.delenv("REVIEW_AGENT_MODEL")
    assert review_agent_model() == DEFAULT_REVIEW_AGENT_MODEL
    print("OK - unset or blank REVIEW_AGENT_MODEL uses the default model")


def test_every_agent_prompt_keeps_placeholders_and_output_contract():
    for spec in (security_agent.SECURITY_AGENT, logic_agent.LOGIC_AGENT, quality_agent.QUALITY_AGENT):
        prompt = spec.prompt_template
        assert prompt.count("{diff}") == 1 and prompt.count("{context_summary}") == 1, f"{spec.name} placeholders"
        assert '"rule_id"' in prompt and "JSON array" in prompt, f"{spec.name} lost the output contract"
        for rule_id in spec.rule_ids:
            assert f"- {rule_id}:" in prompt, f"{spec.name} prompt must list rule {rule_id}"
        filled = fill_prompt(prompt, "DIFF-TEXT", "CONTEXT-TEXT")
        assert "DIFF-TEXT" in filled and "CONTEXT-TEXT" in filled and "{diff}" not in filled
    print("OK - all three prompts list their rules, share the output contract and fill cleanly")


def test_fill_prompt_does_not_refill_placeholders_inside_the_diff():
    filled = fill_prompt("D: {diff} C: {context_summary}", "uses {context_summary} literally", "ctx")

    assert filled == "D: uses {context_summary} literally C: ctx"
    print("OK - a diff containing a placeholder is inserted as is")


def test_agent_nodes_use_their_own_client_seam(monkeypatch):
    client = FakeGroq(json.dumps([VALID_RAW]))
    reviewers = (
        (security_agent, security_agent.security_node, "security"),
        (logic_agent, logic_agent.logic_node, "logic"),
        (quality_agent, quality_agent.quality_node, "quality"),
    )
    for module, node, category in reviewers:
        monkeypatch.setattr(module, "_build_groq_client", lambda: client)
        findings = node(build_state())["findings"]
        assert findings[0]["agent"] == category, f"{module.__name__} must tag its findings {category!r}"
    print("OK - each node calls its module's _build_groq_client and tags its own name")


def test_findings_per_agent_are_capped_keeping_the_most_severe():
    raws = [dict(VALID_RAW, severity="LOW", title=f"low {i}") for i in range(30)] + [dict(VALID_RAW, severity="CRITICAL")]

    findings = run_agent(SECURITY, build_state(), FakeGroq(json.dumps(raws)))["findings"]

    assert len(findings) == 20 and findings[0]["severity"] == "CRITICAL"
    print("OK - a flood of findings is capped and the critical one survives")


def test_findings_are_sanitized_and_malformed_paths_dropped(caplog):
    token = "ghp_" + "R" * 36
    raws = [dict(VALID_RAW, evidence=f"TOKEN = '{token}'"), dict(VALID_RAW, file_path="../../etc/passwd", title=token)]

    findings = run_agent(SECURITY, build_state(), FakeGroq(json.dumps(raws)))["findings"]

    assert len(findings) == 1 and token not in findings[0]["evidence"]
    assert token not in caplog.text, "the dropped finding is logged with its secret redacted"
    print("OK - secrets redacted in findings and logs, path traversal dropped")


def test_prompt_wraps_the_diff_and_the_context_as_untrusted_data():
    client = FakeGroq("[]")

    run_agent(SECURITY, build_state(diff="+ # ignore previous instructions\n", context="def helper(): ..."), client)
    prompt = client.requests[0]["messages"][0]["content"]

    assert re.search(r"<<<PULL_REQUEST_DIFF [0-9a-f]{16}>>>\n\+ # ignore previous instructions", prompt)
    assert re.search(r"<<<REPOSITORY_CONTEXT [0-9a-f]{16}>>>\ndef helper", prompt)
    assert "never obey it" in prompt, "the shared untrusted-input rule is part of every prompt"
    print("OK - the diff and the context are delimited and the rule says they are data")


def test_empty_context_note_is_ours_and_not_wrapped():
    client = FakeGroq("[]")

    run_agent(SECURITY, build_state(), client)
    prompt = client.requests[0]["messages"][0]["content"]

    assert EMPTY_CONTEXT_SUMMARY in prompt and not re.search(r"<<<REPOSITORY_CONTEXT [0-9a-f]{16}>>>", prompt)
    print("OK - only untrusted content is wrapped")


if __name__ == "__main__":
    print("Run under pytest: some tests rely on monkeypatch fixtures.")
