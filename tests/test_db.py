"""Tests for the SQLite database layer."""

import sqlite3
import time
from typing import ClassVar
from unittest.mock import patch

import pytest


class TestInitialize:
    def test_initialize_creates_tables(self, db):
        tables = db.list_tables()
        expected = {"jobs", "duration_history", "health_log", "daemon_state", "settings"}
        assert expected.issubset(set(tables))

    def test_initialize_creates_v2_tables(self, db):
        tables = db.list_tables()
        expected = {
            "jobs",
            "duration_history",
            "health_log",
            "daemon_state",
            "settings",
            "recurring_jobs",
            "schedule_events",
            "dlq",
            "model_registry",
            "model_pulls",
            "eval_prompt_templates",
            "eval_variants",
            "eval_runs",
            "eval_results",
            "judge_attempts",
            "consumers",
            "job_metrics",
            "deferrals",
            "eval_cache",
            "backend_metrics",
        }
        assert expected == set(tables)

    def test_jobs_has_v2_columns(self, db):
        db.submit_job("cmd", "m", 5, 60, "src", tag="aria", max_retries=2, resource_profile="ollama")
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
        assert isinstance(job_id, int) and job_id > 0
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

    def test_next_job_skips_retry_after_in_future(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        # Simulate DLQ retry backoff: set retry_after to the future
        conn = db._connect()
        conn.execute(
            "UPDATE jobs SET retry_after = ? WHERE id = ?",
            (time.time() + 3600, job_id),
        )
        conn.commit()
        assert db.get_next_job() is None

    def test_get_next_job_respects_retry_after(self, db):
        """get_next_job should not return jobs whose retry_after is in the future,
        and should return them once time advances past retry_after."""
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        future_retry = time.time() + 3600
        conn = db._connect()
        conn.execute(
            "UPDATE jobs SET retry_after = ? WHERE id = ?",
            (future_retry, job_id),
        )
        conn.commit()

        # Job is not available while retry_after is in the future
        with patch("ollama_queue.db.jobs.time") as mock_time:
            mock_time.time.return_value = future_retry - 1
            assert db.get_next_job() is None

        # Job becomes available once time passes retry_after
        with patch("ollama_queue.db.jobs.time") as mock_time:
            mock_time.time.return_value = future_retry + 1
            job = db.get_next_job()
            assert job is not None
            assert job["id"] == job_id

    def test_next_job_fifo_within_priority(self, db):
        db.submit_job("first", "m1", priority=5, timeout=600, source="a")
        time.sleep(0.01)  # ensure different submitted_at
        db.submit_job("second", "m1", priority=5, timeout=600, source="b")
        nxt = db.get_next_job()
        assert nxt["command"] == "first"

    def test_next_job_embed_affinity_nomic(self, db):
        """Embed models (nomic) are preferred over LLM jobs at equal priority."""
        db.submit_job("llm-job", "qwen2.5:7b", priority=5, timeout=600, source="a")
        time.sleep(0.01)
        db.submit_job("embed-job", "nomic-embed-text", priority=5, timeout=600, source="b")
        nxt = db.get_next_job()
        assert nxt["command"] == "embed-job"

    def test_next_job_embed_affinity_bge(self, db):
        """Embed models (bge) are preferred over LLM jobs at equal priority."""
        db.submit_job("llm-job", "llama3.2:3b", priority=5, timeout=600, source="a")
        time.sleep(0.01)
        db.submit_job("embed-job", "bge-m3", priority=5, timeout=600, source="b")
        nxt = db.get_next_job()
        assert nxt["command"] == "embed-job"

    def test_next_job_embed_affinity_mxbai(self, db):
        """Embed models (mxbai) are preferred over LLM jobs at equal priority."""
        db.submit_job("llm-job", "deepseek-r1:8b", priority=5, timeout=600, source="a")
        time.sleep(0.01)
        db.submit_job("embed-job", "mxbai-embed-large", priority=5, timeout=600, source="b")
        nxt = db.get_next_job()
        assert nxt["command"] == "embed-job"

    def test_next_job_embed_affinity_all_minilm(self, db):
        """Embed models (all-minilm) are preferred over LLM jobs at equal priority."""
        db.submit_job("llm-job", "qwen2.5-coder:14b", priority=5, timeout=600, source="a")
        time.sleep(0.01)
        db.submit_job("embed-job", "all-minilm", priority=5, timeout=600, source="b")
        nxt = db.get_next_job()
        assert nxt["command"] == "embed-job"

    def test_next_job_priority_beats_embed_affinity(self, db):
        """A higher-priority LLM job (lower number) still beats a lower-priority embed job."""
        db.submit_job("llm-high-priority", "qwen2.5:7b", priority=1, timeout=600, source="a")
        time.sleep(0.01)
        db.submit_job("embed-low-priority", "nomic-embed-text", priority=5, timeout=600, source="b")
        nxt = db.get_next_job()
        assert nxt["command"] == "llm-high-priority"

    def test_next_job_embed_fifo_among_embeds(self, db):
        """Among embed jobs at equal priority, FIFO still applies."""
        db.submit_job("embed-first", "nomic-embed-text", priority=5, timeout=600, source="a")
        time.sleep(0.01)
        db.submit_job("embed-second", "bge-m3", priority=5, timeout=600, source="b")
        nxt = db.get_next_job()
        assert nxt["command"] == "embed-first"

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
        # newest first
        assert history[0]["id"] == j2
        history_ids = [h["id"] for h in history]
        assert j1 in history_ids
        assert j2 in history_ids
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
        assert isinstance(rj_id, int) and rj_id > 0
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
        assert any(e["event_type"] == "promoted" for e in events)

    def test_set_recurring_next_run_updates_correctly(self, db):
        """_set_recurring_next_run updates next_run without direct _connect access."""
        rj_id = db.add_recurring_job("job1", "echo hi", interval_seconds=3600)
        future_time = time.time() + 7200
        db._set_recurring_next_run(rj_id, future_time)
        rj = db.get_recurring_job(rj_id)
        assert abs(rj["next_run"] - future_time) < 0.01


class TestDLQ:
    def test_move_to_dlq(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="err", outcome_reason="exit code 1")
        dlq_id = db.move_to_dlq(job_id, failure_reason="exit code 1")
        assert dlq_id is not None
        entry = db.get_dlq_entry(dlq_id)
        assert entry["original_job_id"] == job_id
        assert entry["failure_reason"] == "exit code 1"
        assert entry["resolution"] is None

    def test_list_dlq(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="")
        db.move_to_dlq(job_id, failure_reason="failed")
        entries = db.list_dlq()
        assert len(entries) == 1

    def test_dismiss_dlq_entry(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="")
        dlq_id = db.move_to_dlq(job_id, failure_reason="failed")
        db.dismiss_dlq_entry(dlq_id)
        entry = db.get_dlq_entry(dlq_id)
        assert entry["resolution"] == "dismissed"

    def test_retry_from_dlq_creates_new_job(self, db):
        job_id = db.submit_job("echo hello", "m", 5, 60, "src", tag="t")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="")
        dlq_id = db.move_to_dlq(job_id, failure_reason="failed")
        new_job_id = db.retry_dlq_entry(dlq_id)
        assert new_job_id is not None
        new_job = db.get_job(new_job_id)
        assert new_job["command"] == "echo hello"
        assert new_job["status"] == "pending"
        entry = db.get_dlq_entry(dlq_id)
        assert entry["resolution"] == "retried"

    def test_clear_dlq_removes_resolved(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="")
        dlq_id = db.move_to_dlq(job_id, failure_reason="failed")
        db.dismiss_dlq_entry(dlq_id)
        db.clear_dlq()
        assert db.list_dlq() == []


