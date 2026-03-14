# Promote & Auto-Promote Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire the "Use this config" button to promote the winning eval variant to production, and auto-promote when all three quality gates pass.

**Architecture:** Extract core promote logic into `do_promote_eval_run()` in `eval_engine.py` so both the manual API endpoint and `check_auto_promote()` share identical validation + DB update + lessons-db call. `check_auto_promote()` runs after `generate_eval_analysis()` inside `run_eval_session()`, never raises, logs outcomes. SPA adds `handlePromote` to RunRow and two new fields to GeneralSettings.

**Tech Stack:** Python/FastAPI (backend), Preact signals (frontend), SQLite (local state), httpx (lessons-db HTTP call)

---

## Background

**Current state of relevant code:**

- `ollama_queue/db.py:65-79` — `EVAL_SETTINGS_DEFAULTS` already has `eval.f1_threshold`, `eval.stability_window`, `eval.error_budget`. Needs `eval.auto_promote` and `eval.auto_promote_min_improvement`.
- `ollama_queue/eval_engine.py:116-121` — `get_eval_variant(db, variant_id)` already exists. `update_eval_run` exists at line 132. **No `update_eval_variant` exists yet.**
- `ollama_queue/api.py:1922-1971` — `promote_eval_run` endpoint currently **requires** `model + prompt_template_id` in body; does NOT update local `eval_variants` at all. Needs full refactor.
- `ollama_queue/api.py:1388-1402` — `_known_eval_keys` allowlist. Needs `auto_promote` and `auto_promote_min_improvement` added.
- `ollama_queue/eval_engine.py:1509-1511` — `run_eval_session()` calls `generate_eval_analysis()` for complete runs. `check_auto_promote()` must be called immediately after.
- `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx:266-270` — "Use this config" button renders but has no `onClick`. Needs `handlePromote` wired.
- `ollama_queue/dashboard/spa/src/components/eval/GeneralSettings.jsx` — `FIELD_DEFS` array drives numeric fields. Needs two new fields added. Boolean toggle needs separate render (not a number input).
- `ollama_queue/dashboard/spa/src/components/eval/translations.js` — needs entries for `auto_promote` and `auto_promote_min_improvement`.

**Key invariant:** `check_auto_promote` NEVER raises. Wrap the entire body in `try/except Exception`. This is the same pattern as `generate_eval_analysis`.

---

## Task 1: New settings defaults + allowed keys

**Files:**
- Modify: `ollama_queue/db.py:79` (after `"eval.analysis_model"`)
- Modify: `ollama_queue/api.py:1401` (after `"analysis_model"` in `_known_eval_keys`)
- Test: `tests/test_api_eval_settings.py`

**Step 1: Write the failing test**

Add to `tests/test_api_eval_settings.py`:

```python
def test_auto_promote_defaults_to_false(client_and_db):
    """eval.auto_promote defaults to False."""
    client, db = client_and_db
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["eval.auto_promote"] is False


def test_auto_promote_min_improvement_default(client_and_db):
    """eval.auto_promote_min_improvement defaults to 0.05."""
    client, db = client_and_db
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["eval.auto_promote_min_improvement"] == pytest.approx(0.05)


def test_can_save_auto_promote_settings(client_and_db):
    """Can save both new auto-promote settings via PUT."""
    client, db = client_and_db
    resp = client.put("/api/eval/settings", json={
        "eval.auto_promote": True,
        "eval.auto_promote_min_improvement": 0.10,
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # Read back
    resp2 = client.get("/api/eval/settings")
    data = resp2.json()
    assert data["eval.auto_promote"] is True
    assert data["eval.auto_promote_min_improvement"] == pytest.approx(0.10)
```

**Step 2: Run tests to verify they fail**

```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
pytest tests/test_api_eval_settings.py::test_auto_promote_defaults_to_false tests/test_api_eval_settings.py::test_auto_promote_min_improvement_default tests/test_api_eval_settings.py::test_can_save_auto_promote_settings -v
```

Expected: FAIL (KeyError or wrong value)

**Step 3: Add defaults to `db.py`**

In `ollama_queue/db.py`, after line 78 (`"eval.analysis_model": ""`):

```python
    "eval.auto_promote": False,                   # explicit opt-in only
    "eval.auto_promote_min_improvement": 0.05,    # min F1 delta over current production
```

**Step 4: Add allowed keys to `api.py`**

In `ollama_queue/api.py`, after line 1401 (`"analysis_model",`):

```python
            "auto_promote",
            "auto_promote_min_improvement",
```

Also add validation in the validation block (after the existing elif chain, around line 1418):

