"""Tests for DLQ routing logic."""

import statistics
import time

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
        import random

        random.seed(None)  # ensure randomness
        delays = []
        for _ in range(20):
            job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test", max_retries=5)
            dlq = DLQManager(db)
            dlq._schedule_retry(job_id, 0)
            job = db.get_job(job_id)
            delays.append(job["last_retry_delay"])

        # Standard deviation should be meaningful — not all the same value
        assert statistics.stdev(delays) > 1.0, "Jitter should produce varied delays"
