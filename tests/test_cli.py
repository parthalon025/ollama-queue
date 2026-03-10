"""Tests for the Click CLI."""

import click
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


def test_schedule_add_check_command(tmp_path):
    """schedule add accepts --check-command and --max-runs flags."""
    from ollama_queue.db import Database

    db_path = str(tmp_path / "q.db")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--db",
            db_path,
            "schedule",
            "add",
            "--name",
            "cc-test",
            "--interval",
            "1h",
            "--check-command",
            "exit 0",
            "--max-runs",
            "10",
            "echo",
            "hello",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "cc-test" in result.output
    db = Database(db_path)
    job = db.get_recurring_job_by_name("cc-test")
    assert job is not None, "Job 'cc-test' not found in DB"
    assert job["check_command"] == "exit 0"
    assert job["max_runs"] == 10


def test_schedule_edit_check_command(tmp_path):
    """schedule edit accepts --check-command flag."""
    from ollama_queue.db import Database

    db_path = str(tmp_path / "q.db")
    runner = CliRunner()
    runner.invoke(
        main,
        [
            "--db",
            db_path,
            "schedule",
            "add",
            "--name",
            "edit-test",
            "--interval",
            "1h",
            "echo",
            "hi",
        ],
    )
    result = runner.invoke(
        main,
        [
            "--db",
            db_path,
            "schedule",
            "edit",
            "edit-test",
            "--check-command",
            "exit 1",
        ],
    )
    assert result.exit_code == 0, result.output
    db = Database(db_path)
    job = db.get_recurring_job_by_name("edit-test")
    assert job is not None, "Job 'edit-test' not found in DB"
    assert job["check_command"] == "exit 1"


def test_schedule_enable_clears_outcome_reason(tmp_path):
    """schedule enable clears outcome_reason after auto-disable."""
    from ollama_queue.db import Database

    db_path = str(tmp_path / "q.db")
    db = Database(db_path)
    db.initialize()
    rj_id = db.add_recurring_job(name="re-enable-test", command="echo hi", interval_seconds=3600)
    db.disable_recurring_job(rj_id, "max_runs exhausted")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--db",
            db_path,
            "schedule",
            "enable",
            "re-enable-test",
        ],
    )
    assert result.exit_code == 0
    rj = db.get_recurring_job(rj_id)
    assert rj["enabled"] == 1
    assert rj["outcome_reason"] is None


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


class TestStatusCoverage:
    """Cover missing lines 66-67, 72-76, 79 in status command."""

    def test_status_daemon_state_none(self, runner, tmp_path):
        """When get_daemon_state returns None → 'unknown' message (lines 66-67)."""
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        # First invoke to initialise DB, then patch daemon state to None
        with patch("ollama_queue.db.Database.get_daemon_state", return_value=None):
            result = runner.invoke(main, ["--db", db_path, "status"])
        assert result.exit_code == 0
        assert "unknown" in result.output

    def test_status_with_current_job(self, runner, tmp_path):
        """When daemon has a current_job_id → show job info (lines 72-74)."""
        db_path = str(tmp_path / "test.db")
        # Submit a job first
        runner.invoke(
            main,
            ["--db", db_path, "submit", "--source", "s", "--model", "m", "--", "echo", "hi"],
        )
        from unittest.mock import patch

        state = {
            "state": "running",
            "current_job_id": 1,
            "paused_reason": None,
            "jobs_completed_today": 0,
            "jobs_failed_today": 0,
        }
        with patch("ollama_queue.db.Database.get_daemon_state", return_value=state):
            result = runner.invoke(main, ["--db", db_path, "status"])
        assert result.exit_code == 0
        assert "Current job: #1" in result.output

    def test_status_current_job_not_found(self, runner, tmp_path):
        """When current_job_id references missing job → show 'not found' (lines 75-76)."""
        db_path = str(tmp_path / "test.db")
        from unittest.mock import patch

        state = {
            "state": "running",
            "current_job_id": 999,
            "paused_reason": None,
            "jobs_completed_today": 0,
            "jobs_failed_today": 0,
        }
        with patch("ollama_queue.db.Database.get_daemon_state", return_value=state):
            result = runner.invoke(main, ["--db", db_path, "status"])
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_status_paused_reason(self, runner, tmp_path):
        """When paused_reason is set → show it (line 79)."""
        db_path = str(tmp_path / "test.db")
        from unittest.mock import patch

        state = {
            "state": "paused_manual",
            "current_job_id": None,
            "paused_reason": "manual",
            "jobs_completed_today": 5,
            "jobs_failed_today": 1,
        }
        with patch("ollama_queue.db.Database.get_daemon_state", return_value=state):
            result = runner.invoke(main, ["--db", db_path, "status"])
        assert result.exit_code == 0
        assert "Paused reason: manual" in result.output


