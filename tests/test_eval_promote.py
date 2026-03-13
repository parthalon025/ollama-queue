"""Tests for ollama_queue.eval.promote — auto-promote gate logic and first-ever run guard."""

from __future__ import annotations

import json
from unittest.mock import patch

from ollama_queue.db import Database
from ollama_queue.eval.engine import create_eval_run, update_eval_run
from ollama_queue.eval.promote import check_auto_promote

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path):
    """Return an initialized Database with auto-promote enabled and default gate settings."""
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    db.set_setting("eval.auto_promote", True)
    db.set_setting("eval.f1_threshold", 0.75)
    db.set_setting("eval.auto_promote_min_improvement", 0.05)
    db.set_setting("eval.error_budget", 0.30)
    db.set_setting("eval.stability_window", 0)
    return db


# ---------------------------------------------------------------------------
# C4: Gate 2 — production variant exists but has never been evaluated
# ---------------------------------------------------------------------------


class TestGate2ProdNoBaseline:
    """C4: prod_row exists but prod_run_row is None → must block, not silently promote."""

    def test_check_auto_promote_blocks_when_prod_has_no_baseline(self, tmp_path):
        """Gate 2 must return without promoting when production variant exists but has no
        completed eval run (no baseline to compare against).

        Without the fix this test exposes the silent pass-through: production_quality is None,
        the `if production_quality is not None` guard short-circuits, Gate 2 is skipped,
        and do_promote_eval_run is called even though we have no baseline to verify
        the new variant is actually an improvement.
        """
        db = _make_db(tmp_path)

        # Winner run: variant A, F1=0.88 — passes Gate 1 (threshold=0.75)
        run_id = create_eval_run(db, variant_id="A")
        metrics = json.dumps({"A": {"f1": 0.88, "precision": 0.90, "recall": 0.86, "actionability": 0.80}})
        update_eval_run(
            db,
            run_id,
            status="complete",
            winner_variant="A",
            metrics=metrics,
            item_count=10,
            error_budget=0.30,
        )

        # Mark variant B as production — it exists but has NEVER been evaluated
        # (no eval_run with winner_variant='B' and status='complete')
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE eval_variants SET is_production = 1 WHERE id = 'B'")
            conn.commit()

        # Expect: do_promote_eval_run is NOT called — no baseline means we can't confirm improvement
        with patch("ollama_queue.eval.promote.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")

        mock_promote.assert_not_called()


# ---------------------------------------------------------------------------
# H1: Gate 3 — error budget denominator must be judge_row_count, not item_count
# ---------------------------------------------------------------------------


class TestGate3JudgeRowCountDenominator:
    """H1: Gate 3 must use the count of judged rows, not source item_count, as denominator."""

    def test_gate3_uses_judge_row_count_not_source_items(self, tmp_path):
        """Gate 3 must NOT auto-promote when errors / judge_row_count exceeds the budget,
        even if errors / source_item_count would appear within budget.

        Setup:
          - source_item_count = 100  (stored as item_count on the run)
          - judge_row_count   =  40  (only 40 rows written to eval_results)
          - error_count       =   5  (5 rows with score_transfer IS NULL)
          - error_budget      = 0.10 (10%)

        With old (buggy) denominator:  5 / 100 = 0.05 <= 0.10  → gate passes → promotes
        With correct denominator:      5 / 40  = 0.125 > 0.10  → gate blocks  → no promote
        """
        from ollama_queue.eval.engine import create_eval_run, insert_eval_result, update_eval_run

        db = _make_db(tmp_path)
        db.set_setting("eval.error_budget", 0.10)

        # Create a run with item_count=100 (full dataset size) but only 40 judge rows
        run_id = create_eval_run(db, variant_id="A")
        metrics = json.dumps({"A": {"f1": 0.88, "precision": 0.90, "recall": 0.86}})
        update_eval_run(
            db,
            run_id,
            status="complete",
            winner_variant="A",
            metrics=metrics,
            item_count=100,  # full source dataset — this is the WRONG denominator
        )

        # Insert 40 judge rows for this run: 35 successful, 5 failed (score_transfer IS NULL)
        for i in range(35):
            insert_eval_result(
                db,
                run_id=run_id,
                variant="A",
                source_item_id=f"src_{i}",
                target_item_id=f"tgt_{i}",
                row_type="judge",
                is_same_cluster=1,
                score_transfer=4,  # valid score
            )
        for i in range(35, 40):
            insert_eval_result(
                db,
                run_id=run_id,
                variant="A",
                source_item_id=f"src_{i}",
                target_item_id=f"tgt_{i}",
                row_type="judge",
                is_same_cluster=1,
                score_transfer=None,  # failed / missing score
            )

        # No production variant — Gate 2 passes (no prod baseline to compare against)
        # Gate 3: 5 errors / 40 judged = 12.5% > 10% budget → must block
        with patch("ollama_queue.eval.promote.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")

        mock_promote.assert_not_called()


# ---------------------------------------------------------------------------
# Gate 3 — zero judge rows must block promotion
# ---------------------------------------------------------------------------


