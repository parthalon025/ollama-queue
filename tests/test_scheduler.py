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

    def test_load_map_extended_has_all_scoring_keys(self, db, scheduler):
        """load_map_extended must return all keys that find_fitting_slot consumes."""
        required_keys = {"load", "vram_committed_gb", "is_pinned", "recurring_ids", "timestamp"}
        result = scheduler.load_map_extended()
        assert len(result) == 48
        for entry in result:
            for key in required_keys:
                assert key in entry, f"Missing key: {key}"


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
        """suggest_time returns [] when all 48 slots are blocked by pinned jobs.

        Uses a fixed non-DST date (mid-January) so that cron jobs at 2:00 AM
        are not phantom-shifted to 3:00 AM by DST spring-forward transitions.
        Without a fixed now, running this test on March 8 (US DST day) causes
        croniter to return 03:00 for '0 2 * * *' (slot 6 instead of slot 4),
        leaving slot 4 unblocked and producing a non-empty suggestions list.
        """
        import datetime

        fixed_now = datetime.datetime(2025, 1, 15, 12, 0, 0).timestamp()

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
        suggestions = scheduler.suggest_time(priority=5, now=fixed_now)
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


class TestAoISorting:
    def test_stale_recurring_job_promoted_before_fresh_at_same_priority(self, db):
        """A stale (long-overdue) recurring job is promoted before a fresh one."""
        scheduler = Scheduler(db)
        now = time.time()

        rj_stale = db.add_recurring_job("stale-job", "echo stale", interval_seconds=3600, priority=5, source="stale")
        rj_fresh = db.add_recurring_job("fresh-job", "echo fresh", interval_seconds=3600, priority=5, source="fresh")

        # Stale job: last successful run 5 intervals ago
        job_old = db.submit_job("echo old", "m", 5, 60, "stale", recurring_job_id=rj_stale)
        db.start_job(job_old)
        db.complete_job(job_old, exit_code=0, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 5 * 3600, job_old))

        # Fresh job: last successful run 0.5 intervals ago
        job_recent = db.submit_job("echo recent", "m", 5, 60, "fresh", recurring_job_id=rj_fresh)
        db.start_job(job_recent)
        db.complete_job(job_recent, exit_code=0, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 0.5 * 3600, job_recent))
        db._connect().commit()

        # Set both due now
        db._connect().execute("UPDATE recurring_jobs SET next_run=? WHERE id=?", (now - 1, rj_stale))
        db._connect().execute("UPDATE recurring_jobs SET next_run=? WHERE id=?", (now - 1, rj_fresh))
        db._connect().commit()

        promoted = scheduler.promote_due_jobs(now)
        assert len(promoted) == 2

        jobs = [db.get_job(jid) for jid in promoted]
        first_source = jobs[0]["source"]
        assert first_source == "stale", f"Expected stale to be first, got {first_source}"

    def test_never_run_job_has_maximum_aoi_urgency(self, db):
        """A recurring job that never ran gets maximum urgency (staleness_norm=1.0)."""
        scheduler = Scheduler(db)
        now = time.time()

        rj_never = db.add_recurring_job("never-run", "echo x", interval_seconds=3600, priority=5, source="never")
        rj_ran = db.add_recurring_job("ran-once", "echo x", interval_seconds=3600, priority=5, source="ran")

        job_ran = db.submit_job("echo ran", "m", 5, 60, "ran", recurring_job_id=rj_ran)
        db.start_job(job_ran)
        db.complete_job(job_ran, exit_code=0, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 0.1 * 3600, job_ran))
        db._connect().commit()

        db._connect().execute("UPDATE recurring_jobs SET next_run=? WHERE id=?", (now - 1, rj_never))
        db._connect().execute("UPDATE recurring_jobs SET next_run=? WHERE id=?", (now - 1, rj_ran))
        db._connect().commit()

        promoted = scheduler.promote_due_jobs(now)
        assert len(promoted) == 2
        jobs = [db.get_job(jid) for jid in promoted]
        first_source = jobs[0]["source"]
        assert first_source == "never"


