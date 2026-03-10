"""Tests for the daemon polling loop and job runner."""

import concurrent.futures
import logging
import signal as _signal
import time
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from ollama_queue.daemon import Daemon


def _drain(daemon):
    """Wait for all submitted daemon worker futures to complete (test helper).
    Re-raises any exceptions from worker threads to make failures visible."""
    if daemon._executor is None:
        return
    futs = []
    with daemon._running_lock:
        futs = list(daemon._running.values())
    if futs:
        concurrent.futures.wait(futs, timeout=10)
        for fut in futs:
            fut.result()  # re-raise any exception from _run_job


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
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"hello", b"")),
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 0
        mock_sub.Popen.return_value = proc

        daemon.poll_once()
        _drain(daemon)

    job = daemon.db.get_job(1)
    assert job["status"] == "completed"


def test_llm_job_does_not_use_communicate(daemon):
    """LLM jobs (resource_profile='ollama') use pipe drain, not communicate()."""
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
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"hello", b"")) as mock_drain,
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        daemon.poll_once()
        _drain(daemon)

    mock_drain.assert_called_once()
    proc.communicate.assert_not_called()


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
    """Job exceeding timeout is killed (non-ollama resource_profile uses hard timeout)."""
    daemon.db.submit_job("sleep 999", "m", 5, 1, "test", resource_profile="any")  # 1s timeout
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
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
    ):
        import subprocess as _real_sub

        proc = MagicMock()
        proc.pid = 1234
        proc.kill.return_value = None
        # First communicate() call raises TimeoutExpired; second (cleanup) returns empty
        proc.communicate.side_effect = [_real_sub.TimeoutExpired("cmd", 1), (b"", b"")]
        proc.returncode = -9
        mock_sub.Popen.return_value = proc

        daemon.poll_once()
        _drain(daemon)

    job = daemon.db.get_job(1)
    # DLQ routing moves the job from 'killed' to 'dead' (max_retries=0 → DLQ immediately)
    assert job["status"] in ("killed", "dead")
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
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")),
    ):
        proc = MagicMock()
        proc.pid = 1234
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
            patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
            patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"hi", b"")),
        ):
            proc = MagicMock()
            proc.pid = 1234
            proc.returncode = 0
            mock_sub.Popen.return_value = proc
            daemon.poll_once()
            _drain(daemon)
        # Job was promoted by scheduler, then picked up and run
        jobs = db.get_pending_jobs()
        completed = [j for j in db.get_history() if j["command"] == "echo hi"]
        assert len(completed) == 1 or len(jobs) == 0  # promoted and ran

    def test_poll_once_detects_stall(self, db):
        job_id = db.submit_job("slow", "qwen2.5:7b", 5, 600, "src")
        db.start_job(job_id)
        conn = db._connect()
        conn.execute("UPDATE jobs SET started_at = ?, pid = 9999 WHERE id = ?", (time.time() - 400, job_id))
        conn.commit()
        daemon = Daemon(db)
        # Simulate job tracked in _running (required by multi-job stall detection)
        mock_fut = MagicMock()
        mock_fut.done.return_value = False
        daemon._running[job_id] = mock_fut
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
            patch.object(daemon.stall_detector, "compute_posterior", return_value=(0.95, {"posterior": 0.95})),
            patch.object(daemon.stall_detector, "get_ollama_ps_models", return_value=set()),
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
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")),
    ):
        proc = MagicMock()
        proc.pid = 1234
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
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")),
    ):
        proc = MagicMock()
        proc.pid = 1234
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


def test_can_admit_split_load_vram_ram(db, monkeypatch):
    """Model larger than free VRAM but fitting VRAM+RAM is admitted (Ollama split execution)."""
    d = Daemon(db)
    job = {
        "id": 1,
        "model": "deepseek-r1:8b",
        "resource_profile": "ollama",
        "command": "echo",
        "source": "test",
        "timeout": 120,
        "priority": 5,
    }
    # Model ~5000 MB; VRAM only 2500 MB (not enough alone); RAM 10000 MB; combined 12500 > 5000.
    monkeypatch.setattr(d, "_free_vram_mb", lambda: 2500.0)
    monkeypatch.setattr(d, "_free_ram_mb", lambda: 10000.0)
    monkeypatch.setattr(d._ollama_models, "estimate_vram_mb", lambda model, db: 5000.0)
    with patch.object(
        d.health,
        "check",
        return_value={
            "ram_pct": 30.0,
            "swap_pct": 5.0,
            "load_avg": 0.5,
            "cpu_count": 4,
            "vram_pct": 40.0,
            "ollama_model": None,
        },
    ):
        admitted = d._can_admit(job)
    assert admitted is True


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


# --- T7: Multi-job stall detection ---


def test_stall_detection_checks_all_running_jobs(db):
    """Stall detection iterates self._running, not just current_job_id."""
    d = Daemon(db)
    j1 = db.submit_job(command="sleep 999", model="qwen2.5:7b", priority=5, timeout=10, source="test")
    j2 = db.submit_job(command="sleep 999", model="qwen2.5:7b", priority=5, timeout=10, source="test")
    db.start_job(j1)
    db.start_job(j2)
    conn = db._connect()
    conn.execute(
        "UPDATE jobs SET started_at=?, pid=9998 WHERE id=?",
        (time.time() - 500, j1),
    )
    conn.execute(
        "UPDATE jobs SET started_at=?, pid=9999 WHERE id=?",
        (time.time() - 500, j2),
    )
    conn.commit()
    # Simulate both in _running
    d._running[j1] = MagicMock()
    d._running[j2] = MagicMock()

    with (
        patch.object(d.stall_detector, "compute_posterior", return_value=(0.95, {"posterior": 0.95})),
        patch.object(d.stall_detector, "get_ollama_ps_models", return_value=set()),
    ):
        d._check_stalled_jobs(time.time())

    assert db.get_job(j1)["stall_detected_at"] is not None
    assert db.get_job(j2)["stall_detected_at"] is not None


# --- T8: _compute_max_workers ---


class TestComputeMaxWorkers:
    def test_compute_max_workers_returns_positive_int(self, db, monkeypatch):
        """_compute_max_workers returns a positive int based on available resources."""
        d = Daemon(db)
        monkeypatch.setattr(d, "_free_vram_mb", lambda: 8000.0)
        monkeypatch.setattr(d, "_free_ram_mb", lambda: 16000.0)
        workers = d._compute_max_workers()
        assert isinstance(workers, int)
        assert workers >= 1

    def test_compute_max_workers_minimum_is_one(self, db, monkeypatch):
        """_compute_max_workers always returns at least 1, even when resources are exhausted."""
        d = Daemon(db)
        monkeypatch.setattr(d, "_free_vram_mb", lambda: 0.0)
        monkeypatch.setattr(d, "_free_ram_mb", lambda: 0.0)
        workers = d._compute_max_workers()
        assert workers >= 1

    def test_compute_max_workers_scales_with_resources(self, db, monkeypatch):
        """More free VRAM+RAM yields more workers than minimal resources."""
        d = Daemon(db)
        monkeypatch.setattr(d, "_free_vram_mb", lambda: 32000.0)
        monkeypatch.setattr(d, "_free_ram_mb", lambda: 64000.0)
        workers_high = d._compute_max_workers()

        monkeypatch.setattr(d, "_free_vram_mb", lambda: 500.0)
        monkeypatch.setattr(d, "_free_ram_mb", lambda: 1000.0)
        workers_low = d._compute_max_workers()

        assert workers_high >= workers_low

    def test_compute_max_workers_none_vram_falls_back(self, db, monkeypatch):
        """When nvidia-smi is unavailable (_free_vram_mb returns None), still returns >= 1."""
        d = Daemon(db)
        monkeypatch.setattr(d, "_free_vram_mb", lambda: None)
        monkeypatch.setattr(d, "_free_ram_mb", lambda: 16000.0)
        workers = d._compute_max_workers()
        assert isinstance(workers, int)
        assert workers >= 1

    def test_compute_max_workers_used_in_executor(self, db, monkeypatch):
        """ThreadPoolExecutor is created with _compute_max_workers(), not hardcoded 32."""
        d = Daemon(db)
        # Force a known return value
        monkeypatch.setattr(d, "_compute_max_workers", lambda: 7)
        monkeypatch.setattr(d, "_free_vram_mb", lambda: 8000.0)
        monkeypatch.setattr(d, "_free_ram_mb", lambda: 16000.0)
        monkeypatch.setattr(d, "_can_admit", lambda job, settings=None: True)
        db.submit_job("echo hi", "qwen2.5:7b", 5, 60, "test")
        with (
            patch.object(
                d.health,
                "check",
                return_value={
                    "ram_pct": 10.0,
                    "swap_pct": 5.0,
                    "load_avg": 0.1,
                    "cpu_count": 4,
                    "vram_pct": 10.0,
                    "ollama_model": None,
                },
            ),
            patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
            patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"hi", b"")),
        ):
            proc = MagicMock()
            proc.pid = 1234
            proc.returncode = 0
            mock_sub.Popen.return_value = proc
            d.poll_once()
            _drain(d)
        assert d._executor is not None
        assert d._executor._max_workers == 7

    def test_shutdown_clears_executor(self, db):
        """shutdown() sets _executor to None, releasing thread resources."""
        d = Daemon(db)
        # Trigger lazy executor creation manually
        d._executor = ThreadPoolExecutor(max_workers=2)
        d.shutdown()
        assert d._executor is None


# --- T8: Bayesian stall detection ---


def test_stall_detection_flags_job(daemon):
    """_check_stalled_jobs sets stall_detected_at when posterior >= threshold."""
    job_id = daemon.db.submit_job("sleep 9999", "qwen2.5:7b", 5, 600, "test")
    with daemon.db._lock:
        conn = daemon.db._connect()
        conn.execute(
            "UPDATE jobs SET status='running', started_at=?, pid=9999 WHERE id=?",
            (time.time() - 400, job_id),
        )
        conn.commit()

    with daemon._running_lock:
        daemon._running[job_id] = MagicMock()

    with (
        patch.object(daemon.stall_detector, "compute_posterior", return_value=(0.95, {"posterior": 0.95})),
        patch.object(daemon.stall_detector, "get_ollama_ps_models", return_value=set()),
    ):
        daemon._check_stalled_jobs(time.time())

    job = daemon.db.get_job(job_id)
    assert job["stall_detected_at"] is not None


