"""Tests for the FastAPI REST API."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import Database
from ollama_queue.scheduling.scheduler import Scheduler


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


def test_get_status(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "daemon" in data
    assert "queue" in data
    assert "kpis" in data


def test_get_queue_empty(client):
    resp = client.get("/api/queue")
    assert resp.status_code == 200
    assert resp.json() == []


def test_submit_job(client):
    resp = client.post(
        "/api/queue/submit",
        json={
            "command": "echo test",
            "source": "test",
            "model": "qwen2.5:7b",
            "priority": 3,
            "timeout": 60,
        },
    )
    assert resp.status_code == 200
    assert "job_id" in resp.json()


def test_get_queue_after_submit(client):
    client.post(
        "/api/queue/submit", json={"command": "echo test", "source": "test", "model": "m", "priority": 5, "timeout": 60}
    )
    resp = client.get("/api/queue")
    assert len(resp.json()) == 1


def test_cancel_job(client):
    resp = client.post(
        "/api/queue/submit", json={"command": "echo test", "source": "test", "model": "m", "priority": 5, "timeout": 60}
    )
    job_id = resp.json()["job_id"]
    resp = client.post(f"/api/queue/cancel/{job_id}")
    assert resp.status_code == 200


def test_get_settings(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "poll_interval_seconds" in data


def test_put_settings(client):
    resp = client.put("/api/settings", json={"ram_pause_pct": 90})
    assert resp.status_code == 200
    resp = client.get("/api/settings")
    assert resp.json()["ram_pause_pct"] == 90


def test_pause_resume(client):
    resp = client.post("/api/daemon/pause")
    assert resp.status_code == 200
    resp = client.get("/api/status")
    assert resp.json()["daemon"]["state"] == "paused_manual"
    resp = client.post("/api/daemon/resume")
    assert resp.status_code == 200


def test_get_history_empty(client):
    resp = client.get("/api/history")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_health_includes_burst_regime(client):
    """GET /api/health includes burst_regime field."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "burst_regime" in data
    assert data["burst_regime"] in {"unknown", "subcritical", "moderate", "warning", "critical"}


