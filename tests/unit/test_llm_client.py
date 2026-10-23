import os
import threading
import time
from types import SimpleNamespace

import groq
import httpx
import pytest

from review import llm_client
from review.llm_client import ReviewLlmClient, build_review_llm_client, is_retryable_llm_error


# An error shaped like a Groq APIStatusError.
class StatusError(Exception):
    def __init__(self, status_code: int, message: str = "") -> None:
        super().__init__(message or f"Error code: {status_code}")
        self.status_code = status_code


PER_MINUTE_429 = StatusError(429, "Rate limit reached on tokens per minute (TPM). Please try again in 7.5s.")
DAILY_429 = StatusError(429, "Rate limit reached on tokens per day (TPD). Please try again in 14m19.6s.")
TIMEOUT = groq.APITimeoutError(request=httpx.Request("POST", "https://api.groq.com"))


# Raises the queued errors in order, then answers; records how many calls ran at once.
class FakeGroq:

    def __init__(self, errors: list[Exception] = (), hold_s: float = 0.0) -> None:
        self.errors, self.hold_s = list(errors), hold_s
        self.calls = self.running = self.most_at_once = 0
        self._lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        with self._lock:
            self.calls += 1
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        try:
            time.sleep(self.hold_s)
            if self.errors:
                raise self.errors.pop(0)
            return SimpleNamespace(model=request["model"])
        finally:
            with self._lock:
                self.running -= 1


def make_client(fake: FakeGroq, max_attempts: int = 3, slots: int = 3):
    waits: list[float] = []
    client = ReviewLlmClient(lambda provider: fake, max_attempts, threading.BoundedSemaphore(slots), sleep=waits.append)
    return client, waits


def test_a_per_minute_rate_limit_is_retried_after_the_wait_groq_asks_for():
    fake = FakeGroq([PER_MINUTE_429])
    client, waits = make_client(fake)
    assert client.chat.completions.create(model="m").model == "m"
    assert fake.calls == 2 and waits == [8.5]  # 7.5s asked, plus 1s margin


def test_a_spent_daily_quota_is_raised_at_once_so_the_fallback_model_takes_over():
    fake = FakeGroq([DAILY_429])
    client, waits = make_client(fake)
    with pytest.raises(StatusError) as raised:
        client.chat.completions.create(model="m")
    assert raised.value.status_code == 429, "the runner's fallback reads the 429"
    assert fake.calls == 1 and waits == []


def test_server_errors_and_timeouts_back_off_then_succeed():
    fake = FakeGroq([StatusError(503), TIMEOUT])
    client, waits = make_client(fake)
    client.chat.completions.create(model="m")
    assert fake.calls == 3 and waits == [3.0, 5.0]  # 2s then 4s backoff, plus 1s margin


def test_the_last_error_is_raised_when_attempts_run_out():
    fake = FakeGroq([StatusError(500), StatusError(502), StatusError(503)])
    client, _ = make_client(fake, max_attempts=3)
    with pytest.raises(StatusError) as raised:
        client.chat.completions.create(model="m")
    assert raised.value.status_code == 503 and fake.calls == 3


def test_client_errors_are_never_retried():
    for error in (StatusError(400), StatusError(401), StatusError(413, "Request too large for model")):
        assert not is_retryable_llm_error(error), error
    assert not is_retryable_llm_error(StatusError(429, "Request too large: tokens per minute"))
    assert is_retryable_llm_error(TIMEOUT) and is_retryable_llm_error(PER_MINUTE_429)


