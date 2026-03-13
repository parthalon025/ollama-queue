"""Tests for run_eval_generate corruption-check logic (H10) and post-HTTP cancellation re-check (#7).

Specifically: null-principle rows from interrupted runs must be deleted and
regenerated. Without the fix, INSERT OR IGNORE in insert_eval_result silently
skips the corrupted row, leaving it in the DB indefinitely.
"""

from __future__ import annotations

from unittest.mock import patch

from ollama_queue.db import Database
from ollama_queue.eval.generate import run_eval_generate


def test_corrupted_null_principle_row_deleted_before_regeneration(tmp_path):
    """Null-principle rows from interrupted runs must be deleted and regenerated.

    Bug (H10): if a generation run is interrupted mid-stream, eval_results may
    contain rows where principle IS NULL and error IS NULL. On the next run,
    insert_eval_result uses INSERT OR IGNORE, silently skipping the corrupt row.
    The fix: detect and delete such rows BEFORE calling _generate_one.
    """
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    # --- Set up a minimal eval run + corrupted generation row ---
    with db._lock:
        conn = db._connect()
        conn.execute(
            """
            INSERT INTO eval_runs (id, run_mode, data_source_url, variants,
                status, error_budget, item_ids, item_count)
            VALUES (99, 'batch', 'http://127.0.0.1', '["A"]',
                'generating', 0.3, NULL, NULL)
            """
        )
        # Corrupted generation row: principle IS NULL, error IS NULL
        conn.execute(
            """
            INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id,
                is_same_cluster, row_type, principle, error)
            VALUES (99, 'A', 'item-1', 'item-1', 0, 'generate', NULL, NULL)
            """
        )
        conn.commit()

    # Verify the corrupted row exists before the fix runs
    with db._lock:
        pre_count = (
            db._connect()
            .execute("SELECT COUNT(*) FROM eval_results WHERE run_id=99 AND principle IS NULL AND error IS NULL")
            .fetchone()[0]
        )
    assert pre_count == 1, "Pre-condition: corrupted row should exist before fix"

    # --- Mock out all network/proxy calls so only the corruption-check runs ---
    with (
        patch("ollama_queue.eval.generate._generate_one") as mock_gen,
        patch("ollama_queue.eval.engine._fetch_items") as mock_fetch,
        patch("ollama_queue.eval.engine.get_eval_variant") as mock_variant,
        patch("ollama_queue.eval.engine.get_eval_template") as mock_template,
        patch("ollama_queue.eval.engine._ensure_seed"),
        patch("ollama_queue.eval.engine._get_eval_setting", return_value=""),
        patch("ollama_queue.eval.engine.update_eval_run"),
    ):
        mock_fetch.return_value = [{"id": "item-1", "title": "T", "one_liner": "o", "cluster_id": "c1"}]
        mock_variant.return_value = {
            "id": "A",
            "model": "test-model",
            "prompt_template_id": "t1",
            "temperature": 0.6,
            "num_ctx": 8192,
            "params": None,
            "system_prompt": None,
        }
        mock_template.return_value = {
            "id": "t1",
            "instruction": "extract",
            "is_chunked": False,
            "is_contrastive": False,
            "is_multi_stage": False,
            "examples": None,
        }
        mock_gen.return_value = True

        run_eval_generate(99, db, _sleep=lambda s: None)

    # --- Primary assertion: corrupted row was deleted before regeneration ---
    with db._lock:
        post_count = (
            db._connect()
            .execute("SELECT COUNT(*) FROM eval_results WHERE run_id=99 AND principle IS NULL AND error IS NULL")
            .fetchone()[0]
        )
    assert post_count == 0, "Corrupted null-principle row should be deleted before regeneration attempt"

    # --- Secondary assertion: _generate_one was called (regeneration happened) ---
    mock_gen.assert_called_once()