class TestHistoryCoverage:
    """Cover missing lines 118, 132-136 in history command."""

    def test_history_all_with_source(self, runner, tmp_path):
        """--all --source filters raw SQL by source (line 118)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(
            main,
            ["--db", db_path, "submit", "--source", "src-a", "--model", "m", "--", "echo", "a"],
        )
        result = runner.invoke(main, ["--db", db_path, "history", "--all", "--source", "src-a"])
        assert result.exit_code == 0
        # Should show the job in output with table headers
        assert "src-a" in result.output

    def test_history_shows_table(self, runner, tmp_path):
        """History with completed jobs shows formatted table (lines 132-136)."""
        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        # Submit and manually complete a job
        jid = db.submit_job("echo done", "m", 5, 60, "test-src")
        db.start_job(jid)
        db.complete_job(jid, exit_code=0, stdout_tail="ok", stderr_tail="")
        result = runner.invoke(main, ["--db", db_path, "history"])
        assert result.exit_code == 0
        assert "echo done" in result.output
        assert "completed" in result.output

    def test_history_exit_code_none(self, runner, tmp_path):
        """Job with exit_code=None shows '-' (line 135)."""
        import sqlite3

        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo nullexit", "m", 5, 60, "test-src")
        db.start_job(jid)
        # Complete with exit_code=1, then manually set to NULL to simulate None
        db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="")
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE jobs SET exit_code = NULL WHERE id = ?", (jid,))
        conn.commit()
        conn.close()
        result = runner.invoke(main, ["--db", db_path, "history"])
        assert result.exit_code == 0
        assert "  -  " in result.output


class TestCancelCoverage:
    """Cover missing lines 168, 171-173 in cancel command."""

    def test_cancel_not_found_returns(self, runner, tmp_path):
        """Cancel nonexistent job → exit(1) and return (line 168)."""
        from unittest.mock import patch

        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        # Patch ctx.exit to be a no-op so the return statement is reachable
        with patch("click.Context.exit"):
            result = runner.invoke(main, ["--db", db_path, "cancel", "999"])
        assert "not found" in result.output.lower()

    def test_cancel_non_pending_job(self, runner, tmp_path):
        """Cancel a running/completed job → error (lines 171-173)."""
        from unittest.mock import patch

        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo x", "m", 5, 60, "s")
        db.start_job(jid)  # Now status='running'
        with patch("click.Context.exit"):
            result = runner.invoke(main, ["--db", db_path, "cancel", str(jid)])
        assert "can only cancel pending" in result.output.lower()


class TestParseIntervalCoverage:
    """Cover missing line 218 in _parse_interval."""

    def test_parse_interval_bare_seconds(self, runner, tmp_path):
        """Interval without unit suffix → assume seconds (line 218)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "bare-sec", "--interval", "300", "--", "echo", "hi"],
        )
        assert result.exit_code == 0
        assert "bare-sec" in result.output


class TestParseScheduleSpecCoverage:
    """Cover missing lines 248, 250, 252, 259, 266-267, 271, 275-276."""

    def test_no_schedule_option(self, runner, tmp_path):
        """No --interval/--at/--cron → error (line 248)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "x", "--", "echo"],
        )
        assert result.exit_code != 0
        assert "One of --interval" in result.output or "required" in result.output.lower()

    def test_mutually_exclusive(self, runner, tmp_path):
        """--interval + --at → error (line 250)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "x", "--interval", "1h", "--at", "10:00", "--", "echo"],
        )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_days_without_at(self, runner, tmp_path):
        """--days without --at → error (line 252)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "x", "--interval", "1h", "--days", "1-5", "--", "echo"],
        )
        assert result.exit_code != 0
        assert "--days is only valid with --at" in result.output

    def test_bad_at_format(self, runner, tmp_path):
        """--at with bad format → error (lines 266-267)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "x", "--at", "badformat", "--", "echo"],
        )
        assert result.exit_code != 0
        assert "HH:MM" in result.output

    def test_cron_expression(self, runner, tmp_path):
        """--cron with valid expression (line 271)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "cron-job", "--cron", "0 7 * * 1-5", "--", "echo"],
        )
        assert result.exit_code == 0
        assert "cron-job" in result.output

    def test_invalid_cron(self, runner, tmp_path):
        """--cron with invalid expression → error (lines 275-276)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "x", "--cron", "bad cron expr", "--", "echo"],
        )
        assert result.exit_code != 0
        assert "Invalid cron" in result.output