```python
            elif bare_key == "auto_promote":
                if not isinstance(value, bool):
                    validation_errors.append(f"auto_promote must be a boolean, got {value!r}")
            elif bare_key == "auto_promote_min_improvement":
                if not isinstance(value, (int, float)) or not (0.0 <= float(value) <= 1.0):
                    validation_errors.append(f"auto_promote_min_improvement must be 0.0–1.0, got {value!r}")
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/test_api_eval_settings.py -v
```

Expected: all pass (including existing 20 tests)

**Step 6: Commit**

```bash
git add ollama_queue/db.py ollama_queue/api.py tests/test_api_eval_settings.py
git commit -m "feat: add eval.auto_promote and auto_promote_min_improvement settings"
```

---

## Task 2: `update_eval_variant` helper

**Files:**
- Modify: `ollama_queue/eval_engine.py` (after `update_eval_run` at line 142)
- Test: `tests/test_eval_engine.py`

**Step 1: Write the failing test**

Add to `tests/test_eval_engine.py` (after existing imports and fixtures):

```python
from ollama_queue.eval_engine import update_eval_variant


def test_update_eval_variant_sets_fields(tmp_path):
    """update_eval_variant sets arbitrary columns on an eval_variants row."""
    from ollama_queue.db import Database
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    # Insert a variant row directly
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_variants (id, label, prompt_template_id, model, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            ("V1", "Variant 1", "tmpl-1", "qwen2.5:7b"),
        )
        conn.commit()

    update_eval_variant(db, "V1", is_recommended=1, is_production=1)

    with db._lock:
        conn = db._connect()
        row = conn.execute("SELECT is_recommended, is_production FROM eval_variants WHERE id = 'V1'").fetchone()
    assert row["is_recommended"] == 1
    assert row["is_production"] == 1
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_eval_engine.py::test_update_eval_variant_sets_fields -v
```

Expected: FAIL (ImportError — `update_eval_variant` not defined)

**Step 3: Add `update_eval_variant` to `eval_engine.py`**

In `ollama_queue/eval_engine.py`, after `update_eval_run` (after line 141):

```python
def update_eval_variant(db: Database, variant_id: str, **kwargs: Any) -> None:
    """UPDATE eval_variants SET <kwargs> WHERE id=variant_id."""
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = [*list(kwargs.values()), variant_id]
    with db._lock:
        conn = db._connect()
        conn.execute(f"UPDATE eval_variants SET {cols} WHERE id = ?", vals)
        conn.commit()
```

**Step 4: Export from module** — add `update_eval_variant` to the import line in the test file (already done above).

**Step 5: Run test to verify it passes**

```bash
pytest tests/test_eval_engine.py::test_update_eval_variant_sets_fields -v
```

Expected: PASS

**Step 6: Commit**

```bash
git add ollama_queue/eval_engine.py tests/test_eval_engine.py
git commit -m "feat: add update_eval_variant helper"
```

---

## Task 3: `do_promote_eval_run` core function

**Files:**
- Modify: `ollama_queue/eval_engine.py` (new function after `update_eval_variant`)
- Test: `tests/test_api_eval_runs.py` (promote endpoint tests reuse this)

This function is the shared core used by both the API endpoint (Task 4) and `check_auto_promote` (Task 5).

**Step 1: Write failing tests**

Add to `tests/test_api_eval_runs.py`:

