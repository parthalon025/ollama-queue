"""Tests for Forge run API endpoints."""

import json

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


def test_list_forge_runs_empty(client):
    c, _ = client
    resp = c.get("/api/forge/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_forge_run(client):
    c, _ = client
    resp = c.post(
        "/api/forge/runs",
        json={
            "data_source_url": "http://127.0.0.1:7685",
            "variant_id": "A",
            "judge_model": "qwen3:14b",
            "oracle_model": "claude-sonnet-4-20250514",
        },
    )
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_get_forge_run(client):
    c, db = client
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="m",
        oracle_model="o",
    )
    resp = c.get(f"/api/forge/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == run_id


def test_get_forge_run_not_found(client):
    c, _ = client
    resp = c.get("/api/forge/runs/9999")
    assert resp.status_code == 404


def test_cancel_forge_run(client):
    c, db = client
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="m",
        oracle_model="o",
    )
    db.update_forge_run(run_id, status="judging")
    resp = c.post(f"/api/forge/runs/{run_id}/cancel")
    assert resp.status_code == 200
    assert db.get_forge_run(run_id)["status"] == "cancelled"


def test_cancel_forge_run_terminal_returns_409(client):
    c, db = client
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="m",
        oracle_model="o",
    )
    db.update_forge_run(run_id, status="complete")
    resp = c.post(f"/api/forge/runs/{run_id}/cancel")
    assert resp.status_code == 409


def test_get_forge_run_results(client):
    c, db = client
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="m",
        oracle_model="o",
    )
    db.insert_forge_result(
        run_id=run_id,
        source_item_id="1",
        target_item_id="2",
        embedding_similarity=0.8,
        quartile="q1_likely",
        judge_score=4,
    )
    resp = c.get(f"/api/forge/runs/{run_id}/results")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_forge_calibration(client):
    c, db = client
    run_id = db.create_forge_run(
        data_source_url="http://127.0.0.1:7685",
        variant_id="A",
        judge_model="m",
        oracle_model="o",
    )
    db.update_forge_run(run_id, oracle_json=json.dumps({"kappa": 0.75, "sample_size": 10}))
    resp = c.get(f"/api/forge/runs/{run_id}/calibration")
    assert resp.status_code == 200
    data = resp.json()
    assert data["oracle"]["kappa"] == 0.75