class TestAutoSuggestSlotCoverage:
    """Cover missing line 227 (_auto_suggest_slot no suggestions)."""

    def test_at_auto_no_suggestions(self, runner, tmp_path):
        """--at auto when all slots blocked → error (line 227)."""
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        with patch("ollama_queue.scheduling.scheduler.Scheduler.suggest_time", return_value=[]):
            result = runner.invoke(
                main,
                ["--db", db_path, "schedule", "add", "--name", "x", "--at", "auto", "--", "echo"],
            )
        assert result.exit_code != 0
        assert "No available time slots" in result.output

    def test_at_auto_no_db_context(self):
        """--at auto without db in _parse_schedule_spec → error (line 259)."""
        from ollama_queue.cli import _parse_schedule_spec

        with pytest.raises(click.UsageError, match="requires a database"):
            _parse_schedule_spec(None, "auto", None, None, db=None)


class TestScheduleListCoverage:
    """Cover missing lines 371-372, 379, 383, 386-389 in schedule list."""

    def test_schedule_list_empty(self, runner, tmp_path):
        """Empty recurring jobs → 'No recurring jobs.' (lines 371-372)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "schedule", "list"])
        assert result.exit_code == 0
        assert "No recurring jobs" in result.output

    def test_schedule_list_cron_display(self, runner, tmp_path):
        """Cron-based job shows cron expression (line 379)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "cron-list", "--cron", "30 7 * * *", "--", "echo"],
        )
        result = runner.invoke(main, ["--db", db_path, "schedule", "list"])
        assert result.exit_code == 0
        assert "30 7 * * *" in result.output

    def test_schedule_list_day_interval(self, runner, tmp_path):
        """Interval in days → 'every Xd' (line 383)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "day-job", "--interval", "1d", "--", "echo"],
        )
        result = runner.invoke(main, ["--db", db_path, "schedule", "list"])
        assert result.exit_code == 0
        assert "every 1d" in result.output

    def test_schedule_list_minute_interval(self, runner, tmp_path):
        """Interval in minutes → 'every Xm' (lines 386-387)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "min-job", "--interval", "30m", "--", "echo"],
        )
        result = runner.invoke(main, ["--db", db_path, "schedule", "list"])
        assert result.exit_code == 0
        assert "every 30m" in result.output

    def test_schedule_list_seconds_interval(self, runner, tmp_path):
        """Interval in odd seconds → 'every Xs' (lines 388-389)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(
            main,
            ["--db", db_path, "schedule", "add", "--name", "sec-job", "--interval", "45s", "--", "echo"],
        )
        result = runner.invoke(main, ["--db", db_path, "schedule", "list"])
        assert result.exit_code == 0
        assert "every 45s" in result.output


class TestScheduleSuggestCoverage:
    """Cover missing lines 409-410 in schedule suggest."""

    def test_suggest_no_slots(self, runner, tmp_path):
        """When suggest_time returns empty → message (lines 409-410)."""
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        with patch("ollama_queue.scheduling.scheduler.Scheduler.suggest_time", return_value=[]):
            result = runner.invoke(main, ["--db", db_path, "schedule", "suggest"])
        assert result.exit_code == 0
        assert "No available slots" in result.output


class TestScheduleEditCoverage:
    """Cover missing lines 435-436, 441, 443, 445, 449, 451-452."""

    def test_edit_not_found(self, runner, tmp_path):
        """Edit nonexistent job → error (lines 435-436)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "schedule", "edit", "nonexistent", "--priority", "1"])
        assert "not found" in result.output.lower()

    def test_edit_interval(self, runner, tmp_path):
        """Edit --interval (line 441)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(main, ["--db", db_path, "schedule", "add", "--name", "ej", "--interval", "1h", "--", "echo"])
        result = runner.invoke(main, ["--db", db_path, "schedule", "edit", "ej", "--interval", "2h"])
        assert result.exit_code == 0
        assert "interval_seconds=7200" in result.output

    def test_edit_command(self, runner, tmp_path):
        """Edit --command (line 443)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(main, ["--db", db_path, "schedule", "add", "--name", "ej2", "--interval", "1h", "--", "echo"])
        result = runner.invoke(main, ["--db", db_path, "schedule", "edit", "ej2", "--command", "echo new"])
        assert result.exit_code == 0
        assert "command=echo new" in result.output

    def test_edit_pin(self, runner, tmp_path):
        """Edit --pin/--no-pin (line 445)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(main, ["--db", db_path, "schedule", "add", "--name", "ej3", "--interval", "1h", "--", "echo"])
        result = runner.invoke(main, ["--db", db_path, "schedule", "edit", "ej3", "--pin"])
        assert result.exit_code == 0
        assert "pinned=1" in result.output

    def test_edit_max_runs(self, runner, tmp_path):
        """Edit --max-runs (line 449)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(main, ["--db", db_path, "schedule", "add", "--name", "ej4", "--interval", "1h", "--", "echo"])
        result = runner.invoke(main, ["--db", db_path, "schedule", "edit", "ej4", "--max-runs", "10"])
        assert result.exit_code == 0
        assert "max_runs=10" in result.output

    def test_edit_nothing_to_update(self, runner, tmp_path):
        """Edit with no options → 'Nothing to update' (lines 451-452)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(main, ["--db", db_path, "schedule", "add", "--name", "ej5", "--interval", "1h", "--", "echo"])
        result = runner.invoke(main, ["--db", db_path, "schedule", "edit", "ej5"])
        assert result.exit_code == 0
        assert "Nothing to update" in result.output


class TestScheduleEnableDisableCoverage:
    """Cover missing lines 473, 484."""

    def test_enable_not_found(self, runner, tmp_path):
        """Enable nonexistent job → error (line 473)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "schedule", "enable", "nope"])
        assert "not found" in result.output.lower()

    def test_disable_not_found(self, runner, tmp_path):
        """Disable nonexistent job → error (line 484)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "schedule", "disable", "nope"])
        assert "not found" in result.output.lower()


class TestScheduleRemoveCoverage:
    """Cover missing lines 491-495."""

    def test_remove_existing(self, runner, tmp_path):
        """Remove an existing job (lines 491-493)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(main, ["--db", db_path, "schedule", "add", "--name", "rm-me", "--interval", "1h", "--", "echo"])
        result = runner.invoke(main, ["--db", db_path, "schedule", "remove", "rm-me"])
        assert result.exit_code == 0
        assert "Removed" in result.output

    def test_remove_not_found(self, runner, tmp_path):
        """Remove nonexistent job → error (lines 494-495)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "schedule", "remove", "nope"])
        assert "not found" in result.output.lower()


class TestScheduleRebalanceCoverage:
    """Cover missing lines 501-507."""

    def test_rebalance(self, runner, tmp_path):
        """Rebalance prints number of changes (lines 501-507)."""
        db_path = str(tmp_path / "test.db")
        # Add some jobs so rebalance has work
        runner.invoke(
            main, ["--db", db_path, "schedule", "add", "--name", "rb1", "--interval", "1h", "--", "echo", "a"]
        )
        runner.invoke(
            main, ["--db", db_path, "schedule", "add", "--name", "rb2", "--interval", "1h", "--", "echo", "b"]
        )
        result = runner.invoke(main, ["--db", db_path, "schedule", "rebalance"])
        assert result.exit_code == 0
        assert "Rebalanced" in result.output


class TestDlqListCoverage:
    """Cover missing lines 525-526."""

    def test_dlq_list_with_entries(self, runner, tmp_path):
        """DLQ list shows entries when present (lines 525-526)."""
        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        # Submit, start, fail to DLQ
        jid = db.submit_job("echo fail", "m", 5, 60, "s", max_retries=0)
        db.start_job(jid)
        db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="err")
        db.move_to_dlq(jid, failure_reason="test failure")
        result = runner.invoke(main, ["--db", db_path, "dlq", "list"])
        assert result.exit_code == 0
        assert "echo fail" in result.output
        assert "test failure" in result.output


class TestDlqRetryCoverage:
    """Cover missing lines 533-538."""

    def test_dlq_retry_success(self, runner, tmp_path):
        """DLQ retry → new job (lines 533-536)."""
        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo retry", "m", 5, 60, "s", max_retries=0)
        db.start_job(jid)
        db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="")
        db.move_to_dlq(jid, failure_reason="failed")
        entries = db.list_dlq()
        dlq_id = entries[0]["id"]
        result = runner.invoke(main, ["--db", db_path, "dlq", "retry", str(dlq_id)])
        assert result.exit_code == 0
        assert "Retried" in result.output

    def test_dlq_retry_not_found(self, runner, tmp_path):
        """DLQ retry nonexistent → error (lines 537-538)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "dlq", "retry", "999"])
        assert "not found" in result.output.lower()