def test_legitimate_error_row_not_deleted(tmp_path):
    """Rows with principle IS NULL but error IS NOT NULL must NOT be deleted.

    These are legitimate failures (e.g. generation_failed), not corruption.
    Only the (principle IS NULL AND error IS NULL) tuple indicates interrupted runs.
    """
    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    with db._lock:
        conn = db._connect()
        conn.execute(
            """
            INSERT INTO eval_runs (id, run_mode, data_source_url, variants,
                status, error_budget, item_ids, item_count)
            VALUES (99, 'batch', 'http://127.0.0.1', '["A"]',
                'generating', 0.3, NULL, NULL)
            """
        )
        # Legitimate failure row: principle IS NULL, error IS NOT NULL
        conn.execute(
            """
            INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id,
                is_same_cluster, row_type, principle, error)
            VALUES (99, 'A', 'item-1', 'item-1', 0, 'generate', NULL, 'generation_failed')
            """
        )
        conn.commit()

    with (
        patch("ollama_queue.eval.generate._generate_one") as mock_gen,
        patch("ollama_queue.eval.engine._fetch_items") as mock_fetch,
        patch("ollama_queue.eval.engine.get_eval_variant") as mock_variant,
        patch("ollama_queue.eval.engine.get_eval_template") as mock_template,
        patch("ollama_queue.eval.engine._ensure_seed"),
        patch("ollama_queue.eval.engine._get_eval_setting", return_value=""),
        patch("ollama_queue.eval.engine.update_eval_run"),
    ):
        mock_fetch.return_value = [{"id": "item-1", "title": "T", "one_liner": "o", "cluster_id": "c1"}]
        mock_variant.return_value = {
            "id": "A",
            "model": "test-model",
            "prompt_template_id": "t1",
            "temperature": 0.6,
            "num_ctx": 8192,
            "params": None,
            "system_prompt": None,
        }
        mock_template.return_value = {
            "id": "t1",
            "instruction": "extract",
            "is_chunked": False,
            "is_contrastive": False,
            "is_multi_stage": False,
            "examples": None,
        }
        mock_gen.return_value = True

        run_eval_generate(99, db, _sleep=lambda s: None)

    # The legitimate error row should still be present (not deleted)
    with db._lock:
        error_row = (
            db._connect().execute("SELECT * FROM eval_results WHERE run_id=99 AND error='generation_failed'").fetchone()
        )
    assert error_row is not None, "Legitimate error row (principle NULL, error NOT NULL) must not be deleted"


import pytest

from ollama_queue.eval.engine import (
    create_eval_run,
    update_eval_run,
)

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
    return [
        {
            "id": "101",
            "title": "Item A",
            "one_liner": "First item",
            "description": "desc A",
            "cluster_id": "c1",
            "category": "cat",
        },
        {
            "id": "102",
            "title": "Item B",
            "one_liner": "Second item",
            "description": "desc B",
            "cluster_id": "c1",
            "category": "cat",
        },
        {
            "id": "103",
            "title": "Item C",
            "one_liner": "Third item",
            "description": "desc C",
            "cluster_id": "c2",
            "category": "cat",
        },
    ]


# ---------------------------------------------------------------------------
# Post-HTTP cancellation re-check
# ---------------------------------------------------------------------------