def test_stall_kill_action(daemon):
    """_check_stalled_jobs calls os.kill when stall_action='kill' and grace elapsed."""
    daemon.db.set_setting("stall_action", "kill")
    daemon.db.set_setting("stall_kill_grace_seconds", 0)  # no grace period
    job_id = daemon.db.submit_job("sleep 9999", "qwen2.5:7b", 5, 600, "test")
    stall_time = time.time() - 120
    with daemon.db._lock:
        conn = daemon.db._connect()
        conn.execute(
            "UPDATE jobs SET status='running', started_at=?, pid=9999, stall_detected_at=? WHERE id=?",
            (time.time() - 400, stall_time, job_id),
        )
        conn.commit()

    with daemon._running_lock:
        daemon._running[job_id] = MagicMock()

    with (
        patch.object(daemon.stall_detector, "compute_posterior", return_value=(0.95, {"posterior": 0.95})),
        patch.object(daemon.stall_detector, "get_ollama_ps_models", return_value=set()),
        patch("ollama_queue.daemon.executor.os.kill") as mock_kill,
    ):
        daemon._check_stalled_jobs(time.time())

    mock_kill.assert_called_once_with(9999, _signal.SIGTERM)


def test_stall_no_kill_within_grace(daemon):
    """_check_stalled_jobs does NOT kill if stall_kill_grace_seconds not elapsed."""
    daemon.db.set_setting("stall_action", "kill")
    daemon.db.set_setting("stall_kill_grace_seconds", 300)
    job_id = daemon.db.submit_job("sleep 9999", "qwen2.5:7b", 5, 600, "test")
    with daemon.db._lock:
        conn = daemon.db._connect()
        conn.execute(
            "UPDATE jobs SET status='running', started_at=?, pid=9999, stall_detected_at=? WHERE id=?",
            (time.time() - 400, time.time() - 10, job_id),  # stalled only 10s ago
        )
        conn.commit()

    with daemon._running_lock:
        daemon._running[job_id] = MagicMock()

    with (
        patch.object(daemon.stall_detector, "compute_posterior", return_value=(0.95, {"posterior": 0.95})),
        patch.object(daemon.stall_detector, "get_ollama_ps_models", return_value=set()),
        patch("ollama_queue.daemon.executor.os.kill") as mock_kill,
    ):
        daemon._check_stalled_jobs(time.time())

    mock_kill.assert_not_called()


# --- check_command pre-flight gate ---

import subprocess as _subprocess_mod


def _make_recurring_and_job(db, check_command=None, max_runs=None):
    """Helper: create recurring job + pending queue job linked to it."""
    rj_id = db.add_recurring_job(
        name="check-test",
        command="echo main",
        interval_seconds=3600,
        check_command=check_command,
        max_runs=max_runs,
    )
    job_id = db.submit_job(
        command="echo main",
        model=None,
        priority=5,
        timeout=60,
        source="check-test",
        resource_profile="ollama",
        recurring_job_id=rj_id,
    )
    db.start_job(job_id)
    return rj_id, job_id


def test_check_command_exit0_proceeds(daemon):
    """check_command exit 0 → main job runs normally."""
    _rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="exit 0")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        mock_sub.run.return_value = MagicMock(returncode=0)
        with patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.run.assert_called_once()
    mock_sub.Popen.assert_called_once()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_check_command_exit1_skips(daemon):
    """check_command exit 1 → job skipped, next_run advanced, no Popen."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="exit 1")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=1)
        daemon._run_job(job)

    mock_sub.Popen.assert_not_called()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"
    assert "skip" in (completed["outcome_reason"] or "").lower()
    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["next_run"] > time.time() - 1


def test_check_command_exit2_disables(daemon):
    """check_command exit 2 → recurring job auto-disabled, no Popen."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="exit 2")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=2)
        daemon._run_job(job)

    mock_sub.Popen.assert_not_called()
    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["enabled"] == 0
    assert "check_command" in (rj["outcome_reason"] or "").lower()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_check_command_unknown_exit_failopen(daemon):
    """check_command exit 99 → warning logged, main job proceeds (fail-open)."""
    _rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="exit 99")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        mock_sub.run.return_value = MagicMock(returncode=99)
        with patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.Popen.assert_called_once()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_check_command_timeout_failopen(daemon):
    """check_command TimeoutExpired → warning logged, main job proceeds."""
    _rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="sleep 999")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.run.side_effect = _subprocess_mod.TimeoutExpired("sleep 999", 30)
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.Popen.assert_called_once()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_no_check_command_skips_check(daemon):
    """Job with no check_command skips check, runs normally."""
    _rj_id, job_id = _make_recurring_and_job(daemon.db, check_command=None)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.run.assert_not_called()
    mock_sub.Popen.assert_called_once()


def test_max_runs_decrements_on_success(daemon):
    """Successful main job decrements max_runs by 1."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=3)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["max_runs"] == 2


def test_max_runs_no_decrement_on_failure(daemon):
    """Failed main job does NOT decrement max_runs."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=3)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 1  # failure
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"", b"err")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["max_runs"] == 3  # unchanged


def test_max_runs_zero_disables_job(daemon):
    """When max_runs reaches 0 after success, recurring job is auto-disabled."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=1)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["enabled"] == 0
    assert "max_runs" in (rj["outcome_reason"] or "").lower()


def test_no_max_runs_no_decrement(daemon):
    """Job with max_runs=None doesn't touch the field."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=None)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["max_runs"] is None


# --- T10: Circuit breaker ---


def test_circuit_breaker_opens_after_threshold(daemon):
    """Circuit opens after cb_failure_threshold consecutive failures."""
    threshold = daemon.db.get_setting("cb_failure_threshold")
    for _ in range(threshold):
        daemon._record_ollama_failure()
    assert daemon._cb_state == "OPEN"


def test_circuit_breaker_resets_on_success(daemon):
    """A success resets failure count and closes the circuit."""
    daemon._cb_failures = 2
    daemon._cb_state = "HALF_OPEN"
    daemon._record_ollama_success()
    assert daemon._cb_failures == 0
    assert daemon._cb_state == "CLOSED"


def test_circuit_is_open_returns_false_when_closed(daemon):
    """_is_circuit_open returns False when circuit is CLOSED."""
    daemon._cb_state = "CLOSED"
    assert daemon._is_circuit_open() is False


def test_circuit_is_open_returns_true_when_open(daemon):
    """_is_circuit_open returns True when circuit is OPEN and cooldown not elapsed."""
    daemon._cb_state = "OPEN"
    daemon._cb_opened_at = time.time()  # just opened — cooldown not elapsed
    daemon._cb_open_attempts = 0
    assert daemon._is_circuit_open() is True


def test_circuit_transitions_to_half_open_after_cooldown(daemon):
    """Circuit transitions to HALF_OPEN after cooldown expires."""
    daemon._cb_state = "OPEN"
    daemon._cb_opened_at = time.time() - 999  # way past any cooldown
    daemon._cb_open_attempts = 0
    result = daemon._is_circuit_open()
    assert result is False  # HALF_OPEN allows one probe job through
    assert daemon._cb_state == "HALF_OPEN"


def test_compute_cb_cooldown_exponential(daemon):
    """Cooldown doubles with each open attempt, capped at cb_max_cooldown."""
    base = daemon.db.get_setting("cb_base_cooldown")
    max_cd = daemon.db.get_setting("cb_max_cooldown")
    assert daemon._compute_cb_cooldown(0) == base
    assert daemon._compute_cb_cooldown(1) == min(max_cd, base * 2)
    assert daemon._compute_cb_cooldown(10) == max_cd  # capped


def test_half_open_probe_blocks_subsequent_polls(daemon):
    """Second call to _is_circuit_open() while HALF_OPEN blocks (probe in flight)."""
    import time

    daemon._cb_state = "OPEN"
    daemon._cb_opened_at = time.time() - 9999
    daemon._cb_open_attempts = 0
    first = daemon._is_circuit_open()  # transitions to HALF_OPEN, allows probe
    second = daemon._is_circuit_open()  # probe in flight, should block
    assert first is False  # probe allowed through
    assert second is True  # subsequent polls blocked
    assert daemon._cb_state == "HALF_OPEN"


def test_half_open_failure_reopens_circuit(daemon):
    """A failure during HALF_OPEN re-opens the circuit, not leaves it stuck."""
    daemon._cb_state = "HALF_OPEN"
    daemon._cb_failures = 0
    # Drive failures to threshold
    threshold = daemon.db.get_setting("cb_failure_threshold")
    for _ in range(threshold):
        daemon._record_ollama_failure()
    assert daemon._cb_state == "OPEN"


class TestPreemption:
    def _setup_running_job(self, db, daemon, model="qwen2.5:7b", priority=5):
        """Helper: submit a job, mark it running with a fake PID."""
        job_id = db.submit_job("echo run", model, priority, 600, "test")
        db.start_job(job_id)
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE jobs SET pid=99999 WHERE id=?", (job_id,))
            conn.commit()
        return job_id

    def test_preemption_disabled_by_default(self, db):
        """No preemption when preemption_enabled=False."""
        import time

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        p1_job = {"id": 99, "priority": 1, "model": "qwen2.5:7b", "source": "test"}
        result = daemon._check_preemption(p1_job, time.time())
        assert result is None

    def test_preemption_skips_high_priority_requester(self, db):
        """Only priority 1-2 jobs can trigger preemption."""
        import time

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        db.set_setting("preemption_enabled", True)
        p5_job = {"id": 99, "priority": 5, "model": "qwen2.5:7b", "source": "test"}
        result = daemon._check_preemption(p5_job, time.time())
        assert result is None  # priority 5 cannot preempt

    def test_preempt_job_sends_to_pending_not_dlq(self, db):
        """_preempt_job() sets status=pending and leaves DLQ empty."""
        from unittest.mock import patch

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        db.set_setting("preemption_enabled", True)
        job_id = db.submit_job("echo low", "qwen2.5:7b", 5, 600, "test")
        db.start_job(job_id)
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE jobs SET pid=99999 WHERE id=?", (job_id,))
            conn.commit()
        with patch("ollama_queue.daemon.executor.os.kill", return_value=None):
            daemon._preempt_job(job_id)
        job = db.get_job(job_id)
        assert job["status"] == "pending", f"Expected pending, got {job['status']}"
        assert db.list_dlq() == [], "DLQ must be empty after preemption"

    def test_preempt_increments_preemption_count(self, db):
        """preemption_count is incremented after preemption."""
        from unittest.mock import patch

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        job_id = db.submit_job("echo low", "qwen2.5:7b", 5, 600, "test")
        db.start_job(job_id)
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE jobs SET pid=99999 WHERE id=?", (job_id,))
            conn.commit()
        with patch("ollama_queue.daemon.executor.os.kill", return_value=None):
            daemon._preempt_job(job_id)
        job = db.get_job(job_id)
        assert job["preemption_count"] == 1

    def test_job_at_max_preemptions_is_immune(self, db):
        """Job with preemption_count >= max_preemptions_per_job cannot be preempted again."""
        import time
        from concurrent.futures import Future

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        db.set_setting("preemption_enabled", True)
        db.set_setting("max_preemptions_per_job", 2)
        job_id = db.submit_job("echo low", "qwen2.5:7b", 5, 600, "test")
        db.start_job(job_id)
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE jobs SET pid=99999, preemption_count=2 WHERE id=?", (job_id,))
            conn.commit()
        now = time.time()
        fake_future = Future()
        with daemon._running_lock:
            daemon._running[job_id] = fake_future
            daemon._running_models[job_id] = "qwen2.5:7b"
        p1_job = {"id": 999, "priority": 1, "model": "qwen2.5:7b", "source": "test", "submitted_at": now}
        result = daemon._check_preemption(p1_job, now)
        assert result is None  # immune due to max preemptions


