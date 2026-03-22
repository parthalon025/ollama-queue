"""Tests for the /api/generate proxy endpoint."""

import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
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

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
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

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
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

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
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

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
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
    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
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

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
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

    with (
        patch("ollama_queue.api.proxy.PROXY_WAIT_TIMEOUT", 1),
        patch("ollama_queue.api.proxy.PROXY_POLL_INTERVAL", 0.1),
    ):
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


def test_try_claim_for_proxy_reads_max_concurrent_jobs_inline(db):
    """try_claim_for_proxy respects max_concurrent_jobs without reentrant get_setting."""
    # Set max_concurrent_jobs = 1 (default) — claim should work then block
    db.set_setting("max_concurrent_jobs", 1)
    assert db.try_claim_for_proxy() is True
    assert db.try_claim_for_proxy() is False
    db.release_proxy_claim()
    # Now set to 0 (disallow all) — claim should always fail
    db.set_setting("max_concurrent_jobs", 0)
    # running count is 0 but 0 >= 0 is True so claim should fail
    assert db.try_claim_for_proxy() is False


def test_release_proxy_claim(db):
    """release_proxy_claim resets state to idle."""
    db.try_claim_for_proxy()
    db.release_proxy_claim()
    state = db.get_daemon_state()
    assert state["state"] == "idle"
    assert state["current_job_id"] is None


# --- Coverage gap tests: proxy error branches ---


def test_proxy_read_timeout_returns_504(client, db):
    """ReadTimeout from Ollama produces 504 with descriptive message. Covers lines 510-518."""
    import httpx as _httpx

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=_httpx.ReadTimeout("timed out"))
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "deepseek-r1:8b", "prompt": "think hard"},
        )

    assert resp.status_code == 504
    assert "timeout" in resp.json()["detail"].lower()
    # Verify job was completed with exit_code=1
    conn = db._connect()
    row = conn.execute("SELECT * FROM jobs WHERE source = 'proxy' ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["exit_code"] == 1


def test_proxy_release_claim_exception_still_completes(client, db):
    """release_proxy_claim exception in finally block is logged, not raised. Covers lines 532-533."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "ok", "done": True}
    mock_response.raise_for_status = MagicMock()

    with (
        patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls,
        patch.object(db, "release_proxy_claim", side_effect=RuntimeError("release failed")),
    ):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "llama3.2:3b", "prompt": "hello"},
        )

    # Should still return 200 despite release_proxy_claim failing
    assert resp.status_code == 200


def test_streaming_proxy_paused_returns_503(client):
    """Streaming path returns 503 when daemon is manually paused. Covers line 565."""
    client.post("/api/daemon/pause")
    resp = client.post(
        "/api/generate",
        json={"model": "llama3.2:3b", "prompt": "hello", "stream": True},
    )
    assert resp.status_code == 503


def test_streaming_proxy_timeout_returns_504(client, db):
    """Streaming path returns 504 when queue slot is not available. Covers lines 594-598."""
    import time

    job_id = db.submit_job("echo busy", model="test", priority=5, timeout=60, source="test")
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (time.time(), job_id))
        conn.commit()
    db.update_daemon_state(state="running", current_job_id=job_id)

    with (
        patch("ollama_queue.api.proxy.PROXY_WAIT_TIMEOUT", 1),
        patch("ollama_queue.api.proxy.PROXY_POLL_INTERVAL", 0.1),
    ):
        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hello", "stream": True},
        )

    assert resp.status_code == 504


def test_streaming_proxy_setup_exception_returns_502(client, db):
    """Streaming setup exception returns 502 and cleans up. Covers lines 615-622."""
    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.build_request = MagicMock(side_effect=Exception("connection refused"))
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "llama3.2:3b", "prompt": "hello", "stream": True},
        )

    assert resp.status_code == 502
    state = db.get_daemon_state()
    assert state["state"] == "idle"


def test_streaming_release_fn_complete_job_exception(client, db):
    """_release() handles complete_job exception gracefully. Covers lines 627-628."""
    chunks = [
        json.dumps({"response": "ok", "done": True}).encode() + b"\n",
    ]

    async def fake_aiter_raw():
        for chunk in chunks:
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()
    mock_resp.headers = {"content-type": "application/x-ndjson"}

    original_complete = db.complete_job

    def failing_complete(*args, **kwargs):
        raise RuntimeError("DB error")

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        with patch.object(db, "complete_job", side_effect=failing_complete):
            resp = client.post(
                "/api/generate",
                json={"model": "llama3.2:3b", "prompt": "hello", "stream": True},
            )

    # Should still return 200 — the release error is logged but not raised
    assert resp.status_code == 200


def test_streaming_release_fn_release_claim_exception(client, db):
    """_release() handles release_proxy_claim exception gracefully. Covers lines 630-632."""
    chunks = [
        json.dumps({"response": "ok", "done": True}).encode() + b"\n",
    ]

    async def fake_aiter_raw():
        for chunk in chunks:
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()
    mock_resp.headers = {"content-type": "application/x-ndjson"}

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        with patch.object(db, "release_proxy_claim", side_effect=RuntimeError("iptables error")):
            resp = client.post(
                "/api/generate",
                json={"model": "llama3.2:3b", "prompt": "hello", "stream": True},
            )

    # Should still return 200
    assert resp.status_code == 200


def test_proxy_consumer_request_count_tracking(client, db):
    """Proxy tracks request_count against matching consumers. Covers lines 458-468."""
    import time

    # Seed a consumer with source_label matching the request's _source
    cid = db.upsert_consumer(
        {
            "name": "aria.service",
            "type": "systemd",
            "platform": "linux",
            "source_label": "aria",
            "detected_at": int(time.time()),
            "status": "patched",
            "request_count": 0,
        }
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "ok", "done": True}
    mock_response.raise_for_status = MagicMock()

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hello", "_source": "aria"},
        )

    assert resp.status_code == 200
    consumer = db.get_consumer(cid)
    assert consumer["request_count"] == 1


def test_proxy_consumer_request_count_tracking_exception(client, db):
    """Consumer request_count tracking exception is caught and logged. Covers lines 467-468."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"response": "ok", "done": True}
    mock_response.raise_for_status = MagicMock()

    with (
        patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls,
        patch.object(db, "list_consumers", side_effect=RuntimeError("db error")),
    ):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hello"},
        )

    # Should still succeed — exception is caught
    assert resp.status_code == 200


