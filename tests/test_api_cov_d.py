"""Tests closing api.py coverage gaps for lines 1747-2757.

Covers: eval datasource prime errors, eval settings validation, eval schedule,
eval run listing with broken metrics, trigger eval run (variants list),
eval run detail with bad metrics, analyze background error, eval run
progress with item_ids fallback, repeat eval run background error,
judge-rerun background error+failure recording, consumer CRUD 404s,
intercept enable/disable/status, SPA static serving, startup scan error.
"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import Database
from ollama_queue.eval.engine import create_eval_run, update_eval_run


@pytest.fixture
def client_and_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    app = create_app(db)
    return TestClient(app)


def _seed_consumer(db, **overrides):
    defaults = {
        "name": "test.service",
        "type": "systemd",
        "platform": "linux",
        "source_label": "test",
        "detected_at": int(time.time()),
    }
    defaults.update(overrides)
    return db.upsert_consumer(defaults)


def _make_run(db, variant_id="A", status="queued", **kwargs):
    run_id = create_eval_run(db, variant_id=variant_id, **kwargs)
    if status != "queued":
        update_eval_run(db, run_id, status=status)
    return run_id


# ---------------------------------------------------------------------------
# Lines 1747-1748: prime_eval_datasource — upstream returns HTTP error
# ---------------------------------------------------------------------------


def test_prime_datasource_upstream_http_error(client_and_db):
    """POST /api/eval/datasource/prime returns 502 on upstream HTTP error status."""
    client, _db = client_and_db

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Internal Server Error",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("httpx.post", return_value=mock_response):
        resp = client.post("/api/eval/datasource/prime")

    assert resp.status_code == 502
    assert "500" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Line 1769: get_eval_settings — masks data_source_token
# ---------------------------------------------------------------------------


def test_get_eval_settings_masks_token(client_and_db):
    """GET /api/eval/settings masks a non-empty data_source_token."""
    client, db = client_and_db
    db.set_setting("eval.data_source_token", "super-secret-token")

    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    masked = "***"
    assert resp.json()["eval.data_source_token"] == masked


# ---------------------------------------------------------------------------
# Lines 1804-1805: put_eval_settings — unknown eval setting key
# ---------------------------------------------------------------------------


def test_put_eval_settings_rejects_unknown_key(client):
    """PUT /api/eval/settings rejects unknown setting keys with 422."""
    resp = client.put("/api/eval/settings", json={"eval.bogus_key": "value"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("unknown" in str(e) for e in detail)


# ---------------------------------------------------------------------------
# Lines 1813-1814: same_cluster_targets / diff_cluster_targets validation
# ---------------------------------------------------------------------------


def test_put_eval_settings_rejects_out_of_range_cluster_targets(client):
    """PUT /api/eval/settings rejects same_cluster_targets > 10."""
    resp = client.put("/api/eval/settings", json={"eval.same_cluster_targets": 99})
    assert resp.status_code == 422

    resp2 = client.put("/api/eval/settings", json={"eval.diff_cluster_targets": 0})
    assert resp2.status_code == 422


# ---------------------------------------------------------------------------
# Line 1820: f1_threshold / error_budget validation
# ---------------------------------------------------------------------------


def test_put_eval_settings_rejects_out_of_range_f1_threshold(client):
    """PUT /api/eval/settings rejects f1_threshold > 1.0."""
    resp = client.put("/api/eval/settings", json={"eval.f1_threshold": 2.5})
    assert resp.status_code == 422

    resp2 = client.put("/api/eval/settings", json={"eval.error_budget": -0.1})
    assert resp2.status_code == 422


# ---------------------------------------------------------------------------
# Line 1839: positive_threshold validation
# ---------------------------------------------------------------------------


def test_put_eval_settings_rejects_out_of_range_positive_threshold(client):
    """PUT /api/eval/settings rejects positive_threshold outside 1-5."""
    resp = client.put("/api/eval/settings", json={"eval.positive_threshold": 10})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Lines 1860-1904: POST /api/eval/schedule — create recurring eval job
# ---------------------------------------------------------------------------


def test_create_eval_schedule_daily(client_and_db):
    """POST /api/eval/schedule creates a daily recurring eval job."""
    client, _db = client_and_db
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A", "B"],
            "per_cluster": 4,
            "run_mode": "batch",
            "recurrence": "daily",
        },
    )
    assert resp.status_code == 200
    assert "job_id" in resp.json()


def test_create_eval_schedule_weekly(client_and_db):
    """POST /api/eval/schedule creates a weekly recurring eval job."""
    client, _db = client_and_db
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A"],
            "per_cluster": 2,
            "run_mode": "batch",
            "recurrence": "weekly",
        },
    )
    assert resp.status_code == 200
    assert "job_id" in resp.json()


def test_create_eval_schedule_invalid_recurrence(client):
    """POST /api/eval/schedule rejects invalid recurrence."""
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A"],
            "per_cluster": 4,
            "run_mode": "batch",
            "recurrence": "off",
        },
    )
    assert resp.status_code == 400
    assert "recurrence" in resp.json()["detail"]


def test_create_eval_schedule_invalid_variants(client):
    """POST /api/eval/schedule rejects non-alphanumeric variant names."""
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A; rm -rf /"],
            "per_cluster": 4,
            "run_mode": "batch",
            "recurrence": "daily",
        },
    )
    assert resp.status_code == 400
    assert "alphanumeric" in resp.json()["detail"]


def test_create_eval_schedule_invalid_per_cluster(client):
    """POST /api/eval/schedule rejects per_cluster outside 1-20."""
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A"],
            "per_cluster": 999,
            "run_mode": "batch",
            "recurrence": "daily",
        },
    )
    assert resp.status_code == 400


def test_create_eval_schedule_invalid_run_mode(client):
    """POST /api/eval/schedule rejects unknown run_mode."""
    resp = client.post(
        "/api/eval/schedule",
        json={
            "variants": ["A"],
            "per_cluster": 4,
            "run_mode": "invalid",
            "recurrence": "daily",
        },
    )
    assert resp.status_code == 400


def test_create_eval_schedule_duplicate_409(client_and_db):
    """POST /api/eval/schedule returns 409 if name already exists."""
    client, _db = client_and_db
    body = {
        "variants": ["A"],
        "per_cluster": 4,
        "run_mode": "batch",
        "recurrence": "daily",
    }
    resp1 = client.post("/api/eval/schedule", json=body)
    assert resp1.status_code == 200

    resp2 = client.post("/api/eval/schedule", json=body)
    assert resp2.status_code == 409
    assert "already exists" in resp2.json()["detail"]


# ---------------------------------------------------------------------------
# Lines 1937-1940: list_eval_runs — broken metrics JSON
# ---------------------------------------------------------------------------


def test_list_eval_runs_handles_broken_metrics(client_and_db):
    """list_eval_runs handles unparseable metrics JSON without crashing."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="complete")

    # Inject broken metrics JSON directly
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE eval_runs SET metrics = 'not-valid-json' WHERE id = ?", (run_id,))
        conn.commit()

    resp = client.get("/api/eval/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 1
    assert runs[0]["metrics"] is None


# ---------------------------------------------------------------------------
# Line 1985: trigger_eval_run — variants list normalisation
# ---------------------------------------------------------------------------


def test_trigger_eval_run_variants_list(client_and_db):
    """POST /api/eval/runs with variants list uses first as variant_id."""
    client, _db = client_and_db

    with (
        patch("ollama_queue.api.eval_runs.create_eval_run", return_value=1) as mock_create,
        patch("ollama_queue.api.eval_runs.update_eval_run"),
        patch("ollama_queue.api.eval_runs.run_eval_session"),
    ):
        resp = client.post(
            "/api/eval/runs",
            json={
                "variants": ["B", "C"],
                "run_mode": "batch",
                "per_cluster": 4,
                "judge_mode": "rubric",
            },
        )

    assert resp.status_code == 201
    # Verify variant_id was set to first item "B"
    call_kwargs = mock_create.call_args
    assert call_kwargs.kwargs.get("variant_id") == "B" or call_kwargs[1].get("variant_id") == "B"


# ---------------------------------------------------------------------------
# Line 2026: trigger_eval_run — judge_model from request body
# ---------------------------------------------------------------------------


def test_trigger_eval_run_sets_judge_model(client_and_db):
    """POST /api/eval/runs passes judge_model from body to update."""
    client, _db = client_and_db

    with (
        patch("ollama_queue.api.eval_runs.create_eval_run", return_value=1),
        patch("ollama_queue.api.eval_runs.update_eval_run") as mock_update,
        patch("ollama_queue.api.eval_runs.run_eval_session"),
    ):
        resp = client.post(
            "/api/eval/runs",
            json={
                "variant_id": "A",
                "run_mode": "batch",
                "judge_model": "deepseek-r1:8b",
                "judge_mode": "rubric",
            },
        )

    assert resp.status_code == 201
    # Should have called update_eval_run with judge_model
    calls = [c for c in mock_update.call_args_list if "judge_model" in (c.kwargs or {})]
    assert any(c.kwargs.get("judge_model") == "deepseek-r1:8b" for c in calls)


# ---------------------------------------------------------------------------
# Lines 2049-2050: trigger_eval_run — background thread exception
# ---------------------------------------------------------------------------


def test_trigger_eval_run_background_exception_is_logged(client_and_db):
    """Background thread exception in trigger_eval_run is logged, not raised."""
    client, _db = client_and_db

    def _raise(*a, **kw):
        raise RuntimeError("session crashed")

    with (
        patch("ollama_queue.api.eval_runs.create_eval_run", return_value=1),
        patch("ollama_queue.api.eval_runs.update_eval_run"),
        patch("ollama_queue.api.eval_runs.run_eval_session", side_effect=_raise),
        patch("threading.Thread") as mock_thread,
    ):
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        resp = client.post(
            "/api/eval/runs",
            json={
                "variant_id": "A",
                "run_mode": "batch",
                "judge_mode": "rubric",
            },
        )
        assert resp.status_code == 201

        # Get the target function and call it manually to exercise the except branch
        target_fn = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1].get("target")
        # The target wraps run_eval_session; calling it should log, not raise
        target_fn()