def test_pinned_column_default_false(tmp_path):
    from ollama_queue.db import Database

    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    rj_id = db.add_recurring_job("j1", "echo hi", interval_seconds=3600)
    rj = db.get_recurring_job(rj_id)
    assert rj["pinned"] == 0  # default unpinned


def test_add_recurring_job_with_pin(tmp_path):
    from ollama_queue.db import Database

    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    rj_id = db.add_recurring_job("j1", "echo hi", interval_seconds=3600, pinned=True)
    rj = db.get_recurring_job(rj_id)
    assert rj["pinned"] == 1


def test_update_recurring_job_pinned(tmp_path):
    from ollama_queue.db import Database

    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    rj_id = db.add_recurring_job("j1", "echo hi", interval_seconds=3600)
    db.update_recurring_job(rj_id, pinned=1)
    rj = db.get_recurring_job(rj_id)
    assert rj["pinned"] == 1


# --- T1: model_registry, model_pulls, jobs.pid, new settings ---


class TestModelConcurrencySchema:
    def test_model_registry_table_exists(self, db):
        tables = db.list_tables()
        assert "model_registry" in tables

    def test_model_pulls_table_exists(self, db):
        tables = db.list_tables()
        assert "model_pulls" in tables

    def test_jobs_has_pid_column(self, db):
        conn = db._connect()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        assert "pid" in cols

    def test_new_settings_seeded(self, db):
        s = db.get_all_settings()
        assert "max_concurrent_jobs" in s
        assert "concurrent_shadow_hours" in s
        assert "vram_safety_factor" in s

    def test_migration_idempotent(self, db):
        # Calling initialize() twice must not raise
        db.initialize()
        db.initialize()


def test_proxy_claim_respects_concurrent_slot_limit(db):
    """Proxy claims count against max_concurrent_jobs."""
    db.set_setting("max_concurrent_jobs", 1)
    jid = db.submit_job("echo", "", 5, 60, "test")
    db.start_job(jid)
    claimed = db.try_claim_for_proxy()
    assert claimed is False


def test_stall_signals_column_exists(db):
    """initialize() creates stall_signals column on jobs table."""
    conn = db._connect()
    row = conn.execute("PRAGMA table_info(jobs)").fetchall()
    col_names = [r["name"] for r in row]
    assert "stall_signals" in col_names


