"""Tests for the SQLite database layer."""

import time

import pytest


class TestInitialize:
    def test_initialize_creates_tables(self, db):
        tables = db.list_tables()
        expected = {"jobs", "duration_history", "health_log", "daemon_state", "settings"}
        assert expected.issubset(set(tables))

    def test_initialize_creates_v2_tables(self, db):
        tables = db.list_tables()
        expected = {
            "jobs", "duration_history", "health_log",
            "daemon_state", "settings",
            "recurring_jobs", "schedule_events", "dlq",
        }
        assert expected == set(tables)

    def test_jobs_has_v2_columns(self, db):
        db.submit_job("cmd", "m", 5, 60, "src",
                      tag="aria", max_retries=2, resource_profile="ollama")
        job = db.get_job(1)
        assert job["tag"] == "aria"
        assert job["max_retries"] == 2
        assert job["retry_count"] == 0
        assert job["retry_after"] is None
        assert job["stall_detected_at"] is None
        assert job["recurring_job_id"] is None
        assert job["resource_profile"] == "ollama"

    def test_daemon_state_singleton_exists(self, db):
        state = db.get_daemon_state()
        assert state is not None
        assert state["state"] == "idle"
        assert state["id"] == 1


class TestJobs:
    def test_submit_job(self, db):
        job_id = db.submit_job(
            command="ollama run llama2",
            model="llama2",
            priority=5,
            timeout=600,
            source="test",
        )
        assert job_id == 1
        job = db.get_job(job_id)
        assert job["status"] == "pending"
        assert job["command"] == "ollama run llama2"
        assert job["model"] == "llama2"
        assert job["source"] == "test"

    def test_next_job_respects_priority(self, db):
        db.submit_job("cmd1", "m1", priority=5, timeout=600, source="a")
        db.submit_job("cmd2", "m2", priority=2, timeout=600, source="b")
        nxt = db.get_next_job()
        assert nxt["command"] == "cmd2"
        assert nxt["priority"] == 2

    def test_next_job_fifo_within_priority(self, db):
        db.submit_job("first", "m1", priority=5, timeout=600, source="a")
        time.sleep(0.01)  # ensure different submitted_at
        db.submit_job("second", "m1", priority=5, timeout=600, source="b")
        nxt = db.get_next_job()
        assert nxt["command"] == "first"

    def test_start_job(self, db):
        job_id = db.submit_job("cmd", "m1", 5, 600, "test")
        db.start_job(job_id)
        job = db.get_job(job_id)
        assert job["status"] == "running"
        assert job["started_at"] is not None

    def test_complete_job(self, db):
        job_id = db.submit_job("cmd", "m1", 5, 600, "test")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=0, stdout_tail="ok", stderr_tail="", outcome_reason=None)
        job = db.get_job(job_id)
        assert job["status"] == "completed"
        assert job["exit_code"] == 0
        assert job["completed_at"] is not None

    def test_fail_job(self, db):
        job_id = db.submit_job("cmd", "m1", 5, 600, "test")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="error", outcome_reason="crashed")
        job = db.get_job(job_id)
        assert job["status"] == "failed"
        assert job["exit_code"] == 1

    def test_kill_job(self, db):
        job_id = db.submit_job("cmd", "m1", 5, 600, "test")
        db.start_job(job_id)
        db.kill_job(job_id, reason="timeout exceeded")
        job = db.get_job(job_id)
        assert job["status"] == "killed"
        assert job["outcome_reason"] == "timeout exceeded"
        assert job["completed_at"] is not None

    def test_cancel_job(self, db):
        job_id = db.submit_job("cmd", "m1", 5, 600, "test")
        db.cancel_job(job_id)
        job = db.get_job(job_id)
        assert job["status"] == "cancelled"
        assert job["outcome_reason"] == "user cancelled"

    def test_cancel_job_only_if_pending(self, db):
        job_id = db.submit_job("cmd", "m1", 5, 600, "test")
        db.start_job(job_id)
        db.cancel_job(job_id)
        job = db.get_job(job_id)
        assert job["status"] == "running"  # should NOT be cancelled

    def test_get_pending_jobs(self, db):
        db.submit_job("low", "m1", priority=10, timeout=600, source="a")
        time.sleep(0.01)
        db.submit_job("high", "m1", priority=1, timeout=600, source="b")
        time.sleep(0.01)
        db.submit_job("mid", "m1", priority=5, timeout=600, source="c")
        pending = db.get_pending_jobs()
        assert len(pending) == 3
        assert pending[0]["command"] == "high"
        assert pending[1]["command"] == "mid"
        assert pending[2]["command"] == "low"

    def test_get_history(self, db):
        j1 = db.submit_job("cmd1", "m1", 5, 600, "test")
        j2 = db.submit_job("cmd2", "m1", 5, 600, "test")
        db.submit_job("cmd3", "m1", 5, 600, "test")  # stays pending
        db.start_job(j1)
        db.complete_job(j1, 0, "", "", None)
        db.start_job(j2)
        db.complete_job(j2, 1, "", "err", "failed")
        history = db.get_history()
        assert len(history) == 2
        # newest first
        assert history[0]["id"] == j2
        commands = [h["command"] for h in history]
        assert "cmd3" not in commands  # pending excluded


