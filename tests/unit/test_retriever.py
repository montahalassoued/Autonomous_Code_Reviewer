from types import SimpleNamespace

import httpx
from groq import APIConnectionError

from review.retriever import JUDGE_FALLBACK_SCORE, _judge_retrieval_quality


DIFF = "+ def reset_password(email):\n+     return send_email(email)\n"
CHUNK = SimpleNamespace(metadata={"function_name": "reset_password", "file_path": "app/auth.py"})


# A fake Groq client: returns `content`, or raises `error`, and records the requested model.
class FakeGroq:

    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self.content, self.error, self.models = content, error, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request) -> SimpleNamespace:
        self.models.append(request["model"])
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def test_judge_uses_the_review_agent_model(monkeypatch):
    monkeypatch.setenv("REVIEW_AGENT_MODEL", "groq-judge-model")
    client = FakeGroq('{"score": 0.8}')
    monkeypatch.setattr("review.retriever._build_groq_client", lambda: client)

    assert _judge_retrieval_quality(DIFF, [CHUNK]) == 0.8
    assert client.models == ["groq-judge-model"], "no hard-coded retired model is left in the judge"
    print("OK - the judge reads REVIEW_AGENT_MODEL")


def test_failed_judge_call_stops_the_retry_loop(monkeypatch):
    error = APIConnectionError(request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    monkeypatch.setattr("review.retriever._build_groq_client", lambda: FakeGroq(error=error))

    assert _judge_retrieval_quality(DIFF, [CHUNK]) == JUDGE_FALLBACK_SCORE
    print("OK - a Groq outage keeps the current context instead of retrying or failing the review")


if __name__ == "__main__":
    print("Run under pytest: these tests use monkeypatch.")
