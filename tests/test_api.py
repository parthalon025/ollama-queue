"""Tests for the FastAPI REST API."""

import pytest
from fastapi.testclient import TestClient

from ollama_queue.api import create_app
from ollama_queue.db import Database


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app)


def test_get_status(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "daemon" in data
    assert "queue" in data
    assert "kpis" in data


def test_get_queue_empty(client):
    resp = client.get("/api/queue")
    assert resp.status_code == 200
    assert resp.json() == []


def test_submit_job(client):
    resp = client.post("/api/queue/submit", json={
        "command": "echo test",
        "source": "test",
        "model": "qwen2.5:7b",
        "priority": 3,
        "timeout": 60,
    })
    assert resp.status_code == 200
    assert "job_id" in resp.json()


def test_get_queue_after_submit(client):
    client.post("/api/queue/submit", json={
        "command": "echo test", "source": "test", "model": "m", "priority": 5, "timeout": 60
    })
    resp = client.get("/api/queue")
    assert len(resp.json()) == 1


def test_cancel_job(client):
    resp = client.post("/api/queue/submit", json={
        "command": "echo test", "source": "test", "model": "m", "priority": 5, "timeout": 60
    })
    job_id = resp.json()["job_id"]
    resp = client.post(f"/api/queue/cancel/{job_id}")
    assert resp.status_code == 200


def test_get_settings(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "poll_interval_seconds" in data


def test_put_settings(client):
    resp = client.put("/api/settings", json={"ram_pause_pct": 90})
    assert resp.status_code == 200
    resp = client.get("/api/settings")
    assert resp.json()["ram_pause_pct"] == 90


def test_pause_resume(client):
    resp = client.post("/api/daemon/pause")
    assert resp.status_code == 200
    resp = client.get("/api/status")
    assert resp.json()["daemon"]["state"] == "paused_manual"
    resp = client.post("/api/daemon/resume")
    assert resp.status_code == 200


def test_get_history_empty(client):
    resp = client.get("/api/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_get_durations(client):
    resp = client.get("/api/durations")
    assert resp.status_code == 200


def test_get_heatmap(client):
    resp = client.get("/api/heatmap")
    assert resp.status_code == 200