def test_no_more_calls_run_at_once_than_the_slots_allow():
    fake = FakeGroq(hold_s=0.05)
    client, _ = make_client(fake, slots=2)
    threads = [threading.Thread(target=client.chat.completions.create, kwargs={"model": "m"}) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert fake.calls == 5 and fake.most_at_once == 2


def test_build_turns_off_the_sdk_retries_and_reads_the_env(monkeypatch):
    os.environ["GROQ_API_KEY"] = "test-key"
    os.environ["REVIEW_LLM_TIMEOUT_S"] = "12"
    os.environ["REVIEW_LLM_MAX_ATTEMPTS"] = "not a number"
    monkeypatch.setattr(llm_client, "_call_slots", None)
    try:
        client = build_review_llm_client()
        groq_client = client.client_for("groq")
        assert groq_client.max_retries == 0, "the SDK's silent retries would double ours"
        assert groq_client.timeout == 12.0
        assert client._max_attempts == llm_client.DEFAULT_LLM_MAX_ATTEMPTS
    finally:
        del os.environ["REVIEW_LLM_TIMEOUT_S"], os.environ["REVIEW_LLM_MAX_ATTEMPTS"]


def test_a_groq_call_without_a_key_says_what_to_set(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    client = build_review_llm_client()
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        client.chat.completions.create(model="openai/gpt-oss-120b", messages=[])
    assert llm_client.provider_config_problems(["openai/gpt-oss-120b"]), "the worker's startup check sees it too"
    assert not llm_client.provider_config_problems(["ollama:qwen2.5-coder:7b"]), "a local model needs no Groq key"


def test_a_model_prefix_picks_the_provider_and_a_bare_model_stays_on_groq(monkeypatch):
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm:8000/v1")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    assert llm_client.split_model("openai/gpt-oss-120b") == ("groq", "openai/gpt-oss-120b")
    assert llm_client.split_model("groq:openai/gpt-oss-20b") == ("groq", "openai/gpt-oss-20b")
    assert llm_client.split_model("ollama:qwen2.5-coder:7b") == ("ollama", "qwen2.5-coder:7b")
    assert llm_client.split_model("vllm:Qwen/Qwen2.5-Coder-7B-Instruct") == ("vllm", "Qwen/Qwen2.5-Coder-7B-Instruct")
    assert llm_client.split_model("qwen2.5-coder:7b") == ("groq", "qwen2.5-coder:7b"), "no provider is named 'qwen2.5-coder'"
    print("OK - provider:model routes; a bare model, or an unconfigured prefix, is a Groq model")


def test_each_provider_gets_its_own_client_and_the_bare_model_name():
    groq_fake, ollama_fake = FakeGroq(), FakeGroq()
    built: list[str] = []

    def provider_clients(provider: str):
        built.append(provider)
        return {"groq": groq_fake, "ollama": ollama_fake}[provider]

    client = ReviewLlmClient(provider_clients, 3, threading.BoundedSemaphore(3), sleep=lambda _s: None)
    answers = [client.chat.completions.create(model=model, messages=[]).model
               for model in ("ollama:qwen2.5-coder:7b", "openai/gpt-oss-120b", "ollama:qwen2.5-coder:7b")]

    assert answers == ["qwen2.5-coder:7b", "openai/gpt-oss-120b", "qwen2.5-coder:7b"], "the provider gets its own model id"
    assert (groq_fake.calls, ollama_fake.calls) == (1, 2) and built == ["ollama", "groq"], "one client per provider, reused"
    print("OK - calls go to the model's provider without its prefix; each provider client is built once")


def test_an_openai_compatible_server_gets_its_url_key_and_no_sdk_retries(monkeypatch):
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm:8000/v1")
    monkeypatch.setenv("VLLM_API_KEY", "local-key")

    vllm = llm_client.build_provider_client("vllm")
    ollama = llm_client.build_provider_client("ollama")

    assert str(vllm.base_url).rstrip("/") == "http://vllm:8000/v1" and vllm.api_key == "local-key" and vllm.max_retries == 0
    assert str(ollama.base_url).rstrip("/") == "http://localhost:11434/v1" and ollama.api_key == "not-needed"
    print("OK - vLLM reads VLLM_BASE_URL and VLLM_API_KEY; Ollama works with no setting at all")


def test_no_call_starts_after_the_review_deadline():
    fake = FakeGroq()
    client, _ = make_client(fake)

    with llm_client.llm_deadline(time.monotonic() - 1):
        with pytest.raises(llm_client.ReviewDeadlineExceeded):
            client.chat.completions.create(model="m")
    assert fake.calls == 0, "past the deadline the provider is never called"


def test_a_retry_wait_that_would_pass_the_deadline_gives_up_with_the_provider_error():
    fake = FakeGroq([PER_MINUTE_429])
    client, waits = make_client(fake)

    with llm_client.llm_deadline(time.monotonic() + 5):  # Groq asks for 7.5s, only 5s are left
        with pytest.raises(StatusError):
            client.chat.completions.create(model="m")
    assert fake.calls == 1 and waits == [], "no sleep that would end after the deadline"


def test_the_deadline_is_per_thread_and_restored_after_the_block():
    with llm_client.llm_deadline(123.0):
        seen_in_other_thread = []
        thread = threading.Thread(target=lambda: seen_in_other_thread.append(llm_client._seconds_left()))
        thread.start()
        thread.join()
        assert llm_client._seconds_left() is not None
    assert seen_in_other_thread == [None], "another agent's thread is not limited by this one"
    assert llm_client._seconds_left() is None
