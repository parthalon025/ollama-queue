"""API tests for dynamic backend management endpoints (POST/DELETE/PUT/GET /api/backends/*)."""

import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

import ollama_queue.api as _api
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


# ── Helper ────────────────────────────────────────────────────────────────────


def _mock_tags_response(model_names=None):
    """Build a mock httpx response for /api/tags."""
    model_names = model_names or ["llama3:8b", "qwen2.5:7b"]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": [{"name": n} for n in model_names]}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ── POST /api/backends ────────────────────────────────────────────────────────


def test_add_backend_success(client, db):
    """POST /api/backends with a reachable URL persists and returns model_count."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_tags_response(["llama3:8b", "qwen2.5:7b"]))

    with patch("ollama_queue.api.backends.httpx.AsyncClient", return_value=mock_client):
        resp = client.post("/api/backends", json={"url": "http://testhost:11434", "weight": 1.5})

    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "http://testhost:11434"
    assert data["weight"] == 1.5
    assert data["healthy"] is True
    assert data["model_count"] == 2

    # Verify persisted in DB
    row = db.get_backend("http://testhost:11434")
    assert row is not None
    assert row["weight"] == 1.5


def test_add_backend_duplicate(client, db):
    """POST /api/backends for an already-registered URL returns 409."""
    db.add_backend("http://testhost:11434", weight=1.0)
    resp = client.post("/api/backends", json={"url": "http://testhost:11434", "weight": 2.0})
    assert resp.status_code == 409
    assert "already registered" in resp.json()["detail"]


def test_add_backend_connectivity_fail(client):
    """POST /api/backends returns 502 when the URL is unreachable (ConnectError)."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    with patch("ollama_queue.api.backends.httpx.AsyncClient", return_value=mock_client):
        resp = client.post("/api/backends", json={"url": "http://unreachable:11434"})

    assert resp.status_code == 502
    assert "connectivity test failed" in resp.json()["detail"]


def test_add_backend_invalid_url(client):
    """POST /api/backends with a non-HTTP URL returns 400."""
    resp = client.post("/api/backends", json={"url": "ftp://invalid:11434"})
    assert resp.status_code == 400
    assert "http://" in resp.json()["detail"]


def test_add_backend_invalid_weight_too_low(client):
    """POST /api/backends with weight < 0.1 returns 400."""
    resp = client.post("/api/backends", json={"url": "http://host:11434", "weight": 0.05})
    assert resp.status_code == 400
    assert "weight" in resp.json()["detail"]


def test_add_backend_invalid_weight_too_high(client):
    """POST /api/backends with weight > 10.0 returns 400."""
    resp = client.post("/api/backends", json={"url": "http://host:11434", "weight": 11.0})
    assert resp.status_code == 400
    assert "weight" in resp.json()["detail"]


# ── DELETE /api/backends/{url} ────────────────────────────────────────────────


def test_remove_backend_success(client, db):
    """DELETE /api/backends/{url} removes the backend and returns the URL."""
    db.add_backend("http://testhost:11434", weight=1.0)

    resp = client.delete("/api/backends/http://testhost:11434")
    assert resp.status_code == 200
    assert resp.json()["removed"] == "http://testhost:11434"

    # Verify removed from DB
    assert db.get_backend("http://testhost:11434") is None


def test_remove_backend_not_found(client):
    """DELETE /api/backends/{url} for an unknown URL returns 404."""
    resp = client.delete("/api/backends/http://missing:11434")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


# ── PUT /api/backends/{url}/weight ───────────────────────────────────────────


