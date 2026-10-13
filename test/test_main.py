"""
Unit test for app/main.py

No Azure, ingestion, or LLM calls: with JOBS_BACKEND=memory the lifespan
startup only builds the in-memory queue and dedupe store. This test
verifies main.py's own wiring (routes mounted, health is immediately ok,
queue and dedupe are on app.state, nothing is ingested). The worker's
startup work is covered by test/test_worker.py.
"""

import os

# Force (not setdefault): other tests load the real .env, which could select Azure.
os.environ["JOBS_BACKEND"] = "memory"
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-secret"

from fastapi.testclient import TestClient

from app.jobs.dedupe import InMemoryDedupeStore
from app.jobs.queue import InMemoryJobQueue


def test_health_reports_ok_without_ingestion():
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert not hasattr(app.state, "compiled_graph"), "The API must not build the review graph anymore"
    print("OK - /health is ok immediately and the API ingests nothing")


def test_startup_creates_queue_and_dedupe_on_app_state():
    from app.main import app

    with TestClient(app):
        assert isinstance(app.state.job_queue, InMemoryJobQueue)
        assert isinstance(app.state.dedupe, InMemoryDedupeStore)
    print("OK - startup stores the job queue and dedupe store on app.state")


def test_webhook_router_is_mounted():
    from app.main import app

    with TestClient(app) as client:
        # missing signature -> 401, not 404 - proves the route exists
        response = client.post("/webhook/github", content=b"{}")

    assert response.status_code == 401
    print("OK - /webhook/github route is mounted and reachable")


if __name__ == "__main__":
    print("Run under pytest.")