# ---------------------------------------------------------------------------
# Lines 2077-2078: get_eval_run_detail — broken metrics JSON
# ---------------------------------------------------------------------------


def test_get_eval_run_detail_broken_metrics(client_and_db):
    """GET /api/eval/runs/{id} handles unparseable metrics JSON."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="complete")

    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE eval_runs SET metrics = '{bad}' WHERE id = ?", (run_id,))
        conn.commit()

    resp = client.get(f"/api/eval/runs/{run_id}")
    assert resp.status_code == 200
    # Broken metrics should not crash; it logs a warning


# ---------------------------------------------------------------------------
# Lines 2129-2134: analyze_eval_run — background analysis error + double exception
# ---------------------------------------------------------------------------


def test_analyze_eval_run_background_error(client_and_db):
    """POST /api/eval/runs/{id}/analyze handles background failure gracefully."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="complete")

    def _raise_analysis(*a, **kw):
        raise RuntimeError("analysis boom")

    with (
        patch("ollama_queue.api.eval_runs.generate_eval_analysis", side_effect=_raise_analysis),
        patch("ollama_queue.api.eval_runs.update_eval_run") as mock_update,
        patch("threading.Thread") as mock_thread,
    ):
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        resp = client.post(f"/api/eval/runs/{run_id}/analyze")
        assert resp.status_code == 200

        target_fn = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1].get("target")
        target_fn()

        # Should have tried to mark analysis as failed
        assert mock_update.called


