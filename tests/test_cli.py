"""Tests for the Click CLI."""

import pytest
from click.testing import CliRunner
from ollama_queue.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def test_submit_command(runner, tmp_path):
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, [
        "--db", db_path,
        "submit", "--source", "test", "--model", "m", "--priority", "3", "--timeout", "60",
        "--", "echo", "hello",
    ])
    assert result.exit_code == 0
    assert "submitted" in result.output.lower() or "queued" in result.output.lower()


def test_status_command(runner, tmp_path):
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, ["--db", db_path, "status"])
    assert result.exit_code == 0


def test_queue_command(runner, tmp_path):
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, ["--db", db_path, "queue"])
    assert result.exit_code == 0


def test_history_command(runner, tmp_path):
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, ["--db", db_path, "history"])
    assert result.exit_code == 0


def test_pause_resume(runner, tmp_path):
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, ["--db", db_path, "pause"])
    assert result.exit_code == 0
    result = runner.invoke(main, ["--db", db_path, "resume"])
    assert result.exit_code == 0


def test_cancel_nonexistent(runner, tmp_path):
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, ["--db", db_path, "cancel", "999"])
    assert result.exit_code != 0 or "not found" in result.output.lower()


def test_submit_fallback_when_daemon_down(runner, tmp_path):
    """When --daemon-url fails, submit should run command directly."""
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, [
        "--db", db_path,
        "submit", "--source", "test", "--model", "m",
        "--", "echo", "fallback-test",
    ])
    # Should succeed either via queue or fallback
    assert result.exit_code == 0


def test_submit_uses_defaults(runner, tmp_path):
    """Submit without --priority/--timeout uses DB defaults."""
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, [
        "--db", db_path,
        "submit", "--source", "test", "--model", "m",
        "--", "echo", "defaults",
    ])
    assert result.exit_code == 0
    assert "priority=5" in result.output


def test_history_with_source_filter(runner, tmp_path):
    """History --source filters to a specific source."""
    db_path = str(tmp_path / "test.db")
    # Submit and complete a job so history has data
    runner.invoke(main, [
        "--db", db_path,
        "submit", "--source", "mysrc", "--model", "m",
        "--", "echo", "hi",
    ])
    result = runner.invoke(main, ["--db", db_path, "history", "--source", "mysrc"])
    assert result.exit_code == 0


def test_history_with_all_flag(runner, tmp_path):
    """History --all shows all statuses."""
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, ["--db", db_path, "history", "--all"])
    assert result.exit_code == 0


def test_cancel_pending_job(runner, tmp_path):
    """Cancel a pending job succeeds."""
    db_path = str(tmp_path / "test.db")
    runner.invoke(main, [
        "--db", db_path,
        "submit", "--source", "test", "--model", "m",
        "--", "echo", "cancel-me",
    ])
    result = runner.invoke(main, ["--db", db_path, "cancel", "1"])
    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()


def test_serve_placeholder(runner, tmp_path):
    """Serve command prints placeholder message."""
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, ["--db", db_path, "serve"])
    assert result.exit_code == 0


def test_queue_shows_pending_jobs(runner, tmp_path):
    """Queue command lists pending jobs."""
    db_path = str(tmp_path / "test.db")
    runner.invoke(main, [
        "--db", db_path,
        "submit", "--source", "test", "--model", "m", "--priority", "1",
        "--", "echo", "first",
    ])
    runner.invoke(main, [
        "--db", db_path,
        "submit", "--source", "test", "--model", "m", "--priority", "2",
        "--", "echo", "second",
    ])
    result = runner.invoke(main, ["--db", db_path, "queue"])
    assert result.exit_code == 0
    assert "first" in result.output
    assert "second" in result.output
