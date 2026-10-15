from types import SimpleNamespace

import httpx
from groq import APIConnectionError, RateLimitError

from rag.query_decomposer import decompose_diff


DIFF = "+ def reset_password(email):\n+     return send_email(email)\n"
GROQ_REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


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


def test_decomposer_uses_the_model_it_is_given(monkeypatch):
    client = FakeGroq('["password reset flow", "email sending"]')
    monkeypatch.setattr("rag.query_decomposer._build_client", lambda: client)

    assert decompose_diff(DIFF, "groq-test-model") == ["password reset flow", "email sending"]
    assert client.models == ["groq-test-model"], "no hard-coded model is left in the decomposer"
    print("OK - the decomposer calls the model passed by the retriever")


def test_decomposer_falls_back_to_the_diff_on_groq_errors(monkeypatch):
    rate_limited = httpx.Response(429, request=GROQ_REQUEST)
    for error in (RateLimitError("rate limited", response=rate_limited, body=None), APIConnectionError(request=GROQ_REQUEST)):
        monkeypatch.setattr("rag.query_decomposer._build_client", lambda error=error: FakeGroq(error=error))

        assert decompose_diff(DIFF, "groq-test-model") == [DIFF[:200]]
    print("OK - a Groq 429 or outage degrades to one naive query instead of failing the review")


if __name__ == "__main__":
    print("Run under pytest: these tests use monkeypatch.")