```python
from ollama_queue.eval_engine import (
    create_eval_run, insert_eval_result, update_eval_run,
    do_promote_eval_run,
)


def _make_variant(db, variant_id: str = "A") -> None:
    """Insert a minimal eval_variants row."""
    from ollama_queue.db import Database
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT OR IGNORE INTO eval_variants "
            "(id, label, prompt_template_id, model, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (variant_id, f"Config {variant_id}", "tmpl-1", "qwen2.5:7b"),
        )
        conn.commit()


class TestDoPromoteEvalRun:
    def test_raises_if_run_not_found(self, tmp_path):
        from ollama_queue.db import Database
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        with pytest.raises(ValueError, match="not found"):
            do_promote_eval_run(db, 9999)

    def test_raises_if_run_not_complete(self, tmp_path):
        from ollama_queue.db import Database
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        run_id = create_eval_run(db, variant_id="A")
        with pytest.raises(ValueError, match="not complete"):
            do_promote_eval_run(db, run_id)

    def test_raises_if_no_winner_variant(self, tmp_path):
        from ollama_queue.db import Database
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete")
        with pytest.raises(ValueError, match="no winner_variant"):
            do_promote_eval_run(db, run_id)

    def test_raises_if_variant_not_in_db(self, tmp_path):
        from ollama_queue.db import Database
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete", winner_variant="A")
        # No variant row inserted
        with pytest.raises(ValueError, match="not found in eval_variants"):
            do_promote_eval_run(db, run_id)

    def test_sets_winner_as_production(self, tmp_path):
        """Winner gets is_recommended=1 + is_production=1; others cleared."""
        from ollama_queue.db import Database
        import httpx
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        _make_variant(db, "A")
        _make_variant(db, "B")
        # Set B as currently production
        from ollama_queue.eval_engine import update_eval_variant
        update_eval_variant(db, "B", is_production=1, is_recommended=1)

        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete", winner_variant="A")

        # Mock the lessons-db call
        with patch("ollama_queue.eval_engine.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            result = do_promote_eval_run(db, run_id)

        assert result["ok"] is True
        assert result["variant_id"] == "A"

        # Check local DB: A is production, B is cleared
        with db._lock:
            conn = db._connect()
            a_row = conn.execute("SELECT is_recommended, is_production FROM eval_variants WHERE id='A'").fetchone()
            b_row = conn.execute("SELECT is_recommended, is_production FROM eval_variants WHERE id='B'").fetchone()
        assert a_row["is_production"] == 1
        assert a_row["is_recommended"] == 1
        assert b_row["is_production"] == 0
        assert b_row["is_recommended"] == 0

    def test_raises_502_if_lessons_db_unreachable(self, tmp_path):
        """Raises httpx.HTTPError when lessons-db is unreachable."""
        from ollama_queue.db import Database
        import httpx
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        _make_variant(db, "A")
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete", winner_variant="A")

        with patch("ollama_queue.eval_engine.httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(httpx.HTTPError):
                do_promote_eval_run(db, run_id)
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_eval_runs.py::TestDoPromoteEvalRun -v
```

Expected: FAIL (ImportError — `do_promote_eval_run` not defined)

**Step 3: Add `do_promote_eval_run` to `eval_engine.py`**

After `update_eval_variant`, add:

```python
def do_promote_eval_run(db: Database, run_id: int) -> dict:
    """Core promote logic: resolve winner variant, call lessons-db, update local DB.

    Returns {"ok": True, "run_id": run_id, "variant_id": variant_id, "label": label}.
    Raises ValueError for validation failures, httpx.HTTPError for lessons-db failures.
    Both callers (promote_eval_run API endpoint and check_auto_promote) use this function.
    """
    import httpx

    run = get_eval_run(db, run_id)
    if run is None:
        raise ValueError(f"Eval run {run_id} not found")
    if run["status"] != "complete":
        raise ValueError(f"Run {run_id} is not complete (status: {run['status']})")

    winner_variant = run.get("winner_variant")
    if not winner_variant:
        raise ValueError(f"Run {run_id} has no winner_variant")

    variant = get_eval_variant(db, winner_variant)
    if variant is None:
        raise ValueError(f"Variant {winner_variant!r} not found in eval_variants")

    # Call lessons-db to register the new production variant
    data_source_url = (
        run.get("data_source_url") or db.get_setting("eval.data_source_url") or "http://127.0.0.1:7685"
    )
    promote_url = f"{data_source_url.rstrip('/')}/eval/production-variant"
    payload = {
        "model": variant["model"],
        "prompt_template_id": variant["prompt_template_id"],
        "temperature": variant.get("temperature"),
        "num_ctx": variant.get("num_ctx"),
    }
    resp = httpx.post(promote_url, json=payload, timeout=10.0)
    if resp.status_code not in (200, 201, 204):
        raise httpx.HTTPStatusError(
            f"lessons-db promote endpoint returned HTTP {resp.status_code}",
            request=resp.request,
            response=resp,
        )

    # Update local eval_variants: winner becomes production + recommended
    update_eval_variant(db, winner_variant, is_recommended=1, is_production=1)
    # Clear production + recommended from all other variants
    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE eval_variants SET is_recommended = 0, is_production = 0 WHERE id != ?",
            (winner_variant,),
        )
        conn.commit()

    label = variant.get("label", winner_variant)
    _log.info("Promoted variant %s (label=%r) to production for run %d", winner_variant, label, run_id)
    return {"ok": True, "run_id": run_id, "variant_id": winner_variant, "label": label}
```

