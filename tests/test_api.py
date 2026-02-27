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


class TestScheduleAPI:
    def test_list_recurring_jobs(self, client):
        r = client.get("/api/schedule")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_add_recurring_job(self, client):
        r = client.post("/api/schedule", json={
            "name": "test-job",
            "command": "echo hello",
            "interval_seconds": 3600,
            "model": "qwen2.5:7b",
            "priority": 5,
            "tag": "test",
        })
        assert r.status_code == 200
        assert r.json()["name"] == "test-job"

    def test_update_recurring_job(self, client):
        client.post("/api/schedule", json={
            "name": "j1", "command": "echo hi", "interval_seconds": 3600
        })
        r = client.put("/api/schedule/1", json={"enabled": False})
        assert r.status_code == 200

    def test_delete_recurring_job(self, client):
        client.post("/api/schedule", json={
            "name": "j1", "command": "echo hi", "interval_seconds": 3600
        })
        r = client.delete("/api/schedule/1")
        assert r.status_code == 200

    def test_trigger_rebalance(self, client):
        r = client.post("/api/schedule/rebalance")
        assert r.status_code == 200

    def test_get_schedule_events(self, client):
        r = client.get("/api/schedule/events")
        assert r.status_code == 200


class TestDLQAPI:
    def test_list_dlq_empty(self, client):
        r = client.get("/api/dlq")
        assert r.status_code == 200
        assert r.json() == []

    def test_retry_all_dlq(self, client):
        r = client.post("/api/dlq/retry-all")
        assert r.status_code == 200

    def test_clear_dlq(self, client):
        r = client.delete("/api/dlq")
        assert r.status_code == 200