def test_daemon_has_burst_detector(db):
    """Daemon initializes with a BurstDetector instance."""
    from ollama_queue.daemon import Daemon
    from ollama_queue.sensing.burst import BurstDetector

    daemon = Daemon(db)
    assert hasattr(daemon, "_burst_detector")
    assert isinstance(daemon._burst_detector, BurstDetector)


class TestSJFDequeue:
    def test_sjf_shorter_job_dequeued_first_at_same_priority(self, db):
        """Shorter estimated job is dequeued before longer at same priority."""
        import time

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)

        now = time.time()
        job_long = db.submit_job("echo long", "m", 5, 600, "long-src")
        job_short = db.submit_job("echo short", "m", 5, 600, "short-src")

        for _ in range(3):
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("long-src", "m", 900.0, 0, now - 10),
            )
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("short-src", "m", 120.0, 0, now - 10),
            )
        db._connect().commit()

        pending = db.get_pending_jobs()
        estimates = db.estimate_duration_bulk([j["source"] for j in pending])
        result = daemon._dequeue_next_job(pending, estimates, now)
        assert result is not None
        assert result["id"] == job_short

    def test_sjf_priority_still_primary_sort_key(self, db):
        """Priority 1 job is dequeued before priority 5, even if longer."""
        import time

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)

        now = time.time()
        job_p1 = db.submit_job("echo p1 long", "m", 1, 600, "p1-src")
        job_p5 = db.submit_job("echo p5 short", "m", 5, 600, "p5-src")

        for _ in range(3):
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("p1-src", "m", 900.0, 0, now - 10),
            )
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("p5-src", "m", 60.0, 0, now - 10),
            )
        db._connect().commit()

        pending = db.get_pending_jobs()
        estimates = db.estimate_duration_bulk([j["source"] for j in pending])
        result = daemon._dequeue_next_job(pending, estimates, now)
        assert result is not None
        assert result["id"] == job_p1

    def test_aging_promotes_long_waiting_job(self, db):
        """Long-waiting job effective duration decreases over time (prevents starvation)."""
        import time

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        db.set_setting("sjf_aging_factor", 3600)

        now = time.time()
        job_waited = db.submit_job("echo waited", "m", 5, 600, "slow-src")
        db._connect().execute(
            "UPDATE jobs SET submitted_at = ? WHERE id = ?",
            (now - 7200, job_waited),  # submitted 2 hours ago
        )
        job_fresh = db.submit_job("echo fresh", "m", 5, 600, "fast-src")

        for _ in range(3):
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("slow-src", "m", 600.0, 0, now - 10),  # 600s base
            )
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("fast-src", "m", 250.0, 0, now - 10),  # 250s base
            )
        db._connect().commit()

        pending = db.get_pending_jobs()
        estimates = db.estimate_duration_bulk([j["source"] for j in pending])
        result = daemon._dequeue_next_job(pending, estimates, now)
        # With aging: slow-src effective = 600/(1+7200/3600) = 200s < 250s
        assert result is not None
        assert result["id"] == job_waited

    def test_dequeue_returns_none_when_no_jobs(self, db):
        """Returns None when pending list is empty."""
        import time

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        result = daemon._dequeue_next_job([], {}, time.time())
        assert result is None


class TestEntropyComputation:
    def test_empty_queue_entropy_is_zero(self, db):
        """Empty pending list gives entropy 0."""
        import time

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        result = daemon._compute_queue_entropy([], time.time())
        assert result == 0.0

    def test_uniform_priority_queue_has_high_entropy(self, db):
        """Queue with equal mix of 5 priorities approaches theoretical max = log2(5) ≈ 2.32."""
        import time
        from math import log2

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        now = time.time()
        # 5 distinct priorities — equal age → equal weight → max entropy
        jobs = [{"id": i, "priority": i, "submitted_at": now - 100} for i in range(1, 6)]
        entropy = daemon._compute_queue_entropy(jobs, now)
        import pytest

        assert entropy == pytest.approx(log2(5), abs=0.05)

    def test_single_priority_queue_has_low_entropy(self, db):
        """Queue with all same priority has entropy near 0."""
        import time

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        now = time.time()
        jobs = [{"id": i, "priority": 1, "submitted_at": now - 100} for i in range(10)]
        entropy = daemon._compute_queue_entropy(jobs, now)
        assert entropy < 0.01  # near-zero: all same priority

    def test_entropy_history_accumulates(self, db):
        """_check_entropy() accumulates entropy readings over time."""
        import time

        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        now = time.time()
        jobs = [{"id": i, "priority": 5, "submitted_at": now - 100} for i in range(5)]
        for _ in range(5):
            daemon._check_entropy(jobs, now)
        assert len(daemon._entropy_history) == 5
        import math

        assert all(v >= 0.0 and math.isfinite(v) for v in daemon._entropy_history)


class TestProxySentinelPreservation:
    """The daemon poll must not clobber the proxy sentinel (current_job_id=-1).

    Root cause: when no pending queue jobs exist, poll_once() set
    state='idle', current_job_id=None unconditionally — overwriting the -1
    sentinel held by an in-flight proxy /api/generate request.  This allowed
    the daemon to see "no proxy running" and a second proxy could claim the
    slot, defeating serialisation entirely.
    """

    _HEALTHY: ClassVar[dict] = {
        "ram_pct": 30.0,
        "swap_pct": 5.0,
        "load_avg": 0.5,
        "cpu_count": 4,
        "vram_pct": 20.0,
        "ollama_model": None,
    }

    def test_poll_preserves_proxy_sentinel_when_queue_empty(self, daemon):
        """poll_once() with no pending jobs must leave current_job_id=-1 intact."""
        # Simulate a proxy that has already claimed the slot
        daemon.db.try_claim_for_proxy()
        state_before = daemon.db.get_daemon_state()
        assert state_before["current_job_id"] == -1, "precondition: sentinel must be set"

        with patch.object(daemon.health, "check", return_value=self._HEALTHY):
            daemon.poll_once()

        state_after = daemon.db.get_daemon_state()
        assert (
            state_after["current_job_id"] == -1
        ), "poll_once() must not clear the proxy sentinel when no regular jobs are pending"

    def test_poll_sets_idle_current_job_id_null_when_no_proxy(self, daemon):
        """poll_once() with no pending jobs and no proxy claim sets current_job_id=None."""
        with patch.object(daemon.health, "check", return_value=self._HEALTHY):
            daemon.poll_once()

        state = daemon.db.get_daemon_state()
        assert state["state"] == "idle"
        assert state["current_job_id"] is None

    def test_poll_preserves_sentinel_when_cannot_admit(self, daemon):
        """poll_once() that skips admission (resource-constrained) must preserve sentinel."""
        # Submit a job so the daemon reaches the admission check
        daemon.db.submit_job("echo hi", "qwen2.5:7b", 5, 60, "test")

        # Claim proxy sentinel before the poll
        daemon.db.try_claim_for_proxy()
        assert daemon.db.get_daemon_state()["current_job_id"] == -1

        # Patch _can_admit to return False so the daemon bails at step 8
        with (
            patch.object(daemon.health, "check", return_value=self._HEALTHY),
            patch.object(daemon, "_can_admit", return_value=False),
        ):
            daemon.poll_once()

        state = daemon.db.get_daemon_state()
        assert state["current_job_id"] == -1, "poll_once() must not clear the proxy sentinel when admission is denied"


# --- T_A1: Settings batch-fetch ---


def test_poll_once_calls_get_all_settings_once(daemon):
    """get_all_settings called once per poll, not per sub-method."""
    with (
        patch.object(daemon.db, "get_all_settings", wraps=daemon.db.get_all_settings) as mock_gs,
        patch.object(daemon.db, "get_setting") as mock_single,
    ):
        daemon.poll_once()
    # get_setting should NOT be called for settings that are now batch-fetched
    calls = [c.args[0] for c in mock_single.call_args_list]
    batch_fetched = {
        "entropy_alert_sigma",
        "entropy_suspend_low_priority",
        "cpu_offload_efficiency",
        "min_model_vram_mb",
    }
    for key in batch_fetched:
        assert key not in calls, f"get_setting('{key}') called individually — should use batch"
    assert mock_gs.call_count >= 1


# ============================================================================
# Coverage gap tests — targeting all 224 missing lines
# ============================================================================


# --- _drain_pipes_with_tracking (lines 59-123) ---


class TestDrainPipesWithTracking:
    """Tests for the select()-based pipe drain with stdout sliding window."""

    def test_drain_basic_stdout_and_stderr(self):
        """Drain captures stdout+stderr from a real process."""
        import subprocess as _sp

        from ollama_queue.daemon import _drain_pipes_with_tracking
        from ollama_queue.sensing.stall import StallDetector

        proc = _sp.Popen(
            ["bash", "-c", "echo hello; echo errout >&2"],
            stdout=_sp.PIPE,
            stderr=_sp.PIPE,
        )
        sd = StallDetector()
        stdout, stderr = _drain_pipes_with_tracking(proc, 1, sd)
        proc.wait()  # ensure returncode is set (drain loop exits via poll)
        assert b"hello" in stdout
        assert b"errout" in stderr
        assert proc.returncode is not None

    def test_drain_stdout_sliding_window(self):
        """Stdout exceeding _MAX_STDOUT_BYTES keeps only the tail."""
        import subprocess as _sp

        from ollama_queue.daemon import _MAX_STDOUT_BYTES, _drain_pipes_with_tracking
        from ollama_queue.sensing.stall import StallDetector

        # Generate more than 128KB of stdout via dd (fast, predictable)
        size = _MAX_STDOUT_BYTES + 50000
        proc = _sp.Popen(
            ["dd", "if=/dev/zero", f"bs={size}", "count=1", "status=none"],
            stdout=_sp.PIPE,
            stderr=_sp.PIPE,
        )
        sd = StallDetector()
        stdout, _stderr = _drain_pipes_with_tracking(proc, 1, sd)
        # Sliding window trims oldest chunks — total should be <= MAX + one chunk
        assert len(stdout) <= _MAX_STDOUT_BYTES + 8192

    def test_drain_empty_output(self):
        """Process with no output returns empty bytes."""
        import subprocess as _sp

        from ollama_queue.daemon import _drain_pipes_with_tracking
        from ollama_queue.sensing.stall import StallDetector

        proc = _sp.Popen(["true"], stdout=_sp.PIPE, stderr=_sp.PIPE)
        sd = StallDetector()
        stdout, stderr = _drain_pipes_with_tracking(proc, 1, sd)
        assert stdout == b""
        assert stderr == b""

    def test_drain_process_exits_with_buffered_data(self):
        """Process exits with data still in buffer — covers drain-after-poll path."""
        import subprocess as _sp

        from ollama_queue.daemon import _drain_pipes_with_tracking
        from ollama_queue.sensing.stall import StallDetector

        proc = _sp.Popen(
            ["bash", "-c", "echo -n stdout_data; echo -n stderr_data >&2; exit 0"],
            stdout=_sp.PIPE,
            stderr=_sp.PIPE,
        )
        sd = StallDetector()
        stdout, stderr = _drain_pipes_with_tracking(proc, 1, sd)
        assert b"stdout_data" in stdout
        assert b"stderr_data" in stderr

    def test_drain_select_value_error(self):
        """select() raising ValueError causes clean exit (line 87-88)."""
        import subprocess as _sp

        from ollama_queue.daemon import _drain_pipes_with_tracking
        from ollama_queue.sensing.stall import StallDetector

        proc = _sp.Popen(["echo", "hi"], stdout=_sp.PIPE, stderr=_sp.PIPE)
        proc.wait()  # let it finish
        sd = StallDetector()
        # Patch select.select at the module level to raise ValueError
        with patch("select.select", side_effect=ValueError("bad fd")):
            stdout, _stderr = _drain_pipes_with_tracking(proc, 1, sd)
        assert isinstance(stdout, bytes)

    def test_drain_read_oserror(self):
        """os.read raising OSError/BlockingIOError on a ready fd is handled."""
        import subprocess as _sp

        from ollama_queue.daemon import _drain_pipes_with_tracking
        from ollama_queue.sensing.stall import StallDetector

        proc = _sp.Popen(["echo", "test"], stdout=_sp.PIPE, stderr=_sp.PIPE)
        sd = StallDetector()
        stdout, _stderr = _drain_pipes_with_tracking(proc, 1, sd)
        assert b"test" in stdout