def test_update_weight_success(client, db):
    """PUT /api/backends/{url}/weight updates the weight and returns it."""
    db.add_backend("http://testhost:11434", weight=1.0)

    resp = client.put("/api/backends/http://testhost:11434/weight", params={"weight": 4.0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "http://testhost:11434"
    assert data["weight"] == 4.0

    # Verify persisted
    row = db.get_backend("http://testhost:11434")
    assert row["weight"] == 4.0


def test_update_weight_not_found(client):
    """PUT /api/backends/{url}/weight for an unknown URL returns 404."""
    resp = client.put("/api/backends/http://missing:11434/weight", params={"weight": 2.0})
    assert resp.status_code == 404


def test_update_weight_invalid_too_low(client, db):
    """PUT /api/backends/{url}/weight with weight < 0.1 returns 400."""
    db.add_backend("http://testhost:11434", weight=1.0)
    resp = client.put("/api/backends/http://testhost:11434/weight", params={"weight": 0.0})
    assert resp.status_code == 400


def test_update_weight_invalid_too_high(client, db):
    """PUT /api/backends/{url}/weight with weight > 10.0 returns 400."""
    db.add_backend("http://testhost:11434", weight=1.0)
    resp = client.put("/api/backends/http://testhost:11434/weight", params={"weight": 15.0})
    assert resp.status_code == 400


# ── GET /api/backends/{url}/test ─────────────────────────────────────────────


def test_test_backend_healthy(client):
    """GET /api/backends/{url}/test returns healthy=True when backend responds."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_tags_response(["llama3:8b"]))

    with patch("ollama_queue.api.backends.httpx.AsyncClient", return_value=mock_client):
        resp = client.get("/api/backends/http://testhost:11434/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["healthy"] is True
    assert data["model_count"] == 1
    assert "latency_ms" in data
    assert data["url"] == "http://testhost:11434"


def test_test_backend_unhealthy(client):
    """GET /api/backends/{url}/test returns healthy=False on connection failure."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("timed out"))

    with patch("ollama_queue.api.backends.httpx.AsyncClient", return_value=mock_client):
        resp = client.get("/api/backends/http://unreachable:11434/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["healthy"] is False
    assert data["model_count"] == 0
    assert "error" in data
    assert "latency_ms" in data


def test_test_backend_never_raises(client):
    """GET /api/backends/{url}/test always returns 200, never raises."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=RuntimeError("unexpected failure"))

    with patch("ollama_queue.api.backends.httpx.AsyncClient", return_value=mock_client):
        resp = client.get("/api/backends/http://testhost:11434/test")

    assert resp.status_code == 200
    assert resp.json()["healthy"] is False


# ── Gap 1: BACKENDS list updated after add/remove ────────────────────────────


def test_add_backend_updates_backends_list(client, db):
    """POST /api/backends adds the URL to _router.BACKENDS for routing."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_tags_response(["llama3:8b"]))

    with patch("ollama_queue.api.backends.httpx.AsyncClient", return_value=mock_client):
        resp = client.post("/api/backends", json={"url": "http://newhost:11434"})

    assert resp.status_code == 200
    assert "http://newhost:11434" in _router.BACKENDS


def test_remove_backend_updates_backends_list(client, db):
    """DELETE /api/backends/{url} removes the URL from _router.BACKENDS."""
    db.add_backend("http://newhost:11434", weight=1.0)
    _router.refresh_backends_from_db()
    assert "http://newhost:11434" in _router.BACKENDS

    resp = client.delete("/api/backends/http://newhost:11434")

    assert resp.status_code == 200
    assert "http://newhost:11434" not in _router.BACKENDS


# ── Gap 2: DB=None returns 503 ────────────────────────────────────────────────


def test_add_backend_returns_503_when_db_unavailable(client):
    """POST /api/backends returns 503 when database is None."""
    with patch.object(_api, "db", None):
        resp = client.post("/api/backends", json={"url": "http://host:11434"})
    assert resp.status_code == 503
    assert "database not available" in resp.json()["detail"]


def test_remove_backend_returns_503_when_db_unavailable(client):
    """DELETE /api/backends/{url} returns 503 when database is None."""
    with patch.object(_api, "db", None):
        resp = client.delete("/api/backends/http://host:11434")
    assert resp.status_code == 503
    assert "database not available" in resp.json()["detail"]


def test_update_weight_returns_503_when_db_unavailable(client):
    """PUT /api/backends/{url}/weight returns 503 when database is None."""
    with patch.object(_api, "db", None):
        resp = client.put("/api/backends/http://host:11434/weight", params={"weight": 2.0})
    assert resp.status_code == 503
    assert "database not available" in resp.json()["detail"]


# ── Gap 3: URL-encoded path parameter round-trip ─────────────────────────────


def test_update_weight_url_encoded_path(client, db):
    """PUT weight route correctly unquotes URL-encoded backend URL in path."""
    db.add_backend("http://testhost:11434", weight=1.0)

    encoded = urllib.parse.quote("http://testhost:11434", safe="")
    resp = client.put(f"/api/backends/{encoded}/weight", params={"weight": 3.5})

    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "http://testhost:11434"
    assert data["weight"] == 3.5


# ── Gap 4: NaN / Infinity weight rejection ────────────────────────────────────


def test_update_weight_rejects_nan(client, db):
    """PUT weight returns non-200 for NaN — bounds check catches it."""
    db.add_backend("http://testhost:11434", weight=1.0)
    resp = client.put("/api/backends/http://testhost:11434/weight", params={"weight": "nan"})
    # 400 from our bounds check or 422 from FastAPI validation are both acceptable
    assert resp.status_code in (400, 422)


def test_update_weight_rejects_infinity(client, db):
    """PUT weight returns non-200 for infinity — 0.1 <= inf but inf <= 10.0 is False."""
    db.add_backend("http://testhost:11434", weight=1.0)
    resp = client.put("/api/backends/http://testhost:11434/weight", params={"weight": "inf"})
    assert resp.status_code in (400, 422)