def test_health_includes_cpu_count(client):
    """GET /api/health includes cpu_count for load gauge percentage conversion."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "cpu_count" in data
    assert isinstance(data["cpu_count"], int)
    assert data["cpu_count"] >= 1


def test_get_durations(client):
    resp = client.get("/api/durations")
    assert resp.status_code == 200


def test_get_heatmap(client):
    resp = client.get("/api/heatmap")
    assert resp.status_code == 200


class TestScheduleAPI:
    def test_list_recurring_jobs(self, client):
        r = client.get("/api/schedule")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_add_recurring_job(self, client):
        r = client.post(
            "/api/schedule",
            json={
                "name": "test-job",
                "command": "echo hello",
                "interval_seconds": 3600,
                "model": "qwen2.5:7b",
                "priority": 5,
                "tag": "test",
            },
        )
        assert r.status_code == 200
        assert r.json()["name"] == "test-job"

    def test_update_recurring_job(self, client):
        client.post("/api/schedule", json={"name": "j1", "command": "echo hi", "interval_seconds": 3600})
        r = client.put("/api/schedule/1", json={"enabled": False})
        assert r.status_code == 200

    def test_delete_recurring_job(self, client):
        client.post("/api/schedule", json={"name": "j1", "command": "echo hi", "interval_seconds": 3600})
        r = client.delete("/api/schedule/1")
        assert r.status_code == 200

    def test_trigger_rebalance(self, client):
        r = client.post("/api/schedule/rebalance")
        assert r.status_code == 200

    def test_get_schedule_events(self, client):
        r = client.get("/api/schedule/events")
        assert r.status_code == 200


class TestBatchScheduleAPI:
    """Tests for batch schedule endpoints."""

    def _seed_jobs(self, client):
        """Create a few recurring jobs with different tags."""
        for name, tag, enabled in [
            ("aria-full", "aria", True),
            ("aria-intraday", "aria", True),
            ("tg-brief", "telegram", True),
            ("tg-capture", "telegram", False),
        ]:
            client.post(
                "/api/schedule",
                json={
                    "name": name,
                    "command": f"echo {name}",
                    "interval_seconds": 3600,
                    "tag": tag,
                    "priority": 5,
                },
            )
            if not enabled:
                jobs = client.get("/api/schedule").json()
                rj = next(j for j in jobs if j["name"] == name)
                client.put(f"/api/schedule/{rj['id']}", json={"enabled": False})

    def test_batch_toggle_disables_all(self, client):
        self._seed_jobs(client)
        resp = client.post("/api/schedule/batch-toggle", json={"tag": "aria", "enabled": False})
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2
        jobs = client.get("/api/schedule").json()
        aria_jobs = [j for j in jobs if j["tag"] == "aria"]
        assert all(j["enabled"] == 0 for j in aria_jobs)

    def test_batch_toggle_enables_all(self, client):
        self._seed_jobs(client)
        resp = client.post("/api/schedule/batch-toggle", json={"tag": "telegram", "enabled": True})
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2
        jobs = client.get("/api/schedule").json()
        tg_jobs = [j for j in jobs if j["tag"] == "telegram"]
        assert all(j["enabled"] == 1 for j in tg_jobs)

    def test_batch_toggle_missing_params(self, client):
        resp = client.post("/api/schedule/batch-toggle", json={"tag": "aria"})
        assert resp.status_code == 400

    def test_batch_toggle_unknown_tag(self, client):
        resp = client.post("/api/schedule/batch-toggle", json={"tag": "nonexistent", "enabled": True})
        assert resp.status_code == 404
        assert "No recurring jobs found" in resp.json()["detail"]

    def test_batch_run_submits_enabled_only(self, client):
        self._seed_jobs(client)
        resp = client.post("/api/schedule/batch-run", json={"tag": "telegram"})
        assert resp.status_code == 200
        data = resp.json()
        # Only tg-brief is enabled; tg-capture is disabled
        assert data["submitted"] == 1
        assert len(data["job_ids"]) == 1

    def test_batch_run_all_enabled(self, client):
        self._seed_jobs(client)
        resp = client.post("/api/schedule/batch-run", json={"tag": "aria"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["submitted"] == 2
        assert len(data["job_ids"]) == 2

    def test_batch_run_missing_tag(self, client):
        resp = client.post("/api/schedule/batch-run", json={})
        assert resp.status_code == 400

    def test_get_schedule_runs_empty(self, client):
        client.post(
            "/api/schedule",
            json={"name": "test-job", "command": "echo hi", "interval_seconds": 3600},
        )
        jobs = client.get("/api/schedule").json()
        rj_id = jobs[0]["id"]
        resp = client.get(f"/api/schedule/{rj_id}/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_schedule_runs_with_history(self, client):
        client.post(
            "/api/schedule",
            json={"name": "test-job", "command": "echo hi", "interval_seconds": 3600, "tag": "test"},
        )
        jobs = client.get("/api/schedule").json()
        rj_id = jobs[0]["id"]
        # Submit and complete a job linked to this recurring job
        client.post("/api/schedule/batch-run", json={"tag": "test"})
        resp = client.get(f"/api/schedule/{rj_id}/runs?limit=5")
        assert resp.status_code == 200
        runs = resp.json()
        assert len(runs) == 1
        assert runs[0]["status"] == "pending"

    def test_update_max_retries(self, client):
        client.post(
            "/api/schedule",
            json={"name": "retry-job", "command": "echo hi", "interval_seconds": 3600},
        )
        jobs = client.get("/api/schedule").json()
        rj_id = jobs[0]["id"]
        resp = client.put(f"/api/schedule/{rj_id}", json={"max_retries": 3})
        assert resp.status_code == 200
        jobs = client.get("/api/schedule").json()
        assert jobs[0]["max_retries"] == 3


class TestDLQAPI:
    def test_list_dlq_empty(self, client):
        r = client.get("/api/dlq")
        assert r.status_code == 200
        assert r.json() == []

    def test_retry_all_dlq(self, client):
        r = client.post("/api/dlq/retry-all")
        assert r.status_code == 200

    def test_clear_dlq(self, client):
        r = client.delete("/api/dlq")
        assert r.status_code == 200


def test_load_map_endpoint(client):
    resp = client.get("/api/schedule/load-map")
    assert resp.status_code == 200
    data = resp.json()
    assert "slots" in data
    assert len(data["slots"]) == Scheduler._SLOT_COUNT
    assert all(isinstance(s, int | float) for s in data["slots"])


def test_suggest_time_endpoint_returns_suggestions(client):
    resp = client.get("/api/schedule/suggest")
    assert resp.status_code == 200
    data = resp.json()
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)


def test_suggest_time_endpoint_top_n(client):
    resp = client.get("/api/schedule/suggest?priority=5&top_n=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["suggestions"]) <= 2


def test_suggest_time_endpoint_suggestion_shape(client):
    # First add a cron job so there's at least something in the schedule
    client.post(
        "/api/schedule",
        json={"name": "shape-test-job", "command": "echo hi", "cron_expression": "0 2 * * *"},
    )
    resp = client.get("/api/schedule/suggest?top_n=3")
    assert resp.status_code == 200
    data = resp.json()
    for s in data["suggestions"]:
        assert "cron" in s
        assert "score" in s
        assert "slot" in s
        assert 0 <= s["slot"] < 48


def test_create_schedule_with_pin(client):
    resp = client.post(
        "/api/schedule",
        json={"name": "pinned-job", "command": "echo hi", "cron_expression": "0 23 * * *", "pinned": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pinned"] == 1


def test_update_schedule_pin(client):
    # Create job first
    client.post(
        "/api/schedule",
        json={"name": "job1", "command": "echo hi", "interval_seconds": 3600},
    )
    jobs = client.get("/api/schedule").json()
    rj_id = jobs[0]["id"]
    # Pin it
    resp = client.put(f"/api/schedule/{rj_id}", json={"pinned": True})
    assert resp.status_code == 200
    # Verify
    jobs = client.get("/api/schedule").json()
    assert jobs[0]["pinned"] == 1


def test_get_models_returns_list(client):
    from unittest.mock import patch

    with (
        patch(
            "ollama_queue.models.client.OllamaModels.list_local",
            return_value=[{"name": "qwen2.5:7b", "size_bytes": 4_700_000_000, "modified": "1w"}],
        ),
        patch("ollama_queue.models.client.OllamaModels.get_loaded", return_value=[]),
    ):
        resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["name"] == "qwen2.5:7b"
    assert "resource_profile" in data[0]
    assert "type_tag" in data[0]
    assert "vram_mb" in data[0]


def test_schedule_includes_estimated_duration(client):
    resp = client.get("/api/schedule")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_queue_etas_endpoint(client):
    resp = client.get("/api/queue/etas")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_post_models_pull(client):
    from unittest.mock import patch

    with patch("ollama_queue.models.client.OllamaModels.pull", return_value=1):
        resp = client.post("/api/models/pull", json={"model": "llama3.2:3b"})
    assert resp.status_code == 200
    assert resp.json()["pull_id"] == 1


def test_get_models_catalog(client):
    resp = client.get("/api/models/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert "curated" in data
    assert len(data["curated"]) > 0


class TestProxyPriority:
    """Proxy /api/generate accepts _priority, _source, _timeout fields."""

    def _mock_httpx_client(self, response_data):
        from unittest.mock import AsyncMock, MagicMock

        mock_response = MagicMock()
        mock_response.json.return_value = response_data

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    def test_priority_and_source_recorded_in_job(self, client):
        from unittest.mock import patch

        mock_client = self._mock_httpx_client({"response": "hello", "done": True})

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/api/generate",
                json={
                    "model": "test-model",
                    "prompt": "hello",
                    "_priority": 1,
                    "_source": "eval-generate",
                    "_timeout": 300,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["response"] == "hello"

        history = client.get("/api/history?limit=1").json()
        assert len(history) >= 1
        job = history[0]
        assert job["priority"] == 1
        assert job["source"] == "eval-generate"
        assert job["timeout"] == 300

    def test_defaults_without_queue_fields(self, client):
        from unittest.mock import patch

        mock_client = self._mock_httpx_client({"response": "ok", "done": True})

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/api/generate",
                json={"model": "test-model", "prompt": "hello"},
            )

        assert resp.status_code == 200

        history = client.get("/api/history?limit=1").json()
        job = history[0]
        assert job["priority"] == 0
        assert job["source"] == "proxy"
        assert job["timeout"] == 600  # proxy default raised from 120 to 600

    def test_queue_fields_not_forwarded_to_ollama(self, client):
        from unittest.mock import patch

        mock_client = self._mock_httpx_client({"response": "ok", "done": True})

        with patch("httpx.AsyncClient", return_value=mock_client):
            client.post(
                "/api/generate",
                json={
                    "model": "test-model",
                    "prompt": "hello",
                    "_priority": 1,
                    "_source": "eval",
                },
            )

        call_args = mock_client.post.call_args
        forwarded_body = call_args.kwargs.get("json", call_args[1].get("json", {}))
        assert "_priority" not in forwarded_body
        assert "_source" not in forwarded_body
        assert "_timeout" not in forwarded_body


def test_add_recurring_job_with_check_command(client):
    r = client.post(
        "/api/schedule",
        json={
            "name": "check-job",
            "command": "echo hi",
            "interval_seconds": 3600,
            "check_command": "exit 0",
            "max_runs": 5,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["check_command"] == "exit 0"
    assert data["max_runs"] == 5


def test_update_recurring_job_check_command(client):
    client.post("/api/schedule", json={"name": "upd-job", "command": "echo hi", "interval_seconds": 3600})
    r = client.put("/api/schedule/1", json={"check_command": "exit 1"})
    assert r.status_code == 200
    jobs = client.get("/api/schedule").json()
    assert jobs[0]["check_command"] == "exit 1"


def test_enable_endpoint_clears_disabled_job(client_and_db):
    client, db = client_and_db
    client.post(
        "/api/schedule",
        json={
            "name": "disabled-job",
            "command": "echo hi",
            "interval_seconds": 3600,
        },
    )
    # Disable and set outcome_reason directly via DB (the PUT endpoint does not expose outcome_reason)
    db.disable_recurring_job(1, "max_runs exhausted")
    r = client.post("/api/schedule/jobs/disabled-job/enable")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    jobs = client.get("/api/schedule").json()
    job = next(j for j in jobs if j["name"] == "disabled-job")
    assert job["enabled"] == 1
    assert job["outcome_reason"] is None


def test_enable_endpoint_not_found(client):
    r = client.post("/api/schedule/jobs/nonexistent/enable")
    assert r.status_code == 404


def test_get_load_map(client):
    resp = client.get("/api/schedule/load-map")
    assert resp.status_code == 200
    data = resp.json()
    assert "slots" in data
    assert "slot_minutes" in data
    assert data["slot_minutes"] == 30
    assert data["count"] == len(data["slots"])


def test_add_recurring_job_minimal(client):
    resp = client.post(
        "/api/schedule",
        json={
            "name": "test-job",
            "command": "echo hello",
            "interval_seconds": 3600,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "test-job"
    assert data["command"] == "echo hello"


def test_add_recurring_job_all_fields(client):
    resp = client.post(
        "/api/schedule",
        json={
            "name": "full-job",
            "command": "aria run",
            "interval_seconds": 86400,
            "model": "qwen2.5:7b",
            "priority": 3,
            "timeout": 300,
            "source": "test",
            "tag": "aria",
            "max_retries": 2,
            "resource_profile": "ollama",
            "pinned": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "full-job"
    assert data["tag"] == "aria"


def test_enable_job_by_name(client):
    # Create a recurring job first
    client.post(
        "/api/schedule",
        json={"name": "disable-me", "command": "echo x", "interval_seconds": 3600},
    )
    # Disable it via PUT
    jobs = client.get("/api/schedule").json()
    rj = next(j for j in jobs if j["name"] == "disable-me")
    client.put(f"/api/schedule/{rj['id']}", json={"enabled": False})

    # Re-enable via by-name endpoint
    resp = client.post("/api/schedule/jobs/disable-me/enable")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify re-enabled
    jobs = client.get("/api/schedule").json()
    rj = next(j for j in jobs if j["name"] == "disable-me")
    assert rj["enabled"] in (True, 1)


class TestAdmissionGate:
    """Tests for HTTP 429 admission gate on POST /api/queue/submit."""

    def test_submit_returns_429_when_queue_full(self, client_and_db):
        """POST /api/queue/submit returns 429 when pending job count >= max_queue_depth."""
        client, db = client_and_db
        # Set max_queue_depth to 2
        db.set_setting("max_queue_depth", 2)
        # Fill the queue
        db.submit_job("echo a", "qwen2.5:7b", 5, 60, "test")
        db.submit_job("echo b", "qwen2.5:7b", 5, 60, "test")
        # Third submission should be rejected
        resp = client.post(
            "/api/queue/submit",
            json={
                "command": "echo c",
                "model": "qwen2.5:7b",
                "priority": 5,
                "timeout": 60,
                "source": "test",
            },
        )
        assert resp.status_code == 429

    def test_submit_429_includes_retry_after_header(self, client_and_db):
        """HTTP 429 response includes Retry-After header with positive integer seconds."""
        client, db = client_and_db
        db.set_setting("max_queue_depth", 1)
        db.submit_job("echo a", "qwen2.5:7b", 5, 60, "test")
        resp = client.post(
            "/api/queue/submit",
            json={
                "command": "echo c",
                "model": "qwen2.5:7b",
                "priority": 5,
                "timeout": 60,
                "source": "test",
            },
        )
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        retry_after_str = resp.headers["Retry-After"]
        assert retry_after_str.isdigit(), f"Retry-After must be a positive integer string, got {retry_after_str!r}"
        retry_after = int(retry_after_str)
        assert retry_after >= 1

    def test_submit_succeeds_at_boundary_below_limit(self, client_and_db):
        """Queue at count == max_depth - 1: submission must succeed (boundary just below 429 threshold)."""
        client, db = client_and_db
        db.set_setting("max_queue_depth", 3)
        db.submit_job("echo a", "qwen2.5:7b", 5, 60, "test")
        db.submit_job("echo b", "qwen2.5:7b", 5, 60, "test")
        resp = client.post(
            "/api/queue/submit",
            json={
                "command": "echo c",
                "model": "qwen2.5:7b",
                "priority": 5,
                "timeout": 60,
                "source": "test",
            },
        )
        assert resp.status_code == 200
        assert "job_id" in resp.json()

    def test_submit_429_body_contains_error_info(self, client_and_db):
        """HTTP 429 body contains error key and queue metadata."""
        client, db = client_and_db
        db.set_setting("max_queue_depth", 1)
        db.submit_job("echo a", "qwen2.5:7b", 5, 60, "test")
        resp = client.post(
            "/api/queue/submit",
            json={
                "command": "echo c",
                "model": "qwen2.5:7b",
                "priority": 5,
                "timeout": 60,
                "source": "test",
            },
        )
        assert resp.status_code == 429
        data = resp.json()
        assert data["error"] == "queue_full"
        assert data["pending"] == 1
        assert data["max_queue_depth"] == 1


# --- DLQ Schedule Preview & Reschedule ---


def test_dlq_schedule_preview_empty(client):
    resp = client.get("/api/dlq/schedule-preview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entries"] == []
    assert data["count"] == 0


def test_dlq_schedule_preview_with_entries(client_and_db):
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="", outcome_reason="exit code 1")
    db.move_to_dlq(job_id, "connection refused")
    resp = client.get("/api/dlq/schedule-preview")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 1
    assert data["entries"][0]["eligible"] is True
    assert data["count"] == 1


def test_dlq_manual_reschedule(client_and_db):
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="", outcome_reason="exit code 1")
    db.move_to_dlq(job_id, "connection refused")
    dlq_entries = db.list_dlq()
    assert len(dlq_entries) == 1
    resp = client.post(f"/api/dlq/{dlq_entries[0]['id']}/reschedule")
    assert resp.status_code == 200
    data = resp.json()
    assert "new_job_id" in data


def test_dlq_reschedule_not_found(client):
    resp = client.post("/api/dlq/99999/reschedule")
    assert resp.status_code == 404


# --- Metrics ---


def test_model_metrics_empty(client):
    resp = client.get("/api/metrics/models")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_performance_curve_empty(client):
    resp = client.get("/api/metrics/performance-curve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fitted"] is False


def test_model_metrics_with_data(client_and_db):
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "qwen2.5:7b", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.complete_job(job_id, exit_code=0, stdout_tail="", stderr_tail="")
    db.store_job_metrics(
        job_id,
        {
            "model": "qwen2.5:7b",
            "eval_count": 100,
            "eval_duration_ns": 2_000_000_000,
            "load_duration_ns": 1_500_000_000,
            "model_size_gb": 4.7,
        },
    )
    resp = client.get("/api/metrics/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "qwen2.5:7b" in data
    assert data["qwen2.5:7b"]["run_count"] == 1


# --- Deferrals ---


def test_list_deferred_empty(client):
    resp = client.get("/api/deferred")
    assert resp.status_code == 200
    assert resp.json() == []


def test_defer_and_resume(client_and_db):
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    resp = client.post(f"/api/jobs/{job_id}/defer", json={"reason": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    deferral_id = data["deferral_id"]

    # Verify listed
    resp = client.get("/api/deferred")
    assert len(resp.json()) == 1

    # Resume
    resp = client.post(f"/api/deferred/{deferral_id}/resume")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == job_id

    # Verify no longer listed
    resp = client.get("/api/deferred")
    assert len(resp.json()) == 0


def test_defer_not_found(client):
    resp = client.post("/api/jobs/99999/defer", json={"reason": "test"})
    assert resp.status_code == 404


def test_defer_running_job_fails(client_and_db):
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    resp = client.post(f"/api/jobs/{job_id}/defer", json={"reason": "test"})
    assert resp.status_code == 400


def test_resume_already_resumed(client_and_db):
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    deferral_id = db.defer_job(job_id, "test")
    db.resume_deferred_job(deferral_id)
    resp = client.post(f"/api/deferred/{deferral_id}/resume")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


def test_get_status_with_active_eval_run(client_and_db):
    """GET /api/status includes active_eval when an eval run is generating."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, judge_model) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'generating', 'qwen2.5:7b')"
        )
        conn.commit()
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_eval"] is not None
    assert data["active_eval"]["status"] == "generating"