def test_streaming_consumer_request_count_tracking(client, db):
    """Streaming path also tracks consumer requests. Covers lines 574-584."""
    import time

    cid = db.upsert_consumer(
        {
            "name": "aria.service",
            "type": "systemd",
            "platform": "linux",
            "source_label": "eval-pipeline",
            "detected_at": int(time.time()),
            "status": "included",
            "request_count": 5,
        }
    )

    chunks = [
        json.dumps({"response": "ok", "done": True}).encode() + b"\n",
    ]

    async def fake_aiter_raw():
        for chunk in chunks:
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()
    mock_resp.headers = {"content-type": "application/x-ndjson"}

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hello", "stream": True, "_source": "eval-pipeline"},
        )

    assert resp.status_code == 200
    consumer = db.get_consumer(cid)
    assert consumer["request_count"] == 6


def test_iter_ndjson_empty_line_skipped(client):
    """_iter_ndjson skips empty lines between chunks. Covers line 408."""
    chunks = [
        b"\n",
        json.dumps({"response": "ok", "done": True}).encode() + b"\n",
    ]

    async def fake_aiter_raw():
        for chunk in chunks:
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()
    mock_resp.headers = {"content-type": "application/x-ndjson"}

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "llama3.2:3b", "prompt": "hi", "stream": True},
        )

    lines = [ln for ln in resp.content.split(b"\n") if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["done"] is True


def test_iter_ndjson_invalid_json_in_done_check(client):
    """_iter_ndjson handles invalid JSON gracefully for done check. Covers lines 414-415."""
    chunks = [
        b"not-json-data\n",
        json.dumps({"response": "ok", "done": True}).encode() + b"\n",
    ]

    async def fake_aiter_raw():
        for chunk in chunks:
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()
    mock_resp.headers = {"content-type": "application/x-ndjson"}

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "llama3.2:3b", "prompt": "hi", "stream": True},
        )

    # Should still produce output — invalid JSON lines are yielded but done check is skipped
    lines = [ln for ln in resp.content.split(b"\n") if ln.strip()]
    assert len(lines) == 2


