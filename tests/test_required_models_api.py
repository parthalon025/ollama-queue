"""Tests for GET /api/required-models endpoint."""

import time

import pytest
from fastapi.testclient import TestClient

import ollama_queue.api.backend_router as _router
from ollama_queue.app import create_app
from ollama_queue.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


@pytest.fixture
def client(db):
    return TestClient(create_app(db))


def test_required_models_unfiltered(client):
    """GET /api/required-models without backend_url returns all models."""
    resp = client.get("/api/required-models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) > 0
    for m in data["models"]:
        assert "name" in m
        assert "vram_mb" in m
        assert "tier" in m


def test_required_models_filtered_by_vram(client, db):
    """GET /api/required-models?backend_url= filters out models that don't fit."""
    backend_url = "http://testhost:11434"
    db.add_backend(backend_url)
    now = time.monotonic()
    _router._vram_total_cache[backend_url] = (now, 8.0)
    resp = client.get(f"/api/required-models?backend_url={backend_url}")
    assert resp.status_code == 200
    data = resp.json()
    names = [m["name"] for m in data["models"]]
    # 8GB = 8192MB * 0.95 = 7782MB threshold
    assert "nomic-embed-text" in names  # 300MB core, always included
    assert "qwen3:14b" not in names  # 9500MB standard tier, doesn't fit 8GB
    # Cleanup
    _router._vram_total_cache.pop(backend_url, None)


def test_required_models_no_vram_data_returns_core_only(client, db):
    """Backend with no VRAM data gets only core models."""
    backend_url = "http://newhost:11434"
    db.add_backend(backend_url)
    resp = client.get(f"/api/required-models?backend_url={backend_url}")
    assert resp.status_code == 200
    data = resp.json()
    for m in data["models"]:
        assert m["tier"] == "core"