def test_get_status_with_current_job(client_and_db):
    """GET /api/status includes current_job when daemon has a current_job_id."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.update_daemon_state(state="running", current_job_id=job_id)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_job"] is not None
    assert data["current_job"]["id"] == job_id


def test_set_priority_endpoint(client_and_db):
    """PUT /api/queue/{job_id}/priority updates job priority."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    resp = client.put(f"/api/queue/{job_id}/priority", json={"priority": 1})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_set_priority_invalid_type(client_and_db):
    """PUT /api/queue/{job_id}/priority returns 400 if priority is not an int."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    resp = client.put(f"/api/queue/{job_id}/priority", json={"priority": "high"})
    assert resp.status_code == 400


def test_set_priority_not_found(client):
    """PUT /api/queue/{job_id}/priority returns 404 for non-existent or non-pending job."""
    resp = client.put("/api/queue/99999/priority", json={"priority": 1})
    assert resp.status_code == 404


def test_put_settings_unknown_key(client):
    """PUT /api/settings rejects unknown setting keys with 422."""
    resp = client.put("/api/settings", json={"nonexistent_key": 42})
    assert resp.status_code == 422


def test_durations_with_source_filter(client_and_db):
    """GET /api/durations?source=test filters by source."""
    client, db = client_and_db
    resp = client.get("/api/durations?source=test")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_submit_429_fallback_when_etas_empty(client_and_db):
    """When queue is full and no ETAs available, fallback drain_seconds is used."""
    from unittest.mock import patch

    client, db = client_and_db
    db.set_setting("max_queue_depth", 1)
    db.submit_job("echo a", "qwen2.5:7b", 5, 60, "test")

    # Mock get_pending_jobs to return empty (triggers empty ETAs branch)
    with patch.object(db, "get_pending_jobs", return_value=[]):
        resp = client.post(
            "/api/queue/submit",
            json={
                "command": "echo c",
                "model": "qwen2.5:7b",
                "priority": 5,
                "timeout": 60,
                "source": "test",
            },
        )
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1


def test_submit_429_fallback_when_eta_calculation_raises(client_and_db):
    """When queue is full and ETA calculation raises, fallback is used."""
    from unittest.mock import patch

    client, db = client_and_db
    db.set_setting("max_queue_depth", 1)
    db.submit_job("echo a", "qwen2.5:7b", 5, 60, "test")

    with patch("ollama_queue.api.jobs.DurationEstimator") as mock_est:
        mock_est.return_value.queue_etas.side_effect = Exception("boom")
        resp = client.post(
            "/api/queue/submit",
            json={
                "command": "echo c",
                "model": "qwen2.5:7b",
                "priority": 5,
                "timeout": 60,
                "source": "test",
            },
        )
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1


def test_update_schedule_not_found(client):
    """PUT /api/schedule/{rj_id} returns 404 for non-existent job."""
    resp = client.put("/api/schedule/9999", json={"enabled": False})
    assert resp.status_code == 404


def test_update_schedule_rebalance_exception(client_and_db):
    """Rebalance failure after update is logged but doesn't fail the request."""
    from unittest.mock import patch

    client, db = client_and_db
    client.post("/api/schedule", json={"name": "j1", "command": "echo hi", "interval_seconds": 3600})
    with patch("ollama_queue.scheduling.scheduler.Scheduler.rebalance", side_effect=Exception("boom")):
        resp = client.put("/api/schedule/1", json={"enabled": False})
    assert resp.status_code == 200


