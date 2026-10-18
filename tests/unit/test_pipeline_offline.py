from types import SimpleNamespace

import pytest

from rag.ast_parser import parse_file
from rag.knowledge_graph import build_knowledge_graph
from review.agents.agent_runner import EMPTY_CONTEXT_SUMMARY
from review.graph import REVIEW_AGENTS, ReviewAgentsUnavailable, build_graph


AUTH_PATH = "auth/service.py"
AUTH_SOURCE = '''class AuthService:
    def __init__(self, db):
        self.db = db

    def login(self, username, password):
        user = self.db.find(username)
        if user and verify_password(password, user.password_hash):
            return create_session(user)
        return None


def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_session(user):
    return {"user_id": user.id}
'''

PR_DIFF = """diff --git a/auth/service.py b/auth/service.py
--- a/auth/service.py
+++ b/auth/service.py
@@ -12,2 +12,4 @@
 def verify_password(password, hashed):
+    if not hashed:
+        return False
"""

CLEAN_AGENT_REPLY = "[]"
FINDING_REPLY = (
    '[{"severity": "HIGH", "file_path": "auth/service.py", "line_number": 13, '
    '"title": "Empty hash accepted", "description": "d", "evidence": "e", "suggestion": "s"}]'
)
RECURSION_LIMIT = 15


# Stand-in for a Groq client: records each request and replies with `content`.
class FakeLLM:

    def __init__(self, role: str, calls: list, content: str | None) -> None:
        self.role, self.calls, self.content = role, calls, content
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request) -> SimpleNamespace:
        self.calls.append((self.role, request))
        message = SimpleNamespace(content=self.content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


# Fakes every agent's Groq client; returns the recorded (role, request) calls.
def install_fakes(monkeypatch, content: str | None) -> list:
    calls: list = []
    for agent in REVIEW_AGENTS:
        monkeypatch.setattr(
            f"review.agents.{agent}.agent._build_groq_client",
            lambda role=agent: FakeLLM(role, calls, content),
        )
    return calls


# Builds the same initial ReviewState app/worker.py passes to the graph.
def build_state() -> dict:
    return {
        "pr_number": 7,
        "repo_full_name": "test-owner/test-repo",
        "pr_diff": PR_DIFF,
        "changed_files": [],
        "review_context": "",
        "findings": [],
        "failed_agents": [],
        "final_review_markdown": "",
    }


# Compiles the real graph over an offline call graph and runs one review.
def run_review(repo_path: str | None = None) -> dict:
    compiled = build_graph(build_knowledge_graph(parse_file(AUTH_PATH, AUTH_SOURCE)), repo_path)
    return compiled.invoke(build_state(), config={"recursion_limit": RECURSION_LIMIT})


def roles_of(calls: list) -> list[str]:
    return [role for role, _request in calls]


def test_clean_pr_terminates_and_calls_each_agent_once(monkeypatch):
    calls = install_fakes(monkeypatch, CLEAN_AGENT_REPLY)

    final_state = run_review()
    roles = roles_of(calls)

    assert sorted(roles) == sorted(REVIEW_AGENTS), f"each agent runs exactly once and nothing else calls Groq: {roles}"
    assert final_state["findings"] == []
    assert "No issues found" in final_state["final_review_markdown"], "a clean PR must still produce a review comment"
    print(f"OK - clean PR finished in one pass, LLM calls: {roles}")


def test_pr_with_findings_reaches_the_synthesizer(monkeypatch):
    install_fakes(monkeypatch, FINDING_REPLY)

    final_state = run_review()

    assert len(final_state["findings"]) == len(REVIEW_AGENTS), "one finding per agent, merged"
    assert "Empty hash accepted" in final_state["final_review_markdown"]
    print("OK - findings from all review agents merged and rendered once")


def test_agent_prompts_carry_the_diff_and_the_empty_context_note(monkeypatch):
    calls = install_fakes(monkeypatch, CLEAN_AGENT_REPLY)

    run_review()

    for role, request in calls:
        prompt = request["messages"][0]["content"]
        assert "+    if not hashed:" in prompt, f"{role} did not get the diff"
        assert EMPTY_CONTEXT_SUMMARY in prompt, f"{role} should be told to use its tools for context"
    print("OK - every agent prompt has the diff and the 'use your tools' context note")


def test_agents_get_their_tools_through_the_graph(monkeypatch, tmp_path):
    calls = install_fakes(monkeypatch, CLEAN_AGENT_REPLY)

    run_review(repo_path=str(tmp_path))

    offered = {role: {tool["function"]["name"] for tool in request.get("tools", [])} for role, request in calls}
    assert "find_callers" in offered["logic"], "graph tools need the call graph passed to build_graph"
    assert "read_file" in offered["security"], "repo tools need the checkout passed to build_graph"
    print("OK - build_graph hands the checkout and the call graph to the agents' tools")


def test_none_llm_content_does_not_crash(monkeypatch):
    install_fakes(monkeypatch, None)

    final_state = run_review()

    assert final_state["findings"] == []
    assert "No issues found" in final_state["final_review_markdown"]
    print("OK - None content from every agent degraded gracefully")


def test_one_failing_agent_gives_a_partial_review(monkeypatch):
    install_fakes(monkeypatch, FINDING_REPLY)

    def groq_down():
        raise RuntimeError("Groq 503")

    monkeypatch.setattr("review.agents.logic.agent._build_groq_client", groq_down)

    final_state = run_review()

    assert final_state["failed_agents"] == ["logic"]
    assert len(final_state["findings"]) == len(REVIEW_AGENTS) - 1, "security and quality still report"
    assert "Partial review" in final_state["final_review_markdown"] and "Logic" in final_state["final_review_markdown"]
    print("OK - a failing agent costs its category, not the review")


def test_every_agent_failing_raises_so_the_job_is_retried(monkeypatch):
    def groq_down():
        raise RuntimeError("Groq 503")

    for agent in REVIEW_AGENTS:
        monkeypatch.setattr(f"review.agents.{agent}.agent._build_groq_client", groq_down)

    with pytest.raises(ReviewAgentsUnavailable):
        run_review()
    print("OK - an outage is retried instead of posting an empty review")


if __name__ == "__main__":
    print("Run under pytest: this module relies on monkeypatch fixtures.")
