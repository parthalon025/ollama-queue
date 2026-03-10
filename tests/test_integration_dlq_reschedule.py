"""End-to-end integration tests for DLQ auto-reschedule and deferral lifecycle.

Trace 1: Submit → fail → DLQ → sweep → new job created → verify reasoning
Trace 2: Submit → defer → sweep → resume → verify same job ID back to pending
"""

import time

import pytest

from ollama_queue.db import Database
from ollama_queue.models.runtime_estimator import RuntimeEstimator
from ollama_queue.scheduling.deferral import DeferralScheduler
from ollama_queue.scheduling.dlq_scheduler import DLQScheduler


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


@pytest.fixture
def estimator(db):
    return RuntimeEstimator(db)


def _empty_load_map():
    """Return an empty 48-slot load map in the dict format find_fitting_slot expects."""
    now = time.time()
    return [
        {
            "load": 0.0,
            "vram_committed_gb": 0.0,
            "timestamp": now + i * 1800,
            "is_pinned": False,
            "historical_quiet": True,
            "queue_depth": 0,
        }
        for i in range(48)
    ]


def _fail_job(db, job_id, reason="error"):
    """Helper: start a job, complete with failure, move to DLQ."""
    db.start_job(job_id)
    db.complete_job(job_id, exit_code=1, outcome_reason=reason, stdout_tail="", stderr_tail=reason)
    return db.move_to_dlq(job_id, reason)


class TestDLQAutoRescheduleIntegration:
    """Vertical trace: job fails → enters DLQ → auto-reschedule creates new job."""

    def test_full_dlq_reschedule_lifecycle(self, db, estimator):
        # 1. Submit a job
        job_id = db.submit_job(
            command="echo test-dlq",
            model="qwen2.5:7b",
            priority=3,
            timeout=120,
            source="integration-test",
        )
        assert job_id is not None

        # 2. Fail the job → DLQ
        dlq_id = _fail_job(db, job_id, "connection refused")
        assert dlq_id is not None

        # 3. Verify DLQ entry exists
        dlq_entries = db.list_dlq()
        assert len(dlq_entries) == 1
        assert dlq_entries[0]["failure_reason"] == "connection refused"

        # 4. DLQ scheduler sweeps and creates a new job
        scheduler = DLQScheduler(db, estimator, _empty_load_map)
        db.set_setting("dlq.auto_reschedule", True)

        scheduler.periodic_sweep()

        # 5. Verify DLQ entry now has reschedule info (include_resolved since resolution='rescheduled')
        entry = db.get_dlq_entry(dlq_id)
        assert entry["auto_reschedule_count"] >= 1
        assert entry["rescheduled_job_id"] is not None
        assert entry["reschedule_reasoning"] is not None
        assert entry["resolution"] == "rescheduled"

        # 6. Verify the new job exists and is pending
        new_job = db.get_job(entry["rescheduled_job_id"])
        assert new_job is not None
        assert new_job["status"] == "pending"
        assert new_job["model"] == "qwen2.5:7b"
        assert new_job["source"] == "dlq-reschedule:integration-test"

    def test_chronic_failure_skipped(self, db, estimator):
        """Jobs that fail too many times are marked chronic and skipped."""
        job_id = db.submit_job(
            command="echo chronic",
            model="test:1b",
            priority=5,
            timeout=60,
            source="integration-test",
        )
        dlq_id = _fail_job(db, job_id, "model not found")

        # Set high reschedule count to simulate chronic failure
        db.set_setting("dlq.chronic_failure_threshold", 3)
        with db._lock:
            conn = db._connect()
            conn.execute(
                "UPDATE dlq SET auto_reschedule_count = 5 WHERE id = ?",
                (dlq_id,),
            )
            conn.commit()

        scheduler = DLQScheduler(db, estimator, _empty_load_map)
        db.set_setting("dlq.auto_reschedule", True)

        scheduler.periodic_sweep()

        # Verify entry was NOT rescheduled (count still 5, no new job)
        entry = db.get_dlq_entry(dlq_id)
        assert entry["auto_reschedule_count"] == 5
        assert entry["rescheduled_job_id"] is None

    def test_event_driven_sweep_on_job_complete(self, db, estimator):
        """on_job_completed triggers a DLQ sweep."""
        job_id = db.submit_job(
            command="echo trigger-test",
            model="test:1b",
            priority=5,
            timeout=60,
            source="integration-test",
        )
        dlq_id = _fail_job(db, job_id, "timed out")

        scheduler = DLQScheduler(db, estimator, _empty_load_map)
        db.set_setting("dlq.auto_reschedule", True)

        # Submit another job — on_job_completed should trigger sweep
        trigger_job = db.submit_job(
            command="echo trigger",
            model="test:1b",
            priority=5,
            timeout=60,
            source="integration-test",
        )
        scheduler.on_job_completed(trigger_job)

        # Verify the DLQ entry was rescheduled
        entry = db.get_dlq_entry(dlq_id)
        assert entry["auto_reschedule_count"] >= 1


