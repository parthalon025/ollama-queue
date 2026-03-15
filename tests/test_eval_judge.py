"""Tests for eval judge post-HTTP cancellation re-check (#7) and parse failure tracking (#22)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ollama_queue.db import Database
from ollama_queue.eval.engine import (
    create_eval_run,
    get_eval_run,
    insert_eval_result,
    update_eval_run,
)
from ollama_queue.eval.judge import run_eval_judge

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


@pytest.fixture
def items():
    """Three items: two in cluster c1, one in cluster c2."""
    return [
        {
            "id": "101",
            "title": "Item A",
            "one_liner": "First",
            "description": "desc A",
            "cluster_id": "c1",
        },
        {
            "id": "102",
            "title": "Item B",
            "one_liner": "Second",
            "description": "desc B",
            "cluster_id": "c1",
        },
        {
            "id": "103",
            "title": "Item C",
            "one_liner": "Third",
            "description": "desc C",
            "cluster_id": "c2",
        },
    ]


def _seed_gen_results(db, run_id, variant="A", source_ids=("101",)):
    """Insert fake generate-phase results so judge has something to score."""
    for sid in source_ids:
        insert_eval_result(
            db,
            run_id=run_id,
            variant=variant,
            source_item_id=sid,
            source_item_title=f"Item {sid}",
            target_item_id=sid,
            is_same_cluster=0,
            row_type="generate",
            principle="Test principle for transfer",
            generation_time_s=1.0,
            queue_job_id=None,
            error=None,
        )


# ---------------------------------------------------------------------------
# Post-HTTP cancellation re-check (rubric mode)
# ---------------------------------------------------------------------------


class TestJudgePostHTTPCancellation:
    """Verify the judge loop stops if the run is cancelled during an HTTP call."""

    def test_stops_after_cancellation_during_judge_call(self, db, items):
        """If run becomes cancelled during _judge_one_target, loop exits."""
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="judging", stage="judging", seed=42)
        _seed_gen_results(db, run_id, source_ids=("101", "102"))

        call_count = 0

        def fake_judge_one_target(**kwargs):
            nonlocal call_count
            call_count += 1
            # Cancel the run after the first judge call
            update_eval_run(db, run_id, status="cancelled")
            return False  # no parse failure

        with (
            patch("ollama_queue.eval.judge._eng._fetch_items", return_value=items),
            patch("ollama_queue.eval.judge._judge_one_target", side_effect=fake_judge_one_target),
        ):
            run_eval_judge(run_id, db)

        # Should stop after first judge call, not process all targets
        assert call_count == 1

    def test_stops_after_failed_during_judge_call(self, db, items):
        """If run becomes failed during _judge_one_target, loop exits."""
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="judging", stage="judging", seed=42)
        _seed_gen_results(db, run_id, source_ids=("101",))

        call_count = 0

        def fake_judge_one_target(**kwargs):
            nonlocal call_count
            call_count += 1
            update_eval_run(db, run_id, status="failed", error="external")
            return False

        with (
            patch("ollama_queue.eval.judge._eng._fetch_items", return_value=items),
            patch("ollama_queue.eval.judge._judge_one_target", side_effect=fake_judge_one_target),
        ):
            run_eval_judge(run_id, db)

        assert call_count == 1


# ---------------------------------------------------------------------------
# Parse failure tracking (#22)
# ---------------------------------------------------------------------------


class TestJudgeParseFailureTracking:
    """Verify parse_failures counter is tracked and stored on the run."""

    def test_parse_failures_counted_and_stored(self, db, items):
        """When _judge_one_target returns True (parse_failed), count is stored."""
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="judging", stage="judging", seed=42)
        _seed_gen_results(db, run_id, source_ids=("101",))

        def fake_judge_always_parse_fail(**kwargs):
            return True  # parse failure

        with (
            patch("ollama_queue.eval.judge._eng._fetch_items", return_value=items),
            patch("ollama_queue.eval.judge._judge_one_target", side_effect=fake_judge_always_parse_fail),
            patch("ollama_queue.eval.judge._eng._fetch_scored_rows", return_value=[]),
            patch("ollama_queue.eval.judge._eng.compute_metrics", return_value={}),
            patch("ollama_queue.eval.judge._eng.render_report", return_value=""),
        ):
            run_eval_judge(run_id, db)

        run = get_eval_run(db, run_id)
        assert run is not None
        assert run["judge_parse_failures"] > 0

    def test_zero_parse_failures_not_stored(self, db, items):
        """When no parse failures, judge_parse_failures stays at default (0)."""
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="judging", stage="judging", seed=42)
        _seed_gen_results(db, run_id, source_ids=("101",))

        def fake_judge_no_parse_fail(**kwargs):
            return False  # no parse failure

        with (
            patch("ollama_queue.eval.judge._eng._fetch_items", return_value=items),
            patch("ollama_queue.eval.judge._judge_one_target", side_effect=fake_judge_no_parse_fail),
            patch("ollama_queue.eval.judge._eng._fetch_scored_rows", return_value=[]),
            patch("ollama_queue.eval.judge._eng.compute_metrics", return_value={}),
            patch("ollama_queue.eval.judge._eng.render_report", return_value=""),
        ):
            run_eval_judge(run_id, db)

        run = get_eval_run(db, run_id)
        assert run is not None
        # Should be 0 (default) — update_eval_run is not called for 0 failures
        assert run.get("judge_parse_failures", 0) == 0


# ---------------------------------------------------------------------------
# Missing judge model fails explicitly (#6)
# ---------------------------------------------------------------------------


class TestJudgeFailsWithoutJudgeModel:
    """Judge should fail explicitly when no judge model is configured."""

    def test_fails_when_no_judge_model_configured(self, db, items):
        """run_eval_judge must fail the run when judge_model resolves to empty string."""
        run_id = create_eval_run(db, variant_id="A")
        # No judge_model on the run, and no eval.judge_model in settings
        update_eval_run(db, run_id, status="judging", stage="judging", seed=42)
        _seed_gen_results(db, run_id, source_ids=("101",))

        # Ensure no judge model setting exists
        with db._lock:
            conn = db._connect()
            conn.execute("DELETE FROM settings WHERE key = 'eval.judge_model'")
            conn.commit()

        with (
            patch("ollama_queue.eval.judge._eng._fetch_items", return_value=items) as mock_fetch,
        ):
            run_eval_judge(run_id, db)

        run = get_eval_run(db, run_id)
        assert run is not None
        assert run["status"] == "failed"
        assert "judge model" in run["error"].lower() or "No judge model" in run["error"]
        # _fetch_items should NOT have been called (we fail before fetching items)
        mock_fetch.assert_not_called()

    def test_uses_explicit_judge_model_when_set(self, db, items):
        """run_eval_judge proceeds normally when judge_model is explicitly set on the run."""
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="judging", stage="judging", seed=42, judge_model="qwen2.5:7b")
        _seed_gen_results(db, run_id, source_ids=("101",))

        def fake_judge(**kwargs):
            return False

        with (
            patch("ollama_queue.eval.judge._eng._fetch_items", return_value=items),
            patch("ollama_queue.eval.judge._judge_one_target", side_effect=fake_judge),
            patch("ollama_queue.eval.judge._eng._fetch_scored_rows", return_value=[]),
            patch("ollama_queue.eval.judge._eng.compute_metrics", return_value={}),
            patch("ollama_queue.eval.judge._eng.render_report", return_value=""),
        ):
            run_eval_judge(run_id, db)

        run = get_eval_run(db, run_id)
        assert run is not None
        # Should complete (not fail) since judge_model was explicitly set
        assert run["status"] == "complete"
