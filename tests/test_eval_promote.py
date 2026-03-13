"""Tests for eval promote — first-ever run requires manual promote (#8)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ollama_queue.db import Database
from ollama_queue.eval.engine import (
    create_eval_run,
    update_eval_run,
)
from ollama_queue.eval.promote import check_auto_promote

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
