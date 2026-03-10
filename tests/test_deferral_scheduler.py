"""Tests for DeferralScheduler — proactive deferred job resumption."""

import time
from unittest.mock import MagicMock, patch

from ollama_queue.models.runtime_estimator import Estimate
from ollama_queue.scheduling.deferral import DeferralScheduler


def _make_scheduler(deferred=None, jobs=None, slot=None, load_map=None):
    """Build a DeferralScheduler with mocked dependencies."""
    db = MagicMock()
    db.list_deferred.return_value = deferred or []
    db.get_job.side_effect = lambda jid: (jobs or {}).get(jid)

    estimator = MagicMock()
    estimator.estimate.return_value = Estimate(total_mean=300.0, total_upper=600.0, confidence="medium")

    load_map_fn = MagicMock(return_value=load_map or [])

    scheduler = DeferralScheduler(db, estimator, load_map_fn)
    return scheduler, db, estimator, load_map_fn, slot


class TestSweepNoDeferred:
    def test_empty_returns_empty(self):
        sched, db, *_ = _make_scheduler(deferred=[])
        result = sched.sweep()
        assert result == []
        # Phase 1 fetches all deferred, phase 2 fetches unscheduled only
        assert db.list_deferred.call_count == 2
        db.list_deferred.assert_any_call()
        db.list_deferred.assert_any_call(unscheduled_only=True)
        db.resume_deferred_job.assert_not_called()
        db.update_deferral_schedule.assert_not_called()


class TestSweepResumesImmediately:
    @patch("ollama_queue.scheduling.deferral.find_fitting_slot")
    def test_slot_index_zero_resumes(self, mock_find):
        mock_find.return_value = {"slot_index": 0, "score": 8.5, "scheduled_time": time.time()}
        deferred = [{"id": 10, "job_id": 42, "scheduled_for": None}]
        jobs = {42: {"status": "deferred", "model": "qwen2.5:7b", "command": "echo hi", "resource_profile": "ollama"}}

        sched, db, *_ = _make_scheduler(deferred=deferred, jobs=jobs)
        result = sched.sweep()

        assert len(result) == 1
        assert result[0] == {"deferral_id": 10, "job_id": 42}
        db.resume_deferred_job.assert_called_once_with(10)
        db.update_deferral_schedule.assert_not_called()


class TestSweepSchedulesFutureSlot:
    @patch("ollama_queue.scheduling.deferral.find_fitting_slot")
    def test_slot_index_gt_zero_schedules(self, mock_find):
        future_time = time.time() + 3600
        mock_find.return_value = {"slot_index": 3, "score": 7.2, "scheduled_time": future_time}
        deferred = [{"id": 20, "job_id": 55, "scheduled_for": None}]
        jobs = {55: {"status": "deferred", "model": "llama3:8b", "command": "run task", "resource_profile": "ollama"}}

        sched, db, *_ = _make_scheduler(deferred=deferred, jobs=jobs)
        result = sched.sweep()

        assert result == []  # Not resumed — scheduled for later
        db.resume_deferred_job.assert_not_called()
        db.update_deferral_schedule.assert_called_once()
        call_args = db.update_deferral_schedule.call_args
        assert call_args[0][0] == 20  # deferral_id
        assert call_args[1]["scheduled_for"] == future_time


class TestSweepSkipsNonDeferredStatus:
    @patch("ollama_queue.scheduling.deferral.find_fitting_slot")
    def test_running_job_skipped(self, mock_find):
        deferred = [{"id": 30, "job_id": 99, "scheduled_for": None}]
        jobs = {99: {"status": "running", "model": "test", "command": "x", "resource_profile": "ollama"}}

        sched, db, *_ = _make_scheduler(deferred=deferred, jobs=jobs)
        result = sched.sweep()

        assert result == []
        db.resume_deferred_job.assert_not_called()
        db.update_deferral_schedule.assert_not_called()
        mock_find.assert_not_called()