Also add `httpx` at the top of `eval_engine.py` if not already imported (check with grep first — it's already imported for proxy calls).

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_eval_runs.py::TestDoPromoteEvalRun -v
```

Expected: all 6 tests PASS

**Step 5: Commit**

```bash
git add ollama_queue/eval_engine.py tests/test_api_eval_runs.py
git commit -m "feat: add do_promote_eval_run shared core function"
```

---

## Task 4: Refactor `promote_eval_run` API endpoint

**Files:**
- Modify: `ollama_queue/api.py:1922-1971`
- Test: `tests/test_api_eval_runs.py`

**Step 1: Write failing tests**

Add to `tests/test_api_eval_runs.py`:

```python
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

        with patch("ollama_queue.eval_engine.httpx.post") as mock_post:
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

    def test_promote_returns_502_if_lessons_db_unreachable(self, client_and_db):
        """Returns 502 when lessons-db is unreachable."""
        client, db = client_and_db
        _make_variant(db, "A")
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="complete", winner_variant="A")

        import httpx
        with patch("ollama_queue.eval_engine.httpx.post", side_effect=httpx.ConnectError("refused")):
            resp = client.post(f"/api/eval/runs/{run_id}/promote", json={})
        assert resp.status_code == 502
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api_eval_runs.py::TestPromoteEvalRunEndpoint -v
```

Expected: FAIL (tests with empty body return 400 from old validation; auto-resolve + local DB update don't happen)

**Step 3: Replace `promote_eval_run` in `api.py`**

Replace lines 1922-1971 with:

```python
    @app.post("/api/eval/runs/{run_id}/promote")
    def promote_eval_run(run_id: int, body: dict = Body(default={})):
        """Mark a completed run's winner variant as the production variant.

        # What it shows: N/A — write action; updates lessons-db + local eval_variants.
        # Decision it drives: Promotes the winning eval config to production so the system
        #   uses it for future inference without manual DB edits.

        Accepts an empty body {}. Resolves the model/template/temperature/num_ctx
        automatically from the run's winner_variant in eval_variants.
        """
        from ollama_queue import eval_engine as _ee

        try:
            result = _ee.do_promote_eval_run(db, run_id)
            return result
        except ValueError as exc:
            msg = str(exc)
            if "not found" in msg and "eval_variants" not in msg:
                raise HTTPException(status_code=404, detail=msg)
            raise HTTPException(status_code=400, detail=msg)
        except httpx.HTTPError as exc:
            _log.warning("promote_eval_run: HTTP error for run %d: %s", run_id, exc)
            raise HTTPException(status_code=502, detail=f"Failed to reach lessons-db: {exc}") from exc
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api_eval_runs.py::TestPromoteEvalRunEndpoint tests/test_api_eval_runs.py::TestDoPromoteEvalRun -v
```

Expected: all PASS

**Step 5: Run full test suite to check for regressions**

```bash
pytest --timeout=120 -x -q
```

Expected: all existing tests pass

**Step 6: Commit**

```bash
git add ollama_queue/api.py tests/test_api_eval_runs.py
git commit -m "feat: refactor promote_eval_run to auto-resolve winner variant + update local DB"
```

---

## Task 5: `check_auto_promote` + wire into `run_eval_session`

**Files:**
- Modify: `ollama_queue/eval_engine.py` (new function + wire)
- Test: `tests/test_eval_engine.py`

**Step 1: Write failing tests**

Add to `tests/test_eval_engine.py`:

```python
from ollama_queue.eval_engine import check_auto_promote, update_eval_variant