class TestDeferralIntegration:
    """Vertical trace: job deferred → deferral scheduler resumes → same job back to pending."""

    def test_full_deferral_lifecycle(self, db, estimator):
        # 1. Submit a job
        job_id = db.submit_job(
            command="echo test-defer",
            model="qwen2.5:7b",
            priority=3,
            timeout=120,
            source="integration-test",
        )
        assert db.get_job(job_id)["status"] == "pending"

        # 2. Defer the job
        deferral_id = db.defer_job(job_id, reason="resource", context="RAM too high")
        assert deferral_id is not None
        assert db.get_job(job_id)["status"] == "deferred"

        # 3. Verify deferral record
        deferral = db.get_deferral(deferral_id)
        assert deferral["reason"] == "resource"
        assert deferral["job_id"] == job_id

        # 4. Deferral scheduler sweeps — since load map is empty, slot 0 is best → immediate resume
        scheduler = DeferralScheduler(db, estimator, _empty_load_map)
        db.set_setting("defer.enabled", True)

        scheduler.sweep()

        # 5. Verify job is back to pending (same job ID!)
        job = db.get_job(job_id)
        assert job["status"] == "pending"

        # 6. Verify deferral record updated
        deferral = db.get_deferral(deferral_id)
        assert deferral["resumed_at"] is not None

    def test_manual_resume(self, db, estimator):
        """User manually resumes a deferred job."""
        job_id = db.submit_job(
            command="echo manual-resume",
            model="test:1b",
            priority=5,
            timeout=60,
            source="integration-test",
        )
        deferral_id = db.defer_job(job_id, reason="manual")

        db.resume_deferred_job(deferral_id)
        assert db.get_job(job_id)["status"] == "pending"

    def test_scheduled_deferral_resume(self, db, estimator):
        """Deferred job with a scheduled_for time in the past gets auto-resumed."""
        job_id = db.submit_job(
            command="echo scheduled-resume",
            model="test:1b",
            priority=5,
            timeout=60,
            source="integration-test",
        )
        deferral_id = db.defer_job(job_id, reason="resource")

        # Set scheduled_for to the past — phase 1 of sweep should resume it
        db.update_deferral_schedule(deferral_id, scheduled_for=time.time() - 60)

        scheduler = DeferralScheduler(db, estimator, _empty_load_map)
        db.set_setting("defer.enabled", True)

        scheduler.sweep()

        assert db.get_job(job_id)["status"] == "pending"


class TestCLIIntegration:
    """Verify new CLI commands work end-to-end."""

    def test_dlq_schedule_preview(self, db, tmp_path):
        from click.testing import CliRunner

        from ollama_queue.cli import main

        db_path = str(tmp_path / "test.db")
        runner = CliRunner()

        result = runner.invoke(main, ["--db", db_path, "dlq", "schedule-preview"])
        assert result.exit_code == 0
        assert "No unscheduled" in result.output

    def test_dlq_reschedule(self, db, tmp_path):
        from click.testing import CliRunner

        from ollama_queue.cli import main

        runner = CliRunner()
        job_id = db.submit_job(
            command="echo test",
            model="test:1b",
            priority=5,
            timeout=60,
            source="cli-test",
        )
        dlq_id = _fail_job(db, job_id, "error")

        result = runner.invoke(main, ["--db", str(db.db_path), "dlq", "reschedule", str(dlq_id)])
        assert result.exit_code == 0
        assert f"DLQ #{dlq_id}" in result.output

    def test_defer_command(self, db, tmp_path):
        from click.testing import CliRunner

        from ollama_queue.cli import main

        job_id = db.submit_job(
            command="echo defer-test",
            model="test:1b",
            priority=5,
            timeout=60,
            source="cli-test",
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--db", str(db.db_path), "defer", str(job_id), "--reason", "manual"],
        )
        assert result.exit_code == 0
        assert "deferred" in result.output.lower()

    def test_metrics_models(self, db, tmp_path):
        from click.testing import CliRunner

        from ollama_queue.cli import main

        db_path = str(tmp_path / "test.db")
        runner = CliRunner()

        result = runner.invoke(main, ["--db", db_path, "metrics", "models"])
        assert result.exit_code == 0

    def test_metrics_curve(self, db, tmp_path):
        from click.testing import CliRunner

        from ollama_queue.cli import main

        db_path = str(tmp_path / "test.db")
        runner = CliRunner()

        result = runner.invoke(main, ["--db", db_path, "metrics", "curve"])
        assert result.exit_code == 0