def test_set_stall_detected(db):
    """set_stall_detected() writes stall_detected_at and stall_signals JSON."""
    import json
    import time

    job_id = db.submit_job("echo hi", "qwen2.5:7b", 5, 600, "test")
    now = time.time()
    signals = {"process": 3.56, "cpu": 2.08, "silence": 1.79, "ps": 0.0, "posterior": 0.92}
    db.set_stall_detected(job_id, now, signals)
    conn = db._connect()
    row = conn.execute("SELECT stall_detected_at, stall_signals FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["stall_detected_at"] == pytest.approx(now, abs=0.01)
    parsed = json.loads(row["stall_signals"])
    assert parsed["posterior"] == pytest.approx(0.92, abs=0.001)


def test_new_stall_settings_have_defaults(db):
    """Three new stall settings exist with correct defaults."""
    settings = db.get_all_settings()
    assert settings["stall_posterior_threshold"] == pytest.approx(0.8)
    assert settings["stall_action"] == "log"
    assert settings["stall_kill_grace_seconds"] == pytest.approx(60)


def test_add_recurring_job_with_check_command(db):
    rj_id = db.add_recurring_job(
        name="test-check",
        command="echo hi",
        interval_seconds=3600,
        check_command="exit 0",
        max_runs=5,
    )
    rj = db.get_recurring_job(rj_id)
    assert rj["check_command"] == "exit 0"
    assert rj["max_runs"] == 5


def test_add_recurring_job_without_check_command(db):
    rj_id = db.add_recurring_job(name="no-check", command="echo hi", interval_seconds=3600)
    rj = db.get_recurring_job(rj_id)
    assert rj["check_command"] is None
    assert rj["max_runs"] is None


def test_disable_recurring_job_with_reason(db):
    rj_id = db.add_recurring_job(name="test-disable", command="echo hi", interval_seconds=3600)
    db.disable_recurring_job(rj_id, "check_command signaled complete")
    rj = db.get_recurring_job(rj_id)
    assert rj["enabled"] == 0
    assert rj["outcome_reason"] == "check_command signaled complete"


def test_enable_recurring_job_clears_reason(db):
    rj_id = db.add_recurring_job(name="re-enable", command="echo hi", interval_seconds=3600)
    db.disable_recurring_job(rj_id, "max_runs exhausted")
    db.set_recurring_job_enabled("re-enable", True)
    rj = db.get_recurring_job(rj_id)
    assert rj["enabled"] == 1
    assert rj["outcome_reason"] is None


def test_update_recurring_job_max_runs(db):
    rj_id = db.add_recurring_job(name="update-test", command="echo hi", interval_seconds=3600, max_runs=10)
    db.update_recurring_job(rj_id, max_runs=9)
    rj = db.get_recurring_job(rj_id)
    assert rj["max_runs"] == 9


def test_recurring_job_schema_has_outcome_reason(db):
    rj_id = db.add_recurring_job(name="schema-test", command="echo hi", interval_seconds=3600)
    rj = db.get_recurring_job(rj_id)
    assert "outcome_reason" in rj


class TestDatabase:
    def test_pragma_synchronous_normal(self, db):
        """PRAGMA synchronous should be NORMAL (1), not FULL (2)."""
        conn = db._connect()
        result = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert result == 1  # 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA

    def test_pragma_temp_store_memory(self, db):
        """PRAGMA temp_store should be MEMORY (2)."""
        conn = db._connect()
        result = conn.execute("PRAGMA temp_store").fetchone()[0]
        assert result == 2  # 0=DEFAULT, 1=FILE, 2=MEMORY

    def test_pragma_busy_timeout(self, db):
        """PRAGMA busy_timeout should be 5000ms."""
        conn = db._connect()
        result = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert result == 5000

    def test_pragma_wal_autocheckpoint(self, db):
        """PRAGMA wal_autocheckpoint should be 1000 pages."""
        conn = db._connect()
        result = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
        assert result == 1000

    def test_pragma_journal_mode_wal(self, db):
        """WAL mode must be active — required for wal_autocheckpoint to have any effect."""
        conn = db._connect()
        result = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert result == "wal"

    def test_pragma_mmap_size(self, db):
        """PRAGMA mmap_size should be 536870912 (512MB)."""
        conn = db._connect()
        result = conn.execute("PRAGMA mmap_size").fetchone()[0]
        assert result == 536870912

    def test_pragma_cache_size(self, db):
        """PRAGMA cache_size should be -64000 (64MB, negative = KiB)."""
        conn = db._connect()
        result = conn.execute("PRAGMA cache_size").fetchone()[0]
        assert result == -64000

    def test_jobs_has_last_retry_delay_column(self, db):
        """jobs table must have last_retry_delay column for decorrelated jitter."""
        job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
        job = db.get_job(job_id)
        assert "last_retry_delay" in job
        assert job["last_retry_delay"] is None  # NULL by default


class TestPR2Admission:
    def test_count_pending_jobs_empty(self, db):
        """count_pending_jobs returns 0 when queue is empty."""
        assert db.count_pending_jobs() == 0

    def test_count_pending_jobs_counts_pending_only(self, db):
        """count_pending_jobs counts only pending jobs, not running/completed/failed."""
        db.submit_job("echo a", "qwen2.5:7b", 5, 60, "test")
        db.submit_job("echo b", "qwen2.5:7b", 5, 60, "test")
        assert db.count_pending_jobs() == 2
        # Claim one job — start_job() transitions it to 'running', should not be counted
        job = db.get_next_job()
        db.start_job(job["id"])
        assert db.count_pending_jobs() == 1

    def test_count_pending_jobs_excludes_future_retry_after(self, db):
        """Jobs with retry_after in the future are pending but not yet actionable — excluded."""
        job_id = db.submit_job("echo c", "qwen2.5:7b", 5, 60, "test")
        conn = db._connect()
        conn.execute("UPDATE jobs SET retry_after = ? WHERE id = ?", (time.time() + 3600, job_id))
        conn.commit()
        assert db.count_pending_jobs() == 0

    def test_pr2_defaults_available(self, db):
        """All PR2 DEFAULTS are seeded by initialize() and accessible via get_setting()."""
        assert db.get_setting("cpu_offload_efficiency") == 0.3
        assert db.get_setting("cb_failure_threshold") == 3
        assert db.get_setting("cb_base_cooldown") == 30
        assert db.get_setting("cb_max_cooldown") == 600
        assert db.get_setting("max_queue_depth") == 50
        assert db.get_setting("min_model_vram_mb") == 2000


class TestDurationBulkAndStats:
    def test_estimate_duration_bulk_empty_sources(self, db):
        """Returns empty dict for empty source list."""
        result = db.estimate_duration_bulk([])
        assert result == {}

    def test_estimate_duration_bulk_returns_avg_per_source(self, db):
        """Returns mean of successful runs per source in one query."""
        import time

        now = time.time()
        # Source A: two successful runs, avg = 300
        db._connect().execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("src-a", "m", 200.0, 0, now),
        )
        db._connect().execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("src-a", "m", 400.0, 0, now),
        )
        db._connect().commit()
        result = db.estimate_duration_bulk(["src-a"])
        assert abs(result["src-a"] - 300.0) < 0.1

    def test_estimate_duration_bulk_excludes_failed_runs(self, db):
        """Only counts exit_code=0 runs in the average."""
        import time

        now = time.time()
        db._connect().execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("src-b", "m", 1000.0, 1, now),  # failed — should be excluded
        )
        db._connect().execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("src-b", "m", 100.0, 0, now),  # success
        )
        db._connect().commit()
        result = db.estimate_duration_bulk(["src-b"])
        assert abs(result["src-b"] - 100.0) < 0.1

    def test_estimate_duration_stats_returns_mean_and_variance(self, db):
        """Returns (mean, variance) tuple from last 10 successful runs."""
        import time

        now = time.time()
        durations = [100.0, 200.0, 300.0]
        for d in durations:
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("stats-src", "m", d, 0, now),
            )
        db._connect().commit()
        result = db.estimate_duration_stats("stats-src")
        assert result is not None
        mean, variance = result
        assert abs(mean - 200.0) < 0.1  # (100+200+300)/3 = 200
        assert variance > 0  # non-zero variance for these three values

    def test_estimate_duration_stats_returns_none_for_missing_source(self, db):
        """Returns None when no history exists for source."""
        result = db.estimate_duration_stats("nonexistent-source")
        assert result is None


