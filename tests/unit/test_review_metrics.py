import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.jobs.models import ReviewJob
from app.jobs.queue import InMemoryJobQueue
from app.worker import process_one
from review.graph import isolate_agent_failure
from review.llm_client import ReviewLlmClient
from review.review_metrics import record_posted_review
from tests.unit.test_webhook import PR_OPENED_PAYLOAD, make_app, post_event


# The registry is shared by the whole test run, so every test reads a counter before and after.
def sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"Error code: {status_code}")
        self.status_code = status_code


# Fails with the queued errors, then answers with token usage.
class FakeProvider:

    def __init__(self, errors=()) -> None:
        self.errors = list(errors)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        if self.errors:
            raise self.errors.pop(0)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=5000, completion_tokens=300))


def gateway(provider: FakeProvider, max_attempts: int = 3) -> ReviewLlmClient:
    return ReviewLlmClient(lambda _name: provider, max_attempts, threading.BoundedSemaphore(3), sleep=lambda _s: None)


def test_an_llm_call_counts_its_retries_tokens_and_duration_by_provider_and_model():
    labels = {"provider": "ollama", "model": "metrics-test-a"}
    before_ok = sample("reviewer_llm_calls_total", **labels, outcome="ok")
    before_retries = sample("reviewer_llm_retries_total", **labels, reason="503")
    before_in = sample("reviewer_llm_tokens_total", **labels, direction="in")
    before_timed = sample("reviewer_llm_call_duration_seconds_count", **labels)

    gateway(FakeProvider([StatusError(503)])).chat.completions.create(model="ollama:metrics-test-a", messages=[])

    assert sample("reviewer_llm_calls_total", **labels, outcome="ok") == before_ok + 1
    assert sample("reviewer_llm_retries_total", **labels, reason="503") == before_retries + 1
    assert sample("reviewer_llm_tokens_total", **labels, direction="in") == before_in + 5000
    assert sample("reviewer_llm_call_duration_seconds_count", **labels) == before_timed + 1
    print("OK - one retried call: 1 ok call, 1 retry (503), 5000 tokens in, 1 timed call")


def test_an_llm_call_that_gives_up_counts_as_one_error():
    labels = {"provider": "ollama", "model": "metrics-test-b"}
    before = sample("reviewer_llm_calls_total", **labels, outcome="error")

    try:
        gateway(FakeProvider([StatusError(400)])).chat.completions.create(model="ollama:metrics-test-b", messages=[])
    except StatusError:
        pass

    assert sample("reviewer_llm_calls_total", **labels, outcome="error") == before + 1
    print("OK - a client error is never retried and counts once as an error")


def test_a_failed_agent_and_a_posted_review_are_counted():
    before_failures = sample("reviewer_agent_failures_total", agent="logic")
    before_partial = sample("reviewer_reviews_total", outcome="partial")
    before_critical = sample("reviewer_posted_findings_total", agent="security", severity="CRITICAL")

    def broken(_state):
        raise RuntimeError("Groq 503")

    isolate_agent_failure("logic", broken)({})
    record_posted_review({"failed_agents": ["logic"], "posted_findings": [{"agent": "security", "severity": "CRITICAL"}]}, 42.0)

    assert sample("reviewer_agent_failures_total", agent="logic") == before_failures + 1
    assert sample("reviewer_reviews_total", outcome="partial") == before_partial + 1
    assert sample("reviewer_posted_findings_total", agent="security", severity="CRITICAL") == before_critical + 1
    print("OK - a review posted without logic counts as partial, with its findings by agent and severity")


def test_a_failed_review_counts_as_failed_then_dead_lettered():
    clock = SimpleNamespace(now=0.0)
    queue = InMemoryJobQueue(clock=lambda: clock.now)
    queue.enqueue(ReviewJob(repo_full_name="owner/repo", pr_number=7, head_sha="abc", delivery_id="d1"))
    before_failed = sample("reviewer_reviews_total", outcome="failed")
    before_dead = sample("reviewer_reviews_total", outcome="dead_lettered")

    def failing_review(_job):
        raise RuntimeError("review crashed")

    for _ in range(2):
        process_one(queue, failing_review, max_attempts=2, visibility_timeout_s=60)
        clock.now += 61

    assert sample("reviewer_reviews_total", outcome="failed") == before_failed + 1, "the first attempt will be retried"
    assert sample("reviewer_reviews_total", outcome="dead_lettered") == before_dead + 1
    print("OK - an attempt that will be retried is 'failed'; the last one is 'dead_lettered'")


def test_webhook_deliveries_are_counted_by_result_and_served_on_metrics():
    client = TestClient(make_app())
    before_queued = sample("reviewer_webhook_deliveries_total", result="queued")
    before_ignored = sample("reviewer_webhook_deliveries_total", result="ignored")
    before_refused = sample("reviewer_webhook_deliveries_total", result="http_401")

    post_event(client, PR_OPENED_PAYLOAD, delivery="metrics-1")
    post_event(client, PR_OPENED_PAYLOAD, event="issues", delivery="metrics-2")
    client.post("/webhook/github", content=b"{}", headers={"X-GitHub-Event": "pull_request"})

    assert sample("reviewer_webhook_deliveries_total", result="queued") == before_queued + 1
    assert sample("reviewer_webhook_deliveries_total", result="ignored") == before_ignored + 1
    assert sample("reviewer_webhook_deliveries_total", result="http_401") == before_refused + 1
    print("OK - queued, ignored and unsigned deliveries are counted apart")


def test_the_api_serves_prometheus_metrics():
    from tests.unit import test_main  # noqa: F401 - sets the env the real app needs
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/plain")
    assert "reviewer_webhook_deliveries_total" in response.text
    print("OK - /metrics answers in the Prometheus text format")