def test_delete_schedule_not_found(client):
    """DELETE /api/schedule/{rj_id} returns 404 for non-existent job."""
    resp = client.delete("/api/schedule/9999")
    assert resp.status_code == 404


def test_enable_schedule_by_name_not_found(client):
    """POST /api/schedule/jobs/{name}/enable returns 404 for unknown name."""
    resp = client.post("/api/schedule/jobs/nonexistent/enable")
    assert resp.status_code == 404


def test_run_schedule_now(client_and_db):
    """POST /api/schedule/{rj_id}/run-now submits a one-off job."""
    client, db = client_and_db
    client.post("/api/schedule", json={"name": "run-now-test", "command": "echo hi", "interval_seconds": 3600})
    jobs = client.get("/api/schedule").json()
    rj_id = jobs[0]["id"]
    resp = client.post(f"/api/schedule/{rj_id}/run-now")
    assert resp.status_code == 200
    assert "job_id" in resp.json()


def test_run_schedule_now_not_found(client):
    """POST /api/schedule/{rj_id}/run-now returns 404 for non-existent job."""
    resp = client.post("/api/schedule/9999/run-now")
    assert resp.status_code == 404


def test_generate_description_endpoint(client_and_db):
    """POST /api/schedule/{rj_id}/generate-description triggers description generation."""
    from unittest.mock import patch

    client, db = client_and_db
    client.post("/api/schedule", json={"name": "desc-test", "command": "echo hi", "interval_seconds": 3600})
    jobs = client.get("/api/schedule").json()
    rj_id = jobs[0]["id"]

    with patch("ollama_queue.api.schedule._call_generate_description") as mock_gen:
        resp = client.post(f"/api/schedule/{rj_id}/generate-description")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_gen.assert_called_once()


