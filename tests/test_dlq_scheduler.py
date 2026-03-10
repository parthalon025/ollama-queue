"""Tests for DLQScheduler — event-driven sweep + slot fitting."""

import time
from unittest.mock import MagicMock, patch

from ollama_queue.dlq_scheduler import DLQScheduler
from ollama_queue.runtime_estimator import Estimate


def _make_entry(
    id=1,
    command="echo hello",
    model="qwen2.5:7b",
    priority=5,
    timeout=600,
    source="test",
    tag=None,
    resource_profile="ollama",
    failure_reason="connection refused",
    auto_reschedule_count=0,
):
    return {
        "id": id,
        "command": command,
        "model": model,
        "priority": priority,
        "timeout": timeout,
        "source": source,
        "tag": tag,
        "resource_profile": resource_profile,
        "failure_reason": failure_reason,
        "auto_reschedule_count": auto_reschedule_count,
    }


def _make_load_map():
    """Simple load map with one available slot."""
    return [
        {
            "slot_index": 0,
            "load": 1.0,
            "vram_committed_gb": 0.0,
            "is_pinned": False,
            "historical_quiet": True,
            "queue_depth": 0,
            "timestamp": time.time() + 1800,
        }
    ]


def _make_estimate():
    return Estimate(
        total_mean=60.0,
        total_upper=120.0,
        confidence="medium",
    )


def _make_scheduler(entries=None, submit_return=100):
    """Build a DLQScheduler with mocked dependencies."""
    db = MagicMock()
    db.list_dlq.return_value = entries or []
    db.submit_job.return_value = submit_return
    db.get_setting.side_effect = lambda key: {
        "dlq.auto_reschedule": True,
        "dlq.chronic_failure_threshold": None,  # falls back to default of 5
    }.get(key)

    estimator = MagicMock()
    estimator.estimate.return_value = _make_estimate()

    load_map_fn = MagicMock(return_value=_make_load_map())

    scheduler = DLQScheduler(db, estimator, load_map_fn)
    return scheduler, db, estimator, load_map_fn


class TestSweepNoEntries:
    def test_sweep_no_entries(self):
        """Empty DLQ returns empty list."""
        sched, db, _, _ = _make_scheduler(entries=[])
        result = sched._sweep([])
        assert result == []
        db.submit_job.assert_not_called()
        db.mark_dlq_scheduling.assert_not_called()
        db.update_dlq_reschedule.assert_not_called()


class TestSweepFindsSlotAndReschedules:
    def test_sweep_finds_slot_and_reschedules(self):
        """Single entry with available slot: new job created, DLQ updated."""
        entry = _make_entry()
        sched, db, _estimator, _load_map_fn = _make_scheduler(entries=[entry], submit_return=42)

        result = sched._sweep([entry])

        assert len(result) == 1
        assert result[0]["dlq_id"] == 1
        assert result[0]["new_job_id"] == 42

        # Verify submit_job was called with entry's fields
        db.submit_job.assert_called_once()
        call_kw = db.submit_job.call_args
        assert call_kw[1]["command"] == "echo hello" or call_kw[0][0] == "echo hello"

        # Verify DLQ entry was marked then finalized
        # First call: mark_dlq_scheduling (crash-safety marker, no count increment)
        db.mark_dlq_scheduling.assert_called_once()
        mark_call = db.mark_dlq_scheduling.call_args
        assert mark_call[0][0] == 1  # dlq_id
        assert "rescheduled_for" in mark_call[1]
        assert "reschedule_reasoning" in mark_call[1]
        # Second call: update_dlq_reschedule (finalize with job ID, increments count)
        db.update_dlq_reschedule.assert_called_once()
        finalize_call = db.update_dlq_reschedule.call_args
        assert finalize_call[0][0] == 1  # dlq_id
        assert finalize_call[1]["rescheduled_job_id"] == 42


class TestSweepSkipsChronicFailures:
    def test_sweep_skips_chronic_failures(self):
        """Entry with reschedule_count >= threshold is skipped."""
        entry = _make_entry(auto_reschedule_count=5)
        sched, db, _, _ = _make_scheduler(entries=[entry])

        result = sched._sweep([entry])

        assert result == []
        db.submit_job.assert_not_called()

    def test_sweep_skips_chronic_custom_threshold(self):
        """Custom chronic threshold from settings is respected."""
        entry = _make_entry(auto_reschedule_count=3)
        sched, db, _, _ = _make_scheduler(entries=[entry])
        db.get_setting.side_effect = lambda key: {
            "dlq.auto_reschedule": True,
            "dlq.chronic_failure_threshold": 3,
        }.get(key)

        result = sched._sweep([entry])

        assert result == []
        db.submit_job.assert_not_called()


class TestSweepSkipsPermanentFailures:
    def test_sweep_skips_permanent_failures(self):
        """Entry classified as 'permanent' is skipped."""
        entry = _make_entry(failure_reason="command not found")
        sched, db, _, _ = _make_scheduler(entries=[entry])

        with patch("ollama_queue.dlq_scheduler.classify_failure", return_value="permanent"):
            result = sched._sweep([entry])

        assert result == []
        db.submit_job.assert_not_called()