class TestLastSuccessfulRunTime:
    def test_returns_none_for_no_history(self, db):
        """Returns None when recurring job has never completed successfully."""
        rj_id = db.add_recurring_job("test-job", "echo test", interval_seconds=3600)
        result = db.get_last_successful_run_time(rj_id)
        assert result is None

    def test_returns_max_completed_at_for_successful_runs(self, db):
        """Returns timestamp of most recent successful completion."""
        import time

        rj_id = db.add_recurring_job("test-job2", "echo test", interval_seconds=3600)
        now = time.time()
        job1 = db.submit_job("echo 1", "m", 5, 60, "s", recurring_job_id=rj_id)
        db.start_job(job1)
        db.complete_job(job1, exit_code=1, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 100, job1))

        job2 = db.submit_job("echo 2", "m", 5, 60, "s", recurring_job_id=rj_id)
        db.start_job(job2)
        db.complete_job(job2, exit_code=0, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 10, job2))
        db._connect().commit()

        result = db.get_last_successful_run_time(rj_id)
        assert result is not None
        assert abs(result - (now - 10)) < 1.0

    def test_ignores_failed_runs(self, db):
        """exit_code != 0 runs do not count as last successful run."""
        import time

        rj_id = db.add_recurring_job("test-job3", "echo test", interval_seconds=3600)
        now = time.time()
        job1 = db.submit_job("echo 1", "m", 5, 60, "s", recurring_job_id=rj_id)
        db.start_job(job1)
        db.complete_job(job1, exit_code=1, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 5, job1))
        db._connect().commit()

        result = db.get_last_successful_run_time(rj_id)
        assert result is None

    def test_returns_latest_of_multiple_successful_runs(self, db):
        """MAX(completed_at) returns the most recent success when multiple exist."""
        import time

        rj_id = db.add_recurring_job("test-job4", "echo test", interval_seconds=3600)
        now = time.time()

        job1 = db.submit_job("echo 1", "m", 5, 60, "s", recurring_job_id=rj_id)
        db.start_job(job1)
        db.complete_job(job1, exit_code=0, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 200, job1))

        job2 = db.submit_job("echo 2", "m", 5, 60, "s", recurring_job_id=rj_id)
        db.start_job(job2)
        db.complete_job(job2, exit_code=0, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 50, job2))
        db._connect().commit()

        result = db.get_last_successful_run_time(rj_id)
        assert result is not None
        assert abs(result - (now - 50)) < 1.0  # must be the later one


class TestPreemptionSupport:
    def test_jobs_has_preemption_count_column(self, db):
        """jobs table must have preemption_count column (default 0)."""
        job_id = db.submit_job("echo test", "m", 5, 60, "test")
        job = db.get_job(job_id)
        assert "preemption_count" in job
        assert job["preemption_count"] == 0

    def test_requeue_preempted_job_sets_pending(self, db):
        """requeue_preempted_job() sets status=pending and increments preemption_count."""
        job_id = db.submit_job("echo test", "m", 1, 60, "test")
        db.start_job(job_id)
        db.requeue_preempted_job(job_id)
        job = db.get_job(job_id)
        assert job["status"] == "pending"
        assert job["started_at"] is None
        assert job["pid"] is None
        assert job["preemption_count"] == 1

    def test_requeue_increments_preemption_count_each_call(self, db):
        """preemption_count increments on each requeue, not reset."""
        job_id = db.submit_job("echo test", "m", 1, 60, "test")
        db.start_job(job_id)
        db.requeue_preempted_job(job_id)
        db.start_job(job_id)
        db.requeue_preempted_job(job_id)
        job = db.get_job(job_id)
        assert job["preemption_count"] == 2

    def test_requeue_does_not_touch_dlq(self, db):
        """requeue_preempted_job() never creates a DLQ entry."""
        job_id = db.submit_job("echo test", "m", 1, 60, "test")
        db.start_job(job_id)
        db.requeue_preempted_job(job_id)
        assert db.list_dlq() == []  # DLQ must remain empty


