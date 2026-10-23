import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Callable, Iterator

import groq
import openai
from groq import Groq
from openai import OpenAI


logger = logging.getLogger(__name__)

DEFAULT_LLM_TIMEOUT_S = 60.0
DEFAULT_LLM_MAX_ATTEMPTS = 3
# One review's three agents call in parallel; more slots only matter if the worker runs reviews concurrently.
DEFAULT_LLM_MAX_CONCURRENCY = 3
RETRYABLE_STATUSES = (429, 500, 502, 503, 504)
FIRST_BACKOFF_S = 2.0

# A provider wait longer than this means the quota is spent for now: stop calling that model.
MAX_PROVIDER_WAIT_S = 60.0
QUOTA_EXHAUSTED = "QuotaExhausted"
DAILY_LIMIT = re.compile(r"per ?day", re.IGNORECASE)
# "try again in 14m19.6s" (Groq), "retry in 43.2s" or 'retryDelay': '43s' (Gemini).
RETRY_DELAY = re.compile(r"(?:try again|retry) in ((?:[\d.]+(?:h|ms|m|s))+)|retryDelay\W+([\d.]+)s")
DELAY_PART = re.compile(r"([\d.]+)(h|ms|m|s)")
DELAY_UNITS_S = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}

# "provider:model" routes a call; a bare model (or one whose prefix is no configured provider) goes to Groq.
GROQ_PROVIDER = "groq"
PROVIDER_PREFIX = re.compile(r"^([a-z][a-z0-9_]*):(.+)$")
# OpenAI-compatible servers that need no setting to try locally; any other prefix needs <PREFIX>_BASE_URL.
DEFAULT_BASE_URLS = {"ollama": "http://localhost:11434/v1"}

_call_slots: threading.BoundedSemaphore | None = None
_call_slots_lock = threading.Lock()
# Each agent runs in its own thread, so the review's deadline is held per thread.
_deadline = threading.local()


# Raised instead of calling or waiting past the review's deadline, so a slow provider can't outlive the job's lease.
class ReviewDeadlineExceeded(RuntimeError):
    pass


# Every LLM call made in this thread inside the block stops at `deadline_monotonic` (None: no deadline).
@contextmanager
def llm_deadline(deadline_monotonic: float | None) -> Iterator[None]:
    previous = getattr(_deadline, "at", None)
    _deadline.at = deadline_monotonic
    try:
        yield
    finally:
        _deadline.at = previous


def _seconds_left() -> float | None:
    at = getattr(_deadline, "at", None)
    return None if at is None else at - time.monotonic()


# Seconds the provider asks us to wait before retrying, if its error says.
def provider_retry_delay_s(message: str) -> float | None:
    match = RETRY_DELAY.search(message)
    if not match:
        return None
    if match.group(2):
        return float(match.group(2))
    return sum(float(amount) * DELAY_UNITS_S[unit] for amount, unit in DELAY_PART.findall(match.group(1)))


# A daily limit, or a wait too long to sit through: no retry can succeed for a while.
def is_quota_exhausted(error: str | BaseException | None) -> bool:
    if error is None:
        return False
    message = str(error)
    delay = provider_retry_delay_s(message)
    return message.startswith(QUOTA_EXHAUSTED) or bool(DAILY_LIMIT.search(message)) or (delay or 0) > MAX_PROVIDER_WAIT_S


# A request bigger than the model's per-minute token limit (413): no retry or wait can make it fit.
def is_request_too_large(error: str | BaseException | None) -> bool:
    if error is None:
        return False
    return getattr(error, "status_code", None) == 413 or "request too large" in str(error).lower() or "error code: 413" in str(error).lower()


# Per-minute 429s, 5xx, dropped connections and timeouts pass on their own; a spent quota or an oversized request never does.
def is_retryable_llm_error(exc: BaseException) -> bool:
    if isinstance(exc, (groq.APIConnectionError, openai.APIConnectionError)):  # APITimeoutError is a subclass
        return True
    return (getattr(exc, "status_code", None) in RETRYABLE_STATUSES and not is_request_too_large(exc)
            and not is_quota_exhausted(exc))


def _read_positive_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "").strip() or default)
    except ValueError:
        logger.warning("%s is not a number, using %s", name, default)
        return default
    return value if value > 0 else default


def llm_timeout_s() -> float:
    return _read_positive_env("REVIEW_LLM_TIMEOUT_S", DEFAULT_LLM_TIMEOUT_S)


def llm_max_attempts() -> int:
    return max(1, int(_read_positive_env("REVIEW_LLM_MAX_ATTEMPTS", DEFAULT_LLM_MAX_ATTEMPTS)))