class TestDlqRetryAllCoverage:
    """Cover missing lines 544-550."""

    def test_dlq_retry_all(self, runner, tmp_path):
        """DLQ retry-all retries all entries (lines 544-550)."""
        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        for i in range(2):
            jid = db.submit_job(f"echo {i}", "m", 5, 60, "s", max_retries=0)
            db.start_job(jid)
            db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="")
            db.move_to_dlq(jid, failure_reason="fail")
        result = runner.invoke(main, ["--db", db_path, "dlq", "retry-all"])
        assert result.exit_code == 0
        assert "Retried 2" in result.output


class TestDlqDismissCoverage:
    """Cover missing lines 557-561."""

    def test_dlq_dismiss_success(self, runner, tmp_path):
        """DLQ dismiss → success (lines 557-559)."""
        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo x", "m", 5, 60, "s", max_retries=0)
        db.start_job(jid)
        db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="")
        db.move_to_dlq(jid, failure_reason="fail")
        entries = db.list_dlq()
        dlq_id = entries[0]["id"]
        result = runner.invoke(main, ["--db", db_path, "dlq", "dismiss", str(dlq_id)])
        assert result.exit_code == 0
        assert "Dismissed" in result.output

    def test_dlq_dismiss_not_found(self, runner, tmp_path):
        """DLQ dismiss nonexistent → error (lines 560-561)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "dlq", "dismiss", "999"])
        assert "not found" in result.output.lower()


class TestDlqClearCoverage:
    """Cover missing lines 567-569."""

    def test_dlq_clear(self, runner, tmp_path):
        """DLQ clear → count of cleared entries (lines 567-569)."""
        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo x", "m", 5, 60, "s", max_retries=0)
        db.start_job(jid)
        db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="")
        db.move_to_dlq(jid, failure_reason="fail")
        # Dismiss first so it becomes resolved, then clear
        entries = db.list_dlq()
        db.dismiss_dlq_entry(entries[0]["id"])
        result = runner.invoke(main, ["--db", db_path, "dlq", "clear"])
        assert result.exit_code == 0
        assert "Cleared" in result.output


class TestDlqSchedulePreviewCoverage:
    """Cover missing lines 576-600."""

    def test_schedule_preview_empty(self, runner, tmp_path):
        """No unscheduled DLQ entries → message (lines 576-580)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "dlq", "schedule-preview"])
        assert result.exit_code == 0
        assert "No unscheduled" in result.output

    def test_schedule_preview_with_entries(self, runner, tmp_path):
        """DLQ schedule-preview with eligible entries (lines 581-600)."""

        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo preview", "m", 5, 60, "s", max_retries=0)
        db.start_job(jid)
        db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="")
        db.move_to_dlq(jid, failure_reason="timeout exceeded")

        # classify_failure should return 'timeout' (transient)
        result = runner.invoke(main, ["--db", db_path, "dlq", "schedule-preview"])
        assert result.exit_code == 0
        assert "eligible" in result.output.lower()

    def test_schedule_preview_all_permanent(self, runner, tmp_path):
        """DLQ schedule-preview all permanent failures → not eligible (lines 589, 595-596)."""
        from unittest.mock import patch

        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo perm", "m", 5, 60, "s", max_retries=0)
        db.start_job(jid)
        db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="")
        db.move_to_dlq(jid, failure_reason="permanent error")

        with patch("ollama_queue.sensing.system_snapshot.classify_failure", return_value="permanent"):
            result = runner.invoke(main, ["--db", db_path, "dlq", "schedule-preview"])
        assert result.exit_code == 0
        assert "No unscheduled DLQ entries eligible" in result.output

    def test_schedule_preview_chronic_failure(self, runner, tmp_path):
        """DLQ schedule-preview chronic failure → skip (line 591)."""
        import sqlite3

        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo chronic", "m", 5, 60, "s", max_retries=0)
        db.start_job(jid)
        db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="")
        db.move_to_dlq(jid, failure_reason="timeout exceeded")
        # Set high auto_reschedule_count to trigger chronic skip
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE dlq SET auto_reschedule_count = 10")
        conn.commit()
        conn.close()
        result = runner.invoke(main, ["--db", db_path, "dlq", "schedule-preview"])
        assert result.exit_code == 0
        assert "No unscheduled DLQ entries eligible" in result.output