class TestSweepPriorityOrdering:
    def test_sweep_priority_ordering(self):
        """Higher-importance entries (lower priority number) are processed first."""
        critical = _make_entry(id=1, priority=1)  # priority 1 = critical (most important)
        background = _make_entry(id=2, priority=10)  # priority 10 = background (least important)
        sched, db, _, _ = _make_scheduler(submit_return=99)

        # Track order via mark_dlq_scheduling (pre-submit) and update_dlq_reschedule (post-submit)
        mark_calls = []
        finalize_calls = []
        db.mark_dlq_scheduling.side_effect = lambda *a, **kw: mark_calls.append(a[0])
        db.update_dlq_reschedule.side_effect = lambda *a, **kw: finalize_calls.append(a[0])

        result = sched._sweep([background, critical])

        # Both should be rescheduled
        assert len(result) == 2
        # Critical (id=1, priority=1) should be processed before background (id=2, priority=10)
        assert mark_calls == [1, 2]
        assert finalize_calls == [1, 2]


class TestSweepLockPreventsConcurrent:
    def test_sweep_lock_prevents_concurrent(self):
        """If lock is held, second sweep returns [] immediately."""
        entry = _make_entry()
        sched, db, _, _ = _make_scheduler(entries=[entry])

        # Acquire the lock externally
        sched._sweep_lock.acquire()
        try:
            result = sched._sweep([entry])
            assert result == []
            db.submit_job.assert_not_called()
        finally:
            sched._sweep_lock.release()


class TestOnJobCompletedTriggersSweep:
    def test_on_job_completed_triggers_sweep(self):
        """on_job_completed triggers sweep when unscheduled entries exist."""
        entry = _make_entry()
        sched, db, _, _ = _make_scheduler(entries=[entry], submit_return=77)
        db.list_dlq.return_value = [entry]

        sched.on_job_completed(job_id=1)

        # Should have attempted to reschedule (mark + finalize)
        db.submit_job.assert_called_once()
        db.mark_dlq_scheduling.assert_called_once()
        db.update_dlq_reschedule.assert_called_once()

    def test_on_job_completed_no_entries(self):
        """on_job_completed with no unscheduled entries does nothing."""
        sched, db, _, _ = _make_scheduler(entries=[])
        db.list_dlq.return_value = []

        sched.on_job_completed(job_id=1)

        db.submit_job.assert_not_called()


class TestPeriodicSweepNoEntries:
    def test_periodic_sweep_no_entries(self):
        """periodic_sweep with no entries takes no action."""
        sched, db, _, _ = _make_scheduler(entries=[])
        db.list_dlq.return_value = []

        sched.periodic_sweep()

        db.submit_job.assert_not_called()
        db.update_dlq_reschedule.assert_not_called()

    def test_periodic_sweep_with_entries(self):
        """periodic_sweep triggers _sweep when unscheduled entries exist (line 46)."""
        entry = _make_entry()
        sched, db, _, _ = _make_scheduler(entries=[entry], submit_return=88)
        db.list_dlq.return_value = [entry]

        sched.periodic_sweep()

        db.submit_job.assert_called_once()


class TestSweepNoSlotAvailable:
    def test_sweep_no_slot_skips(self):
        """Entry with no fitting slot is skipped gracefully."""
        entry = _make_entry()
        sched, db, _, load_map_fn = _make_scheduler(entries=[entry])
        # Return empty load map — no slots available
        load_map_fn.return_value = []

        result = sched._sweep([entry])

        assert result == []
        db.submit_job.assert_not_called()


class TestSweepDisabledSetting:
    def test_auto_reschedule_disabled_returns_empty(self):
        """When dlq.auto_reschedule is falsy, _do_sweep returns [] immediately (line 60)."""
        entry = _make_entry()
        sched, db, _, _ = _make_scheduler(entries=[entry])
        db.get_setting.side_effect = lambda key: {
            "dlq.auto_reschedule": False,
            "dlq.chronic_failure_threshold": None,
        }.get(key)

        result = sched._do_sweep([entry])

        assert result == []
        db.submit_job.assert_not_called()


class TestSweepPassesVramEstimate:
    def test_sweep_passes_vram_estimate(self):
        """Verify find_fitting_slot receives real VRAM estimate, not 0."""
        entry = _make_entry(model="qwen2.5:14b")
        sched, db, estimator, load_map_fn = _make_scheduler(entries=[entry], submit_return=50)

        with patch("ollama_queue.dlq_scheduler.find_fitting_slot") as mock_ffs:
            mock_ffs.return_value = {"slot_index": 2, "score": 10.0, "scheduled_time": time.time() + 3600}
            sched._do_sweep([entry])
            mock_ffs.assert_called_once()
            call_kwargs = mock_ffs.call_args
            # 14b model → ~8.5 GB VRAM — must be > 0
            vram = call_kwargs.kwargs.get("job_vram_needed_gb", call_kwargs[1].get("job_vram_needed_gb", 0))
            assert vram > 0, f"Expected positive VRAM estimate, got {vram}"
