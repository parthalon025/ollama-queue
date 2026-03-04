"""Tests for the /api/embed proxy endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

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
    app = create_app(db)
    return TestClient(app)


def _make_mock_client(embed_response: dict):
    """Build a patched httpx.AsyncClient that returns embed_response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = embed_response
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


def test_embed_proxy_returns_ollama_response(client):
    """POST /api/embed forwards to Ollama and returns embeddings."""
    embed_response = {
        "model": "nomic-embed-text",
        "embeddings": [[0.1, 0.2, 0.3]],
    }

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_mock_client(embed_response)
        resp = client.post(
            "/api/embed",
            json={"model": "nomic-embed-text", "input": "hello world"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "embeddings" in data
    assert data["embeddings"] == [[0.1, 0.2, 0.3]]


def test_embed_proxy_array_input(client):
    """POST /api/embed supports array input."""
    embed_response = {
        "model": "nomic-embed-text",
        "embeddings": [[0.1, 0.2], [0.3, 0.4]],
    }

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_mock_client(embed_response)
        resp = client.post(
            "/api/embed",
            json={"model": "nomic-embed-text", "input": ["text one", "text two"]},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["embeddings"]) == 2


def test_embed_proxy_rejects_when_paused(client):
    """Returns 503 when daemon is manually paused."""
    client.post("/api/daemon/pause")
    resp = client.post(
        "/api/embed",
        json={"model": "nomic-embed-text", "input": "hello"},
    )
    assert resp.status_code == 503


def test_embed_proxy_releases_on_error(client, db):
    """Daemon state released back to idle even when Ollama errors."""
    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/embed",
            json={"model": "nomic-embed-text", "input": "hello"},
        )

    assert resp.status_code == 502
    state = db.get_daemon_state()
    assert state["state"] == "idle"
    assert state["current_job_id"] is None


def test_embed_proxy_logs_job(client, db):
    """Proxy embed request is logged in the jobs table."""
    embed_response = {"model": "nomic-embed-text", "embeddings": [[0.5, 0.6]]}

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_mock_client(embed_response)
        resp = client.post(
            "/api/embed",
            json={"model": "nomic-embed-text", "input": "log this"},
        )

    assert resp.status_code == 200
    conn = db._connect()
    row = conn.execute("SELECT * FROM jobs WHERE command = 'proxy:/api/embed' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["model"] == "nomic-embed-text"
    assert row["status"] == "completed"


def test_embed_proxy_extracts_priority_fields(client, db):
    """_priority, _source, _timeout are extracted and not forwarded to Ollama."""
    embed_response = {"model": "nomic-embed-text", "embeddings": [[0.1]]}

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_client = _make_mock_client(embed_response)
        mock_cls.return_value = mock_client

        client.post(
            "/api/embed",
            json={
                "model": "nomic-embed-text",
                "input": "test",
                "_priority": 2,
                "_source": "aria",
                "_timeout": 60,
            },
        )

    # Confirm private fields were stripped from the forwarded body
    called_kwargs = mock_client.post.call_args[1]
    forwarded = called_kwargs.get("json", {})
    assert "_priority" not in forwarded
    assert "_source" not in forwarded
    assert "_timeout" not in forwarded

    # Confirm source was recorded in the DB
    conn = db._connect()
    row = conn.execute("SELECT * FROM jobs WHERE command = 'proxy:/api/embed' ORDER BY id DESC LIMIT 1").fetchone()
    assert row["source"] == "aria"
    assert row["priority"] == 2


def test_embed_proxy_timeout_when_busy(client, db):
    """Returns 504 if queue slot is never available within timeout."""
    import time

    job_id = db.submit_job("echo busy", model="test", priority=5, timeout=60, source="test")
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (time.time(), job_id))
        conn.commit()
    db.update_daemon_state(state="running", current_job_id=job_id)

    with patch("ollama_queue.api.PROXY_WAIT_TIMEOUT", 1), patch("ollama_queue.api.PROXY_POLL_INTERVAL", 0.1):
        resp = client.post(
            "/api/embed",
            json={"model": "nomic-embed-text", "input": "hello"},
        )

    assert resp.status_code == 504


def test_embed_proxy_forwards_to_correct_url(client):
    """Proxy forwards to /api/embed on OLLAMA_URL, not /api/generate."""
    embed_response = {"model": "nomic-embed-text", "embeddings": [[0.1]]}

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_client = _make_mock_client(embed_response)
        mock_cls.return_value = mock_client

        client.post(
            "/api/embed",
            json={"model": "nomic-embed-text", "input": "test"},
        )

    called_args = mock_client.post.call_args[0]
    called_url = called_args[0] if called_args else mock_client.post.call_args[1].get("url", "")
    assert "/api/embed" in called_url
    assert "/api/generate" not in called_url


def test_embed_proxy_forces_stream_false(client):
    """Proxy forces stream=False even if caller sends stream=True (Fix 1)."""
    embed_response = {"model": "nomic-embed-text", "embeddings": [[0.1]]}

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_client = _make_mock_client(embed_response)
        mock_cls.return_value = mock_client

        client.post(
            "/api/embed",
            json={"model": "nomic-embed-text", "input": "test", "stream": True},
        )

    called_kwargs = mock_client.post.call_args[1]
    forwarded = called_kwargs.get("json", {})
    assert forwarded["stream"] is False


def test_embed_proxy_sets_embed_resource_profile_for_embed_model(client, db):
    """Jobs submitted via /api/embed with embed model get resource_profile='embed' (Fix 2)."""
    embed_response = {"model": "nomic-embed-text", "embeddings": [[0.1]]}

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_mock_client(embed_response)
        client.post(
            "/api/embed",
            json={"model": "nomic-embed-text", "input": "test"},
        )

    conn = db._connect()
    row = conn.execute("SELECT * FROM jobs WHERE command = 'proxy:/api/embed' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["resource_profile"] == "embed"


def test_embed_proxy_empty_model_defaults_embed_profile(client, db):
    """Jobs via /api/embed with no model name get resource_profile='embed' (Fix 2)."""
    embed_response = {"embeddings": [[0.1]]}

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _make_mock_client(embed_response)
        client.post(
            "/api/embed",
            json={"input": "test"},
        )

    conn = db._connect()
    row = conn.execute("SELECT * FROM jobs WHERE command = 'proxy:/api/embed' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["resource_profile"] == "embed"


def test_get_next_job_embed_affinity_via_command(db):
    """get_next_job gives embed affinity to proxy:/api/embed jobs regardless of model (Fix 3)."""
    # Submit an embed proxy job with empty model (falls through model LIKE checks)
    embed_id = db.submit_job(
        command="proxy:/api/embed",
        model="",
        priority=5,
        timeout=60,
        source="test",
    )
    # Submit a normal job with same priority submitted first (earlier submitted_at)
    # but embed job should come first due to affinity
    import time

    time.sleep(0.01)  # ensure embed was submitted after; we confirm it still wins affinity
    normal_id = db.submit_job(
        command="echo hello",
        model="qwen2.5:7b",
        priority=5,
        timeout=60,
        source="test",
    )

    # The normal job was submitted second, but the embed job with command LIKE '%/api/embed%'
    # should be dequeued first (CASE THEN 0 vs ELSE 1).
    # Note: embed_id was submitted first so it wins on submitted_at tie-breaking anyway.
    # To isolate the affinity fix, submit normal first, embed second, then verify embed wins.
    conn = db._connect()
    # Reset submitted_at so embed job appears LATER (to isolate the affinity logic)
    conn.execute("UPDATE jobs SET submitted_at = submitted_at - 10 WHERE id = ?", (normal_id,))
    conn.commit()

    next_job = db.get_next_job()
    assert next_job is not None
    assert next_job["id"] == embed_id, (
        f"Expected embed job (id={embed_id}) to win affinity over "
        f"normal job (id={normal_id}), got id={next_job['id']}"
    )
