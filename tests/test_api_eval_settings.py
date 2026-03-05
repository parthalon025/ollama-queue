"""Tests for eval settings and datasource API endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ollama_queue.api import create_app
from ollama_queue.db import EVAL_SETTINGS_DEFAULTS, Database


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app)


@pytest.fixture
def client_and_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


# --- Eval Settings ---


def test_get_eval_settings_returns_all_eval_keys(client):
    """GET /api/eval/settings should return all 12 eval.* keys."""
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    data = resp.json()
    # Verify all expected keys are present
    expected_keys = set(EVAL_SETTINGS_DEFAULTS.keys())
    assert expected_keys.issubset(set(data))
    # All returned keys must start with eval.
    assert all(k.startswith("eval.") for k in data)


def test_get_eval_settings_count_is_12(client):
    """GET /api/eval/settings should return exactly 12 keys."""
    resp = client.get("/api/eval/settings")
    assert len(resp.json()) == len(EVAL_SETTINGS_DEFAULTS)


def test_put_eval_settings_updates_values(client):
    """PUT /api/eval/settings should update and return new values."""
    resp = client.put("/api/eval/settings", json={"eval.per_cluster": 6})
    assert resp.status_code == 200
    # Returned dict should reflect the update
    data = resp.json()
    assert data["eval.per_cluster"] == 6
    # GET should also reflect it
    get_resp = client.get("/api/eval/settings")
    assert get_resp.json()["eval.per_cluster"] == 6


def test_put_eval_settings_partial_update(client):
    """PUT with only one key should not clobber others."""
    # Set judge_backend first
    client.put("/api/eval/settings", json={"eval.judge_backend": "openai"})
    # Update per_cluster only
    client.put("/api/eval/settings", json={"eval.per_cluster": 8})
    settings = client.get("/api/eval/settings").json()
    assert settings["eval.judge_backend"] == "openai"
    assert settings["eval.per_cluster"] == 8


def test_put_eval_settings_rejects_invalid_judge_backend(client):
    """PUT with invalid judge_backend should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.judge_backend": "anthropic"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("judge_backend" in str(err) for err in (detail if isinstance(detail, list) else [detail]))


def test_put_eval_settings_rejects_out_of_range_per_cluster(client):
    """PUT with per_cluster outside 1-20 should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.per_cluster": 99})
    assert resp.status_code == 422


def test_put_eval_settings_rejects_per_cluster_zero(client):
    """PUT with per_cluster = 0 should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.per_cluster": 0})
    assert resp.status_code == 422


def test_put_eval_settings_rejects_invalid_data_source_url(client):
    """PUT with a non-HTTP data_source_url should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.data_source_url": "ftp://wrong"})
    assert resp.status_code == 422


def test_put_eval_settings_accepts_valid_https_url(client):
    """PUT with an https:// data_source_url should succeed."""
    resp = client.put("/api/eval/settings", json={"eval.data_source_url": "https://example.com"})
    assert resp.status_code == 200


def test_put_eval_settings_rejects_out_of_range_judge_temperature(client):
    """PUT with judge_temperature > 2.0 should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.judge_temperature": 3.5})
    assert resp.status_code == 422


def test_put_eval_settings_rejects_out_of_range_stability_window(client):
    """PUT with stability_window outside 1-20 should return 422."""
    resp = client.put("/api/eval/settings", json={"eval.stability_window": 25})
    assert resp.status_code == 422


def test_put_eval_settings_is_all_or_nothing_on_validation_failure(client):
    """If one field is invalid, no settings should be written (all-or-nothing)."""
    # Get current per_cluster value
    original = client.get("/api/eval/settings").json()["eval.per_cluster"]

    # Attempt to update per_cluster (valid) + judge_backend (invalid) together
    resp = client.put(
        "/api/eval/settings",
        json={
            "eval.per_cluster": 15,
            "eval.judge_backend": "invalid-backend",
        },
    )
    assert resp.status_code == 422

    # per_cluster must NOT have been updated
    after = client.get("/api/eval/settings").json()["eval.per_cluster"]
    assert after == original


def test_put_eval_settings_accepts_valid_batch(client):
    """PUT with multiple valid values should update all and return 200."""
    resp = client.put(
        "/api/eval/settings",
        json={
            "eval.judge_backend": "ollama",
            "eval.per_cluster": 5,
            "eval.f1_threshold": 0.8,
            "eval.stability_window": 5,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["eval.judge_backend"] == "ollama"
    assert data["eval.per_cluster"] == 5
    assert data["eval.f1_threshold"] == 0.8


# --- Datasource test ---


def test_datasource_test_returns_ok_when_reachable(client):
    """GET /api/eval/datasource/test should return ok=True when datasource responds 200."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True, "item_count": 763, "cluster_count": 12}

    with patch("httpx.get", return_value=mock_response):
        resp = client.get("/api/eval/datasource/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["item_count"] == 763
    assert data["cluster_count"] == 12
    assert isinstance(data["response_ms"], int)
    assert data["error"] is None


def test_datasource_test_returns_ok_false_on_connection_error(client):
    """GET /api/eval/datasource/test should return ok=False on connection refused."""
    with patch("httpx.get", side_effect=Exception("connection refused")):
        resp = client.get("/api/eval/datasource/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "connection refused" in data["error"]
    assert isinstance(data["response_ms"], int)


def test_datasource_test_returns_ok_false_on_http_error(client):
    """GET /api/eval/datasource/test should return ok=False on non-200 status."""
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.json.return_value = {}

    with patch("httpx.get", return_value=mock_response):
        resp = client.get("/api/eval/datasource/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "503" in data["error"]


def test_datasource_test_includes_response_ms(client):
    """response_ms should always be present and be a non-negative int."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True, "item_count": 10, "cluster_count": 2}

    with patch("httpx.get", return_value=mock_response):
        resp = client.get("/api/eval/datasource/test")

    data = resp.json()
    assert "response_ms" in data
    assert data["response_ms"] >= 0


# --- Eval Trends (basic sanity) ---


def test_eval_trends_returns_expected_shape_with_no_runs(client):
    """GET /api/eval/trends should return valid structure even with no completed runs."""
    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    data = resp.json()
    assert "variants" in data
    assert "item_sets_differ" in data
    assert isinstance(data["variants"], dict)
    assert data["item_sets_differ"] is False
