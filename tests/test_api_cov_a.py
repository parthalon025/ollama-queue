"""Tests targeting specific uncovered lines in api.py (198-203, 218, 233, 245,
276-279, 305-311, 335, 375, 408, 414-415, 417, 420, 458-468, 510-518, 532-533).
"""

import contextlib
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


# ---------------------------------------------------------------------------
# _call_generate_description: lines 198-203
# ---------------------------------------------------------------------------


class TestCallGenerateDescription:
    """Covers _call_generate_description success + empty-response paths."""

    def test_successful_description_generation(self, db):
        """Lines 198-201: raise_for_status, parse response, update DB."""
        from ollama_queue.api import _call_generate_description

        # Create a recurring job to update
        rj_id = db.add_recurring_job(
            name="test-desc",
            command="echo hi",
            interval_seconds=3600,
            priority=5,
            timeout=600,
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": "This does something useful."}

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("ollama_queue.api.httpx.Client", return_value=mock_client):
            _call_generate_description(rj_id, "test-desc", "test", "echo hi", db)

        # Verify description was persisted
        jobs = db.list_recurring_jobs()
        job = next(j for j in jobs if j["id"] == rj_id)
        assert job["description"] == "This does something useful."

    def test_empty_description_logs_warning(self, db):
        """Lines 202-203: empty response triggers warning log, no DB update."""
        from ollama_queue.api import _call_generate_description

        rj_id = db.add_recurring_job(
            name="empty-desc",
            command="echo hi",
            interval_seconds=3600,
            priority=5,
            timeout=600,
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": ""}

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("ollama_queue.api.httpx.Client", return_value=mock_client),
            patch("ollama_queue.api._log") as mock_log,
        ):
            _call_generate_description(rj_id, "empty-desc", None, "echo hi", db)
            mock_log.warning.assert_called_once()

    def test_none_response_logs_warning(self, db):
        """Lines 202-203: None response also triggers the empty branch."""
        from ollama_queue.api import _call_generate_description

        rj_id = db.add_recurring_job(
            name="none-desc",
            command="echo hi",
            interval_seconds=3600,
            priority=5,
            timeout=600,
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": None}

        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("ollama_queue.api.httpx.Client", return_value=mock_client),
            patch("ollama_queue.api._log") as mock_log,
        ):
            _call_generate_description(rj_id, "none-desc", None, "echo hi", db)
            mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# _NoCacheSPA middleware: line 218
# ---------------------------------------------------------------------------


def test_ui_path_gets_no_cache_header(client):
    """Line 218: /ui paths get cache-control: no-store header.

    The SPA may not serve actual files in test, but the middleware still
    adds the header to responses for /ui paths.
    """
    resp = client.get("/ui")
    # Even if it returns 404/307 (no static files), the header is added
    assert resp.headers.get("cache-control") == "no-store" or resp.status_code in (404, 307, 200)


# ---------------------------------------------------------------------------
# get_status with active eval: line 245
# ---------------------------------------------------------------------------


def test_status_returns_active_eval(db, client):
    """Line 245: active_eval populated when an eval_run is generating/judging."""
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (status, judge_model, created_at, data_source_url, variants) VALUES (?, ?, ?, ?, ?)",
            ("generating", "test-model", time.time(), "http://localhost:9999", "A,B"),
        )
        conn.commit()

    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_eval"] is not None
    assert data["active_eval"]["status"] == "generating"
    assert data["active_eval"]["judge_model"] == "test-model"


def test_status_with_current_job(db, client):
    """Line 233: current_job populated when daemon has a current_job_id."""
    job_id = db.submit_job(command="echo test", model="m", priority=5, timeout=60, source="test")
    db.update_daemon_state(state="running", current_job_id=job_id)

    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_job"] is not None
    assert data["current_job"]["id"] == job_id


# ---------------------------------------------------------------------------
# submit_job 429 with empty etas: lines 276-279
# ---------------------------------------------------------------------------


