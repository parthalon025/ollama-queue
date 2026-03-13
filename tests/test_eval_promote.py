"""Tests for ollama_queue.eval.promote — auto-promote gate logic."""

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