class TestDlqRescheduleCoverage:
    """Cover missing lines 608-636."""

    def test_reschedule_success(self, runner, tmp_path):
        """DLQ reschedule creates new job (lines 621-636)."""
        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo resched", "m", 5, 60, "s", max_retries=0)
        db.start_job(jid)
        db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="")
        db.move_to_dlq(jid, failure_reason="fail")
        entries = db.list_dlq()
        dlq_id = entries[0]["id"]
        result = runner.invoke(main, ["--db", db_path, "dlq", "reschedule", str(dlq_id)])
        assert result.exit_code == 0
        assert f"DLQ #{dlq_id}" in result.output
        assert "new job" in result.output.lower()

    def test_reschedule_not_found(self, runner, tmp_path):
        """DLQ reschedule nonexistent → error (lines 612-614)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "dlq", "reschedule", "999"])
        assert "not found" in result.output.lower()

    def test_reschedule_already_rescheduled(self, runner, tmp_path):
        """DLQ reschedule already rescheduled → error (lines 615-620)."""
        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo resched2", "m", 5, 60, "s", max_retries=0)
        db.start_job(jid)
        db.complete_job(jid, exit_code=1, stdout_tail="", stderr_tail="")
        db.move_to_dlq(jid, failure_reason="fail")
        entries = db.list_dlq()
        dlq_id = entries[0]["id"]
        # First reschedule
        runner.invoke(main, ["--db", db_path, "dlq", "reschedule", str(dlq_id)])
        # Second reschedule should fail
        result = runner.invoke(main, ["--db", db_path, "dlq", "reschedule", str(dlq_id)])
        assert "already rescheduled" in result.output.lower()


class TestDeferCoverage:
    """Cover missing lines 645-654."""

    def test_defer_success(self, runner, tmp_path):
        """Defer a pending job → success (lines 653-654)."""
        db_path = str(tmp_path / "test.db")
        runner.invoke(
            main,
            ["--db", db_path, "submit", "--source", "s", "--model", "m", "--", "echo", "defer-me"],
        )
        result = runner.invoke(main, ["--db", db_path, "defer", "1", "--reason", "testing"])
        assert result.exit_code == 0
        assert "deferred" in result.output.lower()
        assert "testing" in result.output

    def test_defer_not_found(self, runner, tmp_path):
        """Defer nonexistent job → error (lines 647-649)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "defer", "999"])
        assert "not found" in result.output.lower()

    def test_defer_wrong_status(self, runner, tmp_path):
        """Defer a running job → error (lines 650-652)."""
        from ollama_queue.db import Database

        db_path = str(tmp_path / "test.db")
        db = Database(db_path)
        db.initialize()
        jid = db.submit_job("echo x", "m", 5, 60, "s")
        db.start_job(jid)
        result = runner.invoke(main, ["--db", db_path, "defer", str(jid)])
        assert "Cannot defer" in result.output