def test_iter_ndjson_trailing_buffer_emitted(client):
    """_iter_ndjson emits trailing buffer without newline. Covers lines 416-417."""

    # A chunk that doesn't end with \n
    async def fake_aiter_raw():
        yield json.dumps({"response": "partial", "done": True}).encode()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()
    mock_resp.headers = {"content-type": "application/x-ndjson"}

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        resp = client.post(
            "/api/generate",
            json={"model": "llama3.2:3b", "prompt": "hi", "stream": True},
        )

    lines = [ln for ln in resp.content.split(b"\n") if ln.strip()]
    assert len(lines) >= 1


# ---------------------------------------------------------------------------
# /v1/chat/completions tests
# ---------------------------------------------------------------------------


def test_chat_completions_returns_openai_format(client):
    """POST /v1/chat/completions returns OpenAI-shaped response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "model": "qwen3:14b",
        "message": {"role": "assistant", "content": "Hello!"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 10,
        "eval_count": 5,
    }

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen3:14b",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello!"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["prompt_tokens"] == 10
    assert data["usage"]["completion_tokens"] == 5
    assert data["usage"]["total_tokens"] == 15
    assert data["id"].startswith("chatcmpl-")


def test_chat_completions_translates_temperature_and_max_tokens(client):
    """temperature and max_tokens are moved into Ollama options dict."""
    captured = {}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "ok"},
        "done": True,
        "done_reason": "stop",
    }

    async def capture_post(url, json=None, **kwargs):
        captured["body"] = json
        return mock_response

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = capture_post
        mock_cls.return_value = mock_client

        client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen3:14b",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.2,
                "max_tokens": 4000,
            },
        )

    body = captured["body"]
    assert body["options"]["temperature"] == 0.2
    assert body["options"]["num_predict"] == 4000
    assert "temperature" not in body
    assert "max_tokens" not in body


def test_chat_completions_queue_metadata_not_forwarded(client):
    """_priority/_source/_timeout are popped and not sent to Ollama."""
    captured = {}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "ok"},
        "done": True,
        "done_reason": "stop",
    }

    async def capture_post(url, json=None, **kwargs):
        captured["body"] = json
        return mock_response

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = capture_post
        mock_cls.return_value = mock_client

        client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen3:14b",
                "messages": [{"role": "user", "content": "hi"}],
                "_priority": 2,
                "_source": "gpt-researcher",
                "_timeout": 300,
            },
        )

    body = captured["body"]
    assert "_priority" not in body
    assert "_source" not in body
    assert "_timeout" not in body


def test_chat_completions_paused_returns_503(client):
    """Returns 503 when daemon is manually paused."""
    client.post("/api/daemon/pause")
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "qwen3:14b", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503


def test_chat_completions_ollama_error_returns_502(client):
    """Ollama connection error returns 502."""
    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        mock_cls.return_value = mock_client

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "qwen3:14b", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 502


def test_chat_completions_logged_in_jobs_table(client, db):
    """Chat completion is logged with command='proxy:/v1/chat/completions'."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "ok"},
        "done": True,
        "done_reason": "stop",
    }

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        client.post(
            "/v1/chat/completions",
            json={"model": "qwen3:14b", "messages": [{"role": "user", "content": "hi"}]},
        )

    conn = db._connect()
    row = conn.execute(
        "SELECT * FROM jobs WHERE command = 'proxy:/v1/chat/completions' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["status"] == "completed"
    assert row["model"] == "qwen3:14b"


# ---------------------------------------------------------------------------
# /v1/embeddings tests
# ---------------------------------------------------------------------------


def test_embeddings_returns_openai_format(client):
    """POST /v1/embeddings returns OpenAI list-format response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "model": "nomic-embed-text",
        "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
        "prompt_eval_count": 8,
    }

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        resp = client.post(
            "/v1/embeddings",
            json={"model": "nomic-embed-text", "input": ["hello", "world"]},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 2
    assert data["data"][0]["object"] == "embedding"
    assert data["data"][0]["index"] == 0
    assert data["data"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert data["data"][1]["index"] == 1
    assert data["usage"]["prompt_tokens"] == 8
    assert data["usage"]["total_tokens"] == 8


def test_embeddings_queue_metadata_not_forwarded(client):
    """_priority/_source/_timeout are popped and not sent to Ollama."""
    captured = {}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"embeddings": [[0.1]], "model": "nomic-embed-text"}

    async def capture_post(url, json=None, **kwargs):
        captured["body"] = json
        return mock_response

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = capture_post
        mock_cls.return_value = mock_client

        client.post(
            "/v1/embeddings",
            json={
                "model": "nomic-embed-text",
                "input": "hello",
                "_priority": 1,
                "_source": "gpt-researcher",
            },
        )

    body = captured["body"]
    assert "_priority" not in body
    assert "_source" not in body


def test_embeddings_paused_returns_503(client):
    """Returns 503 when daemon is manually paused."""
    client.post("/api/daemon/pause")
    resp = client.post(
        "/v1/embeddings",
        json={"model": "nomic-embed-text", "input": "hello"},
    )
    assert resp.status_code == 503


def test_iter_ndjson_release_on_error(client, db):
    """_iter_ndjson releases via finally when iteration raises. Covers lines 419-420."""

    async def failing_aiter_raw():
        yield json.dumps({"response": "He", "done": False}).encode() + b"\n"
        raise ConnectionError("stream broken")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = failing_aiter_raw
    mock_resp.aclose = AsyncMock()
    mock_resp.headers = {"content-type": "application/x-ndjson"}

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        # The streaming response will raise internally — TestClient may propagate or swallow it
        with contextlib.suppress(Exception):
            resp = client.post(
                "/api/generate",
                json={"model": "llama3.2:3b", "prompt": "hi", "stream": True},
            )

    # After error, proxy claim should be released via the finally block
    state = db.get_daemon_state()
    assert state["state"] == "idle"


# ---------------------------------------------------------------------------
# BitNet routing tests (/v1/chat/completions with bitnet: model prefix)
# ---------------------------------------------------------------------------


def test_bitnet_chat_completions_routes_to_bitnet_url(client):
    """bitnet: model prefix routes to BITNET_URL, not OLLAMA_URL."""
    captured = {}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "chatcmpl-abc",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }

    async def capture_post(url, json=None, **kwargs):
        captured["url"] = url
        captured["body"] = json
        return mock_response

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = capture_post
        mock_cls.return_value = mock_client

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "bitnet:10b", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 200
    # Must route to BITNET_URL, not OLLAMA_URL
    assert "11435" in captured["url"] or "bitnet" in captured["url"].lower()
    assert "/v1/chat/completions" in captured["url"]


def test_bitnet_chat_completions_returns_raw_openai_format(client):
    """BitNet response is returned as-is (no Ollama→OpenAI translation)."""
    openai_response = {
        "id": "chatcmpl-xyz",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "bitnet:10b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Ternary!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = openai_response

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "bitnet:10b", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 200
    data = resp.json()
    # Response is passed through as-is (no translation wrapping)
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Ternary!"
    # _queue_job_id stripped before returning to caller
    assert "_queue_job_id" not in data


def test_bitnet_chat_completions_logged_in_jobs_table(client, db):
    """BitNet requests create a job row with resource_profile='bitnet'."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        client.post(
            "/v1/chat/completions",
            json={"model": "bitnet:10b", "messages": [{"role": "user", "content": "test"}]},
        )

    with db._lock:
        row = (
            db._connect()
            .execute("SELECT * FROM jobs WHERE command = 'proxy:/v1/chat/completions[bitnet]' ORDER BY id DESC LIMIT 1")
            .fetchone()
        )

    assert row is not None
    assert row["model"] == "bitnet:10b"
    assert row["resource_profile"] == "bitnet"
    assert row["status"] == "completed"


