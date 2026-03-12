"""Tests to close api.py coverage gaps (lines 565-940)."""

import json
import time
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


def _make_streaming_mock():
    """Build mocks for the streaming httpx path (successful response)."""
    chunks = [json.dumps({"response": "ok", "done": True}).encode() + b"\n"]

    async def fake_aiter_raw():
        for chunk in chunks:
            yield chunk

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.aiter_raw = fake_aiter_raw
    mock_resp.aclose = AsyncMock()

    mock_client = AsyncMock()
    mock_client.build_request = MagicMock(return_value=MagicMock())
    mock_client.send = AsyncMock(return_value=mock_resp)
    mock_client.aclose = AsyncMock()
    return mock_client


def _seed_dlq(db):
    """Insert a DLQ entry via the real DB path."""
    job_id = db.submit_job("echo fail", model="t", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="oops")
    db.move_to_dlq(job_id, failure_reason="test failure")
    return job_id


# ---------------------------------------------------------------------------
# Line 565: streaming /api/generate returns 503 when manually paused
# ---------------------------------------------------------------------------
def test_streaming_generate_rejects_when_paused_manual(client, db):
    """Covers line 565: raise HTTPException(503) in streaming path when paused_manual."""
    client.post("/api/daemon/pause")
    resp = client.post(
        "/api/generate",
        json={"model": "test", "prompt": "hello", "stream": True},
    )
    assert resp.status_code == 503
    assert "manually paused" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Lines 574-582: consumer request_count tracking in streaming path
# ---------------------------------------------------------------------------
def test_streaming_consumer_request_count_incremented(client, db):
    """Covers lines 574-582: consumer tracking updates request_count and last_seen."""
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO consumers (name, type, platform, source_label, status, request_count, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test-consumer", "systemd", "linux", "proxy", "patched", 0, int(time.time())),
        )
        conn.commit()

    mock_client = _make_streaming_mock()

    with patch("ollama_queue.api.proxy.httpx.AsyncClient", return_value=mock_client):
        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hello", "stream": True},
        )
    assert resp.status_code == 200

    consumers = db.list_consumers()
    matched = [c for c in consumers if c["source_label"] == "proxy"]
    assert len(matched) == 1
    assert matched[0]["request_count"] == 1


# ---------------------------------------------------------------------------
# Lines 583-584: consumer tracking exception swallowed
# ---------------------------------------------------------------------------
def test_streaming_consumer_tracking_exception_handled(client, db):
    """Covers lines 583-584: exception in request_count tracking is caught and logged."""
    mock_client = _make_streaming_mock()

    with (
        patch("ollama_queue.api.proxy.httpx.AsyncClient", return_value=mock_client),
        patch.object(db, "list_consumers", side_effect=RuntimeError("db boom")),
    ):
        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hello", "stream": True},
        )
    # Should still succeed despite tracking failure
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 594-595, 598: proxy claim wait loop and timeout
# ---------------------------------------------------------------------------
def test_streaming_generate_timeout_waiting_for_claim(client, db):
    """Covers lines 594-595 (sleep+wait) and 598 (HTTPException 504)."""
    # Occupy the daemon so try_claim_for_proxy always fails
    job_id = db.submit_job("echo busy", model="t", priority=5, timeout=60, source="t")
    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE jobs SET status='running', started_at=? WHERE id=?",
            (time.time(), job_id),
        )
        conn.commit()
    db.update_daemon_state(state="running", current_job_id=job_id)

    with (
        patch("ollama_queue.api.proxy.PROXY_WAIT_TIMEOUT", 0.3),
        patch("ollama_queue.api.proxy.PROXY_POLL_INTERVAL", 0.1),
    ):
        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hello", "stream": True},
        )
    assert resp.status_code == 504
    assert "Timed out" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Lines 615-622: streaming setup exception (httpx send fails)
