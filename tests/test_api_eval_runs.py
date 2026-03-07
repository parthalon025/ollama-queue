"""Tests for eval run lifecycle API endpoints (Task 4).

Covers: GET/POST /api/eval/runs, GET/DELETE /api/eval/runs/{id},
        GET /api/eval/runs/{id}/results, GET /api/eval/runs/{id}/progress,
        POST /api/eval/runs/{id}/judge-rerun.
"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ollama_queue.api import create_app
from ollama_queue.db import Database
from ollama_queue.eval_engine import create_eval_run, insert_eval_result, update_eval_run


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


def _make_run(db: Database, variant_id: str = "A", status: str = "queued") -> int:
    """Helper: create an eval run row and optionally set status."""
    run_id = create_eval_run(db, variant_id=variant_id)
    if status != "queued":
        update_eval_run(db, run_id, status=status)
    return run_id


# ---------------------------------------------------------------------------
# GET /api/eval/runs
# ---------------------------------------------------------------------------


def test_list_eval_runs_returns_empty_list(client):
    """Returns an empty list when no runs exist."""
    resp = client.get("/api/eval/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_eval_runs_returns_list(client_and_db):
    """Returns a list of runs with expected fields."""
    client, db = client_and_db
    _make_run(db, variant_id="A")
    _make_run(db, variant_id="B")

    resp = client.get("/api/eval/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 2
    # Returned in reverse creation order (most recent first)
    assert runs[0]["variant_id"] == "B"
    assert runs[1]["variant_id"] == "A"


def test_list_eval_runs_has_required_fields(client_and_db):
    """Each run dict has the required response fields."""
    client, db = client_and_db
    _make_run(db)

    resp = client.get("/api/eval/runs")
    assert resp.status_code == 200
    run = resp.json()[0]
    required = {
        "id",
        "status",
        "variant_id",
        "winner_variant",
        "metrics",
        "completed_at",
        "item_count",
        "item_ids",
        "started_at",
        "judge_model",
        "analysis_md",
        "scheduled_by",
        "run_mode",
    }
    assert required.issubset(run.keys())


def test_list_eval_runs_pagination(client_and_db):
    """limit and offset params control pagination."""
    client, db = client_and_db
    for _ in range(5):
        _make_run(db)

    resp = client.get("/api/eval/runs?limit=2&offset=0")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    resp2 = client.get("/api/eval/runs?limit=2&offset=2")
    assert resp2.status_code == 200
    assert len(resp2.json()) == 2

    # IDs should be different pages
    ids_page1 = {r["id"] for r in resp.json()}
    ids_page2 = {r["id"] for r in resp2.json()}
    assert ids_page1.isdisjoint(ids_page2)


# ---------------------------------------------------------------------------
# POST /api/eval/runs
# ---------------------------------------------------------------------------


def test_post_eval_runs_creates_run_and_returns_run_id(client):
    """POST /api/eval/runs creates a run and returns run_id."""
    resp = client.post("/api/eval/runs", json={"variant_id": "A", "run_mode": "batch"})
    assert resp.status_code == 201
    data = resp.json()
    assert "run_id" in data
    assert isinstance(data["run_id"], int)
    assert data["run_id"] > 0


def test_post_eval_runs_run_appears_in_list(client):
    """Run created via POST appears in GET /api/eval/runs."""
    resp = client.post("/api/eval/runs", json={"variant_id": "A"})
    assert resp.status_code == 201
    run_id = resp.json()["run_id"]

    list_resp = client.get("/api/eval/runs")
    ids = [r["id"] for r in list_resp.json()]
    assert run_id in ids


def test_post_eval_runs_missing_variant_id_returns_400(client):
    """POST without variant_id returns 400."""
    resp = client.post("/api/eval/runs", json={"run_mode": "batch"})
    assert resp.status_code == 400


def test_post_eval_runs_unknown_variant_returns_404(client):
    """POST with a variant_id that doesn't exist returns 404."""
    resp = client.post("/api/eval/runs", json={"variant_id": "nonexistent-variant-xyz"})
    assert resp.status_code == 404


def test_post_eval_runs_invalid_run_mode_returns_400(client):
    """POST with an invalid run_mode returns 400."""
    resp = client.post("/api/eval/runs", json={"variant_id": "A", "run_mode": "invalid-mode"})
    assert resp.status_code == 400


