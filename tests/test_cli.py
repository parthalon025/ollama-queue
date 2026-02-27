"""Tests for the Click CLI."""

import pytest
from click.testing import CliRunner

from ollama_queue.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def test_submit_command(runner, tmp_path):
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "test",
            "--model",
            "m",
            "--priority",
            "3",
            "--timeout",
            "60",
            "--",
            "echo",
            "hello",
        ],
    )
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
    result = runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "test",
            "--model",
            "m",
            "--",
            "echo",
            "fallback-test",
        ],
    )
    # Should succeed either via queue or fallback
    assert result.exit_code == 0


def test_submit_uses_defaults(runner, tmp_path):
    """Submit without --priority/--timeout uses DB defaults."""
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "test",
            "--model",
            "m",
            "--",
            "echo",
            "defaults",
        ],
    )
    assert result.exit_code == 0
    assert "priority=5" in result.output


def test_history_with_source_filter(runner, tmp_path):
    """History --source filters to a specific source."""
    db_path = str(tmp_path / "test.db")
    # Submit and complete a job so history has data
    runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "mysrc",
            "--model",
            "m",
            "--",
            "echo",
            "hi",
        ],
    )
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
    runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "test",
            "--model",
            "m",
            "--",
            "echo",
            "cancel-me",
        ],
    )
    result = runner.invoke(main, ["--db", db_path, "cancel", "1"])
    assert result.exit_code == 0
    assert "cancelled" in result.output.lower()


def test_serve_starts_daemon_and_api(runner, tmp_path, monkeypatch):
    """Serve command starts daemon thread and uvicorn."""
    import threading

    started_threads = []
    uvicorn_calls = []

    original_thread_init = threading.Thread.__init__

    def fake_thread_init(self, *args, **kwargs):
        original_thread_init(self, *args, **kwargs)
        started_threads.append(kwargs.get("target"))

    def fake_thread_start(self):
        pass  # Don't actually start the thread

    def fake_uvicorn_run(app, **kwargs):
        uvicorn_calls.append(kwargs)

    monkeypatch.setattr(threading.Thread, "__init__", fake_thread_init)
    monkeypatch.setattr(threading.Thread, "start", fake_thread_start)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, ["--db", db_path, "serve", "--port", "9999"])
    assert result.exit_code == 0
    assert len(uvicorn_calls) == 1
    assert uvicorn_calls[0]["port"] == 9999
    assert uvicorn_calls[0]["host"] == "127.0.0.1"
    assert len(started_threads) >= 1


def test_queue_shows_pending_jobs(runner, tmp_path):
    """Queue command lists pending jobs."""
    db_path = str(tmp_path / "test.db")
    runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "test-a",
            "--model",
            "m",
            "--priority",
            "1",
            "--",
            "echo",
            "first",
        ],
    )
    runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "test-b",
            "--model",
            "m",
            "--priority",
            "2",
            "--",
            "echo",
            "second",
        ],
    )
    result = runner.invoke(main, ["--db", db_path, "queue"])
    assert result.exit_code == 0
    assert "first" in result.output
    assert "second" in result.output


def test_submit_dedup_skips_duplicate_source(runner, tmp_path):
    """Submit with dedup skips if pending job from same source exists."""
    db_path = str(tmp_path / "test.db")
    r1 = runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "alerts",
            "--model",
            "m",
            "--",
            "echo",
            "first",
        ],
    )
    assert "queued" in r1.output

    r2 = runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "alerts",
            "--model",
            "m",
            "--",
            "echo",
            "second",
        ],
    )
    assert "Skipped" in r2.output

    # Only one job in queue
    result = runner.invoke(main, ["--db", db_path, "queue"])
    assert "first" in result.output
    assert "second" not in result.output