def test_bitnet_chat_completions_queue_metadata_not_forwarded(client):
    """_priority/_source/_timeout are popped and not sent to BitNet."""
    captured = {}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {},
    }

    async def capture_post(url, json=None, **kwargs):
        captured["body"] = json
        return mock_response

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = capture_post
        mock_cls.return_value = mock_client

        client.post(
            "/v1/chat/completions",
            json={
                "model": "bitnet:10b",
                "messages": [{"role": "user", "content": "hi"}],
                "_priority": 2,
                "_source": "test",
                "_timeout": 30,
            },
        )

    body = captured["body"]
    assert "_priority" not in body
    assert "_source" not in body
    assert "_timeout" not in body


def test_bitnet_chat_completions_paused_returns_503(client):
    """Returns 503 when daemon is manually paused."""
    client.post("/api/daemon/pause")
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "bitnet:10b", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503


def test_bitnet_chat_completions_server_error_returns_502(client):
    """Returns 502 when BitNet llama-server is unreachable."""
    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        mock_cls.return_value = mock_client

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "bitnet:10b", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 502
    assert "BitNet" in resp.json()["detail"]


def test_non_bitnet_model_still_routes_to_ollama(client):
    """Non-bitnet: models still go through the Ollama translation path."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {"role": "assistant", "content": "Hi"},
        "done": True,
        "done_reason": "stop",
    }
    captured = {}

    async def capture_post(url, json=None, **kwargs):
        captured["url"] = url
        return mock_response

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = capture_post
        mock_cls.return_value = mock_client

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "qwen2.5:7b", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 200
    # Must route to Ollama /api/chat (not BitNet /v1/chat/completions)
    assert "11434" in captured["url"] or "/api/chat" in captured["url"]
    # Response is wrapped in OpenAI format by the Ollama translation layer
    data = resp.json()
    assert data["object"] == "chat.completion"


def test_streaming_proxy_cancelled_error_releases_claim(db):
    """CancelledError raised after claim acquisition but before StreamingResponse setup
    must release the proxy claim (C5, H8).

    CancelledError inherits from BaseException, not Exception — so the existing
    except Exception block in the streaming path cannot catch it.  The claim must
    still be released so future proxy requests are not permanently blocked.
    """
    import asyncio

    app = create_app(db)

    release_called = []
    original_release = db.release_proxy_claim

    def tracking_release():
        release_called.append(True)
        original_release()

    # Patch select_backend (called after claim+submit_job+start_job) to raise CancelledError.
    # This simulates the client disconnecting mid-request before streaming starts.
    with (
        patch.object(db, "release_proxy_claim", side_effect=tracking_release),
        patch(
            "ollama_queue.api.proxy.select_backend",
            side_effect=asyncio.CancelledError("client disconnected"),
        ),
        TestClient(app, raise_server_exceptions=False) as tc,
        contextlib.suppress(Exception),
    ):
        tc.post(
            "/api/generate",
            json={"model": "llama3.2:3b", "prompt": "hello", "stream": True},
        )

    # The proxy claim must have been released — the daemon must not be permanently blocked.
    assert len(release_called) >= 1, (
        "release_proxy_claim was never called — claim is permanently held after CancelledError"
    )
    state = db.get_daemon_state()
    assert state["state"] == "idle", f"Daemon stuck in non-idle state after CancelledError: {state['state']}"
    assert state["current_job_id"] is None, (
        f"current_job_id not cleared after CancelledError: {state['current_job_id']}"
    )


def test_streaming_background_task_forces_release_on_generator_release_failure(client, db):
    """BackgroundTask safety net must NOT double-release when _release() attempted release.

    Scenario: _iter_ndjson's finally block calls _release(), but both
    complete_job AND release_proxy_claim raise. After the H2 fix, _released is
    set to True before the early return, so the BackgroundTask safety net skips
    its retry (it only retries when _released is still False, meaning _release()
    was never called at all).

    This test verifies that with the H2 fix, release_proxy_claim is called
    exactly ONCE — not twice — preventing a double-release that could clear a
    new proxy request's sentinel.
    """
    chunks = [
        json.dumps({"response": "ok", "done": True}).encode() + b"\n",
    ]

    async def fake_aiter_raw():
        for chunk in chunks:
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()
    mock_resp.headers = {"content-type": "application/x-ndjson"}

    release_calls = {"count": 0}
    original_release = db.release_proxy_claim

    def counting_release():
        release_calls["count"] += 1
        if release_calls["count"] == 1:
            raise RuntimeError("first release failed")
        original_release()

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        with (
            patch.object(db, "complete_job", side_effect=RuntimeError("DB error")),
            patch.object(db, "release_proxy_claim", side_effect=counting_release),
            contextlib.suppress(Exception),
        ):
            resp = client.post(
                "/api/generate",
                json={"model": "llama3.2:3b", "prompt": "hi", "stream": True},
            )

    # H2 fix: _released=True after _release() sets it before early return.
    # BackgroundTask sees _released=True → skips retry → exactly 1 call total.
    assert release_calls["count"] == 1, (
        f"Expected exactly 1 release_proxy_claim call (H2 fix prevents BackgroundTask "
        f"retry when _released=True); got {release_calls['count']} calls"
    )


def test_streaming_release_flag_set_when_release_proxy_claim_raises(tmp_path):
    """_released must be set True even when release_proxy_claim() raises.

    Regression test for H2: if release_proxy_claim() raises in _release(),
    _released must still be set True so the BackgroundTask safety net does NOT
    call release_proxy_claim() a second time, which would clear a new proxy
    request's sentinel.

    We verify this by driving the real streaming endpoint with a mocked
    release_proxy_claim that raises once, then counting total calls: with the
    fix, the BackgroundTask sees _released=True and skips its retry, so we
    get exactly 1 call; without the fix (_released stays False) the BackgroundTask
    retries, giving 2 calls.
    """
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    release_calls = {"count": 0}
    original_release = db.release_proxy_claim

    def counting_release_raises():
        release_calls["count"] += 1
        raise RuntimeError("DB locked")

    chunks = [
        json.dumps({"response": "ok", "done": True}).encode() + b"\n",
    ]

    async def fake_aiter_raw():
        for chunk in chunks:
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()
    mock_resp.headers = {"content-type": "application/x-ndjson"}

    app = create_app(db)
    client = TestClient(app)

    with patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.send = AsyncMock(return_value=mock_resp)
        mock_client.build_request = MagicMock(return_value=MagicMock())
        mock_client.aclose = AsyncMock()
        mock_cls.return_value = mock_client

        with (
            patch.object(db, "release_proxy_claim", side_effect=counting_release_raises),
            contextlib.suppress(Exception),
        ):
            client.post(
                "/api/generate",
                json={"model": "llama3.2:3b", "prompt": "hi", "stream": True},
            )

    # With the H2 fix: _released=True after the first (raising) call to release_proxy_claim.
    # BackgroundTask checks `if not _released` → False → skips second call.
    # Total calls must be exactly 1 (not 2).
    assert release_calls["count"] == 1, (
        f"release_proxy_claim was called {release_calls['count']} times — "
        "with H2 fix, _released=True after first raise prevents BackgroundTask retry"
    )


def test_proxy_ollama_request_no_double_complete_job_when_first_raises(tmp_path):
    """complete_job must not be called twice when the first call raises.

    Regression test for H1: if db.complete_job raises on a successful Ollama
    response, the outer except Exception block calls complete_job again,
    marking the job failed despite Ollama returning 200.
    """
    import asyncio

    import ollama_queue.api as _api
    from ollama_queue.api.proxy import _proxy_ollama_request

    db = MagicMock()
    db.get_daemon_state.return_value = {"state": "running"}
    db.list_consumers.return_value = []
    db.try_claim_for_proxy.return_value = True
    db.submit_job.return_value = 42
    db.start_job.return_value = None

    complete_job_calls = []

    def raising_complete_job(**kwargs):
        complete_job_calls.append(kwargs)
        if len(complete_job_calls) == 1:
            raise RuntimeError("SQLITE_BUSY")
        # Second call should NOT happen — if it does, the bug is present

    db.complete_job.side_effect = raising_complete_job
    db.release_proxy_claim.return_value = None

    mock_response = MagicMock()
    mock_response.json.return_value = {"response": "hello", "eval_count": None}

    _api.db = db

    with (
        patch("ollama_queue.api.proxy.select_backend", new=AsyncMock(return_value="http://localhost:11434")),
        patch("ollama_queue.api.proxy.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with pytest.raises(Exception):  # expect HTTPException(502) from outer except
            asyncio.run(
                _proxy_ollama_request(
                    endpoint="/api/generate",
                    command="proxy:/api/generate",
                    body={"model": "test", "stream": False},
                    resource_profile="ollama",
                    extract_stdout_fn=lambda r: str(r.get("response", ""))[:500],
                    error_prefix="test",
                )
            )

    # The fix: complete_job should only be called once (the first call that raised)
    assert len(complete_job_calls) == 1, (
        f"complete_job was called {len(complete_job_calls)} times — must not call it twice when first call raises"
    )
    # And the one call must be the success path (exit_code=0)
    assert complete_job_calls[0]["exit_code"] == 0