def _base_url(provider: str) -> str | None:
    return os.environ.get(f"{provider.upper()}_BASE_URL", "").strip() or DEFAULT_BASE_URLS.get(provider)


# ("ollama", "qwen2.5-coder:7b") for "ollama:qwen2.5-coder:7b"; ("groq", model) for a bare Groq model like "openai/gpt-oss-120b".
def split_model(model: str) -> tuple[str, str]:
    match = PROVIDER_PREFIX.match(model)
    if match and (match.group(1) == GROQ_PROVIDER or _base_url(match.group(1))):
        return match.group(1), match.group(2)
    return GROQ_PROVIDER, model


# What is missing to call these models, as messages; the worker checks its agents' models at startup.
def provider_config_problems(models) -> list[str]:
    problems = []
    for provider in sorted({split_model(model)[0] for model in models if model}):
        if provider == GROQ_PROVIDER and not os.environ.get("GROQ_API_KEY", "").strip():
            problems.append("GROQ_API_KEY is not set, but an agent model routes to Groq. Get a free key at "
                            "https://console.groq.com and set it in your .env file.")
    return problems


# One SDK client per provider; the SDKs' own silent retries are off, ReviewLlmClient's log.
def build_provider_client(provider: str):
    if provider == GROQ_PROVIDER:
        problems = provider_config_problems([GROQ_PROVIDER + ":model"])
        if problems:
            raise RuntimeError(problems[0])
        return Groq(api_key=os.environ["GROQ_API_KEY"], timeout=llm_timeout_s(), max_retries=0)
    # Ollama and a local vLLM ignore the key, but the SDK refuses an empty one.
    api_key = os.environ.get(f"{provider.upper()}_API_KEY", "").strip() or "not-needed"
    return OpenAI(base_url=_base_url(provider), api_key=api_key, timeout=llm_timeout_s(), max_retries=0)


# One process-wide pool of call slots, sized from REVIEW_LLM_MAX_CONCURRENCY on first use.
def shared_call_slots() -> threading.BoundedSemaphore:
    global _call_slots
    with _call_slots_lock:
        if _call_slots is None:
            size = max(1, int(_read_positive_env("REVIEW_LLM_MAX_CONCURRENCY", DEFAULT_LLM_MAX_CONCURRENCY)))
            _call_slots = threading.BoundedSemaphore(size)
        return _call_slots


# The LLM gateway: the Groq call shape (chat.completions.create), routed to the model's provider, with a concurrency
# limit, visible quota-aware retries and the review deadline. Provider clients are built on first use.
class ReviewLlmClient:

    def __init__(self, provider_clients: Callable[[str], object], max_attempts: int, call_slots: threading.BoundedSemaphore,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._build_client, self._max_attempts, self._slots, self._sleep = provider_clients, max_attempts, call_slots, sleep
        self._clients: dict[str, object] = {}
        self._clients_lock = threading.Lock()  # the three agents share one client
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def client_for(self, provider: str):
        with self._clients_lock:
            if provider not in self._clients:
                self._clients[provider] = self._build_client(provider)
            return self._clients[provider]

    def _create(self, **request):
        provider, model = split_model(request["model"])
        provider_client = self.client_for(provider)
        request = {**request, "model": model}
        for attempt in range(1, self._max_attempts + 1):
            seconds_left = _seconds_left()
            if seconds_left is not None and seconds_left <= 0:
                raise ReviewDeadlineExceeded(f"review deadline passed before calling {request.get('model')}")
            try:
                with self._slots:
                    return provider_client.chat.completions.create(**request)
            except Exception as exc:
                if attempt == self._max_attempts or not is_retryable_llm_error(exc):
                    raise
                wait_s = self._retry_wait_s(exc, attempt)
                seconds_left = _seconds_left()
                if seconds_left is not None and wait_s >= seconds_left:
                    raise  # the wait alone would pass the deadline: give up with the provider's error
                logger.warning("%s failed (%s), retrying in %.1fs (attempt %d/%d)", request.get("model"),
                               getattr(exc, "status_code", None) or type(exc).__name__, wait_s, attempt, self._max_attempts)
                self._sleep(wait_s)  # outside the slot, so a waiting call doesn't block the others

    # Wait what the provider asks for (a per-minute window), else back off exponentially.
    @staticmethod
    def _retry_wait_s(exc: BaseException, attempt: int) -> float:
        delay = provider_retry_delay_s(str(exc))
        if delay is None:
            delay = FIRST_BACKOFF_S * 2 ** (attempt - 1)
        return min(delay + 1.0, MAX_PROVIDER_WAIT_S)


# The review agents' gateway; a provider's settings are read when a model first routes to it.
def build_review_llm_client() -> ReviewLlmClient:
    return ReviewLlmClient(build_provider_client, llm_max_attempts(), shared_call_slots())
