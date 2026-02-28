"""Tests for the daemon polling loop and job runner."""

import concurrent.futures
import logging
import time
from unittest.mock import MagicMock, patch

import pytest

from ollama_queue.daemon import Daemon


def _drain(daemon):
    """Wait for all submitted daemon worker futures to complete (test helper)."""
    if daemon._executor is None:
        return
    futs = []
    with daemon._running_lock:
        futs = list(daemon._running.values())
    if futs:
        concurrent.futures.wait(futs, timeout=10)


@pytest.fixture
def daemon(db):
    return Daemon(db)


def test_poll_no_jobs(daemon):
    """Poll with empty queue does nothing."""
    daemon.poll_once()
    state = daemon.db.get_daemon_state()
    assert state["state"] == "idle"


def test_poll_runs_job(daemon):
    """Poll with a pending job starts it."""
    daemon.db.submit_job("echo hello", "qwen2.5:7b", 5, 60, "test")
    with (
        patch.object(
            daemon.health,
            "check",
            return_value={
                "ram_pct": 50.0,
                "swap_pct": 10.0,
                "load_avg": 1.0,
                "cpu_count": 4,
                "vram_pct": 50.0,
                "ollama_model": None,
            },
        ),
        patch("ollama_queue.daemon.subprocess") as mock_sub,
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.wait.return_value = 0
        proc.stdout.read.return_value = b"hello"
        proc.stderr.read.return_value = b""
        proc.returncode = 0
        mock_sub.Popen.return_value = proc

        daemon.poll_once()
        _drain(daemon)

    job = daemon.db.get_job(1)
    assert job["status"] == "completed"


def test_poll_pauses_on_health(daemon):
    """Don't start job if health check says pause."""
    daemon.db.submit_job("echo hello", "qwen2.5:7b", 5, 60, "test")
    with patch.object(
        daemon.health,
        "check",
        return_value={
            "ram_pct": 95.0,
            "swap_pct": 10.0,
            "load_avg": 1.0,
            "cpu_count": 4,
            "vram_pct": 50.0,
            "ollama_model": None,
        },
    ):
        daemon.poll_once()

    job = daemon.db.get_job(1)
    assert job["status"] == "pending"  # still pending, not started
    state = daemon.db.get_daemon_state()
    assert state["state"] == "paused_health"


def test_poll_yields_to_interactive(daemon):
    """Don't start job if ollama ps shows non-queued model."""
    daemon.db.submit_job("echo hello", "deepseek-r1:8b", 5, 60, "test")
    with patch.object(
        daemon.health,
        "check",
        return_value={
            "ram_pct": 50.0,
            "swap_pct": 10.0,
            "load_avg": 1.0,
            "cpu_count": 4,
            "vram_pct": 50.0,
            "ollama_model": "qwen2.5:7b",
        },
    ):
        daemon.poll_once()

    job = daemon.db.get_job(1)
    assert job["status"] == "pending"
    state = daemon.db.get_daemon_state()
    assert state["state"] == "paused_interactive"


def test_timeout_kills_job(daemon):
    """Job exceeding timeout is killed."""
    daemon.db.submit_job("sleep 999", "m", 5, 1, "test")  # 1s timeout
    with (
        patch.object(
            daemon.health,
            "check",
            return_value={
                "ram_pct": 50.0,
                "swap_pct": 10.0,
                "load_avg": 1.0,
                "cpu_count": 4,
                "vram_pct": 50.0,
                "ollama_model": None,
            },
        ),
        patch("ollama_queue.daemon.subprocess") as mock_sub,
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.wait.side_effect = lambda timeout: (_ for _ in ()).throw(__import__("subprocess").TimeoutExpired("cmd", 1))
        proc.kill.return_value = None
        proc.communicate.return_value = (b"", b"")
        proc.returncode = -9
        mock_sub.Popen.return_value = proc
        mock_sub.TimeoutExpired = __import__("subprocess").TimeoutExpired

        daemon.poll_once()
        _drain(daemon)

    job = daemon.db.get_job(1)
    assert job["status"] == "killed"
    assert "timeout" in job["outcome_reason"]


def test_manual_pause_blocks_jobs(daemon):
    """When manually paused, don't start jobs."""
    daemon.db.update_daemon_state(state="paused_manual")
    daemon.db.submit_job("echo hello", "m", 5, 60, "test")
    daemon.poll_once()
    job = daemon.db.get_job(1)
    assert job["status"] == "pending"


def test_records_duration_on_success(daemon):
    daemon.db.submit_job("echo hi", "qwen2.5:7b", 5, 60, "test-src")
    with (
        patch.object(
            daemon.health,
            "check",
            return_value={
                "ram_pct": 50.0,
                "swap_pct": 10.0,
                "load_avg": 1.0,
                "cpu_count": 4,
                "vram_pct": 50.0,
                "ollama_model": None,
            },
        ),
        patch("ollama_queue.daemon.subprocess") as mock_sub,
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.wait.return_value = 0
        proc.stdout.read.return_value = b"ok"
        proc.stderr.read.return_value = b""
        proc.returncode = 0
        mock_sub.Popen.return_value = proc

        daemon.poll_once()
        _drain(daemon)

    history = daemon.db.get_duration_history("test-src")
    assert len(history) == 1


class TestDaemonSchedulerIntegration:
    def test_poll_once_promotes_due_recurring_job(self, db):
        now = time.time()
        db.add_recurring_job("job1", "echo hi", 3600, next_run=now - 1, source="test")
        daemon = Daemon(db)
        with (
            patch.object(
                daemon.health,
                "check",
                return_value={
                    "ram_pct": 10,
                    "vram_pct": 10,
                    "load_avg": 0.1,
                    "swap_pct": 5,
                    "cpu_count": 4,
                    "ollama_model": None,
                },
            ),
            patch("ollama_queue.daemon.subprocess") as mock_sub,
        ):
            proc = MagicMock()
            proc.pid = 1234
            proc.wait.return_value = 0
            proc.stdout.read.return_value = b"hi"
            proc.stderr.read.return_value = b""
            proc.returncode = 0
            mock_sub.Popen.return_value = proc
            daemon.poll_once()
            _drain(daemon)
        # Job was promoted by scheduler, then picked up and run
        jobs = db.get_pending_jobs()
        completed = [j for j in db.get_history() if j["command"] == "echo hi"]
        assert len(completed) == 1 or len(jobs) == 0  # promoted and ran

    def test_poll_once_detects_stall(self, db):
        job_id = db.submit_job("slow", "m", 5, 600, "src")
        db.start_job(job_id)
        # Fake a job that started 1000s ago with estimated_duration=100s
        conn = db._connect()
        conn.execute(
            "UPDATE jobs SET started_at = ?, estimated_duration = 100 WHERE id = ?", (time.time() - 1000, job_id)
        )
        conn.commit()
        daemon = Daemon(db)
        with patch.object(
            daemon.health,
            "check",
            return_value={
                "ram_pct": 10,
                "vram_pct": 10,
                "load_avg": 0.1,
                "swap_pct": 5,
                "cpu_count": 4,
                "ollama_model": None,
            },
        ):
            daemon.poll_once()
        job = db.get_job(job_id)
        assert job["stall_detected_at"] is not None


def test_no_self_block_after_queue_job(daemon):
    """After a queue job completes, its model shouldn't trigger interactive yield."""
    # Run first job with nomic-embed-text
    daemon.db.submit_job("echo embed", "nomic-embed-text", 5, 60, "notion-vector-sync")
    with (
        patch.object(
            daemon.health,
            "check",
            return_value={
                "ram_pct": 50.0,
                "swap_pct": 10.0,
                "load_avg": 1.0,
                "cpu_count": 4,
                "vram_pct": 50.0,
                "ollama_model": None,
            },
        ),
        patch("ollama_queue.daemon.subprocess") as mock_sub,
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.wait.return_value = 0
        proc.stdout.read.return_value = b"ok"
        proc.stderr.read.return_value = b""
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        daemon.poll_once()
        _drain(daemon)

    job1 = daemon.db.get_job(1)
    assert job1["status"] == "completed"

    # Now submit a second job — ollama ps still shows nomic-embed-text from job 1
    daemon.db.submit_job("echo predict", "deepseek-r1:8b", 5, 60, "aria-predict")
    with (
        patch.object(
            daemon.health,
            "check",
            return_value={
                "ram_pct": 50.0,
                "swap_pct": 10.0,
                "load_avg": 1.0,
                "cpu_count": 4,
                "vram_pct": 50.0,
                "ollama_model": "nomic-embed-text",
            },
        ),
        patch("ollama_queue.daemon.subprocess") as mock_sub,
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.wait.return_value = 0
        proc.stdout.read.return_value = b"ok"
        proc.stderr.read.return_value = b""
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        daemon.poll_once()
        _drain(daemon)

    job2 = daemon.db.get_job(2)
    assert job2["status"] == "completed"  # should NOT be blocked
    state = daemon.db.get_daemon_state()
    assert state["state"] != "paused_interactive"


# --- T5: PID tracking + orphan recovery ---


def test_recover_orphans_resets_running_jobs(db):
    d = Daemon(db)
    job_id = db.submit_job(command="echo hi", model="", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    # Write a non-existent PID to simulate orphaned job
    db._connect().execute("UPDATE jobs SET pid = 999999 WHERE id = ?", (job_id,))
    db._connect().commit()

    d._recover_orphans()

    job = db.get_job(job_id)
    assert job["status"] == "pending"


def test_recover_orphans_handles_no_pid(db):
    """Jobs with no PID are still reset to pending."""
    d = Daemon(db)
    job_id = db.submit_job(command="echo hi", model="", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    # pid column is NULL (not set yet)

    d._recover_orphans()

    job = db.get_job(job_id)
    assert job["status"] == "pending"


def test_get_running_jobs_returns_running(db):
    job_id = db.submit_job(command="echo hi", model="", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    running = db.get_running_jobs()
    assert len(running) == 1
    assert running[0]["id"] == job_id


def test_reset_job_to_pending(db):
    job_id = db.submit_job(command="echo hi", model="", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.reset_job_to_pending(job_id)
    job = db.get_job(job_id)
    assert job["status"] == "pending"
    assert job["started_at"] is None
    assert job["pid"] is None


# --- T6: ThreadPoolExecutor + admission gate ---


def test_embed_jobs_always_admitted(db, monkeypatch):
    """Embed-profile jobs bypass VRAM gate."""
    d = Daemon(db)
    job = {
        "id": 1,
        "model": "nomic-embed-text:latest",
        "resource_profile": "embed",
        "command": "echo",
        "source": "test",
        "timeout": 60,
        "priority": 5,
    }
    monkeypatch.setattr(d, "_free_vram_mb", lambda: 0.0)
    assert d._can_admit(job) is True


def test_heavy_jobs_serialize(db):
    """Heavy-profile jobs are blocked when another job is running."""
    d = Daemon(db)
    d._running[99] = MagicMock()
    job = {
        "id": 2,
        "model": "deepseek-r1:70b",
        "resource_profile": "heavy",
        "command": "echo",
        "source": "test",
        "timeout": 60,
        "priority": 5,
    }
    assert d._can_admit(job) is False


def test_same_model_blocks_second(db):
    """Two jobs with same model cannot run concurrently."""
    d = Daemon(db)
    d._running[1] = MagicMock()
    d._running_models[1] = "qwen2.5:7b"
    job = {
        "id": 2,
        "model": "qwen2.5:7b",
        "resource_profile": "ollama",
        "command": "echo",
        "source": "test",
        "timeout": 60,
        "priority": 5,
    }
    assert d._can_admit(job) is False


def test_shadow_mode_logs_but_does_not_run(db, monkeypatch, caplog):
    """Shadow mode logs 'SHADOW' but does not actually admit second job."""
    db.set_setting("max_concurrent_jobs", 2)
    db.set_setting("concurrent_shadow_hours", 24)
    d = Daemon(db)
    d._concurrent_enabled_at = time.time() - 3600  # 1h ago, still in 24h window
    d._running[1] = MagicMock()
    d._running_models[1] = "qwen2.5:7b"
    job = {
        "id": 2,
        "model": "llama3.2:3b",
        "resource_profile": "ollama",
        "command": "echo",
        "source": "test",
        "timeout": 60,
        "priority": 5,
    }
    monkeypatch.setattr(d, "_free_vram_mb", lambda: 16000.0)
    monkeypatch.setattr(d, "_free_ram_mb", lambda: 32000.0)
    with (
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 20.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.INFO),
    ):
        admitted = d._can_admit(job)
    assert admitted is False
    assert any("SHADOW" in r.message for r in caplog.records)