class TestCheckAutoPromote:
    """Tests for check_auto_promote three-gate logic."""

    @pytest.fixture
    def db_with_complete_run(self, tmp_path):
        from ollama_queue.db import Database
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        # Enable auto-promote
        db.set_setting("eval.auto_promote", True)
        db.set_setting("eval.f1_threshold", 0.75)
        db.set_setting("eval.auto_promote_min_improvement", 0.05)
        db.set_setting("eval.error_budget", 0.30)
        db.set_setting("eval.stability_window", 0)  # disabled
        # Insert variant A
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO eval_variants (id, label, prompt_template_id, model, created_at) "
                "VALUES ('A', 'Config A', 'tmpl-1', 'qwen2.5:7b', datetime('now'))"
            )
            conn.commit()
        # Create a completed run with winner A, F1=0.85
        import json
        run_id = create_eval_run(db, variant_id="A")
        metrics = json.dumps({"A": {"f1": 0.85, "precision": 0.9, "recall": 0.8, "actionability": 0.8}})
        update_eval_run(db, run_id, status="complete", winner_variant="A", metrics=metrics,
                        item_count=10, error_budget=0.30)
        return db, run_id

    def test_skips_if_auto_promote_disabled(self, db_with_complete_run):
        db, run_id = db_with_complete_run
        db.set_setting("eval.auto_promote", False)
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_not_called()

    def test_skips_if_f1_below_threshold(self, db_with_complete_run):
        import json
        db, run_id = db_with_complete_run
        db.set_setting("eval.f1_threshold", 0.90)  # raise bar above winner F1=0.85
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_not_called()

    def test_skips_if_improvement_below_min(self, db_with_complete_run):
        """Skips if winner F1 doesn't beat production F1 + min_improvement."""
        import json
        db, run_id = db_with_complete_run
        # Insert production variant B with F1=0.82 in a previous run
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO eval_variants (id, label, prompt_template_id, model, "
                "is_production, created_at) VALUES ('B', 'Config B', 'tmpl-1', "
                "'qwen2.5:7b', 1, datetime('now'))"
            )
            conn.commit()
        old_run_id = create_eval_run(db, variant_id="B")
        old_metrics = json.dumps({"B": {"f1": 0.82, "precision": 0.85, "recall": 0.80, "actionability": 0.75}})
        update_eval_run(db, old_run_id, status="complete", winner_variant="B", metrics=old_metrics)

        # Winner A has F1=0.85, production B has F1=0.82. Delta=0.03 < min_improvement=0.05 → skip
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_not_called()

    def test_promotes_when_all_gates_pass(self, db_with_complete_run):
        """Auto-promotes when F1 ≥ threshold AND delta ≥ min_improvement AND error_budget ok."""
        db, run_id = db_with_complete_run
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            mock_promote.return_value = {"ok": True, "run_id": run_id, "variant_id": "A", "label": "Config A"}
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_called_once_with(db, run_id)

    def test_skips_if_error_budget_exceeded(self, db_with_complete_run):
        """Skips if too many eval_results failed (score_transfer IS NULL)."""
        db, run_id = db_with_complete_run
        db.set_setting("eval.error_budget", 0.05)  # 5% tolerance
        # Insert 5 failed results (no score) out of item_count=10 → 50% failure rate > 5%
        for i in range(5):
            insert_eval_result(
                db, run_id=run_id, variant="A",
                source_item_id=f"src-{i}", target_item_id=f"tgt-{i}",
                is_same_cluster=0, row_type="judge",
            )
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_not_called()

    def test_never_raises_on_unexpected_error(self, tmp_path):
        """check_auto_promote swallows all exceptions — never propagates."""
        from ollama_queue.db import Database
        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        db.set_setting("eval.auto_promote", True)
        # No run exists → get_eval_run returns None → should log and return without raising
        check_auto_promote(db, 9999, "http://localhost:7683")  # must not raise
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_eval_engine.py::TestCheckAutoPromote -v
```

Expected: FAIL (ImportError — `check_auto_promote` not defined)

**Step 3: Add `check_auto_promote` to `eval_engine.py`**

After `do_promote_eval_run`, add:

```python
def check_auto_promote(db: Database, run_id: int, http_base: str) -> None:  # noqa: ARG001
    """Check whether a completed eval run qualifies for auto-promotion.

    Three-gate criteria (all must pass):
    1. Winner F1 >= eval.f1_threshold
    2. Winner F1 > production_F1 + eval.auto_promote_min_improvement
       (gate skipped if no production variant exists)
    3. error_budget_used <= eval.error_budget

    Optional stability gate: winner must have cleared f1_threshold in the
    last eval.stability_window completed runs (if stability_window > 0).

    NEVER raises — all errors are logged and the function returns silently.
    Same contract as generate_eval_analysis.
    """
    try:
        _check_auto_promote_inner(db, run_id)
    except Exception:
        _log.exception("check_auto_promote: unhandled error for run_id=%d", run_id)


