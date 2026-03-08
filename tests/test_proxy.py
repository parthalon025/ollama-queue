"""Tests for the /api/generate proxy endpoint."""

import json
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


def test_generate_proxy_returns_ollama_response(client):
    """POST /api/generate forwards to Ollama and returns response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "Hello!", "done": True}
    mock_response.raise_for_status = MagicMock()

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": "hello",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["response"] == "Hello!"


def test_generate_proxy_rejects_when_paused(client):
    """Returns 503 when daemon is manually paused."""
    client.post("/api/daemon/pause")
    resp = client.post(
        "/api/generate",
        json={
            "model": "llama3.2:3b",
            "prompt": "hello",
        },
    )
    assert resp.status_code == 503


def test_generate_proxy_non_stream_unchanged(client):
    """stream=False (or absent) still returns a single JSON response as before."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "Hello!", "done": True}
    mock_response.raise_for_status = MagicMock()

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        resp = client.post("/api/generate", json={"model": "llama3.2:3b", "prompt": "hello"})

    assert resp.status_code == 200
    assert resp.json()["response"] == "Hello!"


def test_generate_proxy_streams_ndjson_when_stream_true(client):
    """stream=True returns chunked NDJSON, not a single JSON blob."""
    chunks = [
        json.dumps({"response": "He", "done": False}).encode() + b"\n",
        json.dumps({"response": "llo", "done": True, "eval_count": 5}).encode() + b"\n",
    ]

    async def fake_aiter_raw():
        for chunk in chunks:
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "llama3.2:3b", "prompt": "hello", "stream": True},
        )

    assert resp.status_code == 200
    lines = [ln for ln in resp.content.split(b"\n") if ln.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["done"] is False
    assert json.loads(lines[1])["done"] is True


def test_generate_proxy_buffers_misaligned_chunks(client):
    """aiter_raw chunks that split across JSON lines are reassembled correctly."""
    full = json.dumps({"response": "ok", "done": True}).encode() + b"\n"
    part1, part2 = full[:10], full[10:]

    async def fake_aiter_raw():
        yield part1
        yield part2

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "llama3.2:3b", "prompt": "hi", "stream": True},
        )

    lines = [ln for ln in resp.content.split(b"\n") if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["done"] is True


def test_generate_proxy_releases_on_error(client, db):
    """State released back to idle even if Ollama errors."""
    with patch("ollama_queue.api.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        mock_client_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={
                "model": "test",
                "prompt": "hello",
            },
        )

    assert resp.status_code == 502
    state = db.get_daemon_state()
    assert state["state"] == "idle"
    assert state["current_job_id"] is None


def test_generate_proxy_logs_job(client, db):
    """Proxy request is logged in the jobs table with source='proxy'."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "ok", "done": True}
    mock_response.raise_for_status = MagicMock()

    with patch("ollama_queue.api.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={
                "model": "llama3.2:3b",
                "prompt": "hello",
            },
        )

    assert resp.status_code == 200
    # Check job was logged
    conn = db._connect()
    row = conn.execute("SELECT * FROM jobs WHERE source = 'proxy' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["source"] == "proxy"
    assert row["model"] == "llama3.2:3b"
    assert row["status"] == "completed"


def test_generate_proxy_timeout_when_busy(client, db):
    """Returns 504 if daemon never goes idle within timeout."""
    # Simulate a busy queue: submit a job, mark it running in DB, and update daemon_state.
    import time

    job_id = db.submit_job("echo busy", model="test", priority=5, timeout=60, source="test")
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (time.time(), job_id))
        conn.commit()
    db.update_daemon_state(state="running", current_job_id=job_id)

    with patch("ollama_queue.api.PROXY_WAIT_TIMEOUT", 1), patch("ollama_queue.api.PROXY_POLL_INTERVAL", 0.1):
        resp = client.post(
            "/api/generate",
            json={
                "model": "test",
                "prompt": "hello",
            },
        )

    assert resp.status_code == 504


def test_try_claim_for_proxy(db):
    """try_claim_for_proxy succeeds when idle, fails when running."""
    assert db.try_claim_for_proxy() is True
    state = db.get_daemon_state()
    assert state["state"] == "running"
    assert state["current_job_id"] == -1
    # Second claim should fail (already running)
    assert db.try_claim_for_proxy() is False


def test_release_proxy_claim(db):
    """release_proxy_claim resets state to idle."""
    db.try_claim_for_proxy()
    db.release_proxy_claim()
    state = db.get_daemon_state()
    assert state["state"] == "idle"
    assert state["current_job_id"] is None
