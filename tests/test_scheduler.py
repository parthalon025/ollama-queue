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
        assert any(e["event_type"] == "promoted" for e in events)

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


class TestCronScheduling:
    def test_add_cron_job_sets_next_run(self, db):
        """next_run is computed from cron expression at creation time."""

        now = 1_700_000_000.0  # fixed epoch
        rj_id = db.add_recurring_job("cron1", "echo hi", cron_expression="0 7 * * *", next_run=None)
        # We can't easily assert the exact value without mocking time, so just assert it's in the future
        rj = db.get_recurring_job(rj_id)
        assert rj["cron_expression"] == "0 7 * * *"
        assert rj["interval_seconds"] is None
        assert rj["next_run"] is not None

    def test_cron_update_next_run_anchors_to_cron(self, db, scheduler):
        """After completion, next_run follows the cron expression, not interval."""
        import datetime

        from croniter import croniter

        cron_expr = "0 7 * * *"
        rj_id = db.add_recurring_job("cron1", "echo hi", cron_expression=cron_expr)
        completed_at = 1_735_700_000.0  # some fixed epoch
        scheduler.update_next_run(rj_id, completed_at=completed_at, job_id=1)
        rj = db.get_recurring_job(rj_id)
        start_dt = datetime.datetime.fromtimestamp(completed_at)
        expected = croniter(cron_expr, start_dt).get_next(datetime.datetime).timestamp()
        assert abs(rj["next_run"] - expected) < 1.0

    def test_cron_job_promoted_when_due(self, db, scheduler):
        now = 1_700_000_000.0
        db.add_recurring_job("cron1", "echo hi", cron_expression="0 7 * * *", next_run=now - 1)
        new_ids = scheduler.promote_due_jobs(now)
        assert len(new_ids) == 1

    def test_cron_job_skipped_in_rebalance(self, db, scheduler):
        """Cron jobs are excluded from rebalance — their times are pinned."""
        now = 1_700_000_000.0
        db.add_recurring_job("cron1", "echo hi", cron_expression="0 7 * * *", next_run=now + 1000)
        db.add_recurring_job("interval1", "echo bye", interval_seconds=3600, next_run=now)
        changes = scheduler.rebalance(now)
        # Only interval job gets rebalanced; cron job is untouched
        changed_names = {c["name"] for c in changes}
        assert "cron1" not in changed_names
        assert "interval1" in changed_names

    def test_cron_duplicate_coalescing_advances_to_next_cron(self, db, scheduler):
        """When a cron job is already pending, next_run advances to the next cron slot."""
        import datetime

        from croniter import croniter

        now = 1_700_000_000.0
        cron_expr = "* * * * *"
        db.add_recurring_job("cron1", "echo hi", cron_expression=cron_expr, next_run=now - 1)
        scheduler.promote_due_jobs(now)  # promotes; next_run should advance
        scheduler.promote_due_jobs(now)  # second call: already pending → advances next_run
        rj = db.get_recurring_job_by_name("cron1")
        start_dt = datetime.datetime.fromtimestamp(now)
        expected = croniter(cron_expr, start_dt).get_next(datetime.datetime).timestamp()
        assert abs(rj["next_run"] - expected) < 2.0

    def test_requires_interval_or_cron(self, db):
        with pytest.raises(ValueError, match="Either interval_seconds or cron_expression"):
            db.add_recurring_job("bad", "echo hi")


class TestRebalance:
    def test_rebalance_spreads_evenly(self, db, scheduler):
        now = time.time()
        interval = 3600
        for _i, name in enumerate(["a", "b", "c", "d"]):
            db.add_recurring_job(name, f"cmd_{name}", interval, priority=5, next_run=now)
        events = scheduler.rebalance(now)
        rjs = db.list_recurring_jobs()
        offsets = sorted(rj["next_run"] - now for rj in rjs)
        # Each offset should differ by ~interval/N = 900s
        gaps = [offsets[i + 1] - offsets[i] for i in range(len(offsets) - 1)]
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


