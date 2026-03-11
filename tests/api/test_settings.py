"""Tests for daemon control endpoints: pause, resume, restart."""

import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import Database


@pytest.fixture
def client_and_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


def test_daemon_pause(client_and_db):
    """POST /api/daemon/pause transitions daemon state to paused_manual."""
    client, db = client_and_db
    resp = client.post("/api/daemon/pause")
    assert resp.status_code == 200
    assert db.get_daemon_state()["state"] == "paused_manual"


def test_daemon_resume(client_and_db):
    """POST /api/daemon/resume transitions daemon state back to idle."""
    client, db = client_and_db
    # Pause first so resume has something to undo
    client.post("/api/daemon/pause")
    resp = client.post("/api/daemon/resume")
    assert resp.status_code == 200
    assert db.get_daemon_state()["state"] == "idle"


def test_daemon_restart(client_and_db):
    """POST /api/daemon/restart transitions daemon state to restarting."""
    client, db = client_and_db
    resp = client.post("/api/daemon/restart")
    assert resp.status_code == 200
    assert db.get_daemon_state()["state"] == "restarting"
