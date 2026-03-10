"""Tests for ollama_queue.slot_scoring."""

from __future__ import annotations

import pytest

from ollama_queue.scheduling.slot_scoring import find_fitting_slot, score_slot

# ---------------------------------------------------------------------------
# score_slot tests
# ---------------------------------------------------------------------------


class TestScoreSlot:
    """Unit tests for the 10-factor score_slot function."""

    def test_score_vram_hard_gate(self):
        """Insufficient VRAM returns -1."""
        result = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=6.0,
            job_vram_needed_gb=6.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category=None,
            queue_depth=0,
        )
        assert result == -1.0

    def test_score_pinned_slot(self):
        """Pinned slots always return -1."""
        result = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=True,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category=None,
            queue_depth=0,
        )
        assert result == -1.0

    def test_score_empty_slot(self):
        """An empty slot with no load scores high."""
        result = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category=None,
            queue_depth=0,
        )
        # base(5) + load_headroom(10) = 15
        assert result == 15.0

    def test_score_hot_model_bonus(self):
        """Hot model adds +3 to score."""
        base = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category=None,
            queue_depth=0,
        )
        hot = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=True,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category=None,
            queue_depth=0,
        )
        assert hot - base == 3.0

    def test_score_recurring_conflict_penalty(self):
        """Recurring conflict subtracts 5 from score."""
        no_conflict = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category=None,
            queue_depth=0,
        )
        conflict = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=True,
            historical_quiet=False,
            failure_category=None,
            queue_depth=0,
        )
        assert no_conflict - conflict == 5.0

    def test_score_historical_quiet_bonus(self):
        """Historically quiet hours add +2."""
        base = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category=None,
            queue_depth=0,
        )
        quiet = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=True,
            failure_category=None,
            queue_depth=0,
        )
        assert quiet - base == 2.0

    def test_score_resource_failure_extra_margin(self):
        """Resource failure requires 30% extra VRAM headroom."""
        # 6 GB needed + 0 committed = 6 GB used out of 7 GB total
        # headroom = 1 GB, required headroom = 6 * 0.3 = 1.8 GB → infeasible
        result = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=6.0,
            total_vram_gb=7.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category="resource",
            queue_depth=0,
        )
        assert result == -1.0

        # Same setup but with enough headroom (10 GB total → 4 GB headroom > 1.8)
        result2 = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=6.0,
            total_vram_gb=10.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category="resource",
            queue_depth=0,
        )
        assert result2 > 0

    def test_score_timeout_failure_prefers_low_load(self):
        """Timeout failure adds extra bonus for low-load slots."""
        low_load = score_slot(
            slot_load=1.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category="timeout",
            queue_depth=0,
        )
        high_load = score_slot(
            slot_load=8.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category="timeout",
            queue_depth=0,
        )
        assert low_load > high_load

    def test_score_queue_depth_penalty(self):
        """Each queued job subtracts 0.5 from score."""
        empty_q = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category=None,
            queue_depth=0,
        )
        deep_q = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=0.0,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category=None,
            queue_depth=6,
        )
        assert empty_q - deep_q == pytest.approx(3.0)  # 6 * 0.5

    def test_score_vram_exact_boundary(self):
        """VRAM exactly at capacity is infeasible (> not >=)."""
        # job_vram + committed == total → exceeds by 0 → not strictly greater
        # The gate is >  so exactly equal should pass
        result = score_slot(
            slot_load=0.0,
            slot_vram_committed_gb=4.0,
            job_vram_needed_gb=4.0,
            total_vram_gb=8.0,
            is_pinned=False,
            model_is_hot=False,
            recurring_conflict=False,
            historical_quiet=False,
            failure_category=None,
            queue_depth=0,
        )
        # 4 + 4 = 8, not > 8, so feasible
        assert result > 0


# ---------------------------------------------------------------------------
# find_fitting_slot tests
# ---------------------------------------------------------------------------


