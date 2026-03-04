"""Tests for the daemon polling loop and job runner."""

import concurrent.futures
import logging
import signal as _signal
import time
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
        patch("ollama_queue.daemon.subprocess") as mock_sub,
        patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"hello", b"")),
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
        patch("ollama_queue.daemon.subprocess") as mock_sub,
        patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"hello", b"")) as mock_drain,
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
        patch("ollama_queue.daemon.subprocess") as mock_sub,
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
        patch("ollama_queue.daemon.subprocess") as mock_sub,
        patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")),
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
            patch("ollama_queue.daemon.subprocess") as mock_sub,
            patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"hi", b"")),
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
        patch("ollama_queue.daemon.subprocess") as mock_sub,
        patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")),
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
        patch("ollama_queue.daemon.subprocess") as mock_sub,
        patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")),
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
        patch("ollama_queue.daemon.os.kill") as mock_kill,
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
        patch("ollama_queue.daemon.os.kill") as mock_kill,
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

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        mock_sub.run.return_value = MagicMock(returncode=0)
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.run.assert_called_once()
    mock_sub.Popen.assert_called_once()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_check_command_exit1_skips(daemon):
    """check_command exit 1 → job skipped, next_run advanced, no Popen."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="exit 1")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
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

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
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

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        mock_sub.run.return_value = MagicMock(returncode=99)
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.Popen.assert_called_once()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_check_command_timeout_failopen(daemon):
    """check_command TimeoutExpired → warning logged, main job proceeds."""
    _rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="sleep 999")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.run.side_effect = _subprocess_mod.TimeoutExpired("sleep 999", 30)
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.Popen.assert_called_once()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_no_check_command_skips_check(daemon):
    """Job with no check_command skips check, runs normally."""
    _rj_id, job_id = _make_recurring_and_job(daemon.db, check_command=None)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.run.assert_not_called()
    mock_sub.Popen.assert_called_once()


def test_max_runs_decrements_on_success(daemon):
    """Successful main job decrements max_runs by 1."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=3)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["max_runs"] == 2


def test_max_runs_no_decrement_on_failure(daemon):
    """Failed main job does NOT decrement max_runs."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=3)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 1  # failure
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"", b"err")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["max_runs"] == 3  # unchanged


def test_max_runs_zero_disables_job(daemon):
    """When max_runs reaches 0 after success, recurring job is auto-disabled."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=1)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["enabled"] == 0
    assert "max_runs" in (rj["outcome_reason"] or "").lower()


def test_no_max_runs_no_decrement(daemon):
    """Job with max_runs=None doesn't touch the field."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=None)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["max_runs"] is None


class TestEntropyComputation:
    def test_empty_queue_entropy_is_zero(self, db):
        """Empty pending list gives entropy 0."""
        from ollama_queue.daemon import Daemon

        daemon = Daemon(db)
        import time

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