class TestSweepResumesPastScheduled:
    def test_past_scheduled_for_resumes(self):
        past_time = time.time() - 60
        deferred = [{"id": 40, "job_id": 77, "scheduled_for": past_time}]
        jobs = {77: {"status": "deferred", "model": "m", "command": "c", "resource_profile": "ollama"}}

        sched, db, *_ = _make_scheduler(deferred=deferred, jobs=jobs)
        result = sched.sweep()

        assert len(result) == 1
        assert result[0] == {"deferral_id": 40, "job_id": 77}
        db.resume_deferred_job.assert_called_once_with(40)


class TestSweepLockPreventsConcurrent:
    def test_locked_returns_empty(self):
        sched, db, *_ = _make_scheduler(deferred=[{"id": 1, "job_id": 2, "scheduled_for": None}])
        # Acquire the lock externally to simulate a concurrent sweep
        sched._sweep_lock.acquire()
        try:
            result = sched.sweep()
            assert result == []
            db.list_deferred.assert_not_called()
        finally:
            sched._sweep_lock.release()


class TestSweepPreservesJobId:
    @patch("ollama_queue.scheduling.deferral.find_fitting_slot")
    def test_resumed_job_keeps_original_id(self, mock_find):
        """Resumed jobs keep their original job_id — no new job is created."""
        mock_find.return_value = {"slot_index": 0, "score": 9.0, "scheduled_time": time.time()}
        deferred = [{"id": 50, "job_id": 123, "scheduled_for": None}]
        jobs = {123: {"status": "deferred", "model": "m", "command": "c", "resource_profile": "ollama"}}

        sched, db, *_ = _make_scheduler(deferred=deferred, jobs=jobs)
        result = sched.sweep()

        assert result[0]["job_id"] == 123
        # resume_deferred_job is called with the deferral id, not a new job
        db.resume_deferred_job.assert_called_once_with(50)
        # No new job creation method should be called
        db.submit_job.assert_not_called()


class TestSweepRespectsDisabledSetting:
    def test_sweep_respects_disabled_setting(self):
        """When defer.enabled is false, sweep should return empty."""
        db = MagicMock()
        db.get_setting.return_value = False
        est = MagicMock()
        sched = DeferralScheduler(db, est, lambda: [])
        result = sched.sweep()
        assert result == []
        db.list_deferred.assert_not_called()


class TestSweepPhase1SkipsNonDeferred:
    def test_phase1_skips_job_whose_status_changed(self):
        """Phase 1: if job status changed from 'deferred' between listing and processing,
        the continue on line 55 skips it."""
        past_time = time.time() - 60
        deferred = [{"id": 50, "job_id": 200, "scheduled_for": past_time}]
        # Job exists but status changed to 'running' (no longer deferred)
        jobs = {200: {"status": "running", "model": "m", "command": "c", "resource_profile": "ollama"}}

        sched, db, *_ = _make_scheduler(deferred=deferred, jobs=jobs)
        result = sched.sweep()

        assert result == []
        db.resume_deferred_job.assert_not_called()


class TestSweepPassesVramEstimate:
    def test_sweep_passes_vram_estimate(self):
        """find_fitting_slot should receive a non-zero VRAM estimate for known models."""
        db = MagicMock()
        db.get_setting.return_value = True
        db.list_deferred.side_effect = [
            [],  # Phase 1
            [{"id": 1, "job_id": 100, "scheduled_for": None}],  # Phase 2
        ]
        db.get_job.return_value = {
            "id": 100,
            "status": "deferred",
            "model": "qwen2.5:14b",
            "command": "echo test",
            "resource_profile": "ollama",
        }
        est = MagicMock()
        est_result = MagicMock()
        est_result.total_upper = 300.0
        est_result.total_mean = 200.0
        est.estimate.return_value = est_result
        load_map = [
            {
                "load": 0.0,
                "vram_committed_gb": 0.0,
                "is_pinned": False,
                "recurring_ids": [],
                "timestamp": time.time() + i * 1800,
            }
            for i in range(48)
        ]
        sched = DeferralScheduler(db, est, lambda: load_map)
        with patch("ollama_queue.scheduling.deferral.find_fitting_slot") as mock_ffs:
            mock_ffs.return_value = {"slot_index": 5, "score": 10.0, "scheduled_time": time.time() + 9000}
            sched.sweep()
            mock_ffs.assert_called_once()
            _, kwargs = mock_ffs.call_args
            assert kwargs["job_vram_needed_gb"] > 0
