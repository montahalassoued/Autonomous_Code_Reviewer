import json
import re
import threading
import time
from types import SimpleNamespace
from typing import Callable

from eval.benchmark.call_cache import CallCache
from eval.llm_benchmark import MAX_PROVIDER_WAIT_S, is_quota_exhausted, provider_retry_delay_s


# The random nonce of the untrusted-input markers changes on every review; the cache key must not.
NONCE = re.compile(r"(<<<(?:END_)?[A-Z_]+) [0-9a-f]{16}>>>")
MAX_ATTEMPTS = 4


# Raised in --offline mode when a call is not recorded yet.
class CallNotRecorded(RuntimeError):
    pass


def request_key(request: dict, run: int) -> str:
    shape = json.dumps({key: request.get(key) for key in ("messages", "tools", "tool_choice")}, sort_keys=True, default=str)
    return CallCache.key(request["model"], request.get("temperature"), NONCE.sub(r"\1 NONCE>>>", shape), run,
                         request.get("max_completion_tokens"))


# What the agent runner reads from a Groq response, as plain JSON.
def response_to_record(response) -> dict:
    choice = response.choices[0]
    usage = getattr(response, "usage", None)
    tool_calls = getattr(choice.message, "tool_calls", None) or []
    return {
        "content": choice.message.content,
        "tool_calls": [{"id": call.id, "name": call.function.name, "arguments": call.function.arguments} for call in tool_calls],
        "finish_reason": getattr(choice, "finish_reason", None),
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
    }


def record_to_response(record: dict) -> SimpleNamespace:
    tool_calls = [SimpleNamespace(id=call["id"], function=SimpleNamespace(name=call["name"], arguments=call["arguments"]))
                  for call in record["tool_calls"]]
    message = SimpleNamespace(content=record["content"], tool_calls=tool_calls or None)
    usage = SimpleNamespace(prompt_tokens=record["prompt_tokens"], completion_tokens=record["completion_tokens"])
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=record["finish_reason"])], usage=usage)


# A Groq client for the review graph: recorded answers replay for free, new ones are called once and saved.
# A per-minute 429 is waited out so the eval measures quality, not the rate limit; a spent daily quota is re-raised.
class RecordedGroq:

    def __init__(self, cache: CallCache, run: int, live_client: Callable[[], object] | None) -> None:
        self.cache, self.run, self._live_client, self._live = cache, run, live_client, None
        self._lock = threading.Lock()  # the three agents call in parallel
        self.recorded_calls = self.live_calls = 0
        self.exhausted_models: set[str] = set()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        key = request_key(request, self.run)
        with self._lock:
            record = self.cache.get(key)
            if record is not None:
                self.recorded_calls += 1
                return record_to_response(record)
        if self._live_client is None:
            raise CallNotRecorded(f"{request['model']}: this call is not recorded yet (run without --offline)")
        started = time.monotonic()
        response = self._call_live(request)
        with self._lock:
            self.live_calls += 1
            self.cache.put(key, response_to_record(response))
        print(f"  call {self.live_calls} done: {request['model']}, {time.monotonic() - started:.0f}s", flush=True)
        return response

    def _call_live(self, request: dict):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with self._lock:
                    self._live = self._live or self._live_client()
                return self._live.chat.completions.create(**request)
            except Exception as exc:
                if is_quota_exhausted(exc):
                    self.exhausted_models.add(request["model"])
                    raise
                status = getattr(exc, "status_code", None)
                if status not in (429, 500, 502, 503, 504) or attempt == MAX_ATTEMPTS:
                    raise
                wait_s = min((provider_retry_delay_s(str(exc)) or 2.0 * attempt) + 1.0, MAX_PROVIDER_WAIT_S)
                print(f"  {request['model']} answered {status}: waiting {wait_s:.0f}s (attempt {attempt}/{MAX_ATTEMPTS})", flush=True)
                time.sleep(wait_s)