class TestEvalSchema:
    """Tests for the 5 eval tables and their seeded defaults."""

    EVAL_TABLES: ClassVar[set] = {
        "eval_prompt_templates",
        "eval_variants",
        "eval_runs",
        "eval_results",
        "judge_attempts",
    }

    def test_initialize_creates_all_eval_tables(self, db):
        """initialize() must create all 5 eval tables."""
        tables = set(db.list_tables())
        assert self.EVAL_TABLES.issubset(tables)

    def test_seed_eval_defaults_inserts_templates(self, db):
        """seed_eval_defaults() inserts system prompt templates (3 original + contrastive + multistage + mechanism)."""
        conn = db._connect()
        count = conn.execute("SELECT COUNT(*) FROM eval_prompt_templates").fetchone()[0]
        assert count == 6

    def test_seed_eval_defaults_inserts_variants(self, db):
        """seed_eval_defaults() inserts system variants (A-H + M)."""
        conn = db._connect()
        count = conn.execute("SELECT COUNT(*) FROM eval_variants").fetchone()[0]
        assert count == 9

    def test_seed_eval_defaults_idempotent(self, db):
        """Running seed_eval_defaults() twice produces no duplicates and no errors."""
        db.seed_eval_defaults()
        db.seed_eval_defaults()
        conn = db._connect()
        assert conn.execute("SELECT COUNT(*) FROM eval_prompt_templates").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM eval_variants").fetchone()[0] == 9

    def test_eval_settings_all_seeded(self, db):
        """All 12 eval.* settings keys are present after initialize()."""
        settings = db.get_all_settings()
        expected_keys = [
            "eval.data_source_url",
            "eval.data_source_token",
            "eval.per_cluster",
            "eval.same_cluster_targets",
            "eval.diff_cluster_targets",
            "eval.judge_model",
            "eval.judge_backend",
            "eval.judge_temperature",
            "eval.f1_threshold",
            "eval.stability_window",
            "eval.error_budget",
            "eval.setup_complete",
        ]
        for key in expected_keys:
            assert key in settings, f"Missing eval setting: {key}"
        # Verify types are correct (int/float/bool, not pre-stringified)
        assert settings["eval.per_cluster"] == 4
        assert isinstance(settings["eval.per_cluster"], int)
        assert settings["eval.same_cluster_targets"] == 2
        assert settings["eval.diff_cluster_targets"] == 2
        assert settings["eval.judge_temperature"] == pytest.approx(0.1)
        assert isinstance(settings["eval.judge_temperature"], float)
        assert settings["eval.f1_threshold"] == pytest.approx(0.75)
        assert settings["eval.stability_window"] == 3
        assert settings["eval.error_budget"] == pytest.approx(0.30)
        assert settings["eval.setup_complete"] is False

    def test_eval_runs_status_check_rejects_invalid(self, db):
        """eval_runs.status CHECK constraint rejects values not in the allowed set."""
        import sqlite3 as _sqlite3

        conn = db._connect()
        with pytest.raises(_sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO eval_runs
                   (data_source_url, variants, per_cluster, status, started_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("http://localhost", '["A"]', 4, "invalid_status", "2026-03-05T00:00:00"),
            )
            conn.commit()

    def test_eval_runs_run_mode_check_rejects_invalid(self, db):
        """eval_runs.run_mode CHECK constraint rejects values not in the allowed set."""
        import sqlite3 as _sqlite3

        conn = db._connect()
        with pytest.raises(_sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO eval_runs
                   (data_source_url, variants, per_cluster, status, run_mode, started_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("http://localhost", '["A"]', 4, "pending", "not-a-mode", "2026-03-05T00:00:00"),
            )
            conn.commit()

    def test_eval_results_cascade_delete(self, db):
        """Deleting an eval_run cascades to delete its eval_results."""
        conn = db._connect()
        # Insert a run
        conn.execute(
            """INSERT INTO eval_runs
               (data_source_url, variants, per_cluster, status, started_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("http://localhost", '["A"]', 4, "pending", "2026-03-05T00:00:00"),
        )
        conn.commit()
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert a result referencing that run
        conn.execute(
            """INSERT INTO eval_results
               (run_id, variant, source_item_id, target_item_id, is_same_cluster)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, "A", "src-1", "tgt-1", 1),
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM eval_results WHERE run_id = ?", (run_id,)).fetchone()[0] == 1

        # Delete the run — results should cascade-delete
        conn.execute("DELETE FROM eval_runs WHERE id = ?", (run_id,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM eval_results WHERE run_id = ?", (run_id,)).fetchone()[0] == 0

    def test_template_ids_match_expected(self, db):
        """Seeded templates include original 3 + contrastive + contrastive-multistage + mechanism."""
        conn = db._connect()
        rows = conn.execute("SELECT id FROM eval_prompt_templates ORDER BY id").fetchall()
        ids = {r[0] for r in rows}
        assert ids == {"fewshot", "zero-shot-causal", "chunked", "contrastive", "contrastive-multistage", "mechanism"}

    def test_variant_ids_match_expected(self, db):
        """Seeded variants include A-H plus M (mechanism extraction)."""
        conn = db._connect()
        rows = conn.execute("SELECT id FROM eval_variants ORDER BY id").fetchall()
        ids = {r[0] for r in rows}
        assert ids == {"A", "B", "C", "D", "E", "F", "G", "H", "M"}

    def test_recommended_variants_include_contrastive(self, db):
        """Variants D, E, F, G, H are marked is_recommended=1."""
        conn = db._connect()
        rows = conn.execute("SELECT id FROM eval_variants WHERE is_recommended = 1 ORDER BY id").fetchall()
        assert [r[0] for r in rows] == ["D", "E", "F", "G", "H"]

    def test_variant_a_uses_fewshot_template(self, db):
        """Variant A must reference the fewshot prompt template."""
        conn = db._connect()
        row = conn.execute("SELECT prompt_template_id FROM eval_variants WHERE id = 'A'").fetchone()
        assert row is not None
        assert row[0] == "fewshot"


class TestEvalV2Schema:
    """Tests for eval V2 Bayesian fusion schema columns."""

    def test_eval_results_v2_columns_exist(self, db):
        """All V2 columns exist on eval_results after initialize."""
        conn = db._connect()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(eval_results)").fetchall()}
        v2_cols = {
            "score_paired_winner",
            "score_mechanism_match",
            "score_embedding_sim",
            "score_posterior",
            "mechanism_trigger",
            "mechanism_target",
            "mechanism_fix",
        }
        assert v2_cols.issubset(cols), f"Missing: {v2_cols - cols}"

    def test_eval_runs_judge_mode_column_exists(self, db):
        """judge_mode column exists on eval_runs after initialize."""
        conn = db._connect()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(eval_runs)").fetchall()}
        assert "judge_mode" in cols

    def test_judge_mode_defaults_to_rubric(self, db):
        """judge_mode defaults to 'rubric' for new rows."""
        conn = db._connect()
        conn.execute(
            """INSERT INTO eval_runs
               (data_source_url, variants, status, started_at)
               VALUES ('http://localhost', 'A', 'queued', '2026-01-01')"""
        )
        conn.commit()
        row = conn.execute("SELECT judge_mode FROM eval_runs ORDER BY id DESC LIMIT 1").fetchone()
        assert row[0] == "rubric"

    def test_v2_migration_is_idempotent(self, db):
        """Running _run_migrations twice does not raise."""
        conn = db._connect()
        # First run already happened in initialize(). Run again explicitly.
        db._run_migrations(conn)
        # Verify columns still exist
        cols = {row[1] for row in conn.execute("PRAGMA table_info(eval_results)").fetchall()}
        assert "score_posterior" in cols
        assert "mechanism_trigger" in cols

    def test_v2_columns_accept_values(self, db):
        """V2 columns can be written to and read back."""
        conn = db._connect()
        conn.execute(
            """INSERT INTO eval_runs
               (data_source_url, variants, status, started_at, judge_mode)
               VALUES ('http://localhost', 'A', 'queued', '2026-01-01', 'bayesian')"""
        )
        conn.commit()
        run_id = conn.execute("SELECT id FROM eval_runs ORDER BY id DESC LIMIT 1").fetchone()[0]

        conn.execute(
            """INSERT INTO eval_results
               (run_id, variant, source_item_id, target_item_id, is_same_cluster, row_type,
                score_paired_winner, score_mechanism_match, score_embedding_sim, score_posterior,
                mechanism_trigger, mechanism_target, mechanism_fix)
               VALUES (?, 'A', '1', '2', 1, 'judge',
                       'same', 1, 0.85, 0.92,
                       'uncaught exception', 'cleanup handler', 'symmetric teardown')""",
            (run_id,),
        )
        conn.commit()

        row = conn.execute(
            "SELECT score_paired_winner, score_mechanism_match, score_embedding_sim, "
            "score_posterior, mechanism_trigger, mechanism_target, mechanism_fix "
            "FROM eval_results WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert row[0] == "same"
        assert row[1] == 1
        assert abs(row[2] - 0.85) < 1e-6
        assert abs(row[3] - 0.92) < 1e-6
        assert row[4] == "uncaught exception"
        assert row[5] == "cleanup handler"
        assert row[6] == "symmetric teardown"


def test_eval_variants_have_description(tmp_path):
    from ollama_queue.db import Database

    db = Database(tmp_path / "q.db")
    db.initialize()
    with db._lock:
        conn = db._connect()
        variants = conn.execute("SELECT id, description FROM eval_variants WHERE is_system = 1").fetchall()
    assert len(variants) == 9
    for row in variants:
        assert (
            row["description"] is not None and len(row["description"]) > 10
        ), f"Variant {row['id']} has missing or empty description"


def test_eval_results_has_title_columns(tmp_path):
    """Verify source_item_title and target_item_title columns exist."""
    from ollama_queue.db import Database

    db = Database(tmp_path / "q.db")
    db.initialize()
    with db._lock:
        conn = db._connect()
        cols = [row[1] for row in conn.execute("PRAGMA table_info(eval_results)").fetchall()]
    assert "source_item_title" in cols
    assert "target_item_title" in cols


def test_eval_runs_has_analysis_json(tmp_path):
    """Verify analysis_json column exists on eval_runs."""
    from ollama_queue.db import Database

    db = Database(tmp_path / "q.db")
    db.initialize()
    with db._lock:
        conn = db._connect()
        cols = [row[1] for row in conn.execute("PRAGMA table_info(eval_runs)").fetchall()]
    assert "analysis_json" in cols


class TestBatchSetRecurringNextRuns:
    def test_updates_multiple_rows_in_one_call(self, db):
        """batch_set_recurring_next_runs updates all supplied rows atomically."""
        rj1 = db.add_recurring_job("j1", "echo a", interval_seconds=3600)
        rj2 = db.add_recurring_job("j2", "echo b", interval_seconds=3600)
        rj3 = db.add_recurring_job("j3", "echo c", interval_seconds=3600)
        now = time.time()
        updates = {rj1: now + 1000, rj2: now + 2000, rj3: now + 3000}
        db.batch_set_recurring_next_runs(updates)
        assert abs(db.get_recurring_job(rj1)["next_run"] - (now + 1000)) < 0.01
        assert abs(db.get_recurring_job(rj2)["next_run"] - (now + 2000)) < 0.01
        assert abs(db.get_recurring_job(rj3)["next_run"] - (now + 3000)) < 0.01

    def test_empty_dict_is_noop(self, db):
        """batch_set_recurring_next_runs with empty dict doesn't raise."""
        db.batch_set_recurring_next_runs({})  # should not raise

    def test_single_entry_works(self, db):
        """Works correctly with a single-entry dict."""
        rj = db.add_recurring_job("j1", "echo a", interval_seconds=3600)
        now = time.time()
        db.batch_set_recurring_next_runs({rj: now + 500})
        assert abs(db.get_recurring_job(rj)["next_run"] - (now + 500)) < 0.01


class TestGetPendingJobsSentinelFilter:
    def test_excludes_proxy_sentinels_by_default(self, db):
        """get_pending_jobs() hides proxy: sentinel jobs when exclude_sentinel=True (default)."""
        db.submit_job("real-command", "m", 5, 60, "src")
        # Inject a sentinel row directly (mimics try_claim_for_proxy internals)
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO jobs (command, model, priority, timeout, source, status, submitted_at)"
                " VALUES ('proxy:ollama', '', 0, 120, 'proxy', 'pending', ?)",
                (time.time(),),
            )
            conn.commit()
        pending = db.get_pending_jobs()
        commands = [j["command"] for j in pending]
        assert "proxy:ollama" not in commands
        assert "real-command" in commands

    def test_includes_proxy_sentinels_when_flag_false(self, db):
        """get_pending_jobs(exclude_sentinel=False) returns all pending rows including proxy:."""
        db.submit_job("real-command", "m", 5, 60, "src")
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO jobs (command, model, priority, timeout, source, status, submitted_at)"
                " VALUES ('proxy:ollama', '', 0, 120, 'proxy', 'pending', ?)",
                (time.time(),),
            )
            conn.commit()
        pending = db.get_pending_jobs(exclude_sentinel=False)
        commands = [j["command"] for j in pending]
        assert "proxy:ollama" in commands
        assert "real-command" in commands

    def test_default_behavior_unchanged_for_non_sentinel_jobs(self, db):
        """Existing callers with normal jobs see identical behaviour — 3 jobs ordered by priority."""
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


# ────────────────────────────────────────────────────────────────────
# Coverage gap tests — lines 129, 558-559, 929-936, 1002, 1167,
# 1170, 1183, 1342-1343, 1390-1403, 1428, 1558, 1637, 1660, 1664,
# 1736, 1816-1822, 1847, 1933
# ────────────────────────────────────────────────────────────────────


class TestAddColumnIfMissingReraises:
    """Line 129: _add_column_if_missing re-raises non-duplicate OperationalErrors."""

    def test_non_duplicate_column_error_reraises(self, db):
        import sqlite3

        with db._lock:
            conn = db._connect()
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            db._add_column_if_missing(conn, "nonexistent_table", "col", "TEXT")


class TestSeedEvalDefaultsWithExplicitConn:
    """Lines 558-559: seed_eval_defaults() with an explicit conn argument."""

    def test_seed_eval_defaults_with_conn(self, db):
        with db._lock:
            conn = db._connect()
        # Call with explicit connection — exercises the else branch
        db.seed_eval_defaults(conn=conn)
        rows = conn.execute("SELECT * FROM eval_prompt_templates").fetchall()
        assert len(rows) >= 3  # at least the 3 original system templates


class TestSetJobPriority:
    """Lines 929-936: set_job_priority method."""

    def test_set_priority_on_pending_job(self, db):
        job_id = db.submit_job("cmd", "m", priority=5, timeout=60, source="s")
        assert db.set_job_priority(job_id, 2) is True
        job = db.get_job(job_id)
        assert job["priority"] == 2

    def test_set_priority_on_running_job_returns_false(self, db):
        job_id = db.submit_job("cmd", "m", priority=5, timeout=60, source="s")
        db.start_job(job_id)
        assert db.set_job_priority(job_id, 1) is False

    def test_set_priority_nonexistent_job(self, db):
        assert db.set_job_priority(99999, 1) is False


class TestGetSettingStringBool:
    """Line 1002: get_setting converts string 'true'/'false' to Python bool."""

    def test_string_true_returns_bool(self, db):
        # Manually insert a JSON string "true" (not a JSON boolean)
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("test_bool", '"true"', time.time()),
            )
            conn.commit()
        result = db.get_setting("test_bool")
        assert result is True

    def test_string_false_returns_bool(self, db):
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("test_bool", '"false"', time.time()),
            )
            conn.commit()
        result = db.get_setting("test_bool")
        assert result is False


class TestUpdateDaemonStateEdgeCases:
    """Lines 1167, 1170: update_daemon_state early return and unknown field error."""

    def test_empty_kwargs_is_noop(self, db):
        db.update_daemon_state(state="running")
        # Calling with no kwargs should be a no-op (early return)
        db.update_daemon_state()
        state = db.get_daemon_state()
        assert state["state"] == "running"

    def test_unknown_field_raises_value_error(self, db):
        with pytest.raises(ValueError, match="Unknown daemon_state fields"):
            db.update_daemon_state(nonexistent_field="boom")


class TestGetDaemonStateEmptyRow:
    """Line 1183: get_daemon_state returns default dict when row is None."""

    def test_returns_default_when_no_row(self, tmp_path):
        from ollama_queue.db import Database

        db = Database(str(tmp_path / "bare.db"))
        # Initialize just enough to have a connection but no daemon_state row
        with db._lock:
            conn = db._connect()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS daemon_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    state TEXT NOT NULL DEFAULT 'idle',
                    current_job_id INTEGER,
                    paused_since REAL,
                    paused_reason TEXT
                );
            """)
            conn.commit()
        # No INSERT into daemon_state — row is None
        result = db.get_daemon_state()
        assert result == {"state": "idle", "current_job_id": None, "paused_since": None, "paused_reason": None}


class TestUpdateRecurringNextRunNotFound:
    """Lines 1342-1343: update_recurring_next_run when recurring job is deleted."""

    def test_missing_recurring_job_logs_and_returns(self, db):
        with patch("ollama_queue.db.schedule._log") as mock_log:
            db.update_recurring_next_run(99999, completed_at=time.time())
            mock_log.error.assert_called_once()
            assert "not found" in mock_log.error.call_args[0][0]


class TestDeleteRecurringJob:
    """Lines 1390-1403: delete_recurring_job method."""

    def test_delete_existing_recurring_job(self, db):
        rj_id = db.add_recurring_job("test-rj", "echo hi", interval_seconds=3600)
        # Submit a job linked to this recurring job
        job_id = db.submit_job("echo hi", "m", 5, 60, "src", recurring_job_id=rj_id)
        result = db.delete_recurring_job("test-rj")
        assert result is True
        # Recurring job should be gone
        assert db.get_recurring_job(rj_id) is None
        # Job's recurring_job_id should be cleared
        job = db.get_job(job_id)
        assert job["recurring_job_id"] is None

    def test_delete_nonexistent_recurring_job(self, db):
        result = db.delete_recurring_job("does-not-exist")
        assert result is False


class TestUpdateRecurringJobNoAllowedFields:
    """Line 1428: update_recurring_job returns False when no allowed fields given."""

    def test_returns_false_for_disallowed_fields(self, db):
        rj_id = db.add_recurring_job("rj", "echo", interval_seconds=3600)
        result = db.update_recurring_job(rj_id, totally_fake_field="nope")
        assert result is False


class TestMoveToDlqNotFound:
    """Line 1558: move_to_dlq returns None when job doesn't exist."""

    def test_move_nonexistent_job_to_dlq(self, db):
        result = db.move_to_dlq(99999, failure_reason="gone")
        assert result is None


