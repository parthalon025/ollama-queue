"""Tests for eval run lifecycle API endpoints (Task 4).

Covers: GET/POST /api/eval/runs, GET/DELETE /api/eval/runs/{id},
        GET /api/eval/runs/{id}/results, GET /api/eval/runs/{id}/progress,
        POST /api/eval/runs/{id}/judge-rerun.
"""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import Database
from ollama_queue.eval.engine import create_eval_run, insert_eval_result, update_eval_run
from ollama_queue.eval.promote import do_promote_eval_run


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
    """New run created by judge-rerun starts with status='judging'.

    Uses a gated mock to prevent the background judge thread from completing
    before the GET fires — avoids a race where status flips to 'complete'
    before the assertion runs (flaky under xdist parallel execution).
    """
    client, db = client_and_db
    run_id = _make_run(db, status="complete")

    gate = threading.Event()

    def _blocked_judge(*args, **kwargs):
        gate.wait(timeout=5)  # Block until assertion is done

    with patch("ollama_queue.api.eval_runs.run_eval_judge", side_effect=_blocked_judge):
        resp = client.post(f"/api/eval/runs/{run_id}/judge-rerun")
        new_run_id = resp.json()["run_id"]

        detail = client.get(f"/api/eval/runs/{new_run_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "judging"
        gate.set()  # Release background thread before patch context exits


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
    assert resp.status_code == 201
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
    assert resp.status_code == 201
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

    from ollama_queue.eval.engine import get_eval_run
    from ollama_queue.eval.generate import run_eval_generate

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
    with patch("ollama_queue.eval.engine._fetch_items", return_value=[]):
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
    with patch("ollama_queue.api.eval_runs.generate_eval_analysis"):
        resp = client.post(f"/api/eval/runs/{run_id}/analyze")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["run_id"] == run_id


# ---------------------------------------------------------------------------
# do_promote_eval_run() shared core function
# ---------------------------------------------------------------------------


def _make_variant(db, variant_id: str = "A") -> None:
    """Insert a minimal eval_variants row (uses zero-shot-causal to satisfy FK constraint)."""
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT OR IGNORE INTO eval_variants "
            "(id, label, prompt_template_id, model, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (variant_id, f"Config {variant_id}", "zero-shot-causal", "qwen2.5:7b"),
        )
        conn.commit()


