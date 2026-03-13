"""Tests for DLQ routing logic."""

import statistics
import time
from unittest.mock import MagicMock

import pytest

from ollama_queue.db import Database
from ollama_queue.dlq import DLQManager


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


@pytest.fixture
def dlq(db):
    return DLQManager(db)


class TestDLQRouting:
    def _make_failed_job(self, db, max_retries=0, retry_count=0):
        job_id = db.submit_job("echo fail", "m", 5, 60, "src", max_retries=max_retries)
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="out", stderr_tail="err", outcome_reason="exit code 1")
        # Manually set retry_count for testing
        if retry_count:
            conn = db._connect()
            conn.execute("UPDATE jobs SET retry_count = ? WHERE id = ?", (retry_count, job_id))
            conn.commit()
        return job_id

    def test_route_to_dlq_when_retries_exhausted(self, db, dlq):
        job_id = self._make_failed_job(db, max_retries=2, retry_count=2)
        result = dlq.handle_failure(job_id, "exit code 1")
        assert result == "dlq"
        entries = db.list_dlq()
        assert len(entries) == 1

    def test_schedule_retry_when_retries_remain(self, db, dlq):
        job_id = self._make_failed_job(db, max_retries=3, retry_count=1)
        result = dlq.handle_failure(job_id, "exit code 1")
        assert result == "retry"
        job = db.get_job(job_id)
        assert job["retry_after"] is not None

    def test_retry_backoff_increases(self, db, dlq):
        job_id = self._make_failed_job(db, max_retries=3, retry_count=2)
        dlq.handle_failure(job_id, "exit code 1")
        job = db.get_job(job_id)
        # Decorrelated jitter: retry_after must be in the future, delay must be positive
        assert job["retry_after"] > time.time()
        assert job["last_retry_delay"] > 0

    def test_logs_dlq_event(self, db, dlq):
        job_id = self._make_failed_job(db, max_retries=0)
        dlq.handle_failure(job_id, "exit code 1")
        events = db.get_schedule_events()
        assert any(e["event_type"] == "dlq_moved" for e in events)

    def test_retry_uses_decorrelated_jitter(self, db):
        """Multiple retries should produce randomized delays, not fixed exponential."""
        job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test", max_retries=3)
        dlq = DLQManager(db)

        delays = []
        for attempt in range(3):
            result = dlq._schedule_retry(job_id, attempt)
            assert result == "retry"
            updated = db.get_job(job_id)
            delays.append(updated["last_retry_delay"])
            # Reset retry_after for next iteration
            db._conn.execute("UPDATE jobs SET retry_after=NULL WHERE id=?", (job_id,))
            db._conn.commit()

        # All delays should be positive
        assert all(d > 0 for d in delays)
        # Delays should be stored as last_retry_delay
        assert all(d is not None for d in delays)
        base = db.get_setting("retry_backoff_base_seconds")
        cap = db.get_setting("retry_backoff_cap_seconds")
        # All delays must be within [base, cap]
        assert all(d >= base for d in delays), f"Delay below base: {delays}"
        assert all(d <= cap for d in delays), f"Delay above cap: {delays}"
        # Verify the prev_delay chain: delay[i] must be <= prev_delay[i-1] * 3
        # (prev_delay[0] = base since last_retry_delay was NULL before first retry)
        prev = base
        for d in delays:
            assert d <= prev * 3, f"Delay {d} exceeded prev_delay*3 bound ({prev * 3})"
            prev = d

    def test_retry_delay_bounded_by_cap(self, db):
        """Retry delay must never exceed retry_backoff_cap_seconds."""
        job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test", max_retries=10)
        dlq = DLQManager(db)
        cap = db.get_setting("retry_backoff_cap_seconds")

        for attempt in range(5):
            dlq._schedule_retry(job_id, attempt)
            job = db.get_job(job_id)
            assert job["last_retry_delay"] <= cap
            db._conn.execute("UPDATE jobs SET retry_after=NULL WHERE id=?", (job_id,))
            db._conn.commit()

    def test_retry_delays_vary_across_calls(self, db):
        """Decorrelated jitter produces non-deterministic delays (statistical test)."""
        delays = []
        for _ in range(20):
            job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test", max_retries=5)
            dlq = DLQManager(db)
            dlq._schedule_retry(job_id, 0)
            job = db.get_job(job_id)
            delays.append(job["last_retry_delay"])

        # Standard deviation should be meaningful — not all the same value
        assert statistics.stdev(delays) > 1.0, "Jitter should produce varied delays"

    def test_schedule_retry_resets_status_and_increments_count(self, db):
        """After _schedule_retry, job must be pending and retry_count incremented."""
        job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test", max_retries=3)
        # Simulate failure state: mark job as failed, retry_count=1
        db._conn.execute("UPDATE jobs SET status='failed', retry_count=1 WHERE id=?", (job_id,))
        db._conn.commit()

        dlq = DLQManager(db)
        dlq._schedule_retry(job_id, 1)

        job = db.get_job(job_id)
        assert job["status"] == "pending", f"Expected pending, got {job['status']}"
        assert job["retry_count"] == 2, f"Expected retry_count=2, got {job['retry_count']}"
        assert job["last_retry_delay"] is not None
        assert job["retry_after"] is not None