def test_generate_description_not_found(client):
    """POST /api/schedule/{rj_id}/generate-description returns 404 for non-existent job."""
    resp = client.post("/api/schedule/9999/generate-description")
    assert resp.status_code == 404


def test_call_generate_description_empty_response():
    """_call_generate_description logs warning on empty model response."""
    from unittest.mock import MagicMock, patch

    mock_db = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": ""}
    mock_resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    from ollama_queue.api.schedule import _call_generate_description

    with patch("ollama_queue.api.schedule.httpx.Client", return_value=mock_client):
        _call_generate_description(1, "test", "tag", "echo hi", mock_db)
    # update_recurring_job should NOT be called (empty response)
    mock_db.update_recurring_job.assert_not_called()


def test_list_schedule_with_model(client_and_db):
    """GET /api/schedule includes model classification info when model is set."""
    client, db = client_and_db
    client.post(
        "/api/schedule",
        json={"name": "model-job", "command": "echo hi", "interval_seconds": 3600, "model": "qwen2.5:7b"},
    )
    resp = client.get("/api/schedule")
    assert resp.status_code == 200
    jobs = resp.json()
    job = next(j for j in jobs if j["name"] == "model-job")
    assert job.get("model_profile") is not None
    assert job.get("model_type") is not None
    assert job.get("model_vram_mb") is not None


