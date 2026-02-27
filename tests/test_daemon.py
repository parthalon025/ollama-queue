"""Tests for the daemon polling loop and job runner."""

from unittest.mock import MagicMock, patch

import pytest

from ollama_queue.daemon import Daemon


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
        proc.wait.return_value = 0
        proc.stdout.read.return_value = b"hello"
        proc.stderr.read.return_value = b""
        proc.returncode = 0
        mock_sub.Popen.return_value = proc

        daemon.poll_once()

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
        proc.wait.side_effect = lambda timeout: (_ for _ in ()).throw(__import__("subprocess").TimeoutExpired("cmd", 1))
        proc.kill.return_value = None
        proc.communicate.return_value = (b"", b"")
        proc.returncode = -9
        mock_sub.Popen.return_value = proc
        mock_sub.TimeoutExpired = __import__("subprocess").TimeoutExpired

        daemon.poll_once()

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
        proc.wait.return_value = 0
        proc.stdout.read.return_value = b"ok"
        proc.stderr.read.return_value = b""
        proc.returncode = 0
        mock_sub.Popen.return_value = proc

        daemon.poll_once()

    history = daemon.db.get_duration_history("test-src")
    assert len(history) == 1


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
        proc.wait.return_value = 0
        proc.stdout.read.return_value = b"ok"
        proc.stderr.read.return_value = b""
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        daemon.poll_once()

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
        proc.wait.return_value = 0
        proc.stdout.read.return_value = b"ok"
        proc.stderr.read.return_value = b""
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        daemon.poll_once()

    job2 = daemon.db.get_job(2)
    assert job2["status"] == "completed"  # should NOT be blocked
    state = daemon.db.get_daemon_state()
    assert state["state"] != "paused_interactive"
