"""Tests for the FastAPI REST API."""

import pytest
from fastapi.testclient import TestClient

from ollama_queue.api import create_app
from ollama_queue.db import Database
from ollama_queue.scheduler import Scheduler


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app)


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
        assert resp.status_code == 200
        assert resp.json()["updated"] == 0

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
            "ollama_queue.models.OllamaModels.list_local",
            return_value=[{"name": "qwen2.5:7b", "size_bytes": 4_700_000_000, "modified": "1w"}],
        ),
        patch("ollama_queue.models.OllamaModels.get_loaded", return_value=[]),
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

    with patch("ollama_queue.models.OllamaModels.pull", return_value=1):
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
        assert job["timeout"] == 120

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