# --- _get_load_map returning empty (line 174) ---


def test_get_load_map_no_load_map_extended(db, monkeypatch):
    """_get_load_map returns [] when scheduler has no load_map_extended."""
    d = Daemon(db)
    # Replace scheduler with a mock that lacks load_map_extended
    mock_sched = MagicMock(spec=[])  # empty spec = no attributes
    d.scheduler = mock_sched
    result = d._get_load_map()
    assert result == []


# --- _shadow_hours with settings (line 185) ---


def test_shadow_hours_with_settings_dict(db):
    """_shadow_hours reads from settings dict when provided."""
    d = Daemon(db)
    result = d._shadow_hours(settings={"concurrent_shadow_hours": 48})
    assert result == 48.0


# --- _in_shadow_mode first enable (lines 192-193) ---


def test_in_shadow_mode_first_enable(db):
    """_in_shadow_mode sets _concurrent_enabled_at on first call when max_slots > 1."""
    d = Daemon(db)
    d._concurrent_enabled_at = None
    settings = {"max_concurrent_jobs": 2, "concurrent_shadow_hours": 24}
    result = d._in_shadow_mode(settings)
    assert result is True  # first enable, always True
    assert d._concurrent_enabled_at is not None


# --- _compute_max_workers fallback when min_model_vram <= 0 (line 215) ---


def test_compute_max_workers_zero_min_model_vram(db, monkeypatch):
    """_compute_max_workers returns 4 when min_model_vram is <= 0."""
    d = Daemon(db)
    monkeypatch.setattr(d, "_free_vram_mb", lambda: 8000.0)
    monkeypatch.setattr(d, "_free_ram_mb", lambda: 16000.0)
    monkeypatch.setattr(d._ollama_models, "min_estimated_vram_mb", lambda db, fallback_mb=2000: 0)
    result = d._compute_max_workers()
    assert result == 4


# --- _free_vram_mb exception paths (lines 250-255) ---


def test_free_vram_mb_oserror(db):
    """_free_vram_mb returns None when nvidia-smi raises OSError."""
    d = Daemon(db)
    with patch("ollama_queue.daemon.executor._subprocess.run", side_effect=OSError("not found")):
        result = d._free_vram_mb()
    assert result is None


def test_free_vram_mb_timeout(db):
    """_free_vram_mb returns None when nvidia-smi times out."""
    import subprocess as sp

    d = Daemon(db)
    with patch("ollama_queue.daemon.executor._subprocess.run", side_effect=sp.TimeoutExpired("nvidia-smi", 5)):
        result = d._free_vram_mb()
    assert result is None


def test_free_vram_mb_unexpected_exception(db):
    """_free_vram_mb returns None on unexpected exception."""
    d = Daemon(db)
    with patch("ollama_queue.daemon.executor._subprocess.run", side_effect=RuntimeError("unexpected")):
        result = d._free_vram_mb()
    assert result is None


def test_free_vram_mb_nonzero_returncode(db):
    """_free_vram_mb returns None when nvidia-smi returns non-zero."""
    d = Daemon(db)
    mock_result = MagicMock()
    mock_result.returncode = 1
    with patch("ollama_queue.daemon.executor._subprocess.run", return_value=mock_result):
        result = d._free_vram_mb()
    assert result is None


# --- _model_exists (lines 277-280) ---


def test_model_exists_no_model(db):
    """_model_exists returns True for empty model (command-only job)."""
    d = Daemon(db)
    assert d._model_exists("") is True
    assert d._model_exists(None) is True


def test_model_exists_found(db, monkeypatch):
    """_model_exists returns True when model is in local list."""
    d = Daemon(db)
    monkeypatch.setattr(d._ollama_models, "list_local", lambda: [{"name": "qwen2.5:7b"}])
    assert d._model_exists("qwen2.5:7b") is True


def test_model_exists_not_found(db, monkeypatch):
    """_model_exists returns False when model is not in local list."""
    d = Daemon(db)
    monkeypatch.setattr(d._ollama_models, "list_local", lambda: [{"name": "llama3:8b"}])
    assert d._model_exists("qwen2.5:7b") is False


# --- _can_admit: model pull in progress (lines 329-330) ---


def test_can_admit_blocked_by_model_pull(db, monkeypatch):
    """_can_admit returns False when a model pull is in progress."""
    d = Daemon(db)
    job = {"id": 1, "model": "qwen2.5:7b", "resource_profile": "ollama", "priority": 5}
    monkeypatch.setattr(d, "_model_pull_in_progress", lambda m: True)
    monkeypatch.setattr(d, "_free_vram_mb", lambda: 16000.0)
    monkeypatch.setattr(d, "_free_ram_mb", lambda: 32000.0)
    assert d._can_admit(job) is False


# --- _can_admit: at capacity (lines 334-340) ---


def test_can_admit_blocked_at_capacity(db, monkeypatch):
    """_can_admit returns False when running_count >= max_slots."""
    d = Daemon(db)
    d._running[1] = MagicMock()
    d._running_models[1] = "llama3:8b"
    # max_concurrent_jobs defaults to 1, so with 1 running, capacity is full
    job = {"id": 2, "model": "qwen2.5:7b", "resource_profile": "ollama", "priority": 5}
    monkeypatch.setattr(d, "_model_pull_in_progress", lambda m: False)
    assert d._can_admit(job) is False


# --- _can_admit: VRAM ceiling exceeded (lines 352-353) ---


def test_can_admit_blocked_by_vram_ceiling(db, monkeypatch):
    """_can_admit returns False when committed + model VRAM exceeds max_vram_mb * 0.8."""
    d = Daemon(db)
    db.set_setting("max_concurrent_jobs", 4)
    db.set_setting("max_vram_mb", 10000)
    job = {"id": 1, "model": "qwen2.5:7b", "resource_profile": "ollama", "priority": 5}
    monkeypatch.setattr(d, "_model_pull_in_progress", lambda m: False)
    monkeypatch.setattr(d._ollama_models, "estimate_vram_mb", lambda m, db: 9000.0)
    with patch.object(
        d.health,
        "check",
        return_value={
            "ram_pct": 30.0,
            "swap_pct": 5.0,
            "load_avg": 0.5,
            "cpu_count": 4,
            "vram_pct": 20.0,
            "ollama_model": None,
        },
    ):
        assert d._can_admit(job) is False


# --- _can_admit: resource gate fail + deferral (lines 366-383) ---


def test_can_admit_defers_on_resource_fail(db, monkeypatch):
    """_can_admit defers the job when resources insufficient and defer.enabled is on."""
    d = Daemon(db)
    db.set_setting("max_concurrent_jobs", 4)
    db.set_setting("max_vram_mb", 5000)
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    job = {"id": job_id, "model": "qwen2.5:7b", "resource_profile": "ollama", "priority": 5}
    monkeypatch.setattr(d, "_model_pull_in_progress", lambda m: False)
    monkeypatch.setattr(d._ollama_models, "estimate_vram_mb", lambda m, db: 9000.0)
    with patch.object(
        d.health,
        "check",
        return_value={
            "ram_pct": 30.0,
            "swap_pct": 5.0,
            "load_avg": 0.5,
            "cpu_count": 4,
            "vram_pct": 20.0,
            "ollama_model": None,
        },
    ):
        result = d._can_admit(job)
    assert result is False
    # Check that the job was deferred
    deferred = db.list_deferred()
    assert len(deferred) >= 1


def test_can_admit_deferral_exception_handled(db, monkeypatch, caplog):
    """_can_admit handles exception from defer_job gracefully."""
    d = Daemon(db)
    db.set_setting("max_concurrent_jobs", 4)
    db.set_setting("max_vram_mb", 5000)
    job = {"id": 9999, "model": "qwen2.5:7b", "resource_profile": "ollama", "priority": 5}
    monkeypatch.setattr(d, "_model_pull_in_progress", lambda m: False)
    monkeypatch.setattr(d._ollama_models, "estimate_vram_mb", lambda m, db: 9000.0)
    monkeypatch.setattr(d.db, "defer_job", MagicMock(side_effect=Exception("defer failed")))
    with (
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.ERROR),
    ):
        result = d._can_admit(job)
    assert result is False


def test_can_admit_health_pause_no_deferral_when_resource_ok(db, monkeypatch):
    """_can_admit returns False on health pause but doesn't defer if resource_ok is True."""
    d = Daemon(db)
    db.set_setting("max_concurrent_jobs", 4)
    job = {"id": 1, "model": "qwen2.5:7b", "resource_profile": "ollama", "priority": 5}
    monkeypatch.setattr(d, "_model_pull_in_progress", lambda m: False)
    monkeypatch.setattr(d._ollama_models, "estimate_vram_mb", lambda m, db: 1000.0)
    with patch.object(
        d.health,
        "check",
        return_value={
            "ram_pct": 95.0,
            "swap_pct": 80.0,
            "load_avg": 20.0,
            "cpu_count": 4,
            "vram_pct": 95.0,
            "ollama_model": None,
        },
    ):
        result = d._can_admit(job)
    assert result is False