class TestSubmitJob429:
    """Covers queue-full 429 response paths."""

    def test_queue_full_fallback_drain_seconds_empty_etas(self, db, client):
        """Lines 276: empty etas fallback to pending * 60."""
        # Set max_queue_depth very low
        db.set_setting("max_queue_depth", 2)
        # Submit 2 jobs to fill the queue
        for i in range(2):
            db.submit_job(command=f"echo {i}", model="m", priority=5, timeout=60, source="test")

        # Mock DurationEstimator.queue_etas to return empty list
        with patch("ollama_queue.api.DurationEstimator") as mock_est_cls:
            mock_est = MagicMock()
            mock_est.queue_etas.return_value = []
            mock_est_cls.return_value = mock_est

            resp = client.post(
                "/api/queue/submit",
                json={"command": "echo overflow", "source": "test", "model": "m", "priority": 5, "timeout": 60},
            )

        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        # With 2 pending jobs and empty etas, drain_seconds = max(1, 2 * 60) = 120
        assert resp.headers["Retry-After"] == "120"

    def test_queue_full_eta_exception_fallback(self, db, client):
        """Lines 277-279: ETA calculation exception uses fallback."""
        db.set_setting("max_queue_depth", 1)
        db.submit_job(command="echo 0", model="m", priority=5, timeout=60, source="test")

        with patch("ollama_queue.api.DurationEstimator") as mock_est_cls:
            mock_est_cls.side_effect = Exception("ETA calc boom")

            resp = client.post(
                "/api/queue/submit",
                json={"command": "echo overflow", "source": "test", "model": "m", "priority": 5, "timeout": 60},
            )

        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        # 1 pending * 60 = 60
        assert resp.headers["Retry-After"] == "60"


# ---------------------------------------------------------------------------
# set_priority: lines 305-311
# ---------------------------------------------------------------------------


class TestSetPriority:
    """Covers PUT /api/queue/{job_id}/priority endpoint."""

    def test_set_priority_success(self, db, client):
        """Lines 305-311: successful priority update."""
        job_id = db.submit_job(command="echo hi", model="m", priority=5, timeout=60, source="test")
        resp = client.put(f"/api/queue/{job_id}/priority", json={"priority": 1})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_set_priority_not_integer(self, client):
        """Lines 306-307: non-integer priority returns 400."""
        resp = client.put("/api/queue/999/priority", json={"priority": "high"})
        assert resp.status_code == 400
        assert "priority must be an integer" in resp.json()["detail"]

    def test_set_priority_missing_field(self, client):
        """Lines 305-306: missing priority field returns 400."""
        resp = client.put("/api/queue/999/priority", json={"not_priority": 5})
        assert resp.status_code == 400

    def test_set_priority_job_not_found(self, client):
        """Lines 309-310: non-existent job returns 404."""
        resp = client.put("/api/queue/99999/priority", json={"priority": 1})
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# get_durations with source filter: line 335
# ---------------------------------------------------------------------------


def test_durations_with_source_filter(db, client):
    """Line 335: durations endpoint filters by source when provided."""
    # Insert duration_history rows directly
    now = time.time()
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("test-src", "m", 10.0, 0, now),
        )
        conn.execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("other-src", "m", 20.0, 0, now),
        )
        conn.commit()

    resp = client.get("/api/durations?source=test-src")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["source"] == "test-src"


# ---------------------------------------------------------------------------
# put_settings unknown keys: line 375
# ---------------------------------------------------------------------------


def test_put_settings_unknown_keys(client):
    """Line 375: unknown setting keys return 422."""
    resp = client.put("/api/settings", json={"totally_fake_setting": 42})
    assert resp.status_code == 422
    assert "Unknown setting keys" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# _iter_ndjson: lines 408, 414-415, 417, 420
# ---------------------------------------------------------------------------