def _check_auto_promote_inner(db: Database, run_id: int) -> None:
    """Inner implementation called by check_auto_promote. May raise."""
    # Gate 0: auto-promote enabled?
    if not db.get_setting("eval.auto_promote"):
        return

    run = get_eval_run(db, run_id)
    if run is None or run.get("status") != "complete":
        return

    winner_variant = run.get("winner_variant")
    if not winner_variant:
        _log.info("check_auto_promote: run %d has no winner_variant, skipping", run_id)
        return

    # Parse winner F1 from metrics
    metrics_raw = run.get("metrics")
    try:
        parsed_metrics = json.loads(metrics_raw) if isinstance(metrics_raw, str) else (metrics_raw or {})
    except (json.JSONDecodeError, TypeError):
        _log.warning("check_auto_promote: run %d metrics unparseable, skipping", run_id)
        return

    winner_f1 = (parsed_metrics.get(winner_variant) or {}).get("f1")
    if winner_f1 is None:
        _log.info("check_auto_promote: run %d winner %s has no F1, skipping", run_id, winner_variant)
        return

    # Gate 1: F1 >= threshold
    f1_threshold = float(db.get_setting("eval.f1_threshold") or 0.75)
    if winner_f1 < f1_threshold:
        _log.info(
            "check_auto_promote: run %d winner F1=%.3f < threshold %.3f, skipping",
            run_id, winner_f1, f1_threshold,
        )
        return

    # Gate 2: F1 > production_F1 + min_improvement
    min_improvement = float(db.get_setting("eval.auto_promote_min_improvement") or 0.05)
    production_f1: float | None = None

    with db._lock:
        conn = db._connect()
        prod_row = conn.execute(
            "SELECT id FROM eval_variants WHERE is_production = 1 LIMIT 1"
        ).fetchone()

    if prod_row is not None:
        prod_id = prod_row["id"]
        with db._lock:
            conn = db._connect()
            prod_run_row = conn.execute(
                "SELECT metrics FROM eval_runs WHERE winner_variant = ? AND status = 'complete' "
                "ORDER BY id DESC LIMIT 1",
                (prod_id,),
            ).fetchone()
        if prod_run_row is not None:
            try:
                m = json.loads(prod_run_row["metrics"]) if isinstance(prod_run_row["metrics"], str) else {}
                production_f1 = (m.get(prod_id) or {}).get("f1")
            except (json.JSONDecodeError, TypeError):
                pass

        if production_f1 is not None and winner_f1 <= production_f1 + min_improvement:
            _log.info(
                "check_auto_promote: run %d winner F1=%.3f not enough improvement over "
                "production F1=%.3f (need +%.3f), skipping",
                run_id, winner_f1, production_f1, min_improvement,
            )
            return

    # Gate 3: error_budget_used <= error_budget
    error_budget = float(db.get_setting("eval.error_budget") or 0.30)
    item_count = run.get("item_count") or 0
    if item_count > 0:
        with db._lock:
            conn = db._connect()
            failed_count = conn.execute(
                "SELECT COUNT(*) FROM eval_results "
                "WHERE run_id = ? AND score_transfer IS NULL AND row_type = 'judge'",
                (run_id,),
            ).fetchone()[0]
        error_budget_used = failed_count / item_count
        if error_budget_used > error_budget:
            _log.info(
                "check_auto_promote: run %d error_budget_used=%.3f > %.3f, skipping",
                run_id, error_budget_used, error_budget,
            )
            return

    # Stability window gate (optional)
    stability_window = int(db.get_setting("eval.stability_window") or 0)
    if stability_window > 0:
        with db._lock:
            conn = db._connect()
            recent_rows = conn.execute(
                "SELECT metrics FROM eval_runs WHERE winner_variant = ? AND status = 'complete' "
                "ORDER BY id DESC LIMIT ?",
                (winner_variant, stability_window),
            ).fetchall()
        if len(recent_rows) < stability_window:
            _log.info(
                "check_auto_promote: variant %s only has %d/%d runs in stability window, skipping",
                winner_variant, len(recent_rows), stability_window,
            )
            return
        for row in recent_rows:
            try:
                m = json.loads(row["metrics"]) if isinstance(row["metrics"], str) else {}
                row_f1 = (m.get(winner_variant) or {}).get("f1")
                if row_f1 is None or row_f1 < f1_threshold:
                    _log.info(
                        "check_auto_promote: variant %s stability check failed (F1=%s < %.3f), skipping",
                        winner_variant, row_f1, f1_threshold,
                    )
                    return
            except (json.JSONDecodeError, TypeError):
                _log.warning("check_auto_promote: could not parse stability run metrics, skipping")
                return

    # All gates passed — auto-promote
    prod_str = f", +{winner_f1 - production_f1:.2f} over production={production_f1:.2f}" if production_f1 is not None else ""
    _log.info("Auto-promoting variant %s (F1=%.2f%s) for run %d", winner_variant, winner_f1, prod_str, run_id)
    do_promote_eval_run(db, run_id)
```

**Step 4: Wire into `run_eval_session`**

In `ollama_queue/eval_engine.py`, after line 1511 (`generate_eval_analysis(db, run_id, http_base)`):

```python
            check_auto_promote(db, run_id, http_base)
```

The block becomes:

```python
        run = get_eval_run(db, run_id)
        if run is not None and run.get("status") == "complete":
            generate_eval_analysis(db, run_id, http_base)
            check_auto_promote(db, run_id, http_base)
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/test_eval_engine.py::TestCheckAutoPromote -v
```

Expected: all 7 tests PASS

**Step 6: Run full suite**

```bash
pytest --timeout=120 -x -q
```

Expected: all pass

**Step 7: Commit**

```bash
git add ollama_queue/eval_engine.py tests/test_eval_engine.py
git commit -m "feat: add check_auto_promote three-gate logic + wire into run_eval_session"
```

---

## Task 6: Wire "Use this config" button in `RunRow.jsx`

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx`