class TestGate3BlocksWhenNoJudgeRows:
    """Gate 3 must block auto-promotion when there are zero judged rows.

    A run with no judge rows has an undefined error rate — it is a pipeline
    failure, not a clean pass.  The original code silently skipped Gate 3
    entirely when judge_row_count == 0, allowing promotion to proceed.
    """

    def test_gate3_blocks_when_no_judge_rows(self, tmp_path):
        """Gate 3 must NOT auto-promote when there are zero eval_results rows
        with row_type='judge' for the run.

        Setup:
          - Completed run with winner_variant 'A', F1=0.88 (passes Gate 1)
          - No production variant (Gate 2 passes — nothing to compare against)
          - Zero judge rows inserted into eval_results
          - error_budget = 0.30 (default)

        With old guard (`if judge_row_count > 0:`): Gate 3 body is skipped
        entirely → promotion proceeds → do_promote_eval_run IS called (wrong).

        With fix (`if judge_row_count == 0: return`): function returns early
        → do_promote_eval_run is NOT called (correct).
        """
        from ollama_queue.eval.engine import create_eval_run, update_eval_run

        db = _make_db(tmp_path)

        # Create a completed run with a winner that satisfies Gates 1 and 2
        run_id = create_eval_run(db, variant_id="A")
        metrics = json.dumps({"A": {"f1": 0.88, "precision": 0.90, "recall": 0.86}})
        update_eval_run(
            db,
            run_id,
            status="complete",
            winner_variant="A",
            metrics=metrics,
            item_count=50,
        )

        # Insert NO eval_results rows — judge_row_count == 0
        # (no production variant either — Gate 2 is a clean pass)

        with patch("ollama_queue.eval.promote.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")


import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


def _setup_completed_run(db, variant_id="A", f1=0.90, auc=0.90):
    """Create a completed run with metrics that would pass all quality gates."""
    run_id = create_eval_run(db, variant_id=variant_id)
    metrics = {
        variant_id: {
            "f1": f1,
            "recall": 0.85,
            "precision": 0.95,
            "auc": auc,
            "separation": 0.6,
        }
    }
    update_eval_run(
        db,
        run_id,
        status="complete",
        winner_variant=variant_id,
        metrics=json.dumps(metrics),
        item_count=10,
    )
    return run_id


# ---------------------------------------------------------------------------
# First-ever eval run blocks auto-promote (#8)
# ---------------------------------------------------------------------------


class TestFirstEverRunBlocksAutoPromote:
    """When no production variant exists, auto-promote must be blocked."""

    def test_no_production_variant_blocks_auto_promote(self, db):
        """With no is_production=1 variant, auto-promote returns without promoting."""
        # Enable auto-promote
        db.set_setting("eval.auto_promote", True)
        db.set_setting("eval.f1_threshold", 0.75)

        run_id = _setup_completed_run(db, variant_id="A", f1=0.95)

        # Ensure no variant is marked production
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE eval_variants SET is_production = 0")
            conn.commit()

        # Mock do_promote_eval_run to track if it's called
        with patch("ollama_queue.eval.promote.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://127.0.0.1:7683")

        # Promote should NOT be called — first run requires manual promote
        mock_promote.assert_not_called()

    def test_with_production_variant_allows_auto_promote(self, db):
        """When a production variant exists AND quality exceeds threshold,
        auto-promote should proceed."""
        db.set_setting("eval.auto_promote", True)
        db.set_setting("eval.f1_threshold", 0.75)
        db.set_setting("eval.auto_promote_min_improvement", 0.05)
        db.set_setting("eval.stability_window", 0)

        # Set up variant A as current production with a prior run at lower F1
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE eval_variants SET is_production = 1 WHERE id = 'A'")
            conn.commit()
        _setup_completed_run(db, variant_id="A", f1=0.75)

        # New run with variant B has higher F1 — should be auto-promoted
        run_id = _setup_completed_run(db, variant_id="B", f1=0.90)

        with patch("ollama_queue.eval.promote.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://127.0.0.1:7683")

        # Promote SHOULD be called — production A exists (F1=0.75) and B (F1=0.90) improved
        mock_promote.assert_called_once_with(db, run_id)

    def test_no_production_variant_even_with_high_f1(self, db):
        """Even with excellent F1, first run without production baseline is blocked."""
        db.set_setting("eval.auto_promote", True)
        db.set_setting("eval.f1_threshold", 0.50)

        run_id = _setup_completed_run(db, variant_id="A", f1=1.0)

        # No production variant
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE eval_variants SET is_production = 0")
            conn.commit()

        with patch("ollama_queue.eval.promote.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://127.0.0.1:7683")

        mock_promote.assert_not_called()

    def test_bayesian_mode_also_blocked_without_production(self, db):
        """Bayesian judge_mode is also blocked when no production variant exists."""
        db.set_setting("eval.auto_promote", True)
        db.set_setting("eval.auc_threshold", 0.80)

        run_id = _setup_completed_run(db, variant_id="A", auc=0.95)
        update_eval_run(db, run_id, judge_mode="bayesian")

        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE eval_variants SET is_production = 0")
            conn.commit()

        with patch("ollama_queue.eval.promote.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://127.0.0.1:7683")

        mock_promote.assert_not_called()