def test_analyze_eval_run_background_double_error(client_and_db):
    """POST /api/eval/runs/{id}/analyze handles double failure in background."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="complete")

    with (
        patch("ollama_queue.api.eval_runs.generate_eval_analysis", side_effect=RuntimeError("boom1")),
        patch("ollama_queue.api.eval_runs.update_eval_run", side_effect=RuntimeError("boom2")),
        patch("threading.Thread") as mock_thread,
    ):
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        resp = client.post(f"/api/eval/runs/{run_id}/analyze")
        assert resp.status_code == 200

        target_fn = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1].get("target")
        # Should not raise even when update also fails
        target_fn()


# ---------------------------------------------------------------------------
# Lines 2270-2271: get_eval_run_results — classification exception fallback
# ---------------------------------------------------------------------------


def test_get_eval_run_results_classification_exception(client_and_db):
    """classification filter falls back to default threshold on exception."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="complete")

    # Set invalid positive_threshold in settings to trigger exception
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("eval.positive_threshold", '"not-an-int"'),
        )
        conn.commit()

    resp = client.get(f"/api/eval/runs/{run_id}/results?classification=fp")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Lines 2279-2280: get_eval_run_results — tn classification
# ---------------------------------------------------------------------------


def test_get_eval_run_results_tn_classification(client_and_db):
    """classification=tn filter applies correct SQL clause."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="complete")

    resp = client.get(f"/api/eval/runs/{run_id}/results?classification=tn")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Lines 2312-2315: get_eval_run_progress — item_ids fallback parse error
# ---------------------------------------------------------------------------


def test_get_eval_run_progress_uses_item_ids_fallback(client_and_db):
    """Progress computes total from item_ids JSON when item_count is 0."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="generating")

    items = json.dumps(["a", "b", "c", "d"])
    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE eval_runs SET item_count = 0, item_ids = ? WHERE id = ?",
            (items, run_id),
        )
        conn.commit()

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    assert resp.json()["total"] == 4


