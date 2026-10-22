import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Callable, Iterator

import groq
from groq import Groq


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


# Per-minute 429s, 5xx, dropped connections and timeouts pass on their own; a spent quota or an oversized request never does.
def is_retryable_llm_error(exc: BaseException) -> bool:
    if isinstance(exc, groq.APIConnectionError):  # APITimeoutError is a subclass
        return True
    too_large = "request too large" in str(exc).lower()
    return getattr(exc, "status_code", None) in RETRYABLE_STATUSES and not too_large and not is_quota_exhausted(exc)


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


# One process-wide pool of call slots, sized from REVIEW_LLM_MAX_CONCURRENCY on first use.
def shared_call_slots() -> threading.BoundedSemaphore:
    global _call_slots
    with _call_slots_lock:
        if _call_slots is None:
            size = max(1, int(_read_positive_env("REVIEW_LLM_MAX_CONCURRENCY", DEFAULT_LLM_MAX_CONCURRENCY)))
            _call_slots = threading.BoundedSemaphore(size)
        return _call_slots


# Same call shape as a Groq client (chat.completions.create), plus a concurrency limit and visible, quota-aware retries.
class ReviewLlmClient:

    def __init__(self, groq_client, max_attempts: int, call_slots: threading.BoundedSemaphore,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._groq, self._max_attempts, self._slots, self._sleep = groq_client, max_attempts, call_slots, sleep
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        for attempt in range(1, self._max_attempts + 1):
            seconds_left = _seconds_left()
            if seconds_left is not None and seconds_left <= 0:
                raise ReviewDeadlineExceeded(f"review deadline passed before calling {request.get('model')}")
            try:
                with self._slots:
                    return self._groq.chat.completions.create(**request)
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


# Builds the review agents' client from GROQ_API_KEY; the SDK's own silent retries are off, ours log.
def build_review_llm_client() -> ReviewLlmClient:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com and set it "
            "in your .env file."
        )
    groq_client = Groq(api_key=api_key, timeout=llm_timeout_s(), max_retries=0)
    return ReviewLlmClient(groq_client, llm_max_attempts(), shared_call_slots())