def test_post_eval_runs_optional_fields_accepted(client):
    """POST accepts optional cluster_id and label fields."""
    resp = client.post(
        "/api/eval/runs",
        json={"variant_id": "A", "cluster_id": "cluster-1", "label": "Test label"},
    )
    assert resp.status_code == 201
    run_id = resp.json()["run_id"]

    detail = client.get(f"/api/eval/runs/{run_id}")
    assert detail.status_code == 200
    data = detail.json()
    assert data.get("label") == "Test label"
    assert data.get("cluster_id") == "cluster-1"


# ---------------------------------------------------------------------------
# GET /api/eval/runs/{run_id}
# ---------------------------------------------------------------------------


def test_get_eval_run_detail_returns_run(client_and_db):
    """GET /api/eval/runs/{id} returns full run detail."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="B")

    resp = client.get(f"/api/eval/runs/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == run_id
    assert data["variant_id"] == "B"
    assert data["status"] == "queued"


def test_get_eval_run_detail_returns_404_for_unknown_id(client):
    """GET /api/eval/runs/{id} returns 404 for non-existent run."""
    resp = client.get("/api/eval/runs/99999")
    assert resp.status_code == 404


def test_get_eval_run_detail_parses_metrics_json(client_and_db):
    """GET /api/eval/runs/{id} returns metrics as a parsed dict, not a string."""
    client, db = client_and_db
    run_id = _make_run(db)
    metrics = {"A": {"f1": 0.75, "recall": 0.80, "precision": 0.70}}
    update_eval_run(db, run_id, status="complete", metrics=json.dumps(metrics))

    resp = client.get(f"/api/eval/runs/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("metrics"), dict)
    assert data["metrics"]["A"]["f1"] == 0.75


# ---------------------------------------------------------------------------
# DELETE /api/eval/runs/{run_id}
# ---------------------------------------------------------------------------


def test_delete_eval_run_cancels_queued_run(client_and_db):
    """DELETE /api/eval/runs/{id} sets status to cancelled for a queued run."""
    client, db = client_and_db
    run_id = _make_run(db, status="queued")

    resp = client.delete(f"/api/eval/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify status changed
    detail = client.get(f"/api/eval/runs/{run_id}")
    assert detail.json()["status"] == "cancelled"


def test_delete_eval_run_returns_404_for_unknown_id(client):
    """DELETE /api/eval/runs/{id} returns 404 for non-existent run."""
    resp = client.delete("/api/eval/runs/99999")
    assert resp.status_code == 404


def test_delete_eval_run_returns_400_if_already_complete(client_and_db):
    """DELETE /api/eval/runs/{id} returns 400 if run is already complete."""
    client, db = client_and_db
    run_id = _make_run(db, status="complete")

    resp = client.delete(f"/api/eval/runs/{run_id}")
    assert resp.status_code == 400


def test_delete_eval_run_returns_400_if_already_failed(client_and_db):
    """DELETE /api/eval/runs/{id} returns 400 if run is already failed."""
    client, db = client_and_db
    run_id = _make_run(db, status="failed")

    resp = client.delete(f"/api/eval/runs/{run_id}")
    assert resp.status_code == 400


def test_delete_eval_run_returns_400_if_already_cancelled(client_and_db):
    """DELETE /api/eval/runs/{id} returns 400 if run is already cancelled."""
    client, db = client_and_db
    run_id = _make_run(db, status="cancelled")

    resp = client.delete(f"/api/eval/runs/{run_id}")
    assert resp.status_code == 400


def test_delete_eval_run_cancels_generating_run(client_and_db):
    """DELETE should also cancel a run in generating status."""
    client, db = client_and_db
    run_id = _make_run(db, status="generating")

    resp = client.delete(f"/api/eval/runs/{run_id}")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/eval/runs/{run_id}/results
# ---------------------------------------------------------------------------


def test_get_eval_run_results_returns_list(client_and_db):
    """GET /api/eval/runs/{id}/results returns a list."""
    client, db = client_and_db
    run_id = _make_run(db)

    resp = client.get(f"/api/eval/runs/{run_id}/results")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_eval_run_results_returns_inserted_rows(client_and_db):
    """GET /api/eval/runs/{id}/results returns all result rows for that run."""
    client, db = client_and_db
    run_id = _make_run(db)

    insert_eval_result(
        db,
        run_id=run_id,
        variant="A",
        source_item_id="src-1",
        target_item_id="tgt-1",
        is_same_cluster=1,
        row_type="judge",
        score_transfer=3,
    )
    insert_eval_result(
        db,
        run_id=run_id,
        variant="A",
        source_item_id="src-2",
        target_item_id="tgt-2",
        is_same_cluster=0,
        row_type="judge",
        score_transfer=2,
    )

    resp = client.get(f"/api/eval/runs/{run_id}/results")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 2


def test_get_eval_run_results_row_type_filter(client_and_db):
    """?row_type=judge filter returns only judge rows."""
    client, db = client_and_db
    run_id = _make_run(db)

    insert_eval_result(
        db,
        run_id=run_id,
        variant="A",
        source_item_id="src-1",
        target_item_id="tgt-1",
        is_same_cluster=1,
        row_type="judge",
        score_transfer=3,
    )
    insert_eval_result(
        db,
        run_id=run_id,
        variant="A",
        source_item_id="src-3",
        target_item_id="tgt-3",
        is_same_cluster=1,
        row_type="generate",
    )

    resp = client.get(f"/api/eval/runs/{run_id}/results?row_type=judge")
    assert resp.status_code == 200
    results = resp.json()
    assert all(r["row_type"] == "judge" for r in results)
    assert len(results) == 1


def test_get_eval_run_results_returns_404_for_unknown_run(client):
    """GET /api/eval/runs/{id}/results returns 404 for unknown run."""
    resp = client.get("/api/eval/runs/99999/results")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/eval/runs/{run_id}/progress
# ---------------------------------------------------------------------------


def test_get_eval_run_progress_returns_progress_dict(client_and_db):
    """GET /api/eval/runs/{id}/progress returns a progress dict with required fields."""
    client, db = client_and_db
    run_id = _make_run(db, status="generating")
    update_eval_run(db, run_id, item_count=10)

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    data = resp.json()

    required = {"run_id", "status", "generated", "total", "judged", "failed", "pct_complete"}
    assert required.issubset(data.keys())
    assert data["run_id"] == run_id
    assert data["total"] == 10


def test_get_eval_run_progress_counts_results(client_and_db):
    """Progress counts reflect actual eval_results rows."""
    client, db = client_and_db
    run_id = _make_run(db, status="judging")
    update_eval_run(db, run_id, item_count=5)

    # Insert 2 judge rows and 1 generate row
    for i in range(2):
        insert_eval_result(
            db,
            run_id=run_id,
            variant="A",
            source_item_id=f"src-{i}",
            target_item_id=f"tgt-{i}",
            is_same_cluster=1,
            row_type="judge",
            score_transfer=3,
        )
    insert_eval_result(
        db,
        run_id=run_id,
        variant="A",
        source_item_id="src-gen",
        target_item_id="tgt-gen",
        is_same_cluster=1,
        row_type="generate",
    )

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert data["judged"] == 2
    assert data["generated"] == 1


def test_get_eval_run_progress_pct_complete(client_and_db):
    """pct_complete is calculated correctly."""
    client, db = client_and_db
    run_id = _make_run(db, status="judging")
    update_eval_run(db, run_id, item_count=4)

    # Insert 2 out of 4 judged
    for i in range(2):
        insert_eval_result(
            db,
            run_id=run_id,
            variant="A",
            source_item_id=f"src-{i}",
            target_item_id=f"tgt-{i}",
            is_same_cluster=1,
            row_type="judge",
            score_transfer=3,
        )

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    assert resp.json()["pct_complete"] == 50.0


def test_get_eval_run_progress_returns_404_for_unknown_run(client):
    """GET /api/eval/runs/{id}/progress returns 404 for unknown run."""
    resp = client.get("/api/eval/runs/99999/progress")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/eval/runs/{run_id}/judge-rerun
# ---------------------------------------------------------------------------


def test_judge_rerun_creates_new_run(client_and_db):
    """POST /api/eval/runs/{id}/judge-rerun creates a new run and returns run_id."""
    client, db = client_and_db
    run_id = _make_run(db, status="complete")

    resp = client.post(f"/api/eval/runs/{run_id}/judge-rerun")
    assert resp.status_code == 201
    data = resp.json()
    assert "run_id" in data
    new_run_id = data["run_id"]
    assert new_run_id != run_id
    assert new_run_id > 0


def test_judge_rerun_new_run_status_is_judging(client_and_db):
    """New run created by judge-rerun starts with status='judging'."""
    client, db = client_and_db
    run_id = _make_run(db, status="complete")

    resp = client.post(f"/api/eval/runs/{run_id}/judge-rerun")
    new_run_id = resp.json()["run_id"]

    detail = client.get(f"/api/eval/runs/{new_run_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "judging"


def test_judge_rerun_copies_item_ids(client_and_db):
    """Judge-rerun copies item_ids from the original run."""
    client, db = client_and_db
    run_id = _make_run(db, status="complete")
    item_ids = json.dumps(["item-1", "item-2", "item-3"])
    update_eval_run(db, run_id, item_ids=item_ids)

    resp = client.post(f"/api/eval/runs/{run_id}/judge-rerun")
    new_run_id = resp.json()["run_id"]

    detail = client.get(f"/api/eval/runs/{new_run_id}")
    assert detail.json().get("item_ids") == item_ids


def test_judge_rerun_returns_404_for_unknown_run(client):
    """POST /api/eval/runs/{id}/judge-rerun returns 404 for unknown run."""
    resp = client.post("/api/eval/runs/99999/judge-rerun")
    assert resp.status_code == 404


def test_judge_rerun_returns_400_for_queued_run(client_and_db):
    """Judge-rerun is not allowed on a queued run."""
    client, db = client_and_db
    run_id = _make_run(db, status="queued")

    resp = client.post(f"/api/eval/runs/{run_id}/judge-rerun")
    assert resp.status_code == 400


def test_judge_rerun_allowed_on_failed_run(client_and_db):
    """Judge-rerun is allowed on a failed run."""
    client, db = client_and_db
    run_id = _make_run(db, status="failed")

    resp = client.post(f"/api/eval/runs/{run_id}/judge-rerun")
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# POST /api/eval/runs/{run_id}/repeat  (Task 13 — reproducibility)
# ---------------------------------------------------------------------------


def _insert_reproducible_run(db: Database, *, item_ids=None, seed=None, status="complete") -> int:
    """Insert a minimal eval_runs row with reproducibility fields and return its id."""
    import datetime

    with db._lock:
        conn = db._connect()
        cur = conn.execute(
            """INSERT INTO eval_runs
               (data_source_url, variants, per_cluster, status, run_mode,
                item_ids, seed, started_at)
               VALUES (?, ?, ?, ?, 'batch',
                       ?, ?, ?)""",
            (
                "http://127.0.0.1:7685",
                json.dumps(["A", "B"]),
                4,
                status,
                json.dumps(item_ids) if item_ids is not None else None,
                seed,
                datetime.datetime.now(datetime.UTC).isoformat(),
            ),
        )
        conn.commit()
        return cur.lastrowid


def test_repeat_run_returns_new_run_id(client_and_db):
    """POST /api/eval/runs/{id}/repeat creates a new run and returns its run_id."""

    client, db = client_and_db
    pairs = [["101", "202"], ["101", "303"]]
    orig_id = _insert_reproducible_run(db, item_ids=pairs, seed=1234)

    resp = client.post(f"/api/eval/runs/{orig_id}/repeat")
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    new_id = data["run_id"]
    assert isinstance(new_id, int)
    assert new_id != orig_id


def test_repeat_run_copies_seed_and_item_ids(client_and_db):
    """Repeated run inherits the original run's seed and item_ids verbatim."""
    client, db = client_and_db
    pairs = [["10", "20"], ["10", "30"]]
    orig_id = _insert_reproducible_run(db, item_ids=pairs, seed=555)

    resp = client.post(f"/api/eval/runs/{orig_id}/repeat")
    assert resp.status_code == 200
    new_id = resp.json()["run_id"]

    with db._lock:
        conn = db._connect()
        row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (new_id,)).fetchone()
    assert row is not None
    new_run = dict(row)

    assert new_run["seed"] == 555
    assert json.loads(new_run["item_ids"]) == pairs
    # Background session thread starts immediately, so status may have advanced beyond 'pending'
    assert new_run["status"] in ("pending", "queued", "generating", "judging", "complete", "failed")