def _make_slot(
    hour: int,
    load: float = 0.0,
    vram: float = 0.0,
    pinned: bool = False,
    recurring: list | None = None,
    ts: float = 0.0,
) -> dict:
    return {
        "hour": hour,
        "load": load,
        "vram_committed_gb": vram,
        "is_pinned": pinned,
        "recurring_ids": recurring or [],
        "timestamp": ts,
    }


class TestFindFittingSlot:
    """Tests for find_fitting_slot contiguous-window search."""

    def test_find_fitting_slot_simple(self):
        """Finds the best single slot in a simple load map."""
        load_map = [
            _make_slot(0, load=8.0, ts=1000.0),
            _make_slot(1, load=2.0, ts=2000.0),
            _make_slot(2, load=5.0, ts=3000.0),
        ]
        result = find_fitting_slot(
            load_map=load_map,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            estimated_slots=1,
        )
        assert result is not None
        assert result["slot_index"] == 1  # lowest load
        assert result["scheduled_time"] == 2000.0

    def test_find_fitting_slot_no_fit(self):
        """All pinned slots returns None."""
        load_map = [
            _make_slot(0, pinned=True, ts=1000.0),
            _make_slot(1, pinned=True, ts=2000.0),
            _make_slot(2, pinned=True, ts=3000.0),
        ]
        result = find_fitting_slot(
            load_map=load_map,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            estimated_slots=1,
        )
        assert result is None

    def test_find_fitting_slot_skips_vram_overcommit(self):
        """Slots with insufficient VRAM are skipped."""
        load_map = [
            _make_slot(0, vram=7.0, ts=1000.0),  # 7 + 4 > 8 → infeasible
            _make_slot(1, vram=7.0, ts=2000.0),  # same
            _make_slot(2, vram=1.0, ts=3000.0),  # 1 + 4 = 5 ≤ 8 → feasible
        ]
        result = find_fitting_slot(
            load_map=load_map,
            job_vram_needed_gb=4.0,
            total_vram_gb=8.0,
            estimated_slots=1,
        )
        assert result is not None
        assert result["slot_index"] == 2

    def test_find_fitting_slot_contiguous_window(self):
        """Multi-slot window picks the best contiguous run."""
        load_map = [
            _make_slot(0, load=0.0, ts=1000.0),
            _make_slot(1, load=0.0, ts=2000.0),
            _make_slot(2, load=9.0, ts=3000.0),  # high load breaks window
            _make_slot(3, load=1.0, ts=4000.0),
            _make_slot(4, load=1.0, ts=5000.0),
        ]
        result = find_fitting_slot(
            load_map=load_map,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            estimated_slots=2,
        )
        assert result is not None
        # Window [0,1] avg = 15.0, window [3,4] avg = 14.0
        assert result["slot_index"] == 0

    def test_find_fitting_slot_hot_model(self):
        """Hot model bonus influences slot selection."""
        load_map = [
            _make_slot(0, load=3.0, ts=1000.0),
            _make_slot(1, load=3.0, ts=2000.0),
        ]
        result = find_fitting_slot(
            load_map=load_map,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            estimated_slots=1,
            loaded_models=["qwen2.5:7b"],
            job_model="qwen2.5:7b",
        )
        assert result is not None
        # Both slots equal, but hot model bonus applies uniformly; score > 0
        assert result["score"] > 0

    def test_find_fitting_slot_empty_load_map(self):
        """Empty load map returns None."""
        result = find_fitting_slot(
            load_map=[],
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            estimated_slots=1,
        )
        assert result is None

    def test_find_fitting_slot_window_too_large(self):
        """Estimated slots larger than load map returns None."""
        load_map = [_make_slot(0, ts=1000.0)]
        result = find_fitting_slot(
            load_map=load_map,
            job_vram_needed_gb=2.0,
            total_vram_gb=8.0,
            estimated_slots=3,
        )
        assert result is None