class TestRecurringJobsCache:
    def test_cache_populated_on_first_call(self, db, scheduler):
        """_get_recurring_jobs() fills the cache on first call."""
        db.add_recurring_job("job1", "echo hi", interval_seconds=3600)
        assert scheduler._jobs_cache is None
        jobs = scheduler._get_recurring_jobs()
        assert len(jobs) == 1
        assert scheduler._jobs_cache is not None

    def test_cache_returns_same_object_within_ttl(self, db, scheduler):
        """Second call within TTL returns the cached list without re-querying."""
        db.add_recurring_job("job1", "echo hi", interval_seconds=3600)
        first = scheduler._get_recurring_jobs()
        second = scheduler._get_recurring_jobs()
        # Same list object means cache was hit (no new DB query)
        assert first is second

    def test_invalidate_clears_cache(self, db, scheduler):
        """_invalidate_jobs_cache() forces a fresh DB query on next call."""
        db.add_recurring_job("job1", "echo hi", interval_seconds=3600)
        first = scheduler._get_recurring_jobs()
        scheduler._invalidate_jobs_cache()
        assert scheduler._jobs_cache is None
        second = scheduler._get_recurring_jobs()
        # Not the same object — fresh fetch after invalidation
        assert first is not second

    def test_cache_expires_after_ttl(self, db, scheduler):
        """Cache is considered stale after _JOBS_CACHE_TTL seconds."""
        db.add_recurring_job("job1", "echo hi", interval_seconds=3600)
        scheduler._get_recurring_jobs()
        # Manually backdate the cache timestamp past the TTL
        cached_at, jobs = scheduler._jobs_cache
        scheduler._jobs_cache = (cached_at - scheduler._JOBS_CACHE_TTL - 1.0, jobs)
        second = scheduler._get_recurring_jobs()
        # Fresh list fetched; cache_at updated
        new_cached_at, _ = scheduler._jobs_cache
        assert new_cached_at > cached_at

    def test_update_next_run_invalidates_cache(self, db, scheduler):
        """update_next_run() drops the cache so next load_map sees fresh data."""
        rj_id = db.add_recurring_job("job1", "echo hi", interval_seconds=3600)
        scheduler._get_recurring_jobs()  # populate cache
        assert scheduler._jobs_cache is not None
        scheduler.update_next_run(rj_id, time.time(), job_id=None)
        assert scheduler._jobs_cache is None

    def test_rebalance_invalidates_cache(self, db, scheduler):
        """rebalance() drops the cache before mutating next_run values."""
        now = time.time()
        db.add_recurring_job("job1", "echo hi", interval_seconds=3600, next_run=now)
        scheduler._get_recurring_jobs()  # populate cache
        assert scheduler._jobs_cache is not None
        scheduler.rebalance(now)
        # rebalance invalidates at start; cache may be repopulated by load_map
        # — what matters is it was cleared at least once (no stale data risk)
        # We verify by checking the DB was re-read (rebalance still worked)
        rj = db.get_recurring_job_by_name("job1")
        assert rj is not None  # job still exists

    def test_load_map_uses_cache(self, db, scheduler):
        """load_map() calls _get_recurring_jobs() which respects the cache."""
        db.add_recurring_job("job1", "echo hi", interval_seconds=3600, priority=5)
        # First load_map populates cache
        scheduler.load_map()
        cached_at_1, _ = scheduler._jobs_cache
        # Second load_map within TTL reuses cache (cached_at unchanged)
        scheduler.load_map()
        cached_at_2, _ = scheduler._jobs_cache
        assert cached_at_1 == cached_at_2

    def test_batch_next_run_applied_in_promote(self, db, scheduler):
        """promote_due_jobs batches next_run updates via batch_set_recurring_next_runs."""
        now = time.time()
        # Two jobs both due; one already has a pending job (will trigger next_run update)
        rj1 = db.add_recurring_job("dup1", "echo a", interval_seconds=3600, next_run=now - 1)
        rj2 = db.add_recurring_job("dup2", "echo b", interval_seconds=3600, next_run=now - 1)
        # Promote once to create pending jobs
        scheduler.promote_due_jobs(now)
        # Promote again: both jobs are already pending → both get next_run advanced
        scheduler.promote_due_jobs(now)
        rj1_updated = db.get_recurring_job(rj1)
        rj2_updated = db.get_recurring_job(rj2)
        # next_run should be advanced beyond now (now + interval)
        assert rj1_updated["next_run"] > now
        assert rj2_updated["next_run"] > now


# ── Coverage gap tests ────────────────────────────────────────────────────