def test_get_eval_run_progress_broken_item_ids(client_and_db):
    """Progress endpoint handles unparseable item_ids JSON."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="generating")

    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE eval_runs SET item_count = 0, item_ids = 'not-json' WHERE id = ?",
            (run_id,),
        )
        conn.commit()

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Line 2346: get_eval_run_progress — variants JSON parse for gen_model
# ---------------------------------------------------------------------------


def test_get_eval_run_progress_variants_json_fallback(client_and_db):
    """Progress endpoint parses variants JSON to extract gen_model."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="generating")

    # Set variants as a JSON list and clear variant_id to force the fallback path
    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE eval_runs SET variants = ?, variant_id = NULL, item_count = 5 WHERE id = ?",
            (json.dumps(["A", "B"]), run_id),
        )
        conn.commit()

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert "gen_model" in data


# ---------------------------------------------------------------------------
# Lines 2465-2466: repeat_eval_run — background thread exception
# ---------------------------------------------------------------------------


def test_repeat_eval_run_background_error(client_and_db):
    """repeat_eval_run background thread logs exception without raising."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="complete")

    # Set up reproducibility data
    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE eval_runs SET item_ids = ?, seed = ? WHERE id = ?",
            (json.dumps([1, 2, 3]), 42, run_id),
        )
        conn.commit()

    with (
        patch("ollama_queue.api.eval_runs.run_eval_session", side_effect=RuntimeError("repeat boom")),
        patch("threading.Thread") as mock_thread,
    ):
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        resp = client.post(f"/api/eval/runs/{run_id}/repeat")
        assert resp.status_code == 200

        target_fn = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1].get("target")
        # Should not raise
        target_fn()


# ---------------------------------------------------------------------------
# Lines 2571-2586: judge-rerun background thread error + double failure
# ---------------------------------------------------------------------------


def test_judge_rerun_background_error_marks_failed(client_and_db):
    """judge-rerun background thread sets run to failed on exception."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="complete")

    exc = RuntimeError("judge crashed")

    with (
        patch("ollama_queue.api.eval_runs.run_eval_judge", side_effect=exc),
        patch("ollama_queue.api.eval_runs.update_eval_run") as mock_update,
        patch("threading.Thread") as mock_thread,
    ):
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        resp = client.post(f"/api/eval/runs/{run_id}/judge-rerun", json={})
        assert resp.status_code == 201

        target_fn = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1].get("target")
        target_fn()

        # Should have tried to mark as failed
        fail_calls = [c for c in mock_update.call_args_list if c.kwargs.get("status") == "failed"]
        assert len(fail_calls) >= 1