# ---------------------------------------------------------------------------
def test_streaming_generate_setup_failure(client, db):
    """Covers lines 615-622: exception during build_request/send -> 502."""
    mock_client = MagicMock()
    mock_client.build_request = MagicMock(return_value=MagicMock())
    mock_client.send = AsyncMock(side_effect=ConnectionError("connection refused"))
    mock_client.aclose = AsyncMock()

    with patch("ollama_queue.api.proxy.httpx.AsyncClient", return_value=mock_client):
        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hello", "stream": True},
        )
    assert resp.status_code == 502
    assert "connection refused" in resp.json()["detail"]

    # Verify proxy claim was released (state back to idle)
    state = db.get_daemon_state()
    assert state["state"] == "idle"


# ---------------------------------------------------------------------------
# Lines 627-628: _release() complete_job exception
# ---------------------------------------------------------------------------
def test_streaming_release_complete_job_fails(client, db):
    """Covers lines 627-628: complete_job raises inside _release(), logged."""
    mock_client = _make_streaming_mock()

    with (
        patch("ollama_queue.api.proxy.httpx.AsyncClient", return_value=mock_client),
        patch.object(db, "complete_job", side_effect=RuntimeError("complete boom")),
    ):
        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hello", "stream": True},
        )
    # Response is still 200 — error is in the synchronous _release callback
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 631-632: _release() release_proxy_claim exception
# ---------------------------------------------------------------------------
def test_streaming_release_proxy_claim_fails(client, db):
    """Covers lines 631-632: release_proxy_claim raises inside _release(), logged."""
    mock_client = _make_streaming_mock()

    with (
        patch("ollama_queue.api.proxy.httpx.AsyncClient", return_value=mock_client),
        patch.object(db, "release_proxy_claim", side_effect=RuntimeError("release boom")),
    ):
        resp = client.post(
            "/api/generate",
            json={"model": "test", "prompt": "hello", "stream": True},
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 692-695: list_schedule with model classification/vram
# ---------------------------------------------------------------------------
def test_list_schedule_includes_model_info(client, db):
    """Covers lines 692-695: model_profile, model_type, model_vram_mb populated."""
    client.post(
        "/api/schedule",
        json={
            "name": "model-job",
            "command": "echo hi",
            "interval_seconds": 3600,
            "model": "qwen2.5:7b",
        },
    )
    resp = client.get("/api/schedule")
    assert resp.status_code == 200
    jobs = resp.json()
    model_job = next(j for j in jobs if j["name"] == "model-job")
    assert "model_profile" in model_job
    assert "model_type" in model_job
    assert "model_vram_mb" in model_job
    assert model_job["model_profile"] is not None
    assert model_job["model_type"] is not None
    assert isinstance(model_job["model_vram_mb"], int | float)


# ---------------------------------------------------------------------------
# Line 790: update_schedule returns 404 if recurring job not found
# ---------------------------------------------------------------------------
def test_update_schedule_nonexistent(client):
    """Covers line 790: raise HTTPException(404) when update finds no row."""
    resp = client.put("/api/schedule/9999", json={"enabled": False})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Lines 796-797: rebalance exception after update_schedule
# ---------------------------------------------------------------------------
def test_update_schedule_rebalance_fails(client, db):
    """Covers lines 796-797: rebalance exception after update is logged, not raised."""
    client.post(
        "/api/schedule",
        json={"name": "reb-job", "command": "echo hi", "interval_seconds": 3600},
    )
    jobs = client.get("/api/schedule").json()
    rj_id = jobs[0]["id"]

    with patch(
        "ollama_queue.scheduling.scheduler.Scheduler.rebalance",
        side_effect=RuntimeError("scheduler boom"),
    ):
        resp = client.put(f"/api/schedule/{rj_id}", json={"enabled": False})
    # Should still return 200 — rebalance failure is logged, not raised
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 809-823: run-now endpoint
# ---------------------------------------------------------------------------
def test_run_now_submits_job(client, db):
    """Covers lines 809-823: rj found, submit_job called, job_id returned."""
    client.post(
        "/api/schedule",
        json={
            "name": "run-now-job",
            "command": "echo hello",
            "interval_seconds": 3600,
            "model": "qwen2.5:7b",
            "priority": 3,
            "tag": "test",
        },
    )
    jobs = client.get("/api/schedule").json()
    rj_id = jobs[0]["id"]

    resp = client.post(f"/api/schedule/{rj_id}/run-now")
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert isinstance(data["job_id"], int)

    # Verify the job was actually created in the queue
    queue = client.get("/api/queue").json()
    matching = [j for j in queue if j["id"] == data["job_id"]]
    assert len(matching) == 1
    assert matching[0]["source"] == "run-now-job"


def test_run_now_not_found(client):
    """Covers lines 810-811: raise HTTPException(404) when rj not found."""
    resp = client.post("/api/schedule/9999/run-now")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Lines 834-839: generate-description endpoint
# ---------------------------------------------------------------------------
def test_generate_description_success(client, db):
    """Covers lines 834-839: rj found, background thread kicked off, ok=True returned.

    The endpoint spawns a background thread and returns immediately with description=None.
    The description is written to the DB asynchronously and visible on the next GET /api/schedule poll.
    """
    client.post(
        "/api/schedule",
        json={"name": "desc-job", "command": "echo hi", "interval_seconds": 3600},
    )
    jobs = client.get("/api/schedule").json()
    rj_id = jobs[0]["id"]

    def fake_gen(rj_id_arg, name, tag, command, db_ref):
        db_ref.update_recurring_job(rj_id_arg, description="A great job.")

    with patch("ollama_queue.api.schedule._call_generate_description", side_effect=fake_gen):
        resp = client.post(f"/api/schedule/{rj_id}/generate-description")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # Endpoint returns None immediately — description arrives via next GET /api/schedule poll
    assert data["description"] is None


def test_generate_description_not_found(client):
    """Covers lines 835-836: raise HTTPException(404) when rj not found."""
    resp = client.post("/api/schedule/9999/generate-description")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Line 860: delete_schedule returns 404 if not found
# ---------------------------------------------------------------------------
def test_delete_schedule_nonexistent(client):
    """Covers line 860: raise HTTPException(404) when delete finds no row."""
    resp = client.delete("/api/schedule/9999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Lines 878-879: DLQ retry single entry
# ---------------------------------------------------------------------------
def test_retry_dlq_entry(client, db):
    """Covers lines 878-879: retry_dlq_entry called, new_job_id returned."""
    _seed_dlq(db)
    dlq_entries = client.get("/api/dlq").json()
    assert len(dlq_entries) >= 1
    dlq_id = dlq_entries[0]["id"]

    resp = client.post(f"/api/dlq/{dlq_id}/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert "new_job_id" in data


# ---------------------------------------------------------------------------
# Lines 883-884: DLQ dismiss entry
# ---------------------------------------------------------------------------
def test_dismiss_dlq_entry(client, db):
    """Covers lines 883-884: dismiss_dlq_entry called, dismissed id returned."""
    _seed_dlq(db)
    dlq_entries = client.get("/api/dlq").json()
    assert len(dlq_entries) >= 1
    dlq_id = dlq_entries[0]["id"]

    resp = client.post(f"/api/dlq/{dlq_id}/dismiss")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dismissed"] == dlq_id


# ---------------------------------------------------------------------------
# Line 940: reschedule_dlq_entry returns 400 for permanent failure
# ---------------------------------------------------------------------------
def test_reschedule_permanent_failure_rejected(client, db):
    """Covers line 940: raise HTTPException(400) for permanent failure category."""
    _seed_dlq(db)
    dlq_entries = client.get("/api/dlq").json()
    assert len(dlq_entries) >= 1
    dlq_id = dlq_entries[0]["id"]

    with patch("ollama_queue.sensing.system_snapshot.classify_failure", return_value="permanent"):
        resp = client.post(f"/api/dlq/{dlq_id}/reschedule")
    assert resp.status_code == 400
    assert "permanent" in resp.json()["detail"].lower()