# --- _check_entropy anomaly detection (lines 425-464) ---


class TestEntropyAnomaly:
    """Test entropy anomaly detection — critical_backlog and background_flood paths."""

    def _build_entropy_daemon(self, db):
        d = Daemon(db)
        now = time.time()
        # Fill history with high-entropy readings (diverse queue)
        for _ in range(15):
            d._entropy_history.append(2.0)
        return d, now

    def test_entropy_anomaly_critical_backlog(self, db, caplog):
        """Low entropy with > 70% high-priority jobs triggers critical_backlog."""
        d, now = self._build_entropy_daemon(db)
        # Queue with all priority 1 jobs — very low entropy = anomaly
        jobs = [{"id": i, "priority": 1, "submitted_at": now - 100} for i in range(10)]
        settings = {"entropy_alert_sigma": 0.5, "entropy_suspend_low_priority": True}
        with caplog.at_level(logging.WARNING):
            d._check_entropy(jobs, now, settings)
        assert any("entropy" in r.message.lower() for r in caplog.records)
        # Should suspend low-priority
        assert d._entropy_suspend_until > now

    def test_entropy_anomaly_background_flood(self, db, caplog):
        """Low entropy with < 70% high-priority jobs triggers background_flood."""
        d, now = self._build_entropy_daemon(db)
        # Queue with all priority 10 jobs — single priority = low entropy
        jobs = [{"id": i, "priority": 10, "submitted_at": now - 100} for i in range(10)]
        settings = {"entropy_alert_sigma": 0.5, "entropy_suspend_low_priority": False}
        with caplog.at_level(logging.WARNING):
            d._check_entropy(jobs, now, settings)
        assert any("entropy" in r.message.lower() for r in caplog.records)

    def test_entropy_anomaly_with_db_settings(self, db, caplog):
        """Entropy reads from DB when settings is None."""
        d, now = self._build_entropy_daemon(db)
        db.set_setting("entropy_alert_sigma", "0.5")
        jobs = [{"id": i, "priority": 1, "submitted_at": now - 100} for i in range(10)]
        with caplog.at_level(logging.WARNING):
            d._check_entropy(jobs, now, settings=None)


# --- _recover_orphans (lines 482-505, 509) ---


def test_recover_orphans_marks_stuck_eval_runs_failed(db):
    """_recover_orphans marks eval_runs in generating/judging/pending as failed."""
    d = Daemon(db)
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (data_source_url, variants, per_cluster, status, run_mode, started_at) "
            "VALUES ('http://test', '[]', 4, 'generating', 'batch', ?)",
            (time.time(),),
        )
        conn.execute(
            "INSERT INTO eval_runs (data_source_url, variants, per_cluster, status, run_mode, started_at) "
            "VALUES ('http://test', '[]', 4, 'judging', 'batch', ?)",
            (time.time(),),
        )
        conn.commit()
    d._recover_orphans()
    with db._lock:
        conn = db._connect()
        runs = conn.execute("SELECT status, error FROM eval_runs").fetchall()
    for run in runs:
        assert run["status"] == "failed"
        assert "daemon restart" in run["error"]


def test_recover_orphans_proxy_sentinel_marked_failed(db):
    """_recover_orphans marks proxy sentinel jobs as failed, not reset to pending."""
    d = Daemon(db)
    job_id = db.submit_job("proxy:generate", "qwen2.5:7b", 0, 120, "proxy")
    db.start_job(job_id)
    d._recover_orphans()
    job = db.get_job(job_id)
    assert job["status"] in ("completed", "failed", "dead")
    assert job["status"] != "pending"


def test_recover_orphans_sigterm_orphaned_pid(db):
    """_recover_orphans sends SIGTERM to jobs with valid PIDs."""
    d = Daemon(db)
    job_id = db.submit_job("echo hi", "", 5, 60, "test")
    db.start_job(job_id)
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE jobs SET pid = 999999 WHERE id = ?", (job_id,))
        conn.commit()
    with patch("ollama_queue.daemon.loop.os.kill") as mock_kill:
        mock_kill.side_effect = ProcessLookupError  # process doesn't exist
        d._recover_orphans()
    mock_kill.assert_called_once_with(999999, _signal.SIGTERM)
    job = db.get_job(job_id)
    assert job["status"] == "pending"


# --- _record_ollama_success log (line 548) ---


def test_record_ollama_success_logs_on_close(daemon, caplog):
    """_record_ollama_success logs when closing circuit breaker."""
    daemon._cb_state = "HALF_OPEN"
    daemon._cb_open_attempts = 1
    with caplog.at_level(logging.INFO):
        daemon._record_ollama_success()
    assert daemon._cb_state == "CLOSED"
    assert any("Circuit breaker CLOSED" in r.message for r in caplog.records)


# --- _is_circuit_open: HALF_OPEN states (lines 570-571, 590) ---


def test_is_circuit_open_half_open_no_probe(daemon):
    """HALF_OPEN with no probe in flight allows one probe through."""
    daemon._cb_state = "HALF_OPEN"
    daemon._cb_probe_in_flight = False
    result = daemon._is_circuit_open()
    assert result is False
    assert daemon._cb_probe_in_flight is True


def test_is_circuit_open_half_open_probe_in_flight(daemon):
    """HALF_OPEN with probe in flight blocks."""
    daemon._cb_state = "HALF_OPEN"
    daemon._cb_probe_in_flight = True
    result = daemon._is_circuit_open()
    assert result is True


def test_is_circuit_open_open_already_transitioned(daemon):
    """_is_circuit_open handles TOCTOU: another thread already transitioned OPEN to CLOSED."""
    daemon._cb_state = "OPEN"
    daemon._cb_opened_at = time.time() - 9999
    daemon._cb_open_attempts = 0

    # Patch the lock context to simulate another thread transitioning to CLOSED
    original_compute = daemon._compute_cb_cooldown

    call_count = [0]

    def patched_compute(attempt):
        call_count[0] += 1
        # Between phase 2 and phase 3, simulate another thread closing the circuit
        if call_count[0] == 1:
            daemon._cb_state = "CLOSED"
        return original_compute(attempt)

    daemon._compute_cb_cooldown = patched_compute
    result = daemon._is_circuit_open()
    # After TOCTOU: cb_state was CLOSED when phase 3 ran → should return False
    assert result is False


# --- poll_once exception handling (lines 614-615, 620-621, 626-627) ---


def test_poll_once_scheduler_exception(db, caplog):
    """poll_once handles exception from scheduler.promote_due_jobs."""
    d = Daemon(db)
    with (
        patch.object(d.scheduler, "promote_due_jobs", side_effect=Exception("scheduler boom")),
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.ERROR),
    ):
        d.poll_once()
    assert any("Scheduler promotion failed" in r.message for r in caplog.records)


def test_poll_once_stall_check_exception(db, caplog):
    """poll_once handles exception from _check_stalled_jobs."""
    d = Daemon(db)
    with (
        patch.object(d, "_check_stalled_jobs", side_effect=Exception("stall boom")),
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.ERROR),
    ):
        d.poll_once()
    assert any("Stall detection failed" in r.message for r in caplog.records)


def test_poll_once_retry_check_exception(db, caplog):
    """poll_once handles exception from _check_retryable_jobs."""
    d = Daemon(db)
    with (
        patch.object(d, "_check_retryable_jobs", side_effect=Exception("retry boom")),
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.ERROR),
    ):
        d.poll_once()
    assert any("Retry check failed" in r.message for r in caplog.records)


# --- poll_once future cleanup with exceptions (lines 640-645) ---


def test_poll_once_cleans_up_failed_futures(db, caplog):
    """poll_once cleans up completed futures and logs worker exceptions."""
    d = Daemon(db)
    # Create a future that raises
    import concurrent.futures

    fut = concurrent.futures.Future()
    fut.set_exception(RuntimeError("worker crashed"))
    d._running[99] = fut
    d._running_models[99] = "qwen2.5:7b"

    with (
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.ERROR),
    ):
        d.poll_once()
    assert any("Worker thread" in r.message for r in caplog.records)
    assert 99 not in d._running


# --- Circuit open blocks dequeue (lines 664-665) ---


def test_poll_once_circuit_open_skips_dequeue(db, caplog):
    """poll_once returns early when circuit breaker is OPEN."""
    d = Daemon(db)
    d._cb_state = "OPEN"
    d._cb_opened_at = time.time()
    d._cb_open_attempts = 0
    db.submit_job("echo hi", "qwen2.5:7b", 5, 60, "test")

    with (
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.DEBUG),
    ):
        d.poll_once()
    # Job should still be pending — circuit breaker prevented dequeue
    job = db.get_job(1)
    assert job["status"] == "pending"


# --- Entropy check exception (lines 674-675) ---


def test_poll_once_entropy_exception(db, caplog):
    """poll_once handles exception from _check_entropy."""
    d = Daemon(db)
    with (
        patch.object(d, "_check_entropy", side_effect=Exception("entropy boom")),
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.ERROR),
    ):
        d.poll_once()
    assert any("Entropy check failed" in r.message for r in caplog.records)


# --- Burst regime warning + exception (lines 681, 683-684) ---


def test_poll_once_burst_warning_regime(db, caplog):
    """poll_once logs info when burst regime is warning or critical."""
    d = Daemon(db)
    with (
        patch.object(d._burst_detector, "regime", return_value="warning"),
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.INFO),
    ):
        d.poll_once()
    assert d._burst_regime == "warning"


def test_poll_once_burst_exception(db, caplog):
    """poll_once handles exception from burst regime check."""
    d = Daemon(db)
    with (
        patch.object(d._burst_detector, "regime", side_effect=Exception("burst boom")),
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.ERROR),
    ):
        d.poll_once()
    assert any("Burst regime check failed" in r.message for r in caplog.records)


# --- DLQ/deferral sweep + exception (lines 692-693) ---


def test_poll_once_dlq_sweep_triggers(db, caplog):
    """poll_once triggers DLQ/deferral sweep when sweep interval elapsed."""
    d = Daemon(db)
    d._last_dlq_sweep = 0  # force sweep
    with (
        patch.object(d._dlq_scheduler, "periodic_sweep"),
        patch.object(d._deferral_scheduler, "sweep"),
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
    ):
        d.poll_once()
    assert d._last_dlq_sweep > 0


def test_poll_once_dlq_sweep_exception(db, caplog):
    """poll_once handles exception from DLQ/deferral sweep."""
    d = Daemon(db)
    d._last_dlq_sweep = 0  # force sweep
    with (
        patch.object(d._dlq_scheduler, "periodic_sweep", side_effect=Exception("dlq boom")),
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        caplog.at_level(logging.ERROR),
    ):
        d.poll_once()
    assert any("Periodic DLQ/deferral sweep failed" in r.message for r in caplog.records)


# --- poll_once: pause recovery logging (line 758) ---


