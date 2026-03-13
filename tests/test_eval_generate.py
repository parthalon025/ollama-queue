"""Tests for eval generate post-HTTP cancellation re-check (#7)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from ollama_queue.db import Database
from ollama_queue.eval.engine import (
    create_eval_run,
    update_eval_run,
)
from ollama_queue.eval.generate import run_eval_generate

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