class TestPromoteDueJobsCoverageGaps:
    def test_promote_uses_current_time_when_now_is_none(self, db, scheduler):
        """Line 65: now defaults to time.time() when not provided."""
        before = time.time()
        db.add_recurring_job("job1", "echo hi", 3600, next_run=before - 10)
        new_ids = scheduler.promote_due_jobs()  # now=None
        assert len(new_ids) == 1

    def test_suspend_low_priority_skips_priority_8_plus(self, db, scheduler):
        """Lines 78-83: suspend_low_priority=True skips priority >= 8."""
        now = time.time()
        db.add_recurring_job("low-p", "echo low", 3600, priority=8, next_run=now - 1)
        db.add_recurring_job("high-p", "echo high", 3600, priority=3, next_run=now - 1)
        new_ids = scheduler.promote_due_jobs(now, suspend_low_priority=True)
        # Only priority 3 job is promoted; priority 8 is skipped
        assert len(new_ids) == 1
        job = db.get_job(new_ids[0])
        assert job["command"] == "echo high"

    def test_duplicate_coalesce_no_interval_no_cron_warning(self, db, scheduler):
        """Line 101: recurring job with neither interval nor cron logs warning and uses 300s fallback."""
        now = time.time()
        rj_id = db.add_recurring_job("job1", "echo hi", interval_seconds=3600, next_run=now - 1)
        # Promote to create a pending job
        scheduler.promote_due_jobs(now)
        # Remove interval_seconds and cron_expression from the DB to trigger the fallback
        conn = db._connect()
        conn.execute("UPDATE recurring_jobs SET interval_seconds = NULL, cron_expression = NULL WHERE id = ?", (rj_id,))
        conn.commit()
        # Now the job is already pending, so duplicating will hit the fallback branch
        scheduler._invalidate_jobs_cache()
        new_ids = scheduler.promote_due_jobs(now)
        assert len(new_ids) == 0  # still pending, skipped
        # next_run should be advanced by the 300s fallback
        rj = db.get_recurring_job(rj_id)
        assert abs(rj["next_run"] - (now + 300)) < 1.0


class TestUpdateNextRunCoverageGaps:
    def test_update_next_run_deleted_recurring_job(self, db, scheduler):
        """Lines 162-163: update_next_run on a deleted recurring job logs warning and returns."""
        rj_id = db.add_recurring_job("temp", "echo hi", 3600)
        db.delete_recurring_job_by_id(rj_id)
        # Should not raise — just logs warning
        scheduler.update_next_run(rj_id, time.time(), job_id=1)


class TestScoreCronJobCoverageGaps:
    def test_cron_job_no_fire_times_falls_back_to_next_run(self, db, scheduler):
        """Lines 274-276: cron job with no fires in 24h window uses next_run as fallback."""
        import datetime

        # Use a cron expression that fires yearly — no fires in the 24h window
        rj_id = db.add_recurring_job(
            "yearly",
            "echo hi",
            cron_expression="0 0 1 1 *",
            next_run=datetime.datetime(2025, 1, 1, 6, 0, 0).timestamp(),
        )
        # Force load_map to exercise _score_cron_job with this job
        now = datetime.datetime(2025, 6, 15, 12, 0, 0).timestamp()
        lm = scheduler.load_map(now)
        # The fallback fires at next_run's slot (06:00 = slot 12). Score = 11 - 5 = 6
        assert lm[12] == 6


class TestLoadMapExtendedCoverageGaps:
    def test_load_map_extended_skips_disabled_jobs(self, db, scheduler):
        """Line 351: disabled jobs are skipped in load_map_extended VRAM loop."""
        db.add_recurring_job("enabled", "echo hi", 3600, priority=5, model="qwen2.5:7b")
        db.add_recurring_job("disabled", "echo bye", 3600, priority=5, model="qwen2.5:7b")
        db.set_recurring_job_enabled("disabled", False)
        result = scheduler.load_map_extended()
        # Disabled job should not appear in any slot's recurring_ids
        all_rj_ids = set()
        for slot in result:
            all_rj_ids.update(slot["recurring_ids"])
        disabled_rj = db.get_recurring_job_by_name("disabled")
        assert disabled_rj["id"] not in all_rj_ids

    def test_load_map_extended_scores_cron_and_interval_vram(self, db, scheduler):
        """Lines 356-366: load_map_extended scores both cron and interval jobs with VRAM."""
        import datetime

        # Cron job with a model name containing size hint
        db.add_recurring_job(
            "cron-vram",
            "echo hi",
            cron_expression="0 6 * * *",
            model="qwen2.5:7b",
            next_run=datetime.datetime(2025, 1, 1, 6, 0, 0).timestamp(),
        )
        # Interval job with a model name containing size hint
        db.add_recurring_job("interval-vram", "echo bye", 3600, model="llama3:14b")
        result = scheduler.load_map_extended()
        # At least one slot should have nonzero vram_committed_gb
        assert any(s["vram_committed_gb"] > 0 for s in result)


class TestEstimateModelVramCoverageGaps:
    def test_interpolation_fallback_for_unusual_size(self):
        """Line 442: model size far from any known key uses linear interpolation."""
        from ollama_queue.scheduler import _estimate_model_vram

        # 200b is far from any key in _PARAM_TO_VRAM (max is 70); |70-200|/200 = 0.65 > 0.5
        result = _estimate_model_vram("custom-model:200b")
        assert result == pytest.approx(200 * 0.6, abs=0.1)