def test_repeat_run_422_when_no_item_ids(client_and_db):
    """POST repeat returns 422 when the original run has no item_ids."""
    client, db = client_and_db
    orig_id = _insert_reproducible_run(db, item_ids=None, seed=42)

    resp = client.post(f"/api/eval/runs/{orig_id}/repeat")
    assert resp.status_code == 422
    assert "reproducibility" in resp.json()["detail"]


def test_repeat_run_422_when_no_seed(client_and_db):
    """POST repeat returns 422 when the original run has no seed."""
    client, db = client_and_db
    orig_id = _insert_reproducible_run(db, item_ids=[["1", "2"]], seed=None)

    resp = client.post(f"/api/eval/runs/{orig_id}/repeat")
    assert resp.status_code == 422
    assert "reproducibility" in resp.json()["detail"]


def test_repeat_run_404_for_missing_original(client):
    """POST repeat returns 404 when the original run does not exist."""
    resp = client.post("/api/eval/runs/9999/repeat")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Seed persistence — run_eval_generate() writes seed when none present
# ---------------------------------------------------------------------------


def test_get_eval_run_progress_includes_model_fields(client_and_db):
    """Progress response includes gen_model and judge_model fields."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="generating")
    update_eval_run(db, run_id, item_count=10)

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    data = resp.json()
    # Variant A is seeded with model "deepseek-r1:8b"
    assert data["gen_model"] == "deepseek-r1:8b"
    assert "judge_model" in data


def test_get_eval_run_progress_pct_is_per_phase_generating(client_and_db):
    """During generating phase, pct reflects generated/total (not judged/total)."""
    client, db = client_and_db
    run_id = _make_run(db, status="generating")
    update_eval_run(db, run_id, item_count=10)

    # Insert 3 generate rows (0 judge rows)
    for i in range(3):
        insert_eval_result(
            db,
            run_id=run_id,
            variant="A",
            source_item_id=f"src-{i}",
            target_item_id=f"tgt-{i}",
            is_same_cluster=1,
            row_type="generate",
        )

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    data = resp.json()
    # 3/10 generated = 30%, not 0% (which it would be if using judged/total)
    assert data["pct"] == 30.0


def test_get_eval_run_progress_pct_is_per_phase_judging(client_and_db):
    """During judging phase, pct reflects judged/total."""
    client, db = client_and_db
    run_id = _make_run(db, status="judging")
    update_eval_run(db, run_id, item_count=10)

    for i in range(4):
        insert_eval_result(
            db,
            run_id=run_id,
            variant="A",
            source_item_id=f"src-{i}",
            target_item_id=f"tgt-{i}",
            is_same_cluster=1,
            row_type="judge",
            score_transfer=3,
        )

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    assert resp.json()["pct"] == 40.0


def test_run_eval_generate_persists_seed_when_none(tmp_path):
    """run_eval_generate() generates and writes a seed when the run has no seed."""
    import datetime
    from unittest.mock import patch

    from ollama_queue.eval_engine import get_eval_run, run_eval_generate

    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    with db._lock:
        conn = db._connect()
        cur = conn.execute(
            """INSERT INTO eval_runs
               (data_source_url, variants, per_cluster, status, run_mode,
                seed, started_at)
               VALUES (?, ?, ?, 'pending', ?, NULL, ?)""",
            (
                "http://127.0.0.1:7685",
                json.dumps(["A"]),
                4,
                "batch",
                datetime.datetime.now(datetime.UTC).isoformat(),
            ),
        )
        conn.commit()
        run_id = cur.lastrowid

    # Patch _fetch_items to return empty so the function exits early after seeding
    with patch("ollama_queue.eval_engine._fetch_items", return_value=[]):
        run_eval_generate(run_id, db, http_base="http://127.0.0.1:7683")

    run = get_eval_run(db, run_id)
    assert run is not None
    assert run["seed"] is not None
    assert isinstance(run["seed"], int)


# ---------------------------------------------------------------------------
# POST /api/eval/runs/{id}/analyze
# ---------------------------------------------------------------------------


def test_analyze_eval_run_returns_404_for_unknown_id(client):
    resp = client.post("/api/eval/runs/9999/analyze")
    assert resp.status_code == 404


def test_analyze_eval_run_returns_400_for_non_complete_run(client_and_db):
    client, db = client_and_db
    _make_run(db, status="generating")
    with db._lock:
        conn = db._connect()
        run_id = conn.execute("SELECT id FROM eval_runs ORDER BY id DESC LIMIT 1").fetchone()["id"]
    resp = client.post(f"/api/eval/runs/{run_id}/analyze")
    assert resp.status_code == 400


def test_analyze_eval_run_returns_ok_for_complete_run(client_and_db):
    client, db = client_and_db
    _make_run(db, status="complete")
    with db._lock:
        conn = db._connect()
        run_id = conn.execute("SELECT id FROM eval_runs ORDER BY id DESC LIMIT 1").fetchone()["id"]
    with patch("ollama_queue.eval_engine.generate_eval_analysis"):
        resp = client.post(f"/api/eval/runs/{run_id}/analyze")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["run_id"] == run_id