class TestListDlqIncludeResolved:
    """Line 1637: list_dlq with include_resolved=True."""

    def test_include_resolved_returns_all(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="")
        dlq_id = db.move_to_dlq(job_id, failure_reason="failed")
        db.dismiss_dlq_entry(dlq_id)
        # Default (include_resolved=False) should be empty
        assert db.list_dlq(include_resolved=False) == []
        # include_resolved=True should return the dismissed entry
        entries = db.list_dlq(include_resolved=True)
        assert len(entries) == 1
        assert entries[0]["resolution"] == "dismissed"


class TestRetryDlqEntryEdgeCases:
    """Lines 1660, 1664: retry_dlq_entry not found and already resolved."""

    def test_retry_nonexistent_entry(self, db):
        result = db.retry_dlq_entry(99999)
        assert result is None

    def test_retry_already_resolved_entry(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="")
        dlq_id = db.move_to_dlq(job_id, failure_reason="failed")
        db.dismiss_dlq_entry(dlq_id)
        # Entry is now resolved — retry should return None
        result = db.retry_dlq_entry(dlq_id)
        assert result is None


class TestGetJobMetricsNotFound:
    """Line 1736: get_job_metrics returns None when not found."""

    def test_no_metrics_returns_none(self, db):
        result = db.get_job_metrics(99999)
        assert result is None