class TestGeneratePostHTTPCancellation:
    """Verify that if a run is cancelled while _generate_one() is blocking
    on an HTTP call, the loop stops immediately after it returns."""

    def test_stops_after_cancellation_during_http_call(self, db, items):
        """If run status becomes 'cancelled' during _generate_one, no more items processed."""
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="generating", stage="generating")

        call_count = 0

        def fake_generate_one(**kwargs):
            nonlocal call_count
            call_count += 1
            # After the first HTTP call completes, cancel the run
            update_eval_run(db, run_id, status="cancelled")
            return True

        variant = {
            "id": "A",
            "model": "test:7b",
            "prompt_template_id": "zero-shot-causal",
            "temperature": 0.6,
            "num_ctx": 8192,
            "params": "{}",
            "system_prompt": None,
        }
        template = {
            "id": "zero-shot-causal",
            "instruction": "Extract principle",
            "examples": None,
            "is_chunked": 0,
            "is_contrastive": 0,
            "is_multi_stage": 0,
        }

        with (
            patch("ollama_queue.eval.generate._eng._fetch_items", return_value=items),
            patch("ollama_queue.eval.generate._eng.get_eval_variant", return_value=variant),
            patch("ollama_queue.eval.generate._eng.get_eval_template", return_value=template),
            patch("ollama_queue.eval.generate._generate_one", side_effect=fake_generate_one),
            patch("ollama_queue.eval.generate._eng._ensure_seed"),
        ):
            run_eval_generate(run_id, db)

        # Only ONE item should be processed before the loop exits
        assert call_count == 1

    def test_stops_after_failed_during_http_call(self, db, items):
        """If run status becomes 'failed' during _generate_one, loop exits."""
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="generating", stage="generating")

        call_count = 0

        def fake_generate_one(**kwargs):
            nonlocal call_count
            call_count += 1
            # Mark as failed during the HTTP call
            update_eval_run(db, run_id, status="failed", error="external_failure")
            return True

        variant = {
            "id": "A",
            "model": "test:7b",
            "prompt_template_id": "zero-shot-causal",
            "temperature": 0.6,
            "num_ctx": 8192,
            "params": "{}",
            "system_prompt": None,
        }
        template = {
            "id": "zero-shot-causal",
            "instruction": "Extract principle",
            "examples": None,
            "is_chunked": 0,
            "is_contrastive": 0,
            "is_multi_stage": 0,
        }

        with (
            patch("ollama_queue.eval.generate._eng._fetch_items", return_value=items),
            patch("ollama_queue.eval.generate._eng.get_eval_variant", return_value=variant),
            patch("ollama_queue.eval.generate._eng.get_eval_template", return_value=template),
            patch("ollama_queue.eval.generate._generate_one", side_effect=fake_generate_one),
            patch("ollama_queue.eval.generate._eng._ensure_seed"),
        ):
            run_eval_generate(run_id, db)

        assert call_count == 1

    def test_stops_after_run_deleted_during_http_call(self, db, items):
        """If run row is deleted during _generate_one (returns None), loop exits."""
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="generating", stage="generating")

        call_count = 0

        def fake_generate_one(**kwargs):
            nonlocal call_count
            call_count += 1
            # Simulate run deletion by setting a bogus status we can detect
            # Actually delete the row
            with db._lock:
                conn = db._connect()
                conn.execute("DELETE FROM eval_runs WHERE id = ?", (run_id,))
                conn.commit()
            return True

        variant = {
            "id": "A",
            "model": "test:7b",
            "prompt_template_id": "zero-shot-causal",
            "temperature": 0.6,
            "num_ctx": 8192,
            "params": "{}",
            "system_prompt": None,
        }
        template = {
            "id": "zero-shot-causal",
            "instruction": "Extract principle",
            "examples": None,
            "is_chunked": 0,
            "is_contrastive": 0,
            "is_multi_stage": 0,
        }

        with (
            patch("ollama_queue.eval.generate._eng._fetch_items", return_value=items),
            patch("ollama_queue.eval.generate._eng.get_eval_variant", return_value=variant),
            patch("ollama_queue.eval.generate._eng.get_eval_template", return_value=template),
            patch("ollama_queue.eval.generate._generate_one", side_effect=fake_generate_one),
            patch("ollama_queue.eval.generate._eng._ensure_seed"),
        ):
            run_eval_generate(run_id, db)

        assert call_count == 1

    def test_continues_when_not_cancelled(self, db, items):
        """Normal case: if run is still generating, all items are processed."""
        run_id = create_eval_run(db, variant_id="A")
        update_eval_run(db, run_id, status="generating", stage="generating")

        call_count = 0

        def fake_generate_one(**kwargs):
            nonlocal call_count
            call_count += 1
            return True

        variant = {
            "id": "A",
            "model": "test:7b",
            "prompt_template_id": "zero-shot-causal",
            "temperature": 0.6,
            "num_ctx": 8192,
            "params": "{}",
            "system_prompt": None,
        }
        template = {
            "id": "zero-shot-causal",
            "instruction": "Extract principle",
            "examples": None,
            "is_chunked": 0,
            "is_contrastive": 0,
            "is_multi_stage": 0,
        }

        with (
            patch("ollama_queue.eval.generate._eng._fetch_items", return_value=items),
            patch("ollama_queue.eval.generate._eng.get_eval_variant", return_value=variant),
            patch("ollama_queue.eval.generate._eng.get_eval_template", return_value=template),
            patch("ollama_queue.eval.generate._generate_one", side_effect=fake_generate_one),
            patch("ollama_queue.eval.generate._eng._ensure_seed"),
        ):
            run_eval_generate(run_id, db)

        # All 3 items should be processed
        assert call_count == len(items)
