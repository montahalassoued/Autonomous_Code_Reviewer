"""
Unit test for app/main.py

No real ingestion or LLM calls: rag.ingestion.ingest_repository and
agents.graph.build_graph are monkeypatched so the lifespan startup
event can run instantly against fake data. The real ingestion pipeline
is covered separately by test/test_ingestion.py; this test only
verifies main.py's own wiring (routes mounted, health check reflects
startup state).
"""

import os

os.environ.setdefault("REPO_PATH", "/fake/repo/path")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret")

from fastapi.testclient import TestClient


def test_health_reports_ok_after_startup(monkeypatch):
    monkeypatch.setattr(
        "app.main.ingest_repository",
        lambda repo_path: ("fake_collection", ["doc"], ["id"], "fake_graph"),
    )
    monkeypatch.setattr(
        "app.main.build_graph",
        lambda collection, all_documents, all_ids, graph_index: "fake_compiled_graph",
    )

    from app.main import app

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    print("OK - /health reports 'ok' once startup ingestion has completed")


def test_webhook_router_is_mounted(monkeypatch):
    monkeypatch.setattr(
        "app.main.ingest_repository",
        lambda repo_path: ("fake_collection", ["doc"], ["id"], "fake_graph"),
    )
    monkeypatch.setattr(
        "app.main.build_graph",
        lambda collection, all_documents, all_ids, graph_index: "fake_compiled_graph",
    )

    from app.main import app

    with TestClient(app) as client:
        # missing signature -> 401, not 404 - proves the route exists
        response = client.post("/webhook/github", content=b"{}")

    assert response.status_code == 401
    print("OK - /webhook/github route is mounted and reachable")


if __name__ == "__main__":
    print("Run under pytest: this module relies on monkeypatch fixtures.")