class TestSettings:
    def test_get_settings_defaults(self, db):
        val = db.get_setting("poll_interval_seconds")
        assert val == 5

    def test_update_setting(self, db):
        db.set_setting("poll_interval_seconds", 10)
        assert db.get_setting("poll_interval_seconds") == 10

    def test_get_all_settings(self, db):
        settings = db.get_all_settings()
        assert isinstance(settings, dict)
        assert settings["poll_interval_seconds"] == 5
        assert settings["ram_pause_pct"] == 85
        assert settings["yield_to_interactive"] is True


class TestDuration:
    def test_record_duration(self, db):
        db.record_duration(source="test-src", model="llama2", duration=12.5, exit_code=0)
        history = db.get_duration_history("test-src")
        assert len(history) == 1
        assert history[0]["duration"] == 12.5

    def test_estimate_duration_rolling_avg(self, db):
        for i in range(7):
            db.record_duration("src", "m1", duration=10.0 + i, exit_code=0)
        est = db.estimate_duration("src")
        # last 5: 12, 13, 14, 15, 16 → avg = 14.0
        assert est == pytest.approx(14.0)

    def test_estimate_duration_unknown_source(self, db):
        est = db.estimate_duration("nonexistent")
        assert est is None


class TestHealth:
    def test_log_health(self, db):
        db.log_health(
            ram_pct=45.0,
            vram_pct=30.0,
            load_avg=1.5,
            swap_pct=10.0,
            ollama_model="llama2",
            queue_depth=3,
            daemon_state="running",
        )
        logs = db.get_health_log(hours=1)
        assert len(logs) == 1
        assert logs[0]["ram_pct"] == 45.0


class TestDaemonState:
    def test_update_daemon_state(self, db):
        db.update_daemon_state(state="paused", paused_reason="high RAM")
        state = db.get_daemon_state()
        assert state["state"] == "paused"
        assert state["paused_reason"] == "high RAM"


class TestPrune:
    def test_prune_old_data(self, db):
        # Just verify it doesn't crash
        db.prune_old_data()


class TestRecurringJobs:
    def test_add_recurring_job(self, db):
        rj_id = db.add_recurring_job(
            name="aria-full",
            command="aria predict",
            interval_seconds=21600,
            model="qwen2.5:14b",
            priority=3,
            source="aria",
            tag="aria",
        )
        assert rj_id == 1
        rj = db.get_recurring_job(rj_id)
        assert rj["name"] == "aria-full"
        assert rj["interval_seconds"] == 21600
        assert rj["enabled"] == 1

    def test_get_due_recurring_jobs(self, db):
        now = time.time()
        db.add_recurring_job("job1", "cmd1", 3600, next_run=now - 1)
        db.add_recurring_job("job2", "cmd2", 3600, next_run=now + 3600)
        due = db.get_due_recurring_jobs(now)
        assert len(due) == 1
        assert due[0]["name"] == "job1"

    def test_get_due_skips_disabled(self, db):
        now = time.time()
        db.add_recurring_job("job1", "cmd1", 3600, next_run=now - 1)
        db.set_recurring_job_enabled("job1", False)
        due = db.get_due_recurring_jobs(now)
        assert len(due) == 0

    def test_update_next_run(self, db):
        rj_id = db.add_recurring_job("job1", "cmd1", 3600)
        completed_at = time.time()
        db.update_recurring_next_run(rj_id, completed_at)
        rj = db.get_recurring_job(rj_id)
        assert abs(rj["next_run"] - (completed_at + 3600)) < 0.01

    def test_list_recurring_jobs(self, db):
        db.add_recurring_job("a", "cmd_a", 3600)
        db.add_recurring_job("b", "cmd_b", 7200)
        jobs = db.list_recurring_jobs()
        assert len(jobs) == 2

    def test_log_schedule_event(self, db):
        db.log_schedule_event("promoted", details={"job_id": 1})
        events = db.get_schedule_events(limit=10)
        assert len(events) == 1
        assert events[0]["event_type"] == "promoted"
