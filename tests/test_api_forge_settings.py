"""Tests for Forge settings API endpoints."""

import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import Database


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


def test_get_forge_settings(client):
    c, _ = client
    resp = c.get("/api/forge/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "forge.oracle_model" in data
    assert "forge.autonomy_level" in data


def test_put_forge_settings(client):
    c, db = client
    resp = c.put(
        "/api/forge/settings",
        json={
            "forge.oracle_budget": "30",
            "forge.autonomy_level": "advisor",
        },
    )
    assert resp.status_code == 200
    assert db.get_setting("forge.oracle_budget") == "30"
    assert db.get_setting("forge.autonomy_level") == "advisor"


def test_put_forge_settings_rejects_unknown_keys(client):
    c, _ = client
    resp = c.put("/api/forge/settings", json={"forge.unknown_key": "value"})
    assert resp.status_code == 400