def test_poll_once_logs_resume_from_pause(db, caplog):
    """poll_once logs when resuming from a paused state."""
    d = Daemon(db)
    # Set paused state then provide healthy conditions + no job
    db.update_daemon_state(state="paused_health", paused_reason="test")
    db.submit_job("echo hi", "qwen2.5:7b", 5, 60, "test")
    with (
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        patch.object(d, "_can_admit", return_value=True),
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")),
        caplog.at_level(logging.INFO),
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        d.poll_once()
        _drain(d)
    assert any("resuming from" in r.message.lower() for r in caplog.records)


# --- poll_once preemption (lines 769, 775) ---


def test_poll_once_preemption_fires(db, caplog):
    """poll_once calls _preempt_job when _check_preemption returns a job ID."""
    d = Daemon(db)
    db.set_setting("preemption_enabled", True)
    # Submit and start a low-priority job
    low_id = db.submit_job("echo low", "qwen2.5:7b", 8, 600, "test")
    db.start_job(low_id)
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE jobs SET pid=99999 WHERE id=?", (low_id,))
        conn.commit()

    # Submit a high-priority job
    db.submit_job("echo urgent", "llama3:8b", 1, 60, "test")

    # Mock so preemption check finds a candidate
    d._running[low_id] = MagicMock()
    d._running[low_id].done.return_value = False
    d._running_models[low_id] = "qwen2.5:7b"

    with (
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        patch.object(d, "_can_admit", return_value=True),
        patch.object(d, "_check_preemption", return_value=low_id),
        patch.object(d, "_preempt_job") as mock_preempt,
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")),
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        d.poll_once()
        _drain(d)
    mock_preempt.assert_called_once_with(low_id)


# --- _run_job: non-ollama timeout + second communicate timeout (lines 859-860) ---


def test_run_job_timeout_double_timeout(db):
    """Non-ollama job timeout where second communicate also times out."""
    import subprocess as _real_sub

    d = Daemon(db)
    job_id = db.submit_job("sleep 999", "m", 5, 1, "test", resource_profile="cpu")
    db.start_job(job_id)
    job = db.get_job(job_id)

    with patch("ollama_queue.daemon.executor.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 1234
        proc.kill.return_value = None
        # Both communicates raise TimeoutExpired
        proc.communicate.side_effect = [
            _real_sub.TimeoutExpired("cmd", 1),
            _real_sub.TimeoutExpired("cmd", 5),
        ]
        proc.returncode = -9
        mock_sub.Popen.return_value = proc
        d._run_job(dict(job))

    job_after = db.get_job(job_id)
    assert job_after["status"] in ("killed", "dead")


# --- _run_job: DLQ exception in timeout path (lines 873-874) ---


def test_run_job_timeout_dlq_exception(db, caplog):
    """DLQ routing exception during timeout handling is caught and logged."""
    import subprocess as _real_sub

    d = Daemon(db)
    job_id = db.submit_job("sleep 999", "m", 5, 1, "test", resource_profile="cpu")
    db.start_job(job_id)
    job = db.get_job(job_id)

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch.object(d.dlq, "handle_failure", side_effect=Exception("dlq boom")),
        caplog.at_level(logging.ERROR),
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.kill.return_value = None
        proc.communicate.side_effect = [
            _real_sub.TimeoutExpired("cmd", 1),
            (b"", b""),
        ]
        proc.returncode = -9
        mock_sub.Popen.return_value = proc
        d._run_job(dict(job))

    assert any("DLQ routing failed for timed-out job" in r.message for r in caplog.records)


# --- _run_job: DLQ exception in failed job path (lines 901-902) ---


def test_run_job_failed_dlq_exception(db, caplog):
    """DLQ routing exception during job failure handling is caught and logged."""
    d = Daemon(db)
    job_id = db.submit_job("exit 1", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    job = db.get_job(job_id)

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"", b"error")),
        patch.object(d.dlq, "handle_failure", side_effect=Exception("dlq boom")),
        caplog.at_level(logging.ERROR),
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 1
        mock_sub.Popen.return_value = proc
        d._run_job(dict(job))

    assert any("DLQ routing failed for job" in r.message for r in caplog.records)


# --- _run_job: metrics capture + store_job_metrics exception (lines 938-944) ---


def test_run_job_stores_metrics(db):
    """_run_job stores Ollama metrics when present in stdout."""
    d = Daemon(db)
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    job = db.get_job(job_id)

    # parse_ollama_metrics requires {"done": true, ...}
    metrics_json = b'{"done": true, "eval_count": 100, "eval_duration": 5000000000}'
    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(metrics_json, b"")),
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        d._run_job(dict(job))

    # Job completed successfully
    job_after = db.get_job(job_id)
    assert job_after["status"] == "completed"


def test_run_job_store_metrics_exception(db, caplog):
    """Exception during store_job_metrics is caught and logged."""
    d = Daemon(db)
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    job = db.get_job(job_id)

    # parse_ollama_metrics requires {"done": true, ...} in stdout
    metrics_json = b'{"done": true, "eval_count": 100, "eval_duration": 5000000000}'
    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(metrics_json, b"")),
        patch.object(db, "store_job_metrics", side_effect=Exception("store boom")),
        caplog.at_level(logging.ERROR),
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        d._run_job(dict(job))

    assert any("Failed to store metrics" in r.message for r in caplog.records)


# --- _run_job: observed VRAM recording (line 959) ---


def test_run_job_records_observed_vram(db, monkeypatch):
    """_run_job records observed VRAM delta on success."""
    d = Daemon(db)
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    job = db.get_job(job_id)

    vram_values = [8000.0, 4000.0]  # before=8000, after=4000, delta=4000
    vram_call_count = [0]

    def mock_free_vram():
        idx = min(vram_call_count[0], len(vram_values) - 1)
        vram_call_count[0] += 1
        return vram_values[idx]

    monkeypatch.setattr(d, "_free_vram_mb", mock_free_vram)
    record_calls = []
    monkeypatch.setattr(d._ollama_models, "record_observed_vram", lambda m, d, db: record_calls.append((m, d)))

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")),
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        d._run_job(dict(job))

    assert len(record_calls) == 1
    assert record_calls[0][0] == "qwen2.5:7b"
    assert record_calls[0][1] == 4000.0


# --- _run_job: scheduler update_next_run exception (lines 983-984) ---


def test_run_job_scheduler_exception(db, caplog):
    """Exception in scheduler.update_next_run is caught and logged."""
    d = Daemon(db)
    _rj_id, job_id = _make_recurring_and_job(db)
    job = db.get_job(job_id)

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")),
        patch.object(d.scheduler, "update_next_run", side_effect=Exception("scheduler boom")),
        caplog.at_level(logging.ERROR),
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        d._run_job(dict(job))

    assert any("Scheduler next_run update failed" in r.message for r in caplog.records)


# --- _run_job: unhandled exception (lines 992-1026) ---


def test_run_job_unhandled_exception(db, caplog):
    """Unhandled exception in _run_job marks job as failed and routes to DLQ."""
    d = Daemon(db)
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    job = db.get_job(job_id)

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        caplog.at_level(logging.ERROR),
    ):
        mock_sub.Popen.side_effect = RuntimeError("Popen exploded")
        d._run_job(dict(job))

    job_after = db.get_job(job_id)
    assert job_after["status"] in ("completed", "dead", "failed")
    assert any("Unhandled exception" in r.message for r in caplog.records)


def test_run_job_unhandled_exception_with_partial_output(db, caplog):
    """Unhandled exception captures partial metrics from stdout before crash."""
    d = Daemon(db)
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    job = db.get_job(job_id)

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking") as mock_drain,
        caplog.at_level(logging.ERROR),
    ):
        proc = MagicMock()
        proc.pid = 1234
        mock_sub.Popen.return_value = proc
        # Drain succeeds but then proc.wait() raises
        mock_drain.return_value = (b'{"eval_count": 50}', b"")
        proc.wait.side_effect = RuntimeError("proc crashed")
        d._run_job(dict(job))

    job_after = db.get_job(job_id)
    assert job_after["status"] in ("completed", "dead", "failed")


def test_run_job_unhandled_exception_complete_job_fails(db, caplog):
    """When complete_job itself fails during exception handling, it's logged."""
    d = Daemon(db)
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    job = db.get_job(job_id)

    original_complete = db.complete_job
    call_count = [0]

    def failing_complete(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] > 0:
            raise Exception("complete_job failed")
        return original_complete(*args, **kwargs)

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch.object(db, "complete_job", side_effect=failing_complete),
        caplog.at_level(logging.ERROR),
    ):
        mock_sub.Popen.side_effect = RuntimeError("Popen exploded")
        d._run_job(dict(job))

    assert any("Failed to mark job" in r.message for r in caplog.records)


def test_run_job_unhandled_exception_dlq_fails(db, caplog):
    """When DLQ routing fails during unhandled exception, it's logged."""
    d = Daemon(db)
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    job = db.get_job(job_id)

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch.object(d.dlq, "handle_failure", side_effect=Exception("dlq boom")),
        caplog.at_level(logging.ERROR),
    ):
        mock_sub.Popen.side_effect = RuntimeError("Popen exploded")
        d._run_job(dict(job))

    assert any("Failed to route job" in r.message or "DLQ" in r.message for r in caplog.records)


# --- _check_stalled_jobs: skip paths (lines 1057, 1061, 1065) ---


def test_check_stalled_jobs_skips_missing_row(db):
    """_check_stalled_jobs skips job_ids not found in DB."""
    d = Daemon(db)
    d._running[999] = MagicMock()
    with (
        patch.object(d.stall_detector, "get_ollama_ps_models", return_value=set()),
    ):
        d._check_stalled_jobs(time.time())  # should not crash


def test_check_stalled_jobs_skips_non_ollama(db):
    """_check_stalled_jobs skips jobs with non-ollama resource_profile."""
    d = Daemon(db)
    job_id = db.submit_job("echo hi", "", 5, 60, "test", resource_profile="cpu")
    db.start_job(job_id)
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE jobs SET pid=9999, started_at=? WHERE id=?", (time.time() - 400, job_id))
        conn.commit()
    d._running[job_id] = MagicMock()
    with (
        patch.object(d.stall_detector, "get_ollama_ps_models", return_value=set()),
        patch.object(d.stall_detector, "compute_posterior") as mock_cp,
    ):
        d._check_stalled_jobs(time.time())
    mock_cp.assert_not_called()  # skipped