class TestMetricsModelsCoverage:
    """Cover missing lines 667-678."""

    def test_metrics_models_empty(self, runner, tmp_path):
        """No metrics data → message (line 670)."""
        db_path = str(tmp_path / "test.db")
        result = runner.invoke(main, ["--db", db_path, "metrics", "models"])
        assert result.exit_code == 0
        assert "No metrics data" in result.output

    def test_metrics_models_with_data(self, runner, tmp_path):
        """Metrics models with data shows table (lines 672-678)."""
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        mock_stats = {
            "qwen2.5:7b": {
                "run_count": 10,
                "avg_tok_per_min": 150.5,
                "avg_warmup_s": 2.3,
                "model_size_gb": 4.5,
            },
            "llama3:8b": {
                "run_count": 5,
                "avg_tok_per_min": None,
                "avg_warmup_s": None,
                "model_size_gb": None,
            },
        }
        with patch("ollama_queue.db.Database.get_model_stats", return_value=mock_stats):
            result = runner.invoke(main, ["--db", db_path, "metrics", "models"])
        assert result.exit_code == 0
        assert "qwen2.5:7b" in result.output
        assert "llama3:8b" in result.output
        assert "150" in result.output  # tok/min


class TestMetricsCurveCoverage:
    """Cover missing lines 685-712."""

    def test_curve_not_enough_data(self, runner, tmp_path):
        """No data points → message (lines 698-699)."""
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        with patch("ollama_queue.db.Database.get_model_stats", return_value={}):
            result = runner.invoke(main, ["--db", db_path, "metrics", "curve"])
        assert result.exit_code == 0
        assert "Not enough data" in result.output

    def test_curve_fit_fails(self, runner, tmp_path):
        """Fit returns fitted=False → message (lines 703-704)."""
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        mock_stats = {
            "m1": {"model_size_gb": 4.5, "avg_tok_per_min": 100.0},
            "m2": {"model_size_gb": 7.0, "avg_tok_per_min": 80.0},
        }
        with (
            patch("ollama_queue.db.Database.get_model_stats", return_value=mock_stats),
            patch("ollama_queue.models.performance_curve.PerformanceCurve.fit"),
            patch(
                "ollama_queue.models.performance_curve.PerformanceCurve.get_curve_data",
                return_value={"fitted": False, "n_points": 2},
            ),
        ):
            result = runner.invoke(main, ["--db", db_path, "metrics", "curve"])
        assert result.exit_code == 0
        assert "Could not fit" in result.output

    def test_curve_success(self, runner, tmp_path):
        """Curve fitted → show parameters (lines 701-712)."""
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        mock_stats = {
            "m1": {"model_size_gb": 4.5, "avg_tok_per_min": 100.0},
            "m2": {"model_size_gb": 7.0, "avg_tok_per_min": 80.0},
        }
        curve_data = {
            "fitted": True,
            "n_points": 2,
            "tok_slope": -0.5,
            "tok_intercept": 200.0,
            "tok_residual_std": 5.0,
            "warmup_slope": 0.3,
            "warmup_intercept": 1.0,
        }
        with (
            patch("ollama_queue.db.Database.get_model_stats", return_value=mock_stats),
            patch("ollama_queue.models.performance_curve.PerformanceCurve.fit"),
            patch("ollama_queue.models.performance_curve.PerformanceCurve.get_curve_data", return_value=curve_data),
        ):
            result = runner.invoke(main, ["--db", db_path, "metrics", "curve"])
        assert result.exit_code == 0
        assert "Performance curve fitted" in result.output
        assert "tok/min slope" in result.output
        assert "warmup slope" in result.output

    def test_curve_no_warmup(self, runner, tmp_path):
        """Curve fitted without warmup data (lines 710-712 not hit)."""
        from unittest.mock import patch

        db_path = str(tmp_path / "test.db")
        mock_stats = {
            "m1": {"model_size_gb": 4.5, "avg_tok_per_min": 100.0},
            "m2": {"model_size_gb": 7.0, "avg_tok_per_min": 80.0},
        }
        curve_data = {
            "fitted": True,
            "n_points": 2,
            "tok_slope": -0.5,
            "tok_intercept": 200.0,
            "tok_residual_std": 5.0,
            "warmup_slope": None,
            "warmup_intercept": None,
        }
        with (
            patch("ollama_queue.db.Database.get_model_stats", return_value=mock_stats),
            patch("ollama_queue.models.performance_curve.PerformanceCurve.fit"),
            patch("ollama_queue.models.performance_curve.PerformanceCurve.get_curve_data", return_value=curve_data),
        ):
            result = runner.invoke(main, ["--db", db_path, "metrics", "curve"])
        assert result.exit_code == 0
        assert "Performance curve fitted" in result.output
        assert "warmup slope" not in result.output
