"""Coverage tests for api.py lines 983-1671.

Targets specific uncovered lines identified by coverage analysis.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import Database


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


# ---------------------------------------------------------------------------
# Line 983: performance_curve.fit(points) — branch where points is non-empty
# ---------------------------------------------------------------------------


def test_performance_curve_fit_called_with_points(client_and_db):
    """GET /api/metrics/performance-curve calls curve.fit when stats have data.

    Covers line 983.
    """
    client, db = client_and_db
    fake_stats = {
        "qwen2.5:7b": {
            "model_size_gb": 4.5,
            "avg_tok_per_min": 300,
            "avg_warmup_s": 2.0,
        },
        "llama3:8b": {
            "model_size_gb": 5.0,
            "avg_tok_per_min": 280,
            "avg_warmup_s": 3.0,
        },
    }
    with patch.object(db, "get_model_stats", return_value=fake_stats):
        resp = client.get("/api/metrics/performance-curve")
    assert resp.status_code == 200
    data = resp.json()
    assert "tok_slope" in data


# ---------------------------------------------------------------------------
# Line 1011: resume_deferred — deferral not found raises 404
# ---------------------------------------------------------------------------


def test_resume_deferred_not_found(client):
    """POST /api/deferred/{id}/resume raises 404 when deferral doesn't exist.

    Covers line 1011.
    """
    resp = client.post("/api/deferred/999999/resume")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Lines 1047-1062: catalog search with q param (live fetch + cache + error)
# ---------------------------------------------------------------------------


def test_catalog_search_live_fetch(client):
    """GET /api/models/catalog?q=... fetches from ollama.com and caches.

    Covers lines 1047-1060.
    """
    from ollama_queue.api import models as api_module

    # Clear cache to force a live fetch
    api_module._catalog_cache.clear()

    fake_results = [{"name": f"model{i}"} for i in range(12)]

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(fake_results).encode()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        resp = client.get("/api/models/catalog?q=llama")

    assert resp.status_code == 200
    data = resp.json()
    assert "search_results" in data
    # Truncated to 10
    assert len(data["search_results"]) == 10
    assert "curated" in data


def test_catalog_search_uses_cache(client):
    """GET /api/models/catalog?q=... returns cached results on second call.

    Covers lines 1052-1054.
    """
    from ollama_queue.api import models as api_module

    cached_results = [{"name": "cached-model"}]
    api_module._catalog_cache["cached-query"] = (cached_results, time.time() + 3600)

    resp = client.get("/api/models/catalog?q=cached-query")
    assert resp.status_code == 200
    assert resp.json()["search_results"] == cached_results

    # Cleanup
    api_module._catalog_cache.pop("cached-query", None)


def test_catalog_search_error_logged(client):
    """GET /api/models/catalog?q=... logs warning on fetch failure.

    Covers lines 1061-1062.
    """
    from ollama_queue.api import models as api_module

    api_module._catalog_cache.clear()

    with patch("urllib.request.urlopen", side_effect=Exception("connection timeout")):
        resp = client.get("/api/models/catalog?q=broken-search")

    assert resp.status_code == 200
    # search_results is empty on failure, curated still present
    data = resp.json()
    assert data["search_results"] == []
    assert "curated" in data


# ---------------------------------------------------------------------------
# Line 1069: pull model — empty model string returns 400
# ---------------------------------------------------------------------------


def test_pull_model_empty_string_returns_400(client):
    """POST /api/models/pull with empty model returns 400.

    Covers line 1069.
    """
    resp = client.post("/api/models/pull", json={"model": ""})
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"].lower()


def test_pull_model_whitespace_only_returns_400(client):
    """POST /api/models/pull with whitespace-only model returns 400.

    Covers line 1069.
    """
    resp = client.post("/api/models/pull", json={"model": "   "})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Lines 1075-1078: get pull status — success and 404
# ---------------------------------------------------------------------------


def test_get_pull_status_success(client):
    """GET /api/models/pull/{id} returns status when pull exists.

    Covers lines 1075-1076 (happy path).
    """
    fake_status = {"id": 1, "model": "test-model", "status": "pulling", "progress_pct": 50.0}
    with patch("ollama_queue.api.models.OllamaModels") as mock_om:
        mock_om.return_value.get_pull_status.return_value = fake_status
        resp = client.get("/api/models/pull/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "test-model"
    assert data["status"] == "pulling"


def test_get_pull_status_not_found(client):
    """GET /api/models/pull/{id} returns 404 for non-existent pull.

    Covers lines 1076-1078.
    """
    with patch("ollama_queue.api.models.OllamaModels") as mock_om:
        mock_om.return_value.get_pull_status.return_value = {"error": "not found"}
        resp = client.get("/api/models/pull/999999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Lines 1082-1083: cancel pull
# ---------------------------------------------------------------------------


def test_cancel_pull(client_and_db):
    """DELETE /api/models/pull/{id} cancels a pull.

    Covers lines 1082-1083.
    """
    client, db = client_and_db
    # Insert a pull record with a fake pid
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO model_pulls (model, status, progress_pct, started_at, pid) VALUES (?,?,?,?,?)",
            ("cancel-me", "pulling", 30.0, time.time(), 99999),
        )
        conn.commit()
        pull_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # os.kill will fail since pid doesn't exist, but cancel_pull catches that
    resp = client.delete(f"/api/models/pull/{pull_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "cancelled" in data


def test_cancel_pull_no_pid(client_and_db):
    """DELETE /api/models/pull/{id} returns cancelled=false when no pid.

    Covers lines 1082-1083.
    """
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO model_pulls (model, status, progress_pct, started_at) VALUES (?,?,?,?)",
            ("no-pid", "pulling", 0.0, time.time()),
        )
        conn.commit()
        pull_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    resp = client.delete(f"/api/models/pull/{pull_id}")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is False


# ---------------------------------------------------------------------------
# Lines 1128-1141: list_eval_variants — metrics parsing for latest_f1 (bayesian + legacy)
# ---------------------------------------------------------------------------


def test_list_variants_latest_f1_from_legacy_run(client_and_db):
    """list_eval_variants extracts F1 from legacy (non-bayesian) runs.

    Covers lines 1128-1141: metrics parsing loop, is_bayesian=False branch.
    """
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, judge_mode) "
            "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', ?, 'pairwise')",
            (100, json.dumps({"A": {"f1": 0.85, "precision": 0.9, "recall": 0.8}})),
        )
        conn.commit()

    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    a_variant = next(v for v in variants if v["id"] == "A")
    assert a_variant["latest_f1"] == 0.85


def test_list_variants_latest_f1_from_bayesian_run(client_and_db):
    """list_eval_variants uses AUC for bayesian judge_mode.

    Covers lines 1134, 1139: is_bayesian=True branch, auc extraction.
    """
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, judge_mode) "
            "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', ?, 'bayesian')",
            (101, json.dumps({"A": {"auc": 0.92, "f1": 0.5}})),
        )
        conn.commit()

    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    a_variant = next(v for v in variants if v["id"] == "A")
    # Should use AUC (0.92), not F1 (0.5)
    assert a_variant["latest_f1"] == 0.92


def test_list_variants_skips_invalid_metrics(client_and_db):
    """list_eval_variants skips runs with null/invalid metrics.

    Covers lines 1128-1133: null metrics check, JSON parse error handling.
    """
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        # Run with null metrics
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, judge_mode) "
            "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', NULL, 'pairwise')",
            (110,),
        )
        # Run with invalid JSON
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, judge_mode) "
            "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', 'not-json', 'pairwise')",
            (111,),
        )
        # Run with non-dict variant metrics
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, judge_mode) "
            "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', ?, 'pairwise')",
            (112, json.dumps({"A": "not-a-dict"})),
        )
        conn.commit()

    resp = client.get("/api/eval/variants")
    assert resp.status_code == 200
    variants = resp.json()
    a_variant = next(v for v in variants if v["id"] == "A")
    assert a_variant["latest_f1"] is None


# ---------------------------------------------------------------------------
# Lines 1207-1222: import_eval_variants — template import loop
# ---------------------------------------------------------------------------


def test_import_with_templates(client):
    """POST /api/eval/variants/import creates templates from payload.

    Covers lines 1207-1222.
    """
    resp = client.post(
        "/api/eval/variants/import",
        json={
            "templates": [
                {
                    "id": "imported-tmpl-1",
                    "label": "Imported Template",
                    "instruction": "Do the thing",
                    "format_spec": "json",
                    "examples": "ex1",
                    "is_chunked": 0,
                }
            ],
            "variants": [],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["templates_imported"] == 1
    assert data["variants_imported"] == 0


def test_import_templates_uses_defaults(client):
    """Import templates with minimal fields uses defaults for missing fields.

    Covers lines 1207-1222: tmpl.get("is_chunked", 0), tmpl.get("created_at") or now.
    """
    resp = client.post(
        "/api/eval/variants/import",
        json={
            "templates": [
                {
                    "id": "minimal-tmpl",
                    "label": "Minimal",
                    "instruction": "test",
                }
            ],
            "variants": [],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["templates_imported"] == 1


# ---------------------------------------------------------------------------
# Line 1258: generate_eval_variants — empty models list returns 400
# ---------------------------------------------------------------------------


def test_generate_variants_empty_models_400(client):
    """POST /api/eval/variants/generate with empty models list returns 400.

    Covers line 1258.
    """
    resp = client.post("/api/eval/variants/generate", json={"models": []})
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Line 1346: stability with data_source filter
# ---------------------------------------------------------------------------


def test_variant_stability_with_data_source(client_and_db):
    """GET /api/eval/variants/stability?data_source=... filters by data_source_url.

    Covers line 1346.
    """
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        # Runs with a specific data_source_url
        for run_id, f1 in [(1, 0.70), (2, 0.75), (3, 0.72)]:
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
                "VALUES (?, 'http://special:7685', '[\"A\"]', 'A', 'complete', ?)",
                (run_id, json.dumps({"A": {"f1": f1}})),
            )
        # Run with different data_source_url (should be excluded)
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
            "VALUES (?, 'http://other:9999', '[\"A\"]', 'A', 'complete', ?)",
            (10, json.dumps({"A": {"f1": 0.1}})),
        )
        conn.commit()

    resp = client.get("/api/eval/variants/stability?data_source=http://special:7685")
    assert resp.status_code == 200
    data = resp.json()
    assert "A" in data
    assert data["A"]["n_runs"] == 3


# ---------------------------------------------------------------------------
# Lines 1360-1361: stability — JSON decode error handling
# ---------------------------------------------------------------------------


def test_variant_stability_skips_bad_json(client_and_db):
    """stability endpoint skips runs with invalid JSON metrics.

    Covers lines 1360-1361.
    """
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
            "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', 'broken-json')",
            (200,),
        )
        conn.commit()

    resp = client.get("/api/eval/variants/stability")
    assert resp.status_code == 200
    assert resp.json() == {}


# ---------------------------------------------------------------------------
# Lines 1407-1416: variant history — metrics parsing loop
# ---------------------------------------------------------------------------


def test_variant_history_with_runs(client_and_db):
    """GET /api/eval/variants/{id}/history returns parsed metrics per run.

    Covers lines 1407-1416.
    """
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        # Run with good metrics for variant A
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, started_at) "
            "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', ?, '2026-01-01T00:00:00')",
            (300, json.dumps({"A": {"f1": 0.88, "recall": 0.90, "precision": 0.86}})),
        )
        # Run with null metrics (should be skipped)
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, started_at) "
            "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', NULL, '2026-01-02T00:00:00')",
            (301,),
        )
        # Run with invalid JSON metrics (should be skipped)
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, started_at) "
            "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', 'bad', '2026-01-03T00:00:00')",
            (302,),
        )
        # Run where variant A is missing from metrics (should be skipped)
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, started_at) "
            "VALUES (?, 'http://localhost:7685', '[\"B\"]', 'B', 'complete', ?, '2026-01-04T00:00:00')",
            (303, json.dumps({"B": {"f1": 0.5}})),
        )
        # Run where variant A metrics is not a dict
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics, started_at) "
            "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', ?, '2026-01-05T00:00:00')",
            (304, json.dumps({"A": "string-not-dict"})),
        )
        conn.commit()

    resp = client.get("/api/eval/variants/A/history")
    assert resp.status_code == 200
    history = resp.json()
    # Only the first run (300) should appear
    assert len(history) == 1
    assert history[0]["run_id"] == 300
    assert history[0]["f1"] == 0.88
    assert history[0]["recall"] == 0.90
    assert history[0]["precision"] == 0.86


# ---------------------------------------------------------------------------
# Line 1479: update_eval_variant — no updatable fields returns current row
# ---------------------------------------------------------------------------


def test_update_variant_no_updatable_fields(client_and_db):
    """PUT /api/eval/variants/{id} with no updatable fields returns current row unchanged.

    Covers line 1479.
    """
    client, _db = client_and_db
    create_resp = client.post(
        "/api/eval/variants",
        json={
            "label": "No change test",
            "prompt_template_id": "zero-shot-causal",
            "model": "qwen2.5:7b",
        },
    )
    var_id = create_resp.json()["id"]

    # Send body with no updatable fields
    resp = client.put(f"/api/eval/variants/{var_id}", json={"not_a_field": "ignored"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "No change test"


# ---------------------------------------------------------------------------
# Line 1482: update_eval_variant — validates prompt_template_id
# ---------------------------------------------------------------------------


def test_update_variant_validates_template_id(client_and_db):
    """PUT with bad prompt_template_id returns 404.

    Covers line 1482.
    """
    client, _db = client_and_db
    create_resp = client.post(
        "/api/eval/variants",
        json={
            "label": "Template validation test",
            "prompt_template_id": "zero-shot-causal",
            "model": "qwen2.5:7b",
        },
    )
    var_id = create_resp.json()["id"]

    resp = client.put(
        f"/api/eval/variants/{var_id}",
        json={"prompt_template_id": "non-existent-template"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Line 1533: update_eval_template — no updatable fields returns current row
# ---------------------------------------------------------------------------


def test_update_template_no_updatable_fields(client_and_db):
    """PUT /api/eval/templates/{id} with no updatable fields returns current template.

    Covers line 1533.
    """
    client, _db = client_and_db
    # Clone a system template to get a user template
    clone_resp = client.post("/api/eval/templates/fewshot/clone", json={})
    tmpl_id = clone_resp.json()["id"]

    resp = client.put(f"/api/eval/templates/{tmpl_id}", json={"garbage": "stuff"})
    assert resp.status_code == 200
    assert resp.json()["id"] == tmpl_id


# ---------------------------------------------------------------------------
# Lines 1605, 1608-1609, 1614: get_eval_trends — metrics parsing
# ---------------------------------------------------------------------------


def _seed_trend_runs(db, runs):
    """Helper to insert eval_runs for trends tests."""
    with db._lock:
        conn = db._connect()
        for r in runs:
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, "
                "metrics, started_at, item_ids, item_count, judge_mode) "
                "VALUES (?, 'http://localhost:7685', ?, ?, 'complete', ?, ?, ?, ?, ?)",
                (
                    r["id"],
                    json.dumps(r.get("variants_list", ["A"])),
                    r.get("variant_id", "A"),
                    r.get("metrics"),
                    r.get("started_at", "2026-01-01T00:00:00"),
                    r.get("item_ids"),
                    r.get("item_count", 10),
                    r.get("judge_mode", "pairwise"),
                ),
            )
        conn.commit()


def test_trends_skips_null_metrics(client_and_db):
    """Trends endpoint skips runs with null metrics.

    Covers line 1605.
    """
    client, db = client_and_db
    _seed_trend_runs(db, [{"id": 1, "metrics": None}])

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    data = resp.json()
    assert data["variants"] == {}


def test_trends_skips_invalid_json_metrics(client_and_db):
    """Trends endpoint skips runs with unparseable JSON metrics.

    Covers lines 1608-1609.
    """
    client, db = client_and_db
    _seed_trend_runs(db, [{"id": 1, "metrics": "not-valid-json"}])

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["variants"] == {}


def test_trends_skips_non_dict_variant_metrics(client_and_db):
    """Trends endpoint skips variant entries that aren't dicts.

    Covers line 1614.
    """
    client, db = client_and_db
    _seed_trend_runs(db, [{"id": 1, "metrics": json.dumps({"A": "not-a-dict"})}])

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["variants"] == {}


# ---------------------------------------------------------------------------
# Lines 1635-1637: get_eval_trends — agreement_rows processing
# ---------------------------------------------------------------------------


def test_trends_agreement_rate(client_and_db):
    """Trends endpoint includes judge_agreement_rate from eval_results.

    Covers lines 1635-1637.
    """
    client, db = client_and_db
    metrics = {"A": {"f1": 0.8, "recall": 0.9, "precision": 0.7}}
    _seed_trend_runs(
        db,
        [
            {"id": 1, "metrics": json.dumps(metrics), "started_at": "2026-01-01T00:00:00"},
        ],
    )

    # Insert eval_results to populate agreement counts
    with db._lock:
        conn = db._connect()
        for i in range(5):
            # 3 results with score_transfer > 1 (agreed), 2 with score_transfer <= 1
            score = 3 if i < 3 else 1
            conn.execute(
                "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
                "row_type, score_transfer, is_same_cluster) VALUES (?, 'A', ?, ?, 'pair', ?, 1)",
                (1, f"src_{i}", f"tgt_{i}", score),
            )
        conn.commit()

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    data = resp.json()
    a_data = data["variants"]["A"]
    # 3 out of 5 agreed = 0.6
    assert a_data["judge_agreement_rate"] == 0.6


# ---------------------------------------------------------------------------
# Lines 1653-1657: trends — stability calculation (>= 3 quality values)
# ---------------------------------------------------------------------------


def test_trends_stability_statistics_error(client_and_db):
    """Trends stability falls back to None on StatisticsError.

    Covers lines 1656-1657.
    """
    import statistics

    client, db = client_and_db
    runs = []
    for i, f1 in enumerate([0.80, 0.82, 0.81]):
        runs.append(
            {
                "id": i + 1,
                "metrics": json.dumps({"A": {"f1": f1}}),
                "started_at": f"2026-01-0{i + 1}T00:00:00",
            }
        )
    _seed_trend_runs(db, runs)

    with patch("statistics.stdev", side_effect=statistics.StatisticsError("mock error")):
        resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    data = resp.json()
    assert data["variants"]["A"]["stability"] is None


def test_trends_stability_with_enough_runs(client_and_db):
    """Trends stability is computed from last 3 quality values when >= 3 runs.

    Covers lines 1653-1657.
    """
    client, db = client_and_db
    runs = []
    for i, f1 in enumerate([0.80, 0.82, 0.81, 0.83]):
        runs.append(
            {
                "id": i + 1,
                "metrics": json.dumps({"A": {"f1": f1}}),
                "started_at": f"2026-01-0{i + 1}T00:00:00",
            }
        )
    _seed_trend_runs(db, runs)

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    data = resp.json()
    a_data = data["variants"]["A"]
    assert a_data["stability"] is not None
    # Stability should be close to 1.0 (low stdev)
    assert a_data["stability"] > 0.9


def test_trends_stability_none_with_fewer_than_3(client_and_db):
    """Trends stability is None when fewer than 3 quality values.

    Covers line 1652 (len check fails).
    """
    client, db = client_and_db
    runs = [
        {"id": 1, "metrics": json.dumps({"A": {"f1": 0.8}}), "started_at": "2026-01-01T00:00:00"},
        {"id": 2, "metrics": json.dumps({"A": {"f1": 0.85}}), "started_at": "2026-01-02T00:00:00"},
    ]
    _seed_trend_runs(db, runs)

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    data = resp.json()
    assert data["variants"]["A"]["stability"] is None


# ---------------------------------------------------------------------------
# Lines 1662-1671: trends — trend direction calculation
# ---------------------------------------------------------------------------


def test_trends_improving_direction(client_and_db):
    """Trends direction is 'improving' when slope > 0.02.

    Covers lines 1662-1669.
    """
    client, db = client_and_db
    runs = []
    for i, f1 in enumerate([0.50, 0.60, 0.70, 0.80, 0.90]):
        runs.append(
            {
                "id": i + 1,
                "metrics": json.dumps({"A": {"f1": f1}}),
                "started_at": f"2026-01-0{i + 1}T00:00:00",
            }
        )
    _seed_trend_runs(db, runs)

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["variants"]["A"]["trend_direction"] == "improving"


def test_trends_regressing_direction(client_and_db):
    """Trends direction is 'regressing' when slope < -0.02.

    Covers lines 1670-1671.
    """
    client, db = client_and_db
    runs = []
    for i, f1 in enumerate([0.90, 0.80, 0.70, 0.60, 0.50]):
        runs.append(
            {
                "id": i + 1,
                "metrics": json.dumps({"A": {"f1": f1}}),
                "started_at": f"2026-01-0{i + 1}T00:00:00",
            }
        )
    _seed_trend_runs(db, runs)

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["variants"]["A"]["trend_direction"] == "regressing"


def test_trends_stable_direction(client_and_db):
    """Trends direction is 'stable' when slope is near zero.

    Covers lines 1662-1668 (default branch).
    """
    client, db = client_and_db
    runs = []
    for i, f1 in enumerate([0.80, 0.80, 0.80]):
        runs.append(
            {
                "id": i + 1,
                "metrics": json.dumps({"A": {"f1": f1}}),
                "started_at": f"2026-01-0{i + 1}T00:00:00",
            }
        )
    _seed_trend_runs(db, runs)

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["variants"]["A"]["trend_direction"] == "stable"


def test_trends_single_run_stable(client_and_db):
    """Trends with only 1 run defaults to 'stable' direction.

    Covers line 1660-1661 (len < 2, skips slope calc).
    """
    client, db = client_and_db
    _seed_trend_runs(
        db,
        [
            {
                "id": 1,
                "metrics": json.dumps({"A": {"f1": 0.80}}),
                "started_at": "2026-01-01T00:00:00",
            }
        ],
    )

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["variants"]["A"]["trend_direction"] == "stable"


# ---------------------------------------------------------------------------
# Trends: bayesian mode uses AUC as quality key
# ---------------------------------------------------------------------------


def test_trends_uses_auc_for_bayesian(client_and_db):
    """Trends uses AUC instead of F1 for bayesian judge_mode.

    Covers line 1645 (has_bayesian check) and lines 1646-1648 (auc key).
    """
    client, db = client_and_db
    runs = []
    for i, auc in enumerate([0.50, 0.60, 0.70, 0.80, 0.90]):
        runs.append(
            {
                "id": i + 1,
                "metrics": json.dumps({"A": {"auc": auc, "f1": 0.1}}),
                "started_at": f"2026-01-0{i + 1}T00:00:00",
                "judge_mode": "bayesian",
            }
        )
    _seed_trend_runs(db, runs)

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    a_data = resp.json()["variants"]["A"]
    # Should use AUC, so latest is 0.90 (not F1 0.1)
    assert a_data["latest_f1"] == 0.90
    assert a_data["trend_direction"] == "improving"


# ---------------------------------------------------------------------------
# Trends: item_sets_differ
# ---------------------------------------------------------------------------


def test_trends_item_sets_differ(client_and_db):
    """Trends item_sets_differ is True when runs have different item_ids.

    Covers line 1682 (item_sets_differ calculation).
    """
    client, db = client_and_db
    runs = [
        {
            "id": 1,
            "metrics": json.dumps({"A": {"f1": 0.8}}),
            "item_ids": '["a","b"]',
            "started_at": "2026-01-01T00:00:00",
        },
        {
            "id": 2,
            "metrics": json.dumps({"A": {"f1": 0.85}}),
            "item_ids": '["c","d"]',
            "started_at": "2026-01-02T00:00:00",
        },
    ]
    _seed_trend_runs(db, runs)

    resp = client.get("/api/eval/trends")
    assert resp.status_code == 200
    assert resp.json()["item_sets_differ"] is True