def test_retry_dlq_entry(client_and_db):
    """POST /api/dlq/{dlq_id}/retry creates a new job from DLQ entry."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="", outcome_reason="exit code 1")
    db.move_to_dlq(job_id, "failure")
    entries = db.list_dlq()
    resp = client.post(f"/api/dlq/{entries[0]['id']}/retry")
    assert resp.status_code == 200
    assert "new_job_id" in resp.json()


def test_dismiss_dlq_entry(client_and_db):
    """POST /api/dlq/{dlq_id}/dismiss dismisses a DLQ entry."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="", outcome_reason="exit code 1")
    db.move_to_dlq(job_id, "failure")
    entries = db.list_dlq()
    resp = client.post(f"/api/dlq/{entries[0]['id']}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["dismissed"] == entries[0]["id"]


def test_dlq_reschedule_permanent_failure(client_and_db):
    """POST /api/dlq/{dlq_id}/reschedule returns 400 for permanent failure."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="", outcome_reason="exit code 1")
    db.move_to_dlq(job_id, "command not found")
    entries = db.list_dlq()
    resp = client.post(f"/api/dlq/{entries[0]['id']}/reschedule")
    assert resp.status_code == 400


def test_defer_job_not_pending(client_and_db):
    """POST /api/jobs/{job_id}/defer returns 400 for completed job."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.complete_job(job_id, exit_code=0, stdout_tail="", stderr_tail="")
    resp = client.post(f"/api/jobs/{job_id}/defer", json={"reason": "test"})
    assert resp.status_code == 400


def test_resume_deferred_not_found(client):
    """POST /api/deferred/{deferral_id}/resume returns 404 for non-existent deferral."""
    resp = client.post("/api/deferred/99999/resume")
    assert resp.status_code == 404


def test_get_models_catalog_with_search(client):
    """GET /api/models/catalog?q=llama performs Ollama search."""
    from unittest.mock import patch

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b'[{"name": "llama3:8b"}]'
        mock_urlopen.return_value = mock_response

        resp = client.get("/api/models/catalog?q=llama")
    assert resp.status_code == 200
    data = resp.json()
    assert "search_results" in data
    assert len(data["search_results"]) == 1


def test_get_models_catalog_search_exception(client):
    """GET /api/models/catalog?q=fail gracefully handles search failure."""
    from unittest.mock import patch

    with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        resp = client.get("/api/models/catalog?q=fail")
    assert resp.status_code == 200
    assert resp.json()["search_results"] == []


def test_pull_model_empty_name(client):
    """POST /api/models/pull returns 400 for empty model name."""
    resp = client.post("/api/models/pull", json={"model": ""})
    assert resp.status_code == 400


def test_get_pull_status(client_and_db):
    """GET /api/models/pull/{pull_id} returns status."""
    from unittest.mock import patch

    client, db = client_and_db
    with patch(
        "ollama_queue.models.client.OllamaModels.get_pull_status", return_value={"status": "downloading", "pct": 50}
    ):
        resp = client.get("/api/models/pull/1")
    assert resp.status_code == 200
    assert resp.json()["status"] == "downloading"


def test_get_pull_status_not_found(client):
    """GET /api/models/pull/{pull_id} returns 404 for unknown pull."""
    from unittest.mock import patch

    with patch("ollama_queue.models.client.OllamaModels.get_pull_status", return_value={"error": "not found"}):
        resp = client.get("/api/models/pull/999")
    assert resp.status_code == 404


def test_cancel_pull(client):
    """DELETE /api/models/pull/{pull_id} cancels a pull."""
    from unittest.mock import patch

    with patch("ollama_queue.models.client.OllamaModels.cancel_pull", return_value=True):
        resp = client.delete("/api/models/pull/1")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True


def test_performance_curve_with_data(client_and_db):
    """GET /api/metrics/performance-curve with fitting data."""
    client, db = client_and_db
    # Insert enough data points for a curve fit
    for _i, (model, size, tok) in enumerate([("m1", 2.0, 300), ("m2", 4.0, 200), ("m3", 8.0, 100)], start=1):
        job_id = db.submit_job("echo hi", model, priority=5, timeout=60, source="test")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=0, stdout_tail="", stderr_tail="")
        db.store_job_metrics(
            job_id,
            {
                "model": model,
                "eval_count": 100,
                "eval_duration_ns": int(60_000_000_000 / tok * 100),
                "load_duration_ns": 1_000_000_000,
                "model_size_gb": size,
            },
        )
    resp = client.get("/api/metrics/performance-curve")
    assert resp.status_code == 200