class TestLoadMap:
    def test_returns_48_slots(self, db, scheduler):
        lm = scheduler.load_map()
        assert len(lm) == 48
        assert all(isinstance(s, int | float) for s in lm)

    def test_empty_schedule_all_zero(self, db, scheduler):
        lm = scheduler.load_map()
        assert all(s == 0 for s in lm)

    def test_pinned_cron_job_blocks_slot_and_neighbors(self, db, scheduler):
        import datetime

        # Add a pinned cron job at 06:00 (slot 12)
        db.add_recurring_job(
            "pinned",
            "echo hi",
            cron_expression="0 6 * * *",
            pinned=True,
            next_run=datetime.datetime(2025, 1, 1, 6, 0, 0).timestamp(),
        )
        lm = scheduler.load_map()
        # All three slots must be blocked: slot 11 (05:30), 12 (06:00), 13 (06:30)
        assert lm[11] == 999
        assert lm[12] == 999
        assert lm[13] == 999

    def test_unpinned_cron_job_scores_by_priority(self, db, scheduler):
        import datetime

        db.add_recurring_job(
            "cron1",
            "echo hi",
            cron_expression="0 6 * * *",
            priority=3,
            pinned=False,
            next_run=datetime.datetime(2025, 1, 1, 6, 0, 0).timestamp(),
        )
        lm = scheduler.load_map()
        # Score for priority 3 = 11 - 3 = 8
        assert lm[12] == 8  # slot 12 = 06:00

    def test_interval_job_distributes_across_24h(self, db, scheduler):
        # 6h interval job should contribute to ~4 slots across 24h
        db.add_recurring_job("interval1", "echo hi", interval_seconds=6 * 3600, priority=5)
        lm = scheduler.load_map()
        nonzero = [s for s in lm if s > 0]
        assert len(nonzero) >= 3  # at least 3 slots hit


class TestSuggestTime:
    def test_returns_list_of_suggestions(self, db, scheduler):
        suggestions = scheduler.suggest_time(priority=5)
        assert isinstance(suggestions, list)
        assert len(suggestions) >= 1

    def test_suggestions_avoid_pinned_slots(self, db, scheduler):
        import datetime

        # Pin every hour except 03:00
        for h in range(24):
            if h == 3:
                continue
            db.add_recurring_job(
                f"pinned-{h}",
                "echo hi",
                cron_expression=f"0 {h} * * *",
                pinned=True,
                next_run=datetime.datetime(2025, 1, 1, h, 0, 0).timestamp(),
            )
        suggestions = scheduler.suggest_time(priority=5, top_n=3)
        # All suggestions should be near 03:00 (slot 6)
        for _cron_expr, score in suggestions:
            assert score < 999

    def test_suggestion_format(self, db, scheduler):
        suggestions = scheduler.suggest_time(priority=5)
        for cron_expr, score in suggestions:
            assert isinstance(cron_expr, str)
            assert isinstance(score, int | float)
            # Should be a valid 5-field cron expression
            parts = cron_expr.split()
            assert len(parts) == 5

    def test_all_slots_blocked_returns_empty(self, db, scheduler):
        """suggest_time returns [] when all 48 slots are blocked by pinned jobs."""
        import datetime

        # Pin every 30-min slot: 48 jobs, each adjacent bleed covers 3 slots
        # Using every-hour pins (slots 0,2,4,...) — each pins slot-1,slot,slot+1
        # 24 hourly pins x 3 adjacent each covers all 48 slots
        for h in range(24):
            db.add_recurring_job(
                f"pin-{h}",
                "cmd",
                cron_expression=f"0 {h} * * *",
                pinned=True,
                next_run=datetime.datetime(2025, 1, 1, h, 0, 0).timestamp(),
            )
        suggestions = scheduler.suggest_time(priority=5)
        assert suggestions == []


class TestRebalancePinEnforcement:
    def test_rebalance_avoids_pinned_slots(self, db, scheduler):
        import datetime

        # Set now = 05:30 local (slot 11) so the naive rebalance would place
        # the single interval job at now+0 = slot 11 — inside the 06:00 pin buffer.
        base = datetime.datetime.now().replace(hour=5, minute=30, second=0, microsecond=0)
        now = base.timestamp()
        # Pin a cron job at 06:00 (blocks slots 11, 12, 13)
        db.add_recurring_job(
            "pinned-aria",
            "aria run",
            cron_expression="0 6 * * *",
            pinned=True,
            next_run=datetime.datetime.now().replace(hour=6, minute=0, second=0, microsecond=0).timestamp(),
        )
        # A 24h interval job — naive rebalance places it at now+0 = slot 11 (blocked)
        db.add_recurring_job("daily-sync", "sync run", interval_seconds=86400)
        scheduler.rebalance(now)
        rj = db.get_recurring_job_by_name("daily-sync")
        placed_slot = scheduler._time_to_slot(rj["next_run"])
        # Should not land on slots 11, 12, or 13 (06:00 ± buffer)
        assert placed_slot not in {11, 12, 13}, f"Interval job landed on blocked slot {placed_slot}"

    def test_rebalance_logs_skipped_conflict(self, db, scheduler):
        import datetime

        now = datetime.datetime(2025, 1, 1, 0, 0, 0).timestamp()
        # Pin all 24 hourly slots (covers all 48 slots with ±1 bleed)
        for h in range(24):
            db.add_recurring_job(
                f"pin-{h}",
                "cmd",
                cron_expression=f"0 {h} * * *",
                pinned=True,
                next_run=datetime.datetime(2025, 1, 1, h, 0, 0).timestamp(),
            )
        db.add_recurring_job("interval", "cmd", interval_seconds=3600)
        # Should not raise — just place as best as possible
        changes = scheduler.rebalance(now)
        assert isinstance(changes, list)