class TestDoPromoteEvalRun:
    def test_raises_if_run_not_found(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        with pytest.raises(ValueError, match="not found"):
            do_promote_eval_run(db, 9999)

    def test_raises_if_run_not_complete(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        run_id = create_eval_run(db, variant_id="A")
        with pytest.raises(ValueError, match="not complete"):
            do_promote_eval_run(db, run_id)

    def test_raises_if_no_winner_variant(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete")
        with pytest.raises(ValueError, match="no winner_variant"):
            do_promote_eval_run(db, run_id)

    def test_raises_if_variant_not_in_db(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        run_id = create_eval_run(db, variant_id="A")
        # Set winner_variant to a non-existent ID (initialize() seeds A-E, so use something else)
        update_eval_run(db, run_id, status="complete", winner_variant="MISSING-XYZ")
        with pytest.raises(ValueError, match="not found in eval_variants"):
            do_promote_eval_run(db, run_id)

    def test_sets_winner_as_production(self, tmp_path):
        """Winner gets is_recommended=1 + is_production=1; others cleared."""
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        _make_variant(db, "A")
        _make_variant(db, "B")
        # Pre-set B as production
        from ollama_queue.eval.engine import update_eval_variant

        update_eval_variant(db, "B", is_production=1, is_recommended=1)

        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete", winner_variant="A")

        with patch("ollama_queue.eval.promote.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            result = do_promote_eval_run(db, run_id)

        assert result["ok"] is True
        assert result["variant_id"] == "A"

        with db._lock:
            conn = db._connect()
            a_row = conn.execute("SELECT is_recommended, is_production FROM eval_variants WHERE id='A'").fetchone()
            b_row = conn.execute("SELECT is_recommended, is_production FROM eval_variants WHERE id='B'").fetchone()
        assert a_row["is_production"] == 1
        assert a_row["is_recommended"] == 1
        assert b_row["is_production"] == 0
        assert b_row["is_recommended"] == 0

    def test_raises_on_lessons_db_unreachable(self, tmp_path):
        """Raises httpx.HTTPError when lessons-db is unreachable."""
        import httpx

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        _make_variant(db, "A")
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete", winner_variant="A")

        with (
            patch("ollama_queue.eval.promote.httpx.post", side_effect=httpx.ConnectError("refused")),
            pytest.raises(httpx.HTTPError),
        ):
            do_promote_eval_run(db, run_id)


# ---------------------------------------------------------------------------
# promote_eval_run API endpoint
# ---------------------------------------------------------------------------


class TestPromoteEvalRunEndpoint:
    def test_promote_returns_404_for_unknown_run(self, client):
        resp = client.post("/api/eval/runs/9999/promote", json={})
        assert resp.status_code == 404

    def test_promote_returns_400_if_not_complete(self, client_and_db):
        client, db = client_and_db
        run_id = _make_run(db, status="queued")
        resp = client.post(f"/api/eval/runs/{run_id}/promote", json={})
        assert resp.status_code == 400

    def test_promote_returns_400_if_no_winner_variant(self, client_and_db):
        client, db = client_and_db
        run_id = _make_run(db, status="complete")
        resp = client.post(f"/api/eval/runs/{run_id}/promote", json={})
        assert resp.status_code == 400
        assert "winner_variant" in resp.json()["detail"]

    def test_promote_auto_resolves_and_updates_local_db(self, client_and_db):
        """Promote with empty body resolves winner from DB and sets is_production=1."""
        client, db = client_and_db
        _make_variant(db, "A")
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete", winner_variant="A")

        with patch("ollama_queue.eval.promote.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            resp = client.post(f"/api/eval/runs/{run_id}/promote", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["variant_id"] == "A"
        assert data["run_id"] == run_id

        # Verify local DB was updated
        with db._lock:
            conn = db._connect()
            row = conn.execute("SELECT is_production FROM eval_variants WHERE id='A'").fetchone()
        assert row["is_production"] == 1

    def test_promote_returns_400_not_404_when_variant_missing(self, client_and_db):
        """Variant not found in eval_variants routes to 400, not 404."""
        client, db = client_and_db
        # Create a complete run whose winner_variant ("ghost") has no eval_variants row
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete", winner_variant="ghost")
        resp = client.post(f"/api/eval/runs/{run_id}/promote", json={})
        assert resp.status_code == 400  # NOT 404
        assert "eval_variants" in resp.json()["detail"]

    def test_promote_returns_502_if_lessons_db_unreachable(self, client_and_db):
        """Returns 502 when lessons-db is unreachable."""
        import httpx

        client, db = client_and_db
        _make_variant(db, "A")
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete", winner_variant="A")

        with patch("ollama_queue.eval.promote.httpx.post", side_effect=httpx.ConnectError("refused")):
            resp = client.post(f"/api/eval/runs/{run_id}/promote", json={})
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Judge mode API tests (Task 18)
# ---------------------------------------------------------------------------


class TestJudgeModeAPI:
    """Tests for judge_mode parameter in eval run API endpoints."""

    def test_post_accepts_judge_mode(self, client_and_db):
        """POST /api/eval/runs accepts judge_mode in body."""
        client, db = client_and_db
        _make_variant(db, "A")
        with patch("ollama_queue.api.eval_runs.run_eval_session"):
            resp = client.post(
                "/api/eval/runs",
                json={"variant_id": "A", "judge_mode": "bayesian"},
            )
        assert resp.status_code == 201
        run_id = resp.json()["run_id"]

        # Verify stored in DB
        from ollama_queue.eval.engine import get_eval_run

        run = get_eval_run(db, run_id)
        assert run["judge_mode"] == "bayesian"

    def test_post_default_judge_mode_is_bayesian(self, client_and_db):
        """POST /api/eval/runs defaults to 'bayesian' when judge_mode not specified."""
        client, db = client_and_db
        _make_variant(db, "A")
        with patch("ollama_queue.api.eval_runs.run_eval_session"):
            resp = client.post(
                "/api/eval/runs",
                json={"variant_id": "A"},
            )
        assert resp.status_code == 201
        run_id = resp.json()["run_id"]

        from ollama_queue.eval.engine import get_eval_run

        run = get_eval_run(db, run_id)
        assert run["judge_mode"] == "bayesian"

    def test_post_rejects_invalid_judge_mode(self, client_and_db):
        """POST /api/eval/runs returns 400 for invalid judge_mode."""
        client, db = client_and_db
        _make_variant(db, "A")
        resp = client.post(
            "/api/eval/runs",
            json={"variant_id": "A", "judge_mode": "invalid"},
        )
        assert resp.status_code == 400
        assert "judge_mode" in resp.json()["detail"]

    def test_get_detail_returns_judge_mode(self, client_and_db):
        """GET /api/eval/runs/{id} returns judge_mode in response."""
        client, db = client_and_db
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, judge_mode="tournament")
        resp = client.get(f"/api/eval/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["judge_mode"] == "tournament"

    def test_list_returns_judge_mode(self, client_and_db):
        """GET /api/eval/runs includes judge_mode in list response."""
        client, db = client_and_db
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, judge_mode="bayesian")
        resp = client.get("/api/eval/runs")
        assert resp.status_code == 200
        runs = resp.json()
        assert len(runs) >= 1
        assert runs[0]["judge_mode"] == "bayesian"

    def test_trends_includes_judge_mode(self, client_and_db):
        """GET /api/eval/trends includes judge_mode in run data."""
        client, db = client_and_db
        run_id = create_eval_run(db, variant_id="A")
        metrics = {"A": {"f1": 0.8, "recall": 0.9, "precision": 0.7, "actionability": 3.5, "sample_count": 10}}
        update_eval_run(
            db,
            run_id,
            status="complete",
            metrics=json.dumps(metrics),
            judge_mode="bayesian",
        )
        resp = client.get("/api/eval/trends")
        assert resp.status_code == 200
        data = resp.json()
        variant_data = data["variants"].get("A")
        assert variant_data is not None
        assert variant_data["runs"][0]["judge_mode"] == "bayesian"

    def test_all_valid_judge_modes_accepted(self, client_and_db):
        """All four valid judge modes are accepted by POST."""
        client, db = client_and_db
        _make_variant(db, "A")
        for mode in ("rubric", "binary", "tournament", "bayesian"):
            with patch("ollama_queue.api.eval_runs.run_eval_session"):
                resp = client.post(
                    "/api/eval/runs",
                    json={"variant_id": "A", "judge_mode": mode},
                )
            assert resp.status_code == 201, f"Mode {mode} was rejected"


# ---------------------------------------------------------------------------
# GET /api/eval/runs/{id}/confusion
# ---------------------------------------------------------------------------


class TestConfusionMatrixEndpoint:
    def test_404_for_missing_run(self, client):
        resp = client.get("/api/eval/runs/999/confusion")
        assert resp.status_code == 404

    def test_empty_when_no_cluster_data(self, client_and_db):
        """Returns empty matrix when no results have source_cluster_id."""
        client, db = client_and_db
        run_id = _make_run(db, status="complete")
        resp = client.get(f"/api/eval/runs/{run_id}/confusion")
        assert resp.status_code == 200
        data = resp.json()
        assert data["matrix"] == {}
        assert data["flagged"] == []
        assert data["clusters"] == []

    def test_returns_matrix_with_cluster_data(self, client_and_db):
        """Returns populated confusion matrix when results have cluster IDs."""
        client, db = client_and_db
        run_id = _make_run(db, status="complete")
        # Same-cluster pair: source=A, target=A, high transfer
        insert_eval_result(
            db,
            run_id=run_id,
            variant="F",
            source_item_id="1",
            target_item_id="2",
            is_same_cluster=1,
            row_type="judge",
            score_transfer=5,
            score_precision=4,
            score_action=4,
            source_cluster_id="A",
            target_cluster_id="A",
        )
        # Diff-cluster pair: source=A, target=B, low transfer
        insert_eval_result(
            db,
            run_id=run_id,
            variant="F",
            source_item_id="1",
            target_item_id="3",
            is_same_cluster=0,
            row_type="judge",
            score_transfer=1,
            score_precision=4,
            score_action=3,
            source_cluster_id="A",
            target_cluster_id="B",
        )
        resp = client.get(f"/api/eval/runs/{run_id}/confusion")
        assert resp.status_code == 200
        data = resp.json()
        assert "A" in data["matrix"]
        assert data["matrix"]["A"]["A"]["avg_transfer"] == 5.0
        assert data["matrix"]["A"]["B"]["avg_transfer"] == 1.0
        assert data["flagged"] == []  # no cross-cluster avg >= 3.0
        assert sorted(data["clusters"]) == ["A", "B"]

    def test_flags_high_cross_cluster_transfer(self, client_and_db):
        """Flags cross-cluster pairs with avg transfer >= 3.0."""
        client, db = client_and_db
        run_id = _make_run(db, status="complete")
        # Diff-cluster pair with high transfer (principle bleed)
        insert_eval_result(
            db,
            run_id=run_id,
            variant="F",
            source_item_id="1",
            target_item_id="3",
            is_same_cluster=0,
            row_type="judge",
            score_transfer=4,
            score_precision=2,
            score_action=2,
            source_cluster_id="A",
            target_cluster_id="B",
        )
        resp = client.get(f"/api/eval/runs/{run_id}/confusion")
        data = resp.json()
        assert len(data["flagged"]) == 1
        assert data["flagged"][0]["source"] == "A"
        assert data["flagged"][0]["target"] == "B"
        assert data["flagged"][0]["avg_transfer"] == 4.0


# ---------------------------------------------------------------------------
# GET /api/eval/runs/{id}/analysis
# ---------------------------------------------------------------------------


def test_get_analysis_returns_stored_json(client_and_db):
    """GET /api/eval/runs/{id}/analysis returns stored analysis_json."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, analysis_json) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', ?)",
            ('{"per_item": [], "failures": [], "confidence_intervals": {}}',),
        )
        conn.commit()
    resp = client.get("/api/eval/runs/1/analysis")
    assert resp.status_code == 200
    data = resp.json()
    assert "per_item" in data
    assert "failures" in data


def test_get_analysis_not_computed(client_and_db):
    """GET /api/eval/runs/{id}/analysis returns status when analysis_json is NULL."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete')"
        )
        conn.commit()
    resp = client.get("/api/eval/runs/1/analysis")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_computed"


def test_get_analysis_not_found(client):
    """GET /api/eval/runs/999/analysis returns 404."""
    resp = client.get("/api/eval/runs/999/analysis")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/eval/runs/{id}/reanalyze
# ---------------------------------------------------------------------------


def test_reanalyze_computes_analysis(client_and_db):
    """POST /api/eval/runs/{id}/reanalyze recomputes analysis_json."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete')"
        )
        for i in range(12):
            conn.execute(
                "INSERT INTO eval_results "
                "(run_id, variant, source_item_id, target_item_id, "
                "is_same_cluster, score_transfer, row_type, source_cluster_id, target_cluster_id) "
                "VALUES (1, 'A', ?, ?, ?, ?, 'judge', 'c1', ?)",
                (str(i), str(i + 100), 1 if i < 6 else 0, 4 if i % 2 == 0 else 2, "c1" if i < 6 else "c2"),
            )
        conn.commit()
    resp = client.post("/api/eval/runs/1/reanalyze")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    with db._lock:
        conn = db._connect()
        row = conn.execute("SELECT analysis_json FROM eval_runs WHERE id = 1").fetchone()
    assert row["analysis_json"] is not None


def test_reanalyze_not_found(client):
    resp = client.post("/api/eval/runs/999/reanalyze")
    assert resp.status_code == 404


def test_reanalyze_not_complete(client_and_db):
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'generating')"
        )
        conn.commit()
    resp = client.post("/api/eval/runs/1/reanalyze")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/eval/runs/{run_id}/results — classification filter
# ---------------------------------------------------------------------------


def test_results_filter_fp(client_and_db):
    """Filter results by classification=fp returns only false positive rows."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete')"
        )
        # FP: diff cluster, high score (>= threshold 3)
        conn.execute(
            "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
            "is_same_cluster, score_transfer, row_type) "
            "VALUES (1, 'A', '1', '2', 0, 4, 'judge')"
        )
        # TP: same cluster, high score
        conn.execute(
            "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
            "is_same_cluster, score_transfer, row_type) "
            "VALUES (1, 'A', '3', '4', 1, 5, 'judge')"
        )
        conn.commit()
    resp = client.get("/api/eval/runs/1/results?classification=fp")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["is_same_cluster"] == 0


def test_results_filter_fn(client_and_db):
    """Filter results by classification=fn returns only false negative rows."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete')"
        )
        # FN: same cluster, low score (< threshold 3)
        conn.execute(
            "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
            "is_same_cluster, score_transfer, row_type) "
            "VALUES (1, 'A', '1', '2', 1, 2, 'judge')"
        )
        # TP: same cluster, high score
        conn.execute(
            "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
            "is_same_cluster, score_transfer, row_type) "
            "VALUES (1, 'A', '3', '4', 1, 5, 'judge')"
        )
        conn.commit()
    resp = client.get("/api/eval/runs/1/results?classification=fn")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["is_same_cluster"] == 1
    assert data[0]["score_transfer"] == 2


def test_results_filter_tp(client_and_db):
    """Filter results by classification=tp returns only true positive rows."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete')"
        )
        # TP: same cluster, high score
        conn.execute(
            "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
            "is_same_cluster, score_transfer, row_type) "
            "VALUES (1, 'A', '1', '2', 1, 4, 'judge')"
        )
        # TN: diff cluster, low score
        conn.execute(
            "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
            "is_same_cluster, score_transfer, row_type) "
            "VALUES (1, 'A', '3', '4', 0, 1, 'judge')"
        )
        conn.commit()
    resp = client.get("/api/eval/runs/1/results?classification=tp")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["is_same_cluster"] == 1
    assert data[0]["score_transfer"] == 4


def test_results_limit_offset(client_and_db):
    """Results endpoint respects limit and offset parameters."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete')"
        )
        for i in range(5):
            conn.execute(
                "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
                "is_same_cluster, score_transfer, row_type) "
                "VALUES (1, 'A', ?, ?, 1, 4, 'judge')",
                (str(i), str(i + 10)),
            )
        conn.commit()
    resp = client.get("/api/eval/runs/1/results?limit=2&offset=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


# ---------------------------------------------------------------------------
# Coverage gap tests — eval runs
# ---------------------------------------------------------------------------


def test_list_eval_runs_with_bad_metrics_json(client_and_db):
    """list_eval_runs handles unparseable metrics JSON gracefully."""
    client, db = client_and_db
    run_id = _make_run(db, status="complete")
    update_eval_run(db, run_id, metrics="not valid json{{{")
    resp = client.get("/api/eval/runs")
    assert resp.status_code == 200
    runs = resp.json()
    found = next(r for r in runs if r["id"] == run_id)
    assert found["metrics"] is None


def test_get_eval_run_detail_bad_metrics_json(client_and_db):
    """GET /api/eval/runs/{id} handles unparseable metrics gracefully."""
    client, db = client_and_db
    run_id = _make_run(db, status="complete")
    update_eval_run(db, run_id, metrics="not valid json{{{")
    resp = client.get(f"/api/eval/runs/{run_id}")
    assert resp.status_code == 200
    # metrics stays as the raw string or None since parse failed
    data = resp.json()
    assert "metrics" in data


def test_post_eval_runs_variants_list_sets_variant_id(client_and_db):
    """POST /api/eval/runs with variants list sets variant_id to first element."""
    client, db = client_and_db
    with patch("ollama_queue.api.eval_runs.run_eval_session"):
        resp = client.post(
            "/api/eval/runs",
            json={"variants": ["A", "B"], "run_mode": "batch"},
        )
    assert resp.status_code == 201
    run_id = resp.json()["run_id"]
    detail = client.get(f"/api/eval/runs/{run_id}")
    assert detail.json()["variant_id"] == "A"


def test_post_eval_runs_with_judge_model(client_and_db):
    """POST /api/eval/runs with judge_model persists it."""
    client, db = client_and_db
    with patch("ollama_queue.api.eval_runs.run_eval_session"):
        resp = client.post(
            "/api/eval/runs",
            json={"variant_id": "A", "judge_model": "deepseek-r1:8b"},
        )
    assert resp.status_code == 201
    run_id = resp.json()["run_id"]
    from ollama_queue.eval.engine import get_eval_run

    run = get_eval_run(db, run_id)
    assert run["judge_model"] == "deepseek-r1:8b"


def test_post_eval_runs_background_thread_exception(client_and_db):
    """Background eval session exception is handled silently."""
    import time

    client, db = client_and_db

    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    with patch("ollama_queue.api.eval_runs.run_eval_session", side_effect=_raise):
        resp = client.post("/api/eval/runs", json={"variant_id": "A"})
    assert resp.status_code == 201
    time.sleep(0.1)  # let background thread run


def test_analyze_eval_run_exception_sets_failure_message(client_and_db):
    """Background analysis exception sets failure message on run."""
    import time

    client, db = client_and_db
    run_id = _make_run(db, status="complete")

    def _raise(*args, **kwargs):
        raise RuntimeError("analysis boom")

    with patch("ollama_queue.api.eval_runs.generate_eval_analysis", side_effect=_raise):
        resp = client.post(f"/api/eval/runs/{run_id}/analyze")
    assert resp.status_code == 200
    time.sleep(0.2)  # let background thread run
    # The analysis_md should contain the failure message
    detail = client.get(f"/api/eval/runs/{run_id}")
    assert "failed" in (detail.json().get("analysis_md") or "").lower()


def test_analyze_eval_run_double_exception(client_and_db):
    """If both analysis and error recording fail, no crash occurs."""
    import time

    client, db = client_and_db
    run_id = _make_run(db, status="complete")

    def _raise(*args, **kwargs):
        raise RuntimeError("analysis boom")

    with (
        patch("ollama_queue.api.eval_runs.generate_eval_analysis", side_effect=_raise),
        patch("ollama_queue.api.eval_runs.update_eval_run", side_effect=Exception("db boom")),
    ):
        resp = client.post(f"/api/eval/runs/{run_id}/analyze")
    assert resp.status_code == 200
    time.sleep(0.2)


def test_repeat_run_background_thread_exception(client_and_db):
    """Background thread for repeat run handles exceptions."""
    import time

    client, db = client_and_db
    pairs = [["101", "202"]]
    orig_id = _insert_reproducible_run(db, item_ids=pairs, seed=1234)

    with patch("ollama_queue.api.eval_runs.run_eval_session", side_effect=RuntimeError("boom")):
        resp = client.post(f"/api/eval/runs/{orig_id}/repeat")
    assert resp.status_code == 201
    time.sleep(0.1)


def test_judge_rerun_background_thread_exception(client_and_db):
    """Background thread for judge-rerun handles exception and marks run failed."""
    import time

    client, db = client_and_db
    run_id = _make_run(db, status="complete")

    def _raise(*args, **kwargs):
        raise RuntimeError("judge boom")

    with patch("ollama_queue.api.eval_runs.run_eval_judge", side_effect=_raise):
        resp = client.post(f"/api/eval/runs/{run_id}/judge-rerun")
    assert resp.status_code == 201
    new_id = resp.json()["run_id"]
    time.sleep(0.3)  # let background thread run
    from ollama_queue.eval.engine import get_eval_run

    run = get_eval_run(db, new_id)
    assert run["status"] == "failed"


def test_judge_rerun_bg_thread_double_exception(client_and_db):
    """If both judge and failure recording raise, no crash occurs."""
    import time

    client, db = client_and_db
    run_id = _make_run(db, status="complete")

    call_count = [0]

    def _raise_judge(*args, **kwargs):
        raise RuntimeError("judge boom")

    orig_update = update_eval_run

    def _raise_on_failed_update(db_ref, rid, **kwargs):
        if kwargs.get("status") == "failed":
            raise RuntimeError("db boom")
        return orig_update(db_ref, rid, **kwargs)

    with (
        patch("ollama_queue.api.eval_runs.run_eval_judge", side_effect=_raise_judge),
    ):
        resp = client.post(f"/api/eval/runs/{run_id}/judge-rerun")
    assert resp.status_code == 201
    time.sleep(0.3)


def test_eval_progress_with_item_ids_instead_of_item_count(client_and_db):
    """Progress endpoint derives total from item_ids JSON when item_count is 0."""
    client, db = client_and_db
    run_id = _make_run(db, status="generating")
    items = json.dumps(["a", "b", "c", "d", "e"])
    update_eval_run(db, run_id, item_ids=items, item_count=0)

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    assert resp.json()["total"] == 5


def test_eval_progress_bad_item_ids_json(client_and_db):
    """Progress endpoint handles corrupt item_ids JSON gracefully."""
    client, db = client_and_db
    run_id = _make_run(db, status="generating")
    # Set item_count=0 and corrupt item_ids
    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE eval_runs SET item_count = 0, item_ids = 'bad json{{' WHERE id = ?",
            (run_id,),
        )
        conn.commit()

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


def test_eval_progress_variants_json_parse(client_and_db):
    """Progress endpoint parses variants JSON to derive gen_model."""
    client, db = client_and_db
    run_id = _make_run(db, status="generating")
    update_eval_run(db, run_id, item_count=10)

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    assert "gen_model" in resp.json()


def test_results_filter_tn(client_and_db):
    """Filter results by classification=tn returns only true negative rows."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete')"
        )
        # TN: diff cluster, low score (< threshold 3)
        conn.execute(
            "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
            "is_same_cluster, score_transfer, row_type) "
            "VALUES (1, 'A', '1', '2', 0, 1, 'judge')"
        )
        # FP: diff cluster, high score
        conn.execute(
            "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
            "is_same_cluster, score_transfer, row_type) "
            "VALUES (1, 'A', '3', '4', 0, 5, 'judge')"
        )
        conn.commit()
    resp = client.get("/api/eval/runs/1/results?classification=tn")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["score_transfer"] == 1


def test_results_classification_exception_uses_default_threshold(client_and_db):
    """When positive_threshold setting read fails, default threshold is used."""
    client, db = client_and_db
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete')"
        )
        conn.execute(
            "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
            "is_same_cluster, score_transfer, row_type) "
            "VALUES (1, 'A', '1', '2', 0, 4, 'judge')"
        )
        # Set bad value for positive_threshold
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('eval.positive_threshold', 'bad')")
        conn.commit()
    resp = client.get("/api/eval/runs/1/results?classification=fp")
    assert resp.status_code == 200