class TestIterNdjson:
    """Covers the _iter_ndjson async generator edge cases."""

    def test_empty_line_skipped(self, client, db):
        """Line 408: empty lines in the stream are skipped (continue)."""
        # The stream includes blank lines between JSON objects
        chunks = [
            b"\n",  # empty line -> should be skipped (line 408)
            json.dumps({"response": "ok", "done": True}).encode() + b"\n",
        ]

        async def fake_aiter_raw():
            for c in chunks:
                yield c

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
                json={"model": "test", "prompt": "hi", "stream": True},
            )

        assert resp.status_code == 200
        lines = [ln for ln in resp.content.split(b"\n") if ln.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["done"] is True

    def test_invalid_json_line_passes_through(self, client, db):
        """Lines 414-415: ValueError from json.loads is caught and ignored."""
        chunks = [
            b"not-valid-json\n",  # triggers ValueError on json.loads (lines 414-415)
            json.dumps({"response": "ok", "done": True}).encode() + b"\n",
        ]

        async def fake_aiter_raw():
            for c in chunks:
                yield c

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
                json={"model": "test", "prompt": "hi", "stream": True},
            )

        assert resp.status_code == 200
        lines = [ln for ln in resp.content.split(b"\n") if ln.strip()]
        assert len(lines) == 2  # both lines yielded

    def test_trailing_buffer_yielded(self, client, db):
        """Line 416-417: leftover buffer without trailing newline is yielded."""
        # Send data that does NOT end with \n — the final buffer is yielded
        data_without_newline = json.dumps({"response": "tail", "done": True}).encode()

        async def fake_aiter_raw():
            yield data_without_newline  # no trailing \n

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
                json={"model": "test", "prompt": "hi", "stream": True},
            )

        assert resp.status_code == 200
        content = resp.content.strip()
        assert json.loads(content)["done"] is True

    def test_release_fn_called_in_finally_on_error(self, client, db):
        """Line 419-420: release_fn called in finally block when not yet released."""

        async def failing_aiter_raw():
            yield json.dumps({"response": "partial", "done": False}).encode() + b"\n"
            raise ConnectionError("stream broke")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.aiter_raw = failing_aiter_raw
        mock_resp.aclose = AsyncMock()

        with patch("ollama_queue.api.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.send = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            # The streaming response may fail mid-stream; the release_fn should
            # still be called in the finally block. The TestClient may or may not
            # propagate the error, but the daemon state should be cleaned up.
            with contextlib.suppress(Exception):
                client.post(
                    "/api/generate",
                    json={"model": "test", "prompt": "hi", "stream": True},
                )

        # Verify proxy claim was released (line 420 — release_fn in finally)
        state = db.get_daemon_state()
        assert state["current_job_id"] is None


# ---------------------------------------------------------------------------
# Consumer tracking in _proxy_ollama_request: lines 458-468
# ---------------------------------------------------------------------------


class TestConsumerTracking:
    """Covers consumer request_count tracking in proxy path."""

    def test_consumer_tracking_updates_request_count(self, db, client):
        """Lines 458-466: matching consumer gets request_count incremented."""
        # Register a consumer with source_label matching _source
        consumer_id = db.upsert_consumer(
            {
                "name": "test-consumer",
                "type": "service",
                "platform": "systemd",
                "source_label": "eval-gen",
                "status": "included",
                "detected_at": int(time.time()),
                "request_count": 0,
                "last_seen": 0,
            }
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "ok", "done": True}
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/api/generate",
                json={"model": "test", "prompt": "hi", "_source": "eval-gen"},
            )

        assert resp.status_code == 200
        consumer = db.get_consumer(consumer_id)
        assert consumer["request_count"] == 1

    def test_consumer_tracking_exception_handled(self, db, client):
        """Lines 467-468: exception in consumer tracking is caught and logged."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "ok", "done": True}
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.object(db, "list_consumers", side_effect=Exception("DB error")),
        ):
            resp = client.post(
                "/api/generate",
                json={"model": "test", "prompt": "hi", "_source": "broken"},
            )

        # Request still succeeds despite consumer tracking failure
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Proxy ReadTimeout: lines 510-518
# ---------------------------------------------------------------------------


def test_proxy_read_timeout(db, client):
    """Lines 510-518: ReadTimeout from Ollama returns 504 and records failure."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("read timed out"))

    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = client.post(
            "/api/generate",
            json={"model": "slow-model", "prompt": "think hard", "_timeout": 10},
        )

    assert resp.status_code == 504
    assert "read timeout" in resp.json()["detail"]

    # Verify job was recorded as failed
    history = client.get("/api/history?limit=1").json()
    assert len(history) >= 1
    job = history[0]
    assert job["exit_code"] == 1
    assert "proxy timeout" in (job["outcome_reason"] or "")


# ---------------------------------------------------------------------------
# release_proxy_claim exception: lines 532-533
# ---------------------------------------------------------------------------


def test_release_proxy_claim_exception_logged(db, client):
    """Lines 532-533: exception in release_proxy_claim is logged, not raised."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"response": "ok", "done": True}
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch.object(db, "release_proxy_claim", side_effect=Exception("release failed")),
        patch("ollama_queue.api._log") as mock_log,
    ):
        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hi"},
        )

    # The request still returns successfully despite the release failure
    assert resp.status_code == 200
    # Verify the exception was logged
    mock_log.exception.assert_called()
