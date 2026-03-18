# tests/test_api_forge_archive.py
"""Tests for Forge archive API endpoints."""

import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import Database


@pytest.fixture
def client():
    db = Database(":memory:")
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


def test_get_forge_archive_empty(client):
    c, _ = client
    resp = c.get("/api/forge/archive")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cells"] == []
    assert data["qd_score"] == 0.0
    assert data["coverage"] == 0.0


def test_get_forge_archive_with_cells(client):
    c, db = client
    db.upsert_forge_archive_cell(
        x_bin=0,
        y_bin=0,
        x_value=0.1,
        y_value=0.2,
        variant_id="A",
        fitness=0.75,
    )
    db.upsert_forge_archive_cell(
        x_bin=1,
        y_bin=1,
        x_value=0.5,
        y_value=0.5,
        variant_id="B",
        fitness=0.85,
    )
    resp = c.get("/api/forge/archive")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["cells"]) == 2
    assert data["qd_score"] == 1.6
    assert data["coverage"] > 0


def test_get_forge_archive_heatmap(client):
    c, db = client
    db.upsert_forge_archive_cell(
        x_bin=0,
        y_bin=0,
        x_value=0.0,
        y_value=0.0,
        variant_id="A",
        fitness=0.8,
    )
    resp = c.get("/api/forge/archive/heatmap?grid_size=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["heatmap"]) == 5
    assert data["heatmap"][0][0] == 0.8


def test_get_forge_archive_cell(client):
    c, db = client
    db.upsert_forge_archive_cell(
        x_bin=3,
        y_bin=4,
        x_value=0.3,
        y_value=0.4,
        variant_id="A",
        fitness=0.65,
    )
    resp = c.get("/api/forge/archive/cell?x=3&y=4")
    assert resp.status_code == 200
    assert resp.json()["variant_id"] == "A"


def test_get_forge_archive_cell_not_found(client):
    c, _ = client
    resp = c.get("/api/forge/archive/cell?x=99&y=99")
    assert resp.status_code == 404