No automated test for JSX. Visual verification after SPA rebuild.

**Step 1: Read current RunRow.jsx**

(Already read — lines 50-320 confirmed)

**Step 2: Add `promoteFb` + `handlePromote`**

In `RunRow.jsx`:

1. After line 53 (`const [analyzeFb, analyzeAct] = useActionFeedback();`), add:
   ```js
   const [promoteFb, promoteAct] = useActionFeedback();
   ```

2. After `handleRepeat` function (around line 106), add:
   ```js
   async function handlePromote(evt) {
     evt.stopPropagation();
     await promoteAct(
       'Promoting…',
       async () => {
         const res = await fetch(`${API}/eval/runs/${id}/promote`, {
           method: 'POST',
           headers: { 'Content-Type': 'application/json' },
           body: JSON.stringify({}),
         });
         let data = null;
         try { data = await res.json(); } catch { /* non-JSON body */ }
         if (!res.ok) throw new Error(data?.detail || `Promote failed: ${res.status}`);
         await fetchEvalRuns();
         await fetchEvalVariants();
         return data;
       },
       data => `Config ${data.variant_id} promoted to production`
     );
   }
   ```

3. Update the "Use this config" button (lines 266-270) to wire it:
   ```jsx
   {status === 'complete' && winner_variant && (
     <div>
       <button
         class="t-btn t-btn-primary"
         style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
         disabled={promoteFb.phase === 'loading'}
         onClick={handlePromote}
       >
         {promoteFb.phase === 'loading' ? 'Promoting…' : 'Use this config'}
       </button>
       {promoteFb.msg && <div class={`action-fb action-fb--${promoteFb.phase}`}>{promoteFb.msg}</div>}
     </div>
   )}
   ```

   **Note:** The original button was shown when `winner_variant` is truthy (line 266). The new version gates on `status === 'complete' && winner_variant` which matches the design doc (already gated in the design). Also update the `fetchEvalVariants` import in the import line:

   Current: `import { API, evalActiveRun, evalSubTab, fetchEvalRuns, startEvalPoll } from '../../store.js';`
   New: `import { API, evalActiveRun, evalSubTab, fetchEvalRuns, fetchEvalVariants, startEvalPoll } from '../../store.js';`

**Step 3: Rebuild SPA**

```bash
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build
```

Expected: build succeeds with no errors

**Step 4: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx ollama_queue/dashboard/spa/dist/
git commit -m "feat: wire Use this config button in RunRow to promote endpoint"
```

---

## Task 7: Auto-promote settings UI

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/GeneralSettings.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/eval/translations.js`

**Step 1: Add translations**

In `translations.js`, in the existing translations object, add after `stability_window` entry:

```js
  auto_promote:             { label: 'Auto-promote',           tooltip: 'Automatically promote the winner when all quality gates pass. Off by default.' },
  auto_promote_min_improvement: { label: 'Min improvement',   tooltip: 'Minimum quality score gain over current production required to auto-promote.' },
```

**Step 2: Read `GeneralSettings.jsx` to confirm current state**

(Already read — FIELD_DEFS drives numeric inputs; no boolean toggle exists yet)

**Step 3: Add new section to `GeneralSettings.jsx`**

The existing `FIELD_DEFS` array only supports number inputs. The new `auto_promote` field is a boolean toggle. Rather than contorting `FIELD_DEFS` to support booleans, add a separate `TOGGLE_DEFS` array and a separate render section.

Add after line 57 (after `FIELD_DEFS` closing bracket):

```js
// What it shows: Auto-promote toggle and minimum improvement threshold.
// Decision it drives: User opts into automatic promotion and sets the bar for how much
//   better a variant must be before it replaces the current production config.
const TOGGLE_DEFS = [
  {
    key:      'eval.auto_promote',
    transKey: 'auto_promote',
    default:  false,
  },
];

const IMPROVEMENT_DEFS = [
  {
    key:      'eval.auto_promote_min_improvement',
    transKey: 'auto_promote_min_improvement',
    type:     'number',
    min:      0.0,
    max:      1.0,
    step:     0.01,
    parse:    parseFloat,
    validate: v => v >= 0.0 && v <= 1.0 ? '' : 'Must be 0.0–1.0',
    default:  0.05,
  },
];
```

Update `useState` init to include toggle + improvement fields:

```js
  const [values, setValues] = useState(() => {
    const init = {};
    FIELD_DEFS.forEach(def => {
      init[def.key] = settings[def.key] != null ? def.parse(settings[def.key]) : def.default;
    });
    TOGGLE_DEFS.forEach(def => {
      init[def.key] = settings[def.key] != null ? Boolean(settings[def.key]) : def.default;
    });
    IMPROVEMENT_DEFS.forEach(def => {
      init[def.key] = settings[def.key] != null ? def.parse(settings[def.key]) : def.default;
    });
    return init;
  });
```

Update `handleSave` payload to include all keys:

```js
      const payload = {};
      FIELD_DEFS.forEach(def => { payload[def.key] = values[def.key]; });
      TOGGLE_DEFS.forEach(def => { payload[def.key] = values[def.key]; });
      IMPROVEMENT_DEFS.forEach(def => { payload[def.key] = values[def.key]; });
```

Update validation to include improvement fields:

```js
    IMPROVEMENT_DEFS.forEach(def => {
      const msg = def.validate(values[def.key]);
      if (msg) { newErrors[def.key] = msg; anyError = true; }
    });
```

Add render section in JSX, before the `<div class="eval-settings-form__footer">` closing:

```jsx
      {/* Auto-promote section */}
      <div style={{ marginTop: '1rem', borderTop: '1px solid var(--border-subtle)', paddingTop: '0.75rem' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
          Auto-promote
        </div>
        {TOGGLE_DEFS.map(def => {
          const trans = T[def.transKey] || { label: def.transKey, tooltip: null };
          return (
            <label key={def.key} class="eval-settings-label" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.75rem' }}>
              <input
                type="checkbox"
                checked={values[def.key]}
                onChange={evt => setValues(prev => ({ ...prev, [def.key]: evt.currentTarget.checked }))}
              />
              <span>
                {trans.label}
                {trans.tooltip && (
                  <span class="eval-tooltip-trigger" title={trans.tooltip} aria-label={trans.tooltip}> ?</span>
                )}
              </span>
            </label>
          );
        })}
        {IMPROVEMENT_DEFS.map(def => {
          const trans = T[def.transKey] || { label: def.transKey, tooltip: null };
          return (
            <label key={def.key} class="eval-settings-label">
              <span>
                {trans.label}
                {trans.tooltip && (
                  <span class="eval-tooltip-trigger" title={trans.tooltip} aria-label={trans.tooltip}> ?</span>
                )}
              </span>
              <input
                class="t-input eval-settings-input"
                type={def.type}
                min={def.min}
                max={def.max}
                step={def.step}
                value={values[def.key]}
                onInput={evt => handleChange(def.key, evt.currentTarget.value, def)}
              />
              {errors[def.key] && (
                <span class="eval-settings-error" role="alert">{errors[def.key]}</span>
              )}
            </label>
          );
        })}
      </div>
```

**Step 4: Rebuild SPA**

```bash
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build
```

Expected: build succeeds

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/eval/GeneralSettings.jsx
git add ollama_queue/dashboard/spa/src/components/eval/translations.js
git add ollama_queue/dashboard/spa/dist/
git commit -m "feat: add auto-promote toggle and min_improvement settings to eval UI"
```

---

## Task 8: Final verification

**Step 1: Run full test suite**

```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q
```

Expected: all tests pass (was 543 before this feature)

**Step 2: Rebuild SPA (final)**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 3: Restart service**

```bash
systemctl --user restart ollama-queue.service
sleep 2
systemctl --user status ollama-queue.service
```

Expected: active (running)

**Step 4: Verify "Use this config" button in browser**

- Navigate to `/queue/ui/` → Eval tab → Runs
- Find a `complete` run with a `winner_variant`
- Expand L2
- Click "Use this config"
- Expected: button shows "Promoting…" then success feedback "Config A promoted to production"
- Expected: Variants tab shows `★ Recommended` and `Production` badges on the winner

**Step 5: Verify auto-promote settings in UI**

- Navigate to Eval → Settings
- Expected: Auto-promote toggle + Min improvement input visible
- Toggle auto-promote on, save, reload — confirm it persists

---

## Success Criteria

- "Use this config" button calls `POST /api/eval/runs/{id}/promote` with `{}` → Variants tab updates immediately
- Promote auto-resolves winner from DB; returns 400 if no winner_variant; returns 502 if lessons-db unreachable
- `check_auto_promote` fires after every completed run; auto-promotes when all three gates pass
- Auto-promote defaults to `false`; must be explicitly enabled
- All tests pass; new tests cover promote endpoint + `do_promote_eval_run` + `check_auto_promote`