def test_dlq_auto_reschedule_columns(db):
    """DLQ entries have auto-reschedule tracking columns."""
    job_id = db.submit_job("echo fail", "test-model", 5, 60, "test")
    db.start_job(job_id)
    db.complete_job(job_id, 1, "out", "err")
    dlq_id = db.move_to_dlq(job_id, "exit code 1")
    entry = db.get_dlq_entry(dlq_id)
    assert entry["auto_reschedule_count"] == 0
    assert entry["auto_rescheduled_at"] is None
    assert entry["rescheduled_job_id"] is None
    assert entry["rescheduled_for"] is None
    assert entry["reschedule_reasoning"] is None


def test_update_dlq_reschedule(db):
    """Update DLQ entry with reschedule info."""
    job_id = db.submit_job("echo fail", "test-model", 5, 60, "test")
    db.start_job(job_id)
    db.complete_job(job_id, 1, "out", "err")
    dlq_id = db.move_to_dlq(job_id, "exit code 1")

    import json

    now = time.time()
    reasoning = json.dumps({"score": 7.2, "reasons": ["load headroom: 8.0"]})
    db.update_dlq_reschedule(dlq_id, rescheduled_job_id=999, rescheduled_for=now + 3600, reschedule_reasoning=reasoning)

    entry = db.get_dlq_entry(dlq_id)
    assert entry["auto_rescheduled_at"] is not None
    assert entry["rescheduled_job_id"] == 999
    assert entry["rescheduled_for"] > now
    assert "load headroom" in entry["reschedule_reasoning"]


def test_handle_failure_job_not_found(db):
    """handle_failure returns 'dlq' immediately when job doesn't exist (lines 32-33)."""
    dlq_mgr = DLQManager(db)
    result = dlq_mgr.handle_failure(999999, "job vanished")
    assert result == "dlq"


def test_dlq_handle_failure_prevents_double_retry_under_concurrency():
    """handle_failure concurrent calls must retry at most once — not once per thread.

    The fix wraps the get_job + decision in a single lock acquisition. We simulate
    state mutation inside _set_job_retry so that the second/third threads see
    retry_count >= max_retries after the first thread commits its retry.
    """
    import threading

    from ollama_queue.dlq import DLQManager

    retry_calls = []
    _state = {"retry_count": 0}
    lock = threading.RLock()

    def fake_get_job(_job_id):
        return {
            "id": 1,
            "retry_count": _state["retry_count"],
            "max_retries": 1,  # only one retry allowed
            "last_retry_delay": 30,
            "command": "echo",
            "model": None,
            "timeout": 60,
            "source": "test",
        }

    def fake_set_job_retry(*a, **k):
        _state["retry_count"] += 1
        retry_calls.append(1)

    mock_db = MagicMock()
    mock_db._lock = lock
    mock_db.get_job.side_effect = fake_get_job
    mock_db.get_all_settings.return_value = {}
    mock_db._set_job_retry = fake_set_job_retry
    mock_db.log_schedule_event = MagicMock()
    mock_db.move_to_dlq = MagicMock(return_value=99)

    manager = DLQManager(mock_db)
    threads = [threading.Thread(target=manager.handle_failure, args=(1, "test error")) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(retry_calls) <= 1, f"Expected at most 1 retry, got {len(retry_calls)}"


def test_list_dlq_unscheduled_only(db):
    """list_dlq with unscheduled_only=True excludes already-rescheduled entries."""
    j1 = db.submit_job("cmd1", "m", 5, 60, "test")
    db.start_job(j1)
    db.complete_job(j1, 1, "out", "err")
    dlq1 = db.move_to_dlq(j1, "fail1")

    j2 = db.submit_job("cmd2", "m", 5, 60, "test")
    db.start_job(j2)
    db.complete_job(j2, 1, "out", "err")
    dlq2 = db.move_to_dlq(j2, "fail2")

    # Reschedule one
    db.update_dlq_reschedule(dlq1, rescheduled_job_id=100, rescheduled_for=9999999999.0)

    # Unscheduled only should return just the second
    unscheduled = db.list_dlq(unscheduled_only=True)
    assert len(unscheduled) == 1
    assert unscheduled[0]["id"] == dlq2