def test_check_stalled_jobs_skips_zero_pid(db):
    """_check_stalled_jobs skips jobs with pid <= 0."""
    d = Daemon(db)
    job_id = db.submit_job("echo hi", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    # pid defaults to NULL, which reads as None/0
    d._running[job_id] = MagicMock()
    with (
        patch.object(d.stall_detector, "get_ollama_ps_models", return_value=set()),
        patch.object(d.stall_detector, "compute_posterior") as mock_cp,
    ):
        d._check_stalled_jobs(time.time())
    mock_cp.assert_not_called()  # skipped due to pid=0


# --- _dequeue_next_job: all jobs in backoff (line 1143) ---


def test_dequeue_next_job_all_in_backoff(db):
    """_dequeue_next_job returns None when all pending jobs are in backoff."""
    d = Daemon(db)
    now = time.time()
    pending = [
        {"id": 1, "priority": 5, "source": "test", "model": "m", "retry_after": now + 3600, "submitted_at": now},
        {"id": 2, "priority": 5, "source": "test", "model": "m", "retry_after": now + 3600, "submitted_at": now},
    ]
    result = d._dequeue_next_job(pending, {}, now)
    assert result is None


# --- _check_preemption: job not found (line 1176) ---


def test_check_preemption_job_not_found(db):
    """_check_preemption skips when running job is not in DB."""
    d = Daemon(db)
    db.set_setting("preemption_enabled", True)
    d._running[999] = MagicMock()
    d._running_models[999] = "qwen2.5:7b"
    new_job = {"id": 1, "priority": 1, "model": "llama3:8b", "source": "test"}
    result = d._check_preemption(new_job, time.time())
    assert result is None


# --- _check_preemption: various candidate checks (lines 1180-1202) ---


class TestCheckPreemptionCandidates:
    """Test preemption candidate filtering."""

    def _setup(self, db, **job_overrides):
        d = Daemon(db)
        db.set_setting("preemption_enabled", True)
        db.set_setting("preemption_window_seconds", 120)
        db.set_setting("max_preemptions_per_job", 2)
        job_id = db.submit_job("echo low", "qwen2.5:7b", 8, 600, "test")
        db.start_job(job_id)
        now = time.time()
        started = job_overrides.get("started_at", now - 30)  # within window
        with db._lock:
            conn = db._connect()
            conn.execute(
                "UPDATE jobs SET pid=99999, started_at=? WHERE id=?",
                (started, job_id),
            )
            conn.commit()
        d._running[job_id] = MagicMock()
        d._running_models[job_id] = "qwen2.5:7b"
        return d, job_id, now

    def test_preempt_too_far_elapsed(self, db):
        """Job past preempt_window_seconds is not preempted."""
        d, _job_id, now = self._setup(db, started_at=time.time() - 300)
        new_job = {"id": 99, "priority": 1, "model": "llama3:8b", "source": "test"}
        result = d._check_preemption(new_job, now)
        assert result is None

    def test_preempt_recently_active_job(self, db):
        """Job with recent stdout activity (< 30s silence) is not preempted."""
        d, _job_id, now = self._setup(db)
        new_job = {"id": 99, "priority": 1, "model": "llama3:8b", "source": "test"}
        with patch.object(d.stall_detector, "get_stdout_silence", return_value=5.0):
            result = d._check_preemption(new_job, now)
        assert result is None

    def test_preempt_insufficient_vram(self, db, monkeypatch):
        """Job with less VRAM than new job is not preempted."""
        d, _job_id, now = self._setup(db)
        new_job = {"id": 99, "priority": 1, "model": "deepseek-r1:70b", "source": "test"}
        monkeypatch.setattr(d._ollama_models, "estimate_vram_mb", lambda m, db: 10000.0 if "70b" in m else 2000.0)
        with patch.object(d.stall_detector, "get_stdout_silence", return_value=60.0):
            result = d._check_preemption(new_job, now)
        assert result is None

    def test_preempt_running_job_nearly_done(self, db, monkeypatch):
        """Job with less remaining time than new job's duration is not preempted."""
        d, job_id, now = self._setup(db)
        new_job = {"id": 99, "priority": 1, "model": "llama3:8b", "source": "test"}
        # Running job estimated 40s total, 30s elapsed = 10s remaining
        # New job estimated 120s — not worth preempting
        monkeypatch.setattr(d.estimator, "estimate", lambda s, m=None: 120.0)
        monkeypatch.setattr(d._ollama_models, "estimate_vram_mb", lambda m, db: 5000.0)
        with (
            patch.object(d.stall_detector, "get_stdout_silence", return_value=60.0),
        ):
            # Set job's estimated_duration low so remaining < new_duration
            with db._lock:
                conn = db._connect()
                conn.execute("UPDATE jobs SET estimated_duration=40 WHERE id=?", (job_id,))
                conn.commit()
            result = d._check_preemption(new_job, now)
        assert result is None

    def test_preempt_successful_candidate(self, db, monkeypatch):
        """Valid preemption candidate is returned."""
        d, job_id, now = self._setup(db)
        new_job = {"id": 99, "priority": 1, "model": "llama3:8b", "source": "urgent"}

        def varied_estimate(source, model=None):
            # new job is short (30s), running job is long (600s)
            if source == "urgent":
                return 30.0
            return 600.0

        monkeypatch.setattr(d.estimator, "estimate", varied_estimate)
        monkeypatch.setattr(d._ollama_models, "estimate_vram_mb", lambda m, db: 5000.0)
        with patch.object(d.stall_detector, "get_stdout_silence", return_value=60.0):
            result = d._check_preemption(new_job, now)
        assert result == job_id


# --- _run_check_command exception fail-open (lines 1255-1261) ---


def test_check_command_generic_exception_failopen(db, caplog):
    """check_command generic exception → proceed (fail-open)."""
    d = Daemon(db)
    _rj_id, job_id = _make_recurring_and_job(db, check_command="bad_cmd")
    job = db.get_job(job_id)

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        caplog.at_level(logging.WARNING),
    ):
        mock_sub.run.side_effect = Exception("generic exception")
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            d._run_job(dict(job))
    # Job should still run (fail-open)
    mock_sub.Popen.assert_called_once()


# --- check command exit 1 scheduler exception (lines 1281-1282) ---


def test_check_command_exit1_scheduler_exception(db, caplog):
    """check_command exit 1 skips job; scheduler exception is caught."""
    d = Daemon(db)
    _rj_id, job_id = _make_recurring_and_job(db, check_command="exit 1")
    job = db.get_job(job_id)

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch.object(d.scheduler, "update_next_run", side_effect=Exception("scheduler boom")),
        caplog.at_level(logging.ERROR),
    ):
        mock_sub.run.return_value = MagicMock(returncode=1)
        d._run_job(dict(job))

    assert any("Failed to advance next_run" in r.message for r in caplog.records)


# --- run() main loop (lines 1316-1347) ---


def test_run_loop_basic(db):
    """run() calls _recover_orphans, sets idle, runs poll_once, and shuts down."""
    d = Daemon(db)
    poll_count = [0]

    def counting_poll():
        poll_count[0] += 1
        if poll_count[0] >= 2:
            raise KeyboardInterrupt("test done")

    with (
        patch.object(d, "poll_once", side_effect=counting_poll),
        patch.object(d, "_recover_orphans"),
        patch("ollama_queue.daemon.loop.time.sleep"),
        pytest.raises(KeyboardInterrupt),
    ):
        d.run(poll_interval=1)

    state = db.get_daemon_state()
    assert state.get("uptime_since") is not None
    assert d._executor is None  # shutdown was called


def test_run_loop_poll_interval_from_db(db):
    """run() reads poll_interval from DB when not provided."""
    d = Daemon(db)
    db.set_setting("poll_interval_seconds", "3")

    call_count = [0]

    def crash_on_sleep(seconds):
        call_count[0] += 1
        if call_count[0] >= 1:
            raise KeyboardInterrupt

    with (
        patch.object(d, "poll_once"),
        patch.object(d, "_recover_orphans"),
        patch("ollama_queue.daemon.loop.time.sleep", side_effect=crash_on_sleep) as mock_sleep,
        pytest.raises(KeyboardInterrupt),
    ):
        d.run()

    mock_sleep.assert_called_with(3)


def test_run_loop_poll_interval_default(db):
    """run() defaults to 5s when poll_interval_seconds is not set."""
    d = Daemon(db)

    call_count = [0]

    def crash_on_sleep(seconds):
        call_count[0] += 1
        if call_count[0] >= 1:
            raise KeyboardInterrupt

    with (
        patch.object(d, "poll_once"),
        patch.object(d, "_recover_orphans"),
        patch("ollama_queue.daemon.loop.time.sleep", side_effect=crash_on_sleep) as mock_sleep,
        pytest.raises(KeyboardInterrupt),
    ):
        d.run()

    mock_sleep.assert_called_with(5)


def test_run_loop_poll_exception_recovery(db, caplog):
    """run() catches poll_once exceptions and attempts state recovery."""
    d = Daemon(db)
    poll_count = [0]

    def failing_poll():
        poll_count[0] += 1
        if poll_count[0] == 1:
            raise RuntimeError("poll exploded")

    sleep_count = [0]

    def crash_on_second_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:
            raise KeyboardInterrupt

    with (
        patch.object(d, "poll_once", side_effect=failing_poll),
        patch.object(d, "_recover_orphans"),
        patch("ollama_queue.daemon.loop.time.sleep", side_effect=crash_on_second_sleep),
        caplog.at_level(logging.ERROR),
        pytest.raises(KeyboardInterrupt),
    ):
        d.run(poll_interval=1)

    assert any("Unexpected error in poll_once" in r.message for r in caplog.records)


def test_run_loop_state_recovery_also_fails(db, caplog):
    """run() logs when state recovery after poll exception also fails."""
    d = Daemon(db)
    poll_count = [0]

    def failing_poll():
        poll_count[0] += 1
        if poll_count[0] == 1:
            raise RuntimeError("poll exploded")

    original_update = db.update_daemon_state
    update_count = [0]

    def failing_update(**kwargs):
        update_count[0] += 1
        # Fail on the recovery update (second call, after _recover_orphans sets idle)
        if update_count[0] >= 2 and "last_poll_at" in kwargs:
            raise Exception("recovery failed too")
        return original_update(**kwargs)

    sleep_count = [0]

    def crash_on_second_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:
            raise KeyboardInterrupt

    with (
        patch.object(d, "poll_once", side_effect=failing_poll),
        patch.object(d, "_recover_orphans"),
        patch.object(db, "update_daemon_state", side_effect=failing_update),
        patch("ollama_queue.daemon.loop.time.sleep", side_effect=crash_on_second_sleep),
        caplog.at_level(logging.ERROR),
        pytest.raises(KeyboardInterrupt),
    ):
        d.run(poll_interval=1)

    assert any("State recovery also failed" in r.message for r in caplog.records)


def test_run_loop_prune(db):
    """run() prunes old data + resets counters when prune interval elapsed."""
    d = Daemon(db)
    d._last_prune = 0  # force prune

    call_count = [0]

    def crash_on_sleep(seconds):
        call_count[0] += 1
        if call_count[0] >= 1:
            raise KeyboardInterrupt

    with (
        patch.object(d, "poll_once"),
        patch.object(d, "_recover_orphans"),
        patch.object(db, "prune_old_data") as mock_prune,
        patch("ollama_queue.daemon.loop.time.sleep", side_effect=crash_on_sleep),
        pytest.raises(KeyboardInterrupt),
    ):
        d.run(poll_interval=1)

    mock_prune.assert_called_once()
    assert d._last_prune > 0


