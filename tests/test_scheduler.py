"""Tests for the Scheduler class."""

import time
import pytest
from ollama_queue.db import Database
from ollama_queue.scheduler import Scheduler


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


@pytest.fixture
def scheduler(db):
    return Scheduler(db)


class TestPromoteDueJobs:
    def test_promotes_due_job(self, db, scheduler):
        now = time.time()
        db.add_recurring_job("job1", "echo hi", 3600, next_run=now - 1)
        rj = db.get_recurring_job_by_name("job1")
        new_ids = scheduler.promote_due_jobs(now)
        assert len(new_ids) == 1
        job = db.get_job(new_ids[0])
        assert job["command"] == "echo hi"
        assert job["status"] == "pending"
        assert job["recurring_job_id"] == rj["id"]

    def test_skips_not_yet_due(self, db, scheduler):
        now = time.time()
        db.add_recurring_job("job1", "echo hi", 3600, next_run=now + 100)
        new_ids = scheduler.promote_due_jobs(now)
        assert len(new_ids) == 0

    def test_coalesces_duplicate(self, db, scheduler):
        now = time.time()
        db.add_recurring_job("job1", "echo hi", 3600, next_run=now - 1)
        scheduler.promote_due_jobs(now)  # first promotion
        new_ids = scheduler.promote_due_jobs(now)  # second call same cycle
        assert len(new_ids) == 0  # already pending, not promoted again

    def test_logs_promoted_event(self, db, scheduler):
        now = time.time()
        db.add_recurring_job("job1", "echo hi", 3600, next_run=now - 1)
        scheduler.promote_due_jobs(now)
        events = db.get_schedule_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "promoted"

    def test_skips_disabled_job(self, db, scheduler):
        now = time.time()
        db.add_recurring_job("job1", "echo hi", 3600, next_run=now - 1)
        db.set_recurring_job_enabled("job1", False)
        new_ids = scheduler.promote_due_jobs(now)
        assert len(new_ids) == 0


class TestUpdateNextRun:
    def test_sets_next_run_from_completion(self, db, scheduler):
        rj_id = db.add_recurring_job("job1", "echo hi", 3600)
        completed_at = time.time()
        scheduler.update_next_run(rj_id, completed_at, job_id=42)
        rj = db.get_recurring_job(rj_id)
        assert abs(rj["next_run"] - (completed_at + 3600)) < 0.01
        assert rj["last_run"] == completed_at
        assert rj["last_job_id"] == 42


class TestRebalance:
    def test_rebalance_spreads_evenly(self, db, scheduler):
        now = time.time()
        interval = 3600
        for i, name in enumerate(["a", "b", "c", "d"]):
            db.add_recurring_job(name, f"cmd_{name}", interval, priority=5, next_run=now)
        events = scheduler.rebalance(now)
        rjs = db.list_recurring_jobs()
        offsets = sorted(rj["next_run"] - now for rj in rjs)
        # Each offset should differ by ~interval/N = 900s
        gaps = [offsets[i+1] - offsets[i] for i in range(len(offsets)-1)]
        for gap in gaps:
            assert abs(gap - 900) < 1.0  # within 1 second

    def test_rebalance_respects_priority(self, db, scheduler):
        now = time.time()
        db.add_recurring_job("low", "cmd_low", 3600, priority=8, next_run=now)
        db.add_recurring_job("high", "cmd_high", 3600, priority=2, next_run=now)
        scheduler.rebalance(now)
        high = db.get_recurring_job_by_name("high")
        low = db.get_recurring_job_by_name("low")
        assert high["next_run"] < low["next_run"]

    def test_rebalance_logs_events(self, db, scheduler):
        now = time.time()
        db.add_recurring_job("a", "cmd_a", 3600, next_run=now)
        db.add_recurring_job("b", "cmd_b", 3600, next_run=now)
        events = scheduler.rebalance(now)
        db_events = db.get_schedule_events()
        assert any(e["event_type"] == "rebalanced" for e in db_events)
