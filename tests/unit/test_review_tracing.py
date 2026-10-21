import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from review import review_tracing
from review.agents.agent_runner import run_agent
from review.agents.security.agent import SECURITY_AGENT
from review.review_tracing import is_tracing_configured, mask_secrets, review_trace, traced_step


FINDING = {"severity": "HIGH", "file_path": "app/x.py", "line_number": 1, "title": "SQL injection",
           "description": "Query built from input.", "evidence": "query = f\"...\"", "suggestion": "Parameterize it."}
GITHUB_TOKEN = "ghp_" + "c" * 36


# An observation that records what was sent to it.
class FakeObservation:

    def __init__(self, name: str, as_type: str, parent: str | None, fields: dict) -> None:
        self.name, self.as_type, self.parent, self.fields = name, as_type, parent, dict(fields)
        self.trace_id, self.id, self.ended = "trace-1", f"span-{name}", False

    def update(self, **fields) -> None:
        self.fields.update(fields)

    def end(self) -> None:
        self.ended = True


# Stands in for the Langfuse client: keeps every observation, never sends anything.
class FakeLangfuse:

    def __init__(self, fail: bool = False) -> None:
        self.fail, self.observations = fail, []

    def start_observation(self, *, trace_context, name, as_type, **fields):
        if self.fail:
            raise ConnectionError("langfuse unreachable")
        observation = FakeObservation(name, as_type, trace_context.get("parent_span_id"), fields)
        self.observations.append(observation)
        return observation

    @contextmanager
    def start_as_current_observation(self, *, name, as_type, **fields):
        observation = FakeObservation(name, as_type, None, fields)
        self.observations.append(observation)
        yield observation
        observation.ended = True

    def named(self, name: str) -> FakeObservation:
        return next(observation for observation in self.observations if observation.name == name)


# A Groq stand-in that answers with one finding and token counts.
class FakeGroq:

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        message = SimpleNamespace(content=json.dumps([FINDING]), tool_calls=None)
        usage = SimpleNamespace(prompt_tokens=120, completion_tokens=30)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage)


@pytest.fixture
def langfuse(monkeypatch):
    client = FakeLangfuse()
    monkeypatch.setattr(review_tracing, "_client", client)
    monkeypatch.setattr(review_tracing, "_client_built", True)
    return client


def make_state(trace_context=None) -> dict:
    diff = "+++ b/app/x.py\n@@ -0,0 +1 @@\n+query = f\"...\"\n"
    return {"pr_number": 1, "repo_full_name": "o/r", "pr_diff": diff, "changed_files": ["app/x.py"],
            "review_context": "", "findings": [], "trace_context": trace_context}


def test_tracing_is_off_in_tests_even_with_real_keys_in_env(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-x")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-x")
    assert not is_tracing_configured(), "tests/conftest.py forces LANGFUSE_TRACING_ENABLED=false"
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    assert is_tracing_configured()
    monkeypatch.delenv("LANGFUSE_SECRET_KEY")
    assert not is_tracing_configured(), "both keys are needed"


def test_without_a_trace_every_step_is_a_no_op(langfuse):
    with traced_step(None, "security", "agent") as step:
        step.update(output="ignored")
    assert step.context is None and langfuse.observations == []


def test_an_agent_run_becomes_an_agent_span_with_one_generation_inside(langfuse):
    result = run_agent(SECURITY_AGENT, make_state({"trace_id": "trace-1", "parent_span_id": "span-review"}), FakeGroq())

    agent, generation = langfuse.named("security"), langfuse.named("llm call")
    assert len(result["findings"]) == 1
    assert agent.as_type == "agent" and agent.parent == "span-review"
    assert generation.as_type == "generation" and generation.parent == agent.id, "the call hangs under its agent"
    assert generation.fields["usage_details"] == {"input": 120, "output": 30}
    assert generation.fields["model"] and "SQL injection" in generation.fields["output"]["content"]
    assert agent.fields["output"]["findings"] == 1 and agent.ended and generation.ended


def test_a_broken_tracing_backend_never_fails_the_review(monkeypatch):
    monkeypatch.setattr(review_tracing, "_client", FakeLangfuse(fail=True))
    monkeypatch.setattr(review_tracing, "_client_built", True)

    result = run_agent(SECURITY_AGENT, make_state({"trace_id": "trace-1", "parent_span_id": "span-review"}), FakeGroq())

    assert len(result["findings"]) == 1


def test_the_review_trace_records_the_outcome_and_marks_errors(langfuse):
    with review_trace("o/r", 7, "abc123", "job-1") as trace:
        trace.record_outcome({"posted_findings": [FINDING], "failed_agents": ["logic"], "llm_calls": 3})
    review = langfuse.named("review")
    assert trace.context == {"trace_id": "trace-1", "parent_span_id": "span-review"}
    assert review.fields["output"]["blocking"] == 1 and review.fields["output"]["failed_agents"] == ["logic"]

    with pytest.raises(RuntimeError):
        with review_trace("o/r", 7, "abc123", "job-2"):
            raise RuntimeError("every review agent failed")
    assert langfuse.observations[-1].fields["level"] == "ERROR", "a failed review is visible as an error trace"


def test_review_trace_yields_none_when_tracing_is_off(monkeypatch):
    monkeypatch.setattr(review_tracing, "_client", None)
    monkeypatch.setattr(review_tracing, "_client_built", True)
    with review_trace("o/r", 7, "abc123", "job-1") as trace:
        assert trace is None


def test_everything_sent_to_langfuse_is_redacted_and_capped(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "d" * 30)
    data = {"messages": [{"content": f'token = "{GITHUB_TOKEN}"'}], "note": "key gsk_" + "d" * 30, "long": "x" * 70_000, "n": 3}

    masked = mask_secrets(data=data)

    assert GITHUB_TOKEN not in json.dumps(masked) and "gsk_" not in masked["note"]
    assert len(masked["long"]) == review_tracing.MAX_TRACED_TEXT_CHARS and masked["n"] == 3