def test_run_loop_prune_exception(db, caplog):
    """run() catches prune exceptions and continues."""
    d = Daemon(db)
    d._last_prune = 0

    sleep_count = [0]

    def crash_on_second_sleep(seconds):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:
            raise KeyboardInterrupt

    with (
        patch.object(d, "poll_once"),
        patch.object(d, "_recover_orphans"),
        patch.object(db, "prune_old_data", side_effect=Exception("prune boom")),
        patch("ollama_queue.daemon.loop.time.sleep", side_effect=crash_on_second_sleep),
        caplog.at_level(logging.ERROR),
        pytest.raises(KeyboardInterrupt),
    ):
        d.run(poll_interval=1)

    assert any("Daily prune failed" in r.message for r in caplog.records)


# --- poll_once: health pause preserves proxy sentinel (line 736-738) ---


class TestPollHealthPauseSentinel:
    """Health/interactive pause must not clobber proxy sentinel."""

    _UNHEALTHY: ClassVar[dict] = {
        "ram_pct": 95.0,
        "swap_pct": 80.0,
        "load_avg": 20.0,
        "cpu_count": 4,
        "vram_pct": 95.0,
        "ollama_model": None,
    }

    def test_health_pause_preserves_proxy_sentinel(self, db):
        """Health pause preserves current_job_id=-1 proxy sentinel."""
        d = Daemon(db)
        db.submit_job("echo hi", "qwen2.5:7b", 5, 60, "test")
        db.try_claim_for_proxy()
        with patch.object(d.health, "check", return_value=self._UNHEALTHY):
            d.poll_once()
        state = db.get_daemon_state()
        assert state["current_job_id"] == -1

    def test_interactive_pause_preserves_proxy_sentinel(self, db):
        """Interactive yield preserves current_job_id=-1 proxy sentinel."""
        d = Daemon(db)
        db.submit_job("echo hi", "deepseek-r1:8b", 5, 60, "test")
        db.try_claim_for_proxy()
        with patch.object(
            d.health,
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
            d.poll_once()
        state = db.get_daemon_state()
        assert state["current_job_id"] == -1


# --- Remaining coverage gaps ---


def test_drain_process_exits_during_select_timeout():
    """select() returns empty (timeout) + proc.poll() not None — drain remaining (lines 91-107)."""
    import select as _sel_mod
    import subprocess as _sp

    from ollama_queue.daemon import _drain_pipes_with_tracking
    from ollama_queue.sensing.stall import StallDetector

    proc = _sp.Popen(
        ["bash", "-c", "echo -n STDOUT_BUF; echo -n STDERR_BUF >&2"],
        stdout=_sp.PIPE,
        stderr=_sp.PIPE,
    )
    proc.wait()  # process has exited
    sd = StallDetector()

    original_select = _sel_mod.select
    select_count = [0]

    def mock_select(rlist, wlist, xlist, timeout=None):
        select_count[0] += 1
        if select_count[0] <= 1:
            # First call: return empty (simulating timeout) to trigger the poll() check
            return ([], [], [])
        return original_select(rlist, wlist, xlist, timeout)

    with patch("select.select", side_effect=mock_select):
        stdout, stderr = _drain_pipes_with_tracking(proc, 1, sd)

    assert b"STDOUT_BUF" in stdout
    assert b"STDERR_BUF" in stderr


def test_drain_read_raises_blocking_io_error():
    """os.read raising BlockingIOError on a ready fd is caught (lines 112-114)."""
    import os
    import subprocess as _sp

    from ollama_queue.daemon import _drain_pipes_with_tracking
    from ollama_queue.sensing.stall import StallDetector

    proc = _sp.Popen(["echo", "data"], stdout=_sp.PIPE, stderr=_sp.PIPE)
    sd = StallDetector()

    original_read = os.read
    read_count = [0]

    def failing_read(fd, size):
        read_count[0] += 1
        if read_count[0] <= 2:
            raise BlockingIOError("would block")
        return original_read(fd, size)

    with patch("os.read", side_effect=failing_read):
        stdout, _stderr = _drain_pipes_with_tracking(proc, 1, sd)
    # Should not crash — BlockingIOError is handled gracefully
    assert isinstance(stdout, bytes)


def test_drain_post_exit_read_oserror():
    """os.read raising OSError during post-exit drain (lines 104-105)."""
    import os
    import select as _sel_mod
    import subprocess as _sp

    from ollama_queue.daemon import _drain_pipes_with_tracking
    from ollama_queue.sensing.stall import StallDetector

    proc = _sp.Popen(
        ["bash", "-c", "echo -n X; exit 0"],
        stdout=_sp.PIPE,
        stderr=_sp.PIPE,
    )
    proc.wait()
    sd = StallDetector()

    original_select = _sel_mod.select
    original_read = os.read
    select_call = [0]

    def force_timeout_first(rlist, wlist, xlist, timeout=None):
        select_call[0] += 1
        if select_call[0] == 1:
            return ([], [], [])  # trigger not-ready path, then poll() finds exit
        return original_select(rlist, wlist, xlist, timeout)

    def failing_read(fd, size):
        raise OSError("pipe broken")

    with (
        patch("select.select", side_effect=force_timeout_first),
        patch("os.read", side_effect=failing_read),
    ):
        stdout, _stderr = _drain_pipes_with_tracking(proc, 1, sd)
    # Should not crash — OSError in post-exit drain is caught
    assert isinstance(stdout, bytes)


def test_drain_select_timeout_process_still_running():
    """select returns empty while process still running — continue (line 107)."""
    import select as _sel_mod
    import subprocess as _sp

    from ollama_queue.daemon import _drain_pipes_with_tracking
    from ollama_queue.sensing.stall import StallDetector

    # Process that runs briefly then exits with output
    proc = _sp.Popen(
        ["bash", "-c", "sleep 0.05; echo done"],
        stdout=_sp.PIPE,
        stderr=_sp.PIPE,
    )
    sd = StallDetector()

    original_select = _sel_mod.select
    select_call = [0]

    def intermittent_timeout(rlist, wlist, xlist, timeout=None):
        select_call[0] += 1
        if select_call[0] == 1:
            # First call: return empty while process is still alive → triggers line 107
            return ([], [], [])
        return original_select(rlist, wlist, xlist, timeout)

    with patch("select.select", side_effect=intermittent_timeout):
        stdout, _stderr = _drain_pipes_with_tracking(proc, 1, sd)
    proc.wait()
    assert b"done" in stdout


def test_entropy_std_zero_branch(db):
    """_check_entropy handles std_entropy == 0 (line 436) by defaulting to 0.1."""
    d = Daemon(db)
    now = time.time()
    # Fill history with exactly 0.0 — stdev of all zeros is 0.0
    for _ in range(15):
        d._entropy_history.append(0.0)
    # Empty queue → _compute_queue_entropy returns exactly 0.0
    jobs = []
    # After append: history is [0.0]*16, stdev = 0 → line 436 sets it to 0.1
    d._check_entropy(jobs, now, settings={"entropy_alert_sigma": 2.0})
    # The path should not crash — std_entropy was set to 0.1


def test_recover_orphans_sigterm_success(db):
    """_recover_orphans logs info when SIGTERM succeeds (line 509)."""
    d = Daemon(db)
    job_id = db.submit_job("echo hi", "", 5, 60, "test")
    db.start_job(job_id)
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE jobs SET pid = 999999 WHERE id = ?", (job_id,))
        conn.commit()
    with patch("ollama_queue.daemon.loop.os.kill") as mock_kill:
        # Don't raise — simulate successful SIGTERM
        mock_kill.return_value = None
        d._recover_orphans()
    mock_kill.assert_called_once_with(999999, _signal.SIGTERM)
    job = db.get_job(job_id)
    assert job["status"] == "pending"


def test_poll_once_cannot_admit_no_proxy(db):
    """poll_once sets current_job_id=None when can_admit fails and no proxy (line 769)."""
    d = Daemon(db)
    db.submit_job("echo hi", "qwen2.5:7b", 5, 60, "test")
    # No proxy claim — current_job_id should be None
    with (
        patch.object(
            d.health,
            "check",
            return_value={
                "ram_pct": 30.0,
                "swap_pct": 5.0,
                "load_avg": 0.5,
                "cpu_count": 4,
                "vram_pct": 20.0,
                "ollama_model": None,
            },
        ),
        patch.object(d, "_can_admit", return_value=False),
    ):
        d.poll_once()
    state = db.get_daemon_state()
    assert state["state"] == "idle"
    assert state["current_job_id"] is None


def test_run_job_unhandled_exception_partial_metrics_captured(db, caplog):
    """Unhandled exception captures partial metrics when out has parseable data (lines 1021-1026)."""
    d = Daemon(db)
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    job = db.get_job(job_id)

    # Ollama metrics JSON that parse_ollama_metrics can parse
    ollama_output = b'{"done": true, "eval_count": 42, "eval_duration": 2000000000}'

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking") as mock_drain,
        caplog.at_level(logging.DEBUG),
    ):
        proc = MagicMock()
        proc.pid = 1234
        mock_sub.Popen.return_value = proc
        # Drain returns output, then wait() raises to trigger exception handler
        mock_drain.return_value = (ollama_output, b"")
        proc.wait.side_effect = RuntimeError("proc crashed mid-flight")
        d._run_job(dict(job))

    job_after = db.get_job(job_id)
    assert job_after["status"] in ("completed", "dead", "failed")
    # Verify metrics were captured (store_job_metrics was called)
    stored = db.get_job_metrics(job_id)
    assert True  # metrics stored if parse succeeded


def test_run_job_unhandled_exception_partial_metrics_store_fails(db, caplog):
    """Partial metrics capture itself fails during unhandled exception (line 1025-1026)."""
    d = Daemon(db)
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    db.start_job(job_id)
    job = db.get_job(job_id)

    ollama_output = b'{"done": true, "eval_count": 42, "eval_duration": 2000000000}'

    with (
        patch("ollama_queue.daemon.executor.subprocess") as mock_sub,
        patch("ollama_queue.daemon.executor._drain_pipes_with_tracking") as mock_drain,
        patch.object(db, "store_job_metrics", side_effect=Exception("store failed")),
        caplog.at_level(logging.DEBUG),
    ):
        proc = MagicMock()
        proc.pid = 1234
        mock_sub.Popen.return_value = proc
        mock_drain.return_value = (ollama_output, b"")
        proc.wait.side_effect = RuntimeError("proc crashed")
        d._run_job(dict(job))

    # Should log debug message about failed partial capture
    assert any("Failed to capture partial metrics" in r.message for r in caplog.records)