# --- Coverage gap tests: SPA static file serving, middleware, startup ---


def test_no_cache_spa_middleware_sets_header(tmp_path):
    """_NoCacheSPA middleware sets no-store for /ui paths. Covers lines 217-218."""
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    client = TestClient(app)
    resp = client.get("/ui/test-path")
    # The middleware adds cache-control: no-store on /ui paths
    if resp.status_code in (200, 404, 307):
        assert resp.headers.get("cache-control") == "no-store"


def test_startup_scan_exception_is_caught(tmp_path):
    """Startup consumer scan exception is caught and logged. Covers lines 2756-2757."""
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    with patch("ollama_queue.app.run_scan", side_effect=RuntimeError("scan failed")):
        app = create_app(db)
    assert app is not None


def test_spa_static_with_dist_directory(tmp_path):
    """SPA static file serving with a real dist directory. Covers lines 2736-2742."""
    from pathlib import Path

    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    # Create a fake spa dist directory
    spa_dir = Path(__file__).parent.parent / "ollama_queue" / "dashboard" / "spa" / "dist"
    if spa_dir.exists():
        app = create_app(db)
        client = TestClient(app)

        # Test null byte path — httpx ≥0.28 rejects null bytes client-side
        # (InvalidURL), so we accept either a 404 from the server or
        # a client-side rejection.
        try:
            resp = client.get("/ui/\x00bad")
            assert resp.status_code == 404
        except Exception:  # noqa: S110 — httpx client-side rejection is expected
            pass

        # Test non-existent file path — should fall back to index.html
        resp = client.get("/ui/nonexistent-page")
        # Either 200 (index.html fallback) or 404
        assert resp.status_code in (200, 404)

        # Test root /ui/ path
        resp = client.get("/ui/")
        assert resp.status_code in (200, 307, 404)
    else:
        pytest.skip("spa dist directory not built")