def test_submit_no_dedup_allows_duplicates(runner, tmp_path):
    """Submit with --no-dedup allows duplicate sources."""
    db_path = str(tmp_path / "test.db")
    runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "alerts",
            "--model",
            "m",
            "--",
            "echo",
            "first",
        ],
    )
    r2 = runner.invoke(
        main,
        [
            "--db",
            db_path,
            "submit",
            "--source",
            "alerts",
            "--model",
            "m",
            "--no-dedup",
            "--",
            "echo",
            "second",
        ],
    )
    assert "queued" in r2.output


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


class TestScheduleCLI:
    def test_schedule_add(self, runner, db_path):
        result = runner.invoke(
            main,
            [
                "--db",
                db_path,
                "schedule",
                "add",
                "--name",
                "test-job",
                "--interval",
                "6h",
                "--model",
                "qwen2.5:14b",
                "--priority",
                "3",
                "--tag",
                "aria",
                "--",
                "echo hello",
            ],
        )
        assert result.exit_code == 0
        assert "test-job" in result.output

    def test_schedule_list(self, runner, db_path):
        runner.invoke(main, ["--db", db_path, "schedule", "add", "--name", "j1", "--interval", "1h", "--", "echo a"])
        result = runner.invoke(main, ["--db", db_path, "schedule", "list"])
        assert result.exit_code == 0
        assert "j1" in result.output

    def test_schedule_disable_enable(self, runner, db_path):
        runner.invoke(main, ["--db", db_path, "schedule", "add", "--name", "j1", "--interval", "1h", "--", "echo a"])
        result = runner.invoke(main, ["--db", db_path, "schedule", "disable", "j1"])
        assert result.exit_code == 0
        result = runner.invoke(main, ["--db", db_path, "schedule", "enable", "j1"])
        assert result.exit_code == 0

    def test_dlq_list(self, runner, db_path):
        result = runner.invoke(main, ["--db", db_path, "dlq", "list"])
        assert result.exit_code == 0


class TestSubmitWithNewFlags:
    def test_submit_with_tag_and_retries(self, runner, db_path):
        result = runner.invoke(
            main,
            [
                "--db",
                db_path,
                "submit",
                "--source",
                "test",
                "--model",
                "qwen2.5:7b",
                "--tag",
                "aria",
                "--max-retries",
                "2",
                "--",
                "echo hello",
            ],
        )
        assert result.exit_code == 0


def test_schedule_add_pin_flag(tmp_path):
    runner = CliRunner()
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(
        main,
        ["--db", db_path, "schedule", "add", "--name", "pinned-aria", "--at", "23:30", "--pin", "--", "aria", "run"],
    )
    assert result.exit_code == 0, result.output
    assert "pinned" in result.output.lower() or "★" in result.output


def test_schedule_add_at_auto(tmp_path):
    runner = CliRunner()
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(
        main,
        ["--db", db_path, "schedule", "add", "--name", "auto-job", "--at", "auto", "--priority", "5", "--", "cmd"],
    )
    assert result.exit_code == 0, result.output
    assert "Suggested" in result.output or "cron=" in result.output


def test_schedule_suggest(tmp_path):
    runner = CliRunner()
    db_path = str(tmp_path / "test.db")
    result = runner.invoke(main, ["--db", db_path, "schedule", "suggest", "--priority", "5"])
    assert result.exit_code == 0, result.output
    # Should output at least one time suggestion with HH:MM format
    assert ":" in result.output


def test_schedule_edit_priority(tmp_path):
    runner = CliRunner()
    db_path = str(tmp_path / "test.db")
    # First add a job
    runner.invoke(main, ["--db", db_path, "schedule", "add", "--name", "myjob", "--interval", "1h", "--", "cmd"])
    # Then edit its priority
    result = runner.invoke(main, ["--db", db_path, "schedule", "edit", "myjob", "--priority", "2"])
    assert result.exit_code == 0, result.output
    # Verify in list
    list_result = runner.invoke(main, ["--db", db_path, "schedule", "list"])
    assert "myjob" in list_result.output
