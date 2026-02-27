"""Tests for DLQ routing logic."""

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
        db.complete_job(job_id, exit_code=1, stdout_tail="out", stderr_tail="err",
                        outcome_reason="exit code 1")
        # Manually set retry_count for testing
        if retry_count:
            conn = db._connect()
            conn.execute(
                "UPDATE jobs SET retry_count = ? WHERE id = ?", (retry_count, job_id)
            )
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
        # retry 2 → backoff = 60 * 2^2 = 240s
        expected_delay = 60 * (2.0 ** 2)
        actual_delay = job["retry_after"] - time.time()
        assert abs(actual_delay - expected_delay) < 2.0

    def test_logs_dlq_event(self, db, dlq):
        job_id = self._make_failed_job(db, max_retries=0)
        dlq.handle_failure(job_id, "exit code 1")
        events = db.get_schedule_events()
        assert any(e["event_type"] == "dlq_moved" for e in events)
