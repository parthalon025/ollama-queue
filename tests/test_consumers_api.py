import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ollama_queue.api import create_app
from ollama_queue.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


@pytest.fixture
def client(db):
    return TestClient(create_app(db))


def _seed_consumer(db, **overrides):
    defaults = {
        "name": "aria.service",
        "type": "systemd",
        "platform": "linux",
        "source_label": "aria",
        "detected_at": int(time.time()),
    }
    defaults.update(overrides)
    return db.upsert_consumer(defaults)


# ── List and scan ────────────────────────────────────────────────────────────


def test_list_consumers_empty(client):
    resp = client.get("/api/consumers")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_consumers_returns_rows(client, db):
    _seed_consumer(db)
    resp = client.get("/api/consumers")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["name"] == "aria.service"


def test_scan_triggers_and_returns_consumers(client):
    with patch(
        "ollama_queue.api.run_scan",
        return_value=[
            {
                "name": "aria.service",
                "type": "systemd",
                "platform": "linux",
                "source_label": "aria",
                "is_managed_job": False,
                "streaming_confirmed": False,
                "streaming_suspect": False,
            }
        ],
    ):
        resp = client.post("/api/consumers/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "aria.service"


# ── Include guards ───────────────────────────────────────────────────────────


def test_include_managed_job_returns_409(client, db):
    cid = _seed_consumer(db, is_managed_job=1)
    resp = client.post(f"/api/consumers/{cid}/include", json={"restart_policy": "deferred"})
    assert resp.status_code == 409
    assert "deadlock" in resp.json()["detail"].lower()


def test_include_streaming_confirmed_without_override_returns_422(client, db):
    cid = _seed_consumer(db, streaming_confirmed=1)
    resp = client.post(
        f"/api/consumers/{cid}/include",
        json={"restart_policy": "deferred", "force_streaming_override": False},
    )
    assert resp.status_code == 422
    assert "stream" in resp.json()["detail"].lower()


def test_include_streaming_confirmed_with_override_proceeds(client, db, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\n")
    cid = _seed_consumer(db, streaming_confirmed=1, patch_path=str(env), type="env_file")
    with patch(
        "ollama_queue.api.patch_consumer",
        return_value={"patch_applied": True, "status": "patched", "patch_type": "env_file"},
    ):
        resp = client.post(
            f"/api/consumers/{cid}/include",
            json={"restart_policy": "deferred", "force_streaming_override": True},
        )
    assert resp.status_code == 200


def test_include_system_path_without_confirm_returns_422(client, db):
    cid = _seed_consumer(db, patch_path="/etc/systemd/system/aria.service")
    resp = client.post(
        f"/api/consumers/{cid}/include",
        json={"restart_policy": "deferred", "system_confirm": False},
    )
    assert resp.status_code == 422
    assert "system" in resp.json()["detail"].lower()


def test_include_deferred_sets_pending_restart(client, db, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\n")
    cid = _seed_consumer(db, patch_path=str(env), type="env_file")
    with patch(
        "ollama_queue.api.patch_consumer",
        return_value={"patch_applied": True, "status": "pending_restart", "patch_type": "env_file"},
    ):
        resp = client.post(f"/api/consumers/{cid}/include", json={"restart_policy": "deferred"})
    assert resp.status_code == 200
    assert db.get_consumer(cid)["status"] == "pending_restart"


# ── ignore / revert / health ─────────────────────────────────────────────────


def test_ignore_sets_status(client, db):
    cid = _seed_consumer(db)
    resp = client.post(f"/api/consumers/{cid}/ignore")
    assert resp.status_code == 200
    assert db.get_consumer(cid)["status"] == "ignored"


def test_revert_calls_revert_and_resets_status(client, db):
    cid = _seed_consumer(db, status="patched", patch_applied=1)
    with patch("ollama_queue.api.revert_consumer"):
        resp = client.post(f"/api/consumers/{cid}/revert")
    assert resp.status_code == 200
    assert db.get_consumer(cid)["status"] == "discovered"


def test_health_endpoint_returns_status(client, db):
    cid = _seed_consumer(db, status="patched")
    with patch(
        "ollama_queue.api.check_health",
        return_value={
            "old_port_clear": True,
            "new_port_active": True,
            "request_seen": False,
            "status": "confirmed",
        },
    ):
        resp = client.get(f"/api/consumers/{cid}/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