class TestHasPullingModel:
    """Lines 1816-1822: has_pulling_model method."""

    def test_no_pulling_model(self, db):
        assert db.has_pulling_model("llama2:7b") is False

    def test_with_pulling_model(self, db):
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_pulls (model, status, started_at) VALUES (?, ?, ?)",
                ("llama2:7b", "pulling", time.time()),
            )
            conn.commit()
        assert db.has_pulling_model("llama2:7b") is True

    def test_completed_pull_not_detected(self, db):
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_pulls (model, status, started_at) VALUES (?, ?, ?)",
                ("llama2:7b", "completed", time.time()),
            )
            conn.commit()
        assert db.has_pulling_model("llama2:7b") is False


class TestUpsertConsumerKeyFieldsOnly:
    """Line 1847: upsert_consumer returns existing id when only key fields provided."""

    def test_upsert_with_only_key_fields(self, db):
        first_id = db.upsert_consumer(
            {
                "name": "test-svc",
                "platform": "systemd",
                "type": "timer",
                "source_label": "test",
                "status": "discovered",
                "detected_at": int(time.time()),
            }
        )
        # Upsert with only key fields — nothing to update
        second_id = db.upsert_consumer({"name": "test-svc", "platform": "systemd"})
        assert second_id == first_id


class TestResumeDeferredJobNotFound:
    """Line 1933: resume_deferred_job returns early when deferral not found."""

    def test_resume_nonexistent_deferral(self, db):
        # Should not raise — just returns silently
        db.resume_deferred_job(99999)