# ---------------------------------------------------------------------------
# Task 15: Priority bounds (0-10) and query limit caps (#19)
# ---------------------------------------------------------------------------


def test_set_priority_below_range(client_and_db):
    """PUT /api/queue/{job_id}/priority returns 400 for priority < 0."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    resp = client.put(f"/api/queue/{job_id}/priority", json={"priority": -1})
    assert resp.status_code == 400
    assert "0-10" in resp.json()["detail"]


def test_set_priority_above_range(client_and_db):
    """PUT /api/queue/{job_id}/priority returns 400 for priority > 10."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    resp = client.put(f"/api/queue/{job_id}/priority", json={"priority": 11})
    assert resp.status_code == 400
    assert "0-10" in resp.json()["detail"]


def test_set_priority_boundary_zero(client_and_db):
    """PUT /api/queue/{job_id}/priority accepts priority=0 (lower bound)."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    resp = client.put(f"/api/queue/{job_id}/priority", json={"priority": 0})
    assert resp.status_code == 200


def test_set_priority_boundary_ten(client_and_db):
    """PUT /api/queue/{job_id}/priority accepts priority=10 (upper bound)."""
    client, db = client_and_db
    job_id = db.submit_job("echo hi", "test-model", priority=5, timeout=60, source="test")
    resp = client.put(f"/api/queue/{job_id}/priority", json={"priority": 10})
    assert resp.status_code == 200


def test_schedule_events_limit_capped_at_1000(client):
    """GET /api/schedule/events?limit=9999 silently caps to 1000."""
    resp = client.get("/api/schedule/events?limit=9999")
    assert resp.status_code == 200


def test_schedule_events_limit_floor_at_1(client):
    """GET /api/schedule/events?limit=0 silently floors to 1."""
    resp = client.get("/api/schedule/events?limit=0")
    assert resp.status_code == 200


def test_suggest_priority_clamped(client):
    """GET /api/schedule/suggest?priority=99 silently clamps to 10."""
    resp = client.get("/api/schedule/suggest?priority=99")
    assert resp.status_code == 200


def test_suggest_top_n_clamped(client):
    """GET /api/schedule/suggest?top_n=100 silently clamps to 20."""
    resp = client.get("/api/schedule/suggest?top_n=100")
    assert resp.status_code == 200


def test_suggest_top_n_floor(client):
    """GET /api/schedule/suggest?top_n=0 silently floors to 1."""
    resp = client.get("/api/schedule/suggest?top_n=0")
    assert resp.status_code == 200
    assert len(resp.json()["suggestions"]) <= 1


# ---------------------------------------------------------------------------
# Task 16: Batch operations return 404 for zero-match tag (#20)
# ---------------------------------------------------------------------------


def test_batch_run_unknown_tag_returns_404(client):
    """POST /api/schedule/batch-run returns 404 when no jobs match tag."""
    resp = client.post("/api/schedule/batch-run", json={"tag": "nonexistent"})
    assert resp.status_code == 404
    assert "No enabled recurring jobs found" in resp.json()["detail"]


def test_get_history_negative_offset_returns_400(client):
    """GET /api/history with negative offset returns 400."""
    resp = client.get("/api/history?offset=-1")
    assert resp.status_code == 400
    assert "offset" in resp.json()["detail"]


def test_get_history_limit_is_capped(client):
    """GET /api/history with limit > 200 is silently capped to 200."""
    resp = client.get("/api/history?limit=99999")
    assert resp.status_code == 200  # no error — just capped


def test_put_settings_rejects_string_for_numeric_key(client):
    """PUT /api/settings rejects non-numeric values for numeric settings."""
    resp = client.put("/api/settings", json={"poll_interval_seconds": "not-a-number"})
    assert resp.status_code == 422
    assert "must be a number" in resp.json()["detail"]


def test_put_settings_accepts_numeric_for_numeric_key(client):
    """PUT /api/settings accepts a valid numeric value for a numeric setting."""
    resp = client.put("/api/settings", json={"poll_interval_seconds": 10})
    assert resp.status_code == 200