def test_judge_rerun_background_double_error(client_and_db):
    """judge-rerun background thread handles double failure (judge + update)."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="complete")

    def _mock_update(*a, **kw):
        # Let the first call (setting judge_mode) succeed, but fail on
        # the 'status=failed' call
        if kw.get("status") == "failed":
            raise RuntimeError("update also failed")

    with (
        patch("ollama_queue.api.eval_runs.run_eval_judge", side_effect=RuntimeError("judge crash")),
        patch("ollama_queue.api.eval_runs.update_eval_run", side_effect=_mock_update),
        patch("threading.Thread") as mock_thread,
    ):
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        resp = client.post(f"/api/eval/runs/{run_id}/judge-rerun", json={})
        assert resp.status_code == 201

        target_fn = mock_thread.call_args.kwargs.get("target") or mock_thread.call_args[1].get("target")
        # Should not raise
        target_fn()


# ---------------------------------------------------------------------------
# Line 2612: include_consumer — consumer not found
# ---------------------------------------------------------------------------


def test_include_consumer_not_found(client):
    """POST /api/consumers/{id}/include returns 404 for missing consumer."""
    resp = client.post("/api/consumers/9999/include", json={"restart_policy": "deferred"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Line 2621: include_consumer — windows platform
# ---------------------------------------------------------------------------


def test_include_consumer_windows_returns_422(client_and_db):
    """POST /api/consumers/{id}/include rejects Windows consumers."""
    client, db = client_and_db
    cid = _seed_consumer(db, platform="windows")
    resp = client.post(f"/api/consumers/{cid}/include", json={"restart_policy": "deferred"})
    assert resp.status_code == 422
    assert "Windows" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Lines 2641-2642: include_consumer — patch_consumer exception
# ---------------------------------------------------------------------------


def test_include_consumer_patch_exception(client_and_db):
    """POST /api/consumers/{id}/include returns 500 when patch_consumer fails."""
    client, db = client_and_db
    cid = _seed_consumer(db, patch_path="/home/fake/test.env")
    with patch("ollama_queue.api.consumers.patch_consumer", side_effect=RuntimeError("patch failed")):
        resp = client.post(f"/api/consumers/{cid}/include", json={"restart_policy": "deferred"})
    assert resp.status_code == 500
    assert "Patch failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Lines 2658-2659: include_consumer — health check exception in background
# ---------------------------------------------------------------------------


def test_include_consumer_health_check_exception(client_and_db):
    """Health check thread exception after patching is logged, not raised."""
    client, db = client_and_db
    cid = _seed_consumer(db, patch_path="/home/fake/test.env")

    with (
        patch(
            "ollama_queue.api.consumers.patch_consumer",
            return_value={"patch_applied": True, "status": "patched", "patch_type": "env_file"},
        ),
        patch("ollama_queue.api.consumers.check_health", side_effect=RuntimeError("health boom")),
        patch("threading.Thread") as mock_thread,
    ):
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        resp = client.post(f"/api/consumers/{cid}/include", json={"restart_policy": "deferred"})
        assert resp.status_code == 200

        # Find the health check thread call and exercise it
        health_calls = [c for c in mock_thread.call_args_list if c.kwargs.get("target")]
        if health_calls:
            target_fn = health_calls[-1].kwargs["target"]
            target_fn()  # Should not raise


# ---------------------------------------------------------------------------
# Line 2669: ignore_consumer — not found
# ---------------------------------------------------------------------------


def test_ignore_consumer_not_found(client):
    """POST /api/consumers/{id}/ignore returns 404 for missing consumer."""
    resp = client.post("/api/consumers/9999/ignore")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Lines 2677, 2680-2682: revert_consumer — not found + exception
# ---------------------------------------------------------------------------


def test_revert_consumer_not_found(client):
    """POST /api/consumers/{id}/revert returns 404 for missing consumer."""
    resp = client.post("/api/consumers/9999/revert")
    assert resp.status_code == 404


def test_revert_consumer_exception(client_and_db):
    """POST /api/consumers/{id}/revert returns 500 when revert fails."""
    client, db = client_and_db
    cid = _seed_consumer(db, status="patched", patch_applied=1)
    with patch("ollama_queue.api.consumers.revert_consumer", side_effect=RuntimeError("revert boom")):
        resp = client.post(f"/api/consumers/{cid}/revert")
    assert resp.status_code == 500
    assert "revert" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Line 2690: consumer_health — not found
# ---------------------------------------------------------------------------


def test_consumer_health_not_found(client):
    """GET /api/consumers/{id}/health returns 404 for missing consumer."""
    resp = client.get("/api/consumers/9999/health")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Lines 2697-2713: intercept_enable — non-Linux, no consumers, success
# ---------------------------------------------------------------------------


def test_intercept_enable_non_linux(client):
    """POST /api/consumers/intercept/enable returns 422 on non-Linux."""
    with patch("platform.system", return_value="Darwin"):
        resp = client.post("/api/consumers/intercept/enable")
    assert resp.status_code == 422
    assert "Linux" in resp.json()["detail"]


def test_intercept_enable_no_included_consumers(client):
    """POST /api/consumers/intercept/enable returns 422 with no included consumers."""
    with patch("platform.system", return_value="Linux"):
        resp = client.post("/api/consumers/intercept/enable")
    assert resp.status_code == 422
    assert "at least one" in resp.json()["detail"].lower()


def test_intercept_enable_iptables_failure(client_and_db):
    """POST /api/consumers/intercept/enable returns 422 if iptables fails."""
    client, db = client_and_db
    _seed_consumer(db, status="patched")

    with (
        patch("platform.system", return_value="Linux"),
        patch(
            "ollama_queue.api.consumers.enable_intercept", return_value={"enabled": False, "error": "iptables failed"}
        ),
    ):
        resp = client.post("/api/consumers/intercept/enable")
    assert resp.status_code == 422


def test_intercept_enable_success(client_and_db):
    """POST /api/consumers/intercept/enable succeeds with included consumer."""
    client, db = client_and_db
    _seed_consumer(db, status="patched")

    with (
        patch("platform.system", return_value="Linux"),
        patch("os.getuid", return_value=1000),
        patch("ollama_queue.api.consumers.enable_intercept", return_value={"enabled": True}),
    ):
        resp = client.post("/api/consumers/intercept/enable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


# ---------------------------------------------------------------------------
# Lines 2717-2722: intercept_disable
# ---------------------------------------------------------------------------


def test_intercept_disable_success(client_and_db):
    """POST /api/consumers/intercept/disable succeeds."""
    client, _db = client_and_db
    with patch("ollama_queue.api.consumers.disable_intercept", return_value={"enabled": False}):
        resp = client.post("/api/consumers/intercept/disable")
    assert resp.status_code == 200


def test_intercept_disable_error(client_and_db):
    """POST /api/consumers/intercept/disable returns 500 on iptables error."""
    client, _db = client_and_db
    with patch(
        "ollama_queue.api.consumers.disable_intercept",
        return_value={"enabled": True, "error": "iptables -D failed"},
    ):
        resp = client.post("/api/consumers/intercept/disable")
    assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Lines 2726-2727: intercept_status
# ---------------------------------------------------------------------------


def test_intercept_status(client_and_db):
    """GET /api/consumers/intercept/status returns status."""
    client, _db = client_and_db
    with patch(
        "ollama_queue.api.consumers.get_intercept_status",
        return_value={"enabled": False, "rules": []},
    ):
        resp = client.get("/api/consumers/intercept/status")
    assert resp.status_code == 200
    assert "enabled" in resp.json()


# ---------------------------------------------------------------------------
# Lines 2732-2748: SPA static file serving
# ---------------------------------------------------------------------------


def test_spa_static_serves_file(tmp_path):
    """GET /ui/{path} serves files from the SPA dist directory.

    Creates a real dist directory so create_app registers the SPA routes,
    then tests file serving, index fallback, and the null-byte guard.
    """
    import ollama_queue.app as app_mod

    # Create the dist directory at the path create_app expects (app.py is in ollama_queue/)
    real_spa_dir = Path(app_mod.__file__).parent / "dashboard" / "spa" / "dist"
    created = False
    if not real_spa_dir.exists():
        real_spa_dir.mkdir(parents=True, exist_ok=True)
        created = True

    try:
        # Write test files
        index = real_spa_dir / "index.html"
        index.write_text("<html>test</html>")
        js_file = real_spa_dir / "app.js"
        js_file.write_text("console.log('test');")

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        app = create_app(db)
        client = TestClient(app)

        # Test serving an existing file (line 2740)
        resp = client.get("/ui/app.js")
        assert resp.status_code == 200

        # Test fallback to index.html for unknown paths (lines 2742-2745)
        resp = client.get("/ui/nonexistent/route")
        assert resp.status_code == 200

        # Test empty path (line 2738: path is empty string)
        resp = client.get("/ui/")
        assert resp.status_code == 200

    finally:
        # Clean up created files
        for f in (real_spa_dir / "app.js", real_spa_dir / "index.html"):
            if f.exists():
                f.unlink()
        if created and real_spa_dir.exists():
            # Only remove if we created it; remove dirs up to dashboard level
            import shutil

            shutil.rmtree(real_spa_dir, ignore_errors=True)


def test_spa_static_null_byte_guard(tmp_path):
    """SPA static route returns 404 for paths containing null bytes (line 2737).

    TestClient refuses null bytes in URLs, so we call the route handler directly.
    """
    import asyncio

    import ollama_queue.app as app_mod

    real_spa_dir = Path(app_mod.__file__).parent / "dashboard" / "spa" / "dist"
    created = False
    if not real_spa_dir.exists():
        real_spa_dir.mkdir(parents=True, exist_ok=True)
        created = True

    try:
        index = real_spa_dir / "index.html"
        index.write_text("<html>test</html>")

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        app = create_app(db)

        # Find the spa_static route handler
        spa_handler = None
        for route in app.routes:
            if hasattr(route, "path") and route.path == "/ui/{path:path}":
                spa_handler = route.endpoint
                break

        assert spa_handler is not None, "spa_static route not found"

        # Call with a null-byte path
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(spa_handler("foo\x00bar"))
        finally:
            loop.close()
        assert result.status_code == 404
    finally:
        for f in [real_spa_dir / "index.html"]:
            if f.exists():
                f.unlink()
        if created and real_spa_dir.exists():
            import shutil

            shutil.rmtree(real_spa_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Lines 2756-2757: _startup_scan exception
# ---------------------------------------------------------------------------


def test_startup_scan_exception_logged(tmp_path):
    """Startup scan exception is logged without crashing app creation."""
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    with patch("ollama_queue.app.run_scan", side_effect=RuntimeError("scan failed")):
        # App creation should not raise even when startup scan fails
        app = create_app(db)
        assert app is not None