class TestRetryOnBusy:
    """SQLITE_BUSY retry logic for WAL checkpoint contention (#16)."""

    def test_retry_succeeds_after_transient_busy(self, db):
        """Operation succeeds on second attempt after SQLITE_BUSY."""
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        result = db._retry_on_busy(flaky)
        assert result == "ok"
        assert call_count == 2

    def test_retry_exhausted_raises(self, db):
        """After max_retries, the error propagates."""

        def always_locked():
            raise sqlite3.OperationalError("database is locked")

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            db._retry_on_busy(always_locked, max_retries=2)

    def test_non_locked_error_not_retried(self, db):
        """Non-locked OperationalErrors propagate immediately without retry."""
        call_count = 0

        def bad_sql():
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("no such table: bogus")

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            db._retry_on_busy(bad_sql)
        assert call_count == 1  # no retry

    def test_retry_returns_value(self, db):
        """Return value from the wrapped function is propagated."""
        result = db._retry_on_busy(lambda: 42)
        assert result == 42

    @patch("ollama_queue.db._time.sleep")
    def test_retry_uses_exponential_backoff(self, mock_sleep, db):
        """Backoff doubles on each retry attempt."""
        attempt = 0

        def fail_twice():
            nonlocal attempt
            attempt += 1
            if attempt <= 2:
                raise sqlite3.OperationalError("database is locked")
            return "done"

        result = db._retry_on_busy(fail_twice, max_retries=2, backoff=0.1)
        assert result == "done"
        assert mock_sleep.call_count == 2
        # First retry: 0.1 * 2^0 = 0.1
        assert mock_sleep.call_args_list[0][0][0] == pytest.approx(0.1)
        # Second retry: 0.1 * 2^1 = 0.2
        assert mock_sleep.call_args_list[1][0][0] == pytest.approx(0.2)


class TestRetryOnBusyIntegration:
    """Verify high-frequency write methods use _retry_on_busy (#16)."""

    def test_log_health_calls_retry(self, db):
        """log_health routes its write through _retry_on_busy."""
        calls = []
        original = db._retry_on_busy

        def spy(fn, **kw):
            calls.append(True)
            return original(fn, **kw)

        db._retry_on_busy = spy
        db.log_health(50.0, 30.0, 1.5, 10.0, "llama2:7b", 3, "running")
        assert len(calls) == 1

        # Verify data was actually written
        logs = db.get_health_log(hours=1)
        assert len(logs) >= 1

    def test_complete_job_calls_retry(self, db):
        """complete_job routes its write through _retry_on_busy."""
        job_id = db.submit_job("echo hi", "m", 5, 60, "test")
        db.start_job(job_id)

        calls = []
        original = db._retry_on_busy

        def spy(fn, **kw):
            calls.append(True)
            return original(fn, **kw)

        db._retry_on_busy = spy
        db.complete_job(job_id, 0, "output", "")
        assert len(calls) == 1

        job = db.get_job(job_id)
        assert job["status"] == "completed"

    def test_submit_job_calls_retry(self, db):
        """submit_job routes its write through _retry_on_busy."""
        calls = []
        original = db._retry_on_busy

        def spy(fn, **kw):
            calls.append(True)
            return original(fn, **kw)

        db._retry_on_busy = spy
        job_id = db.submit_job("echo hi", "m", 5, 60, "test")
        assert len(calls) == 1

        assert job_id is not None
        assert job_id > 0
        job = db.get_job(job_id)
        assert job["command"] == "echo hi"

    def test_update_daemon_state_calls_retry(self, db):
        """update_daemon_state routes its write through _retry_on_busy."""
        calls = []
        original = db._retry_on_busy

        def spy(fn, **kw):
            calls.append(True)
            return original(fn, **kw)

        db._retry_on_busy = spy
        db.update_daemon_state(state="paused", paused_reason="test")
        assert len(calls) == 1

        state = db.get_daemon_state()
        assert state["state"] == "paused"
        assert state["paused_reason"] == "test"


class TestBackendMetrics:
    """store_backend_metrics and get_backend_stats round-trip."""

    def test_store_and_retrieve(self, db):
        db.store_backend_metrics(
            backend_url="http://127.0.0.1:11434",
            model="deepseek-r1:8b",
            metrics={
                "eval_count": 120,
                "eval_duration": 6_000_000_000,  # 6s → 1200 tok/min
                "load_duration": 2_000_000_000,
                "prompt_eval_count": 10,
                "prompt_eval_duration": 500_000_000,
                "total_duration": 8_000_000_000,
            },
        )
        rows = db.get_backend_stats()
        assert len(rows) == 1
        row = rows[0]
        assert row["backend_url"] == "http://127.0.0.1:11434"
        assert row["model"] == "deepseek-r1:8b"
        assert row["run_count"] == 1
        assert row["avg_tok_per_min"] == pytest.approx(1200.0, rel=0.01)
        assert row["avg_warmup_s"] == pytest.approx(2.0, rel=0.01)

    def test_no_eval_count_skips_tok_per_min(self, db):
        db.store_backend_metrics(
            backend_url="http://127.0.0.1:11434",
            model="nomic-embed-text",
            metrics={"load_duration": 1_000_000_000},
        )
        rows = db.get_backend_stats()
        assert rows[0]["avg_tok_per_min"] is None
        assert rows[0]["avg_warmup_s"] == pytest.approx(1.0, rel=0.01)

    def test_empty_returns_empty_list(self, db):
        assert db.get_backend_stats() == []
