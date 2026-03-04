# PR 3 — Scheduling Intelligence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the naive FCFS+embed tiebreaker with SJF + aging + risk-adjusted estimation. Add variance tracking to DurationEstimator. Add Age of Information (AoI) tiebreaker for recurring job promotion.

**Architecture:** Changes in `db.py` (new query methods), `estimator.py` (variance method), `daemon.py` (new `_dequeue_next_job()`), and `scheduler.py` (AoI sort). PR 2 must be merged first.

**Tech Stack:** Python 3.12, sqlite3, threading.RLock (existing)

**Design doc:** `docs/plans/2026-03-04-queue-optimization-design.md` §PR3

**Prerequisite:** PR 1 must be merged (needs `last_retry_delay` migration infrastructure). PR 2 can be merged in parallel.

**Quality Gates:**
- `pytest --timeout=120 -x -q` — must pass before every commit
- `make lint` — must pass before every commit

---

## Task 1: DB methods — `estimate_duration_bulk()` and `estimate_duration_stats()`

**Files:**
- Modify: `ollama_queue/db.py` (add two methods after `estimate_duration()`)
- Test: `tests/test_db.py`

### Step 1: Write failing tests

In `tests/test_db.py`, add a new test class:

```python
class TestDurationBulkAndStats:
    def test_estimate_duration_bulk_empty_sources(self, db):
        """Returns empty dict for empty source list."""
        result = db.estimate_duration_bulk([])
        assert result == {}

    def test_estimate_duration_bulk_returns_avg_per_source(self, db):
        """Returns mean of successful runs per source in one query."""
        import time
        now = time.time()
        # Source A: two successful runs, avg = 300
        db._connect().execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("src-a", "m", 200.0, 0, now)
        )
        db._connect().execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("src-a", "m", 400.0, 0, now)
        )
        db._connect().commit()
        result = db.estimate_duration_bulk(["src-a"])
        assert abs(result["src-a"] - 300.0) < 0.1

    def test_estimate_duration_bulk_excludes_failed_runs(self, db):
        """Only counts exit_code=0 runs in the average."""
        import time
        now = time.time()
        db._connect().execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("src-b", "m", 1000.0, 1, now)  # failed — should be excluded
        )
        db._connect().execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("src-b", "m", 100.0, 0, now)  # success
        )
        db._connect().commit()
        result = db.estimate_duration_bulk(["src-b"])
        assert abs(result["src-b"] - 100.0) < 0.1

    def test_estimate_duration_stats_returns_mean_and_variance(self, db):
        """Returns (mean, variance) tuple from last 10 successful runs."""
        import time
        now = time.time()
        durations = [100.0, 200.0, 300.0]
        for d in durations:
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("stats-src", "m", d, 0, now)
            )
        db._connect().commit()
        result = db.estimate_duration_stats("stats-src")
        assert result is not None
        mean, variance = result
        assert abs(mean - 200.0) < 0.1  # (100+200+300)/3 = 200
        assert variance > 0  # non-zero variance for these three values

    def test_estimate_duration_stats_returns_none_for_missing_source(self, db):
        """Returns None when no history exists for source."""
        result = db.estimate_duration_stats("nonexistent-source")
        assert result is None
```

### Step 2: Run to verify failure

```bash
cd ~/Documents/projects/ollama-queue && source .venv/bin/activate
pytest tests/test_db.py::TestDurationBulkAndStats -v
```

Expected: FAIL — `AttributeError: 'Database' object has no attribute 'estimate_duration_bulk'`

### Step 3: Implement

In `ollama_queue/db.py`, add after `estimate_duration()`:

```python
def estimate_duration_bulk(self, sources: list[str]) -> dict[str, float]:
    """Return mean duration per source in a single query.

    Only counts successful runs (exit_code=0). Used by SJF sort to avoid
    N separate DB queries per dequeue cycle.
    """
    if not sources:
        return {}
    conn = self._connect()
    placeholders = ",".join("?" * len(sources))
    rows = conn.execute(
        f"""SELECT source, AVG(duration) as avg_dur
            FROM duration_history
            WHERE source IN ({placeholders}) AND exit_code = 0
            GROUP BY source""",
        sources,
    ).fetchall()
    return {row["source"]: row["avg_dur"] for row in rows if row["avg_dur"] is not None}

def estimate_duration_stats(self, source: str) -> tuple[float, float] | None:
    """Return (mean, variance) from last 10 successful runs for a source.

    Uses the computational formula: Var = E[X^2] - E[X]^2
    Returns None if no history exists.
    Used by estimate_with_variance() for risk-adjusted SJF sort.
    """
    conn = self._connect()
    row = conn.execute(
        """SELECT AVG(duration) as mean_dur,
                  AVG(duration * duration) - AVG(duration) * AVG(duration) as variance
           FROM (
               SELECT duration FROM duration_history
               WHERE source = ? AND exit_code = 0
               ORDER BY recorded_at DESC
               LIMIT 10
           )""",
        (source,),
    ).fetchone()
    if row is None or row["mean_dur"] is None:
        return None
    return float(row["mean_dur"]), float(row["variance"] or 0.0)
```

### Step 4: Run tests

```bash
pytest tests/test_db.py -x -q
```

Expected: all tests pass

### Step 5: Commit

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat: add estimate_duration_bulk() and estimate_duration_stats() for SJF bulk queries"
```

---

## Task 2: `estimate_with_variance()` in DurationEstimator

**Files:**
- Modify: `ollama_queue/estimator.py` (add method after `estimate()`)
- Test: `tests/test_estimator.py`

### Step 1: Write failing tests

In `tests/test_estimator.py`:

```python
def test_estimate_with_variance_returns_tuple(self, db):
    """Returns (mean, cv_squared) tuple."""
    from ollama_queue.estimator import DurationEstimator
    estimator = DurationEstimator(db)
    mean, cv_sq = estimator.estimate_with_variance("unknown-src")
    assert isinstance(mean, float) and mean > 0
    assert isinstance(cv_sq, float) and cv_sq >= 0

def test_estimate_with_variance_uses_db_stats(self, db):
    """Uses actual duration history when available."""
    import time
    from ollama_queue.estimator import DurationEstimator
    now = time.time()
    # Insert predictable history: all same duration → variance = 0
    for _ in range(5):
        db._connect().execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("uniform-src", "m", 600.0, 0, now)
        )
    db._connect().commit()
    estimator = DurationEstimator(db)
    mean, cv_sq = estimator.estimate_with_variance("uniform-src")
    assert abs(mean - 600.0) < 1.0
    assert cv_sq < 0.01  # near-zero variance → near-zero cv_sq

def test_estimate_with_variance_cv_sq_high_for_mixed_durations(self, db):
    """High variance history produces cv_squared > 1.0."""
    import time
    from ollama_queue.estimator import DurationEstimator
    now = time.time()
    # Very mixed durations: 100, 1000, 100, 1000 (high variance)
    for d in [100.0, 1000.0, 100.0, 1000.0]:
        db._connect().execute(
            "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
            ("mixed-src", "m", d, 0, now)
        )
    db._connect().commit()
    estimator = DurationEstimator(db)
    _, cv_sq = estimator.estimate_with_variance("mixed-src")
    assert cv_sq > 0.5  # high variance relative to mean

def test_estimate_with_variance_uses_cached_mean(self, db):
    """Uses cached bulk dict for mean when no db stats available."""
    from ollama_queue.estimator import DurationEstimator
    estimator = DurationEstimator(db)
    cached = {"cached-src": 300.0}
    mean, cv_sq = estimator.estimate_with_variance("cached-src", cached=cached)
    assert mean == 300.0
    assert cv_sq == 1.5  # unknown variance default

def test_estimate_with_variance_default_cv_sq_is_conservative(self, db):
    """Returns cv_squared=1.5 when no history exists (conservative default)."""
    from ollama_queue.estimator import DurationEstimator
    estimator = DurationEstimator(db)
    _, cv_sq = estimator.estimate_with_variance("no-history-src")
    assert cv_sq == 1.5
```

### Step 2: Run to verify failure

```bash
pytest tests/test_estimator.py -v
```

Expected: FAIL — `AttributeError: 'DurationEstimator' object has no attribute 'estimate_with_variance'`

### Step 3: Implement

In `ollama_queue/estimator.py`, add after `estimate()`:

```python
def estimate_with_variance(
    self,
    source: str,
    model: str | None = None,
    cached: dict | None = None,
) -> tuple[float, float]:
    """Return (mean_seconds, cv_squared) for a source.

    cv_squared = Var(S) / Mean(S)^2

    Interpretation guide:
      cv_squared < 0.5:  highly predictable (same model, consistent workload)
      cv_squared 0.5-1.5: normal variance
      cv_squared > 1.5:  unreliable estimate (mixed workloads, treat skeptically)

    Falls back to cached bulk mean, then model default, then GENERIC_DEFAULT.
    Returns cv_squared=1.5 (conservative) when no variance data available.
    """
    stats = self.db.estimate_duration_stats(source)
    if stats is not None:
        mean, variance = stats
        if mean > 0:
            cv_sq = variance / (mean ** 2)
            return mean, max(0.0, cv_sq)

    # No variance data — fall back to mean only
    mean = None
    if cached:
        mean = cached.get(source)
    if mean is None:
        mean = self.db.estimate_duration(source)
    if mean is None:
        if model:
            for key, default in self.MODEL_DEFAULTS.items():
                if key in model:
                    mean = float(default)
                    break
    if mean is None:
        mean = float(self.GENERIC_DEFAULT)

    return mean, 1.5  # unknown variance → conservative default
```

### Step 4: Run tests

```bash
pytest tests/test_estimator.py -x -q
```

Expected: all tests pass

### Step 5: Commit

```bash
git add ollama_queue/estimator.py tests/test_estimator.py
git commit -m "feat: add estimate_with_variance() to DurationEstimator (cv_squared for risk-adjusted SJF)"
```

---

## Task 3: SJF Sort with Aging in `_dequeue_next_job()`

**Files:**
- Modify: `ollama_queue/daemon.py` (add `_dequeue_next_job()`, modify `poll_once()`)
- Add new DEFAULTS entry (add `sjf_aging_factor` to db.py)
- Test: `tests/test_daemon.py`

### Step 1: Add DEFAULTS entry

In `ollama_queue/db.py`, add to DEFAULTS:

```python
"sjf_aging_factor": 3600,  # PR3: seconds of wait to halve effective duration; 0=pure SJF
```

### Step 2: Write failing tests

In `tests/test_daemon.py`:

```python
class TestSJFDequeue:
    def test_sjf_shorter_job_dequeued_first_at_same_priority(self, db):
        """Shorter estimated job is dequeued before longer at same priority."""
        import time
        from ollama_queue.daemon import Daemon
        from unittest.mock import patch
        daemon = Daemon(db)

        now = time.time()
        # Submit two jobs at same priority; seed duration history
        job_long = db.submit_job("echo long", "m", 5, 600, "long-src")
        job_short = db.submit_job("echo short", "m", 5, 600, "short-src")

        # Seed history: long-src=900s, short-src=120s
        for _ in range(3):
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("long-src", "m", 900.0, 0, now - 10)
            )
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("short-src", "m", 120.0, 0, now - 10)
            )
        db._connect().commit()

        pending = db.get_pending_jobs()
        estimates = db.estimate_duration_bulk([j["source"] for j in pending])
        result = daemon._dequeue_next_job(pending, estimates, now)
        assert result is not None
        assert result["id"] == job_short

    def test_sjf_priority_still_primary_sort_key(self, db):
        """Priority 1 job is dequeued before priority 5, even if longer."""
        import time
        from ollama_queue.daemon import Daemon
        daemon = Daemon(db)

        now = time.time()
        job_p1 = db.submit_job("echo p1 long", "m", 1, 600, "p1-src")
        job_p5 = db.submit_job("echo p5 short", "m", 5, 600, "p5-src")

        for _ in range(3):
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("p1-src", "m", 900.0, 0, now - 10)
            )
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("p5-src", "m", 60.0, 0, now - 10)
            )
        db._connect().commit()

        pending = db.get_pending_jobs()
        estimates = db.estimate_duration_bulk([j["source"] for j in pending])
        result = daemon._dequeue_next_job(pending, estimates, now)
        assert result is not None
        assert result["id"] == job_p1

    def test_aging_promotes_long_waiting_job(self, db):
        """Long-waiting job effective duration decreases over time (prevents starvation)."""
        import time
        from ollama_queue.daemon import Daemon
        daemon = Daemon(db)
        db.set_setting("sjf_aging_factor", 3600)

        # A job that waited 2 hours has effective_duration / (1 + 7200/3600) = /3
        # 600s / 3 = 200s effective — now competes with 180s job
        now = time.time()
        job_waited = db.submit_job("echo waited", "m", 5, 600, "slow-src")
        db._connect().execute(
            "UPDATE jobs SET submitted_at = ? WHERE id = ?",
            (now - 7200, job_waited)  # submitted 2 hours ago
        )
        job_fresh = db.submit_job("echo fresh", "m", 5, 600, "fast-src")

        for _ in range(3):
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("slow-src", "m", 600.0, 0, now - 10)  # 600s base
            )
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("fast-src", "m", 250.0, 0, now - 10)  # 250s base
            )
        db._connect().commit()

        pending = db.get_pending_jobs()
        estimates = db.estimate_duration_bulk([j["source"] for j in pending])
        result = daemon._dequeue_next_job(pending, estimates, now)
        # With aging: slow-src effective = 600/(1+7200/3600) = 200s < 250s
        # So waited job should be picked first
        assert result is not None
        assert result["id"] == job_waited

    def test_dequeue_returns_none_when_no_jobs(self, db):
        """Returns None when pending list is empty."""
        from ollama_queue.daemon import Daemon
        import time
        daemon = Daemon(db)
        result = daemon._dequeue_next_job([], {}, time.time())
        assert result is None
```

### Step 3: Run to verify failure

```bash
pytest tests/test_daemon.py::TestSJFDequeue -v
```

Expected: FAIL — `AttributeError: 'Daemon' object has no attribute '_dequeue_next_job'`

### Step 4: Implement

Add `_dequeue_next_job()` to `Daemon` class in `ollama_queue/daemon.py`:

```python
def _dequeue_next_job(
    self,
    pending: list[dict],
    estimates: dict[str, float],
    now: float,
) -> dict | None:
    """Return the highest-priority pending job using SJF + aging sort.

    Sort key: (priority, effective_duration) where:
      effective_duration = risk_adjusted / (1 + wait/aging_factor)
      risk_adjusted = mean + 0.5 * std_dev  (penalizes high-variance estimates)
      aging_factor from settings (default 3600s = 1 hour)

    Only returns jobs whose retry_after has elapsed (or is NULL).
    Embed-profile jobs are handled by _can_admit() — they don't participate
    in this sort.
    """
    if not pending:
        return None

    aging_factor = float(self.db.get_setting("sjf_aging_factor") or 3600)

    def sort_key(j: dict) -> tuple:
        duration, cv_sq = self.estimator.estimate_with_variance(
            j["source"],
            model=j.get("model"),
            cached=estimates,
        )
        std_dev = duration * (cv_sq ** 0.5)
        risk_adjusted = duration + 0.5 * std_dev

        wait = now - (j.get("submitted_at") or now)
        if aging_factor > 0 and wait > 0:
            effective = risk_adjusted / (1.0 + wait / aging_factor)
        else:
            effective = risk_adjusted

        return (j["priority"], effective)

    # Filter out jobs still in backoff
    eligible = [
        j for j in pending
        if j.get("retry_after") is None or j["retry_after"] <= now
    ]
    if not eligible:
        return None

    eligible.sort(key=sort_key)
    return eligible[0]
```

Modify `poll_once()` to use `_dequeue_next_job()` instead of `db.get_next_job()`. In the existing `poll_once()`, step 4 currently calls `self.db.get_next_job()`. Change it to:

```python
# Step 4: Get next pending job — SJF + aging sort
# pending_jobs was already fetched in step 3 for health logging
estimates = self.db.estimate_duration_bulk([j["source"] for j in pending_jobs])
job = self._dequeue_next_job(pending_jobs, estimates, now)
```

This removes the call to `self.db.get_next_job()` in `poll_once()`.

### Step 5: Run full test suite

```bash
pytest --timeout=120 -x -q
```

Expected: all tests pass

### Step 6: Commit

```bash
git add ollama_queue/db.py ollama_queue/daemon.py tests/test_daemon.py
git commit -m "feat: SJF dequeue with aging (risk-adjusted sort, starvation prevention)"
```

---

## Task 4: Age of Information Tiebreaker for Recurring Jobs

**Files:**
- Modify: `ollama_queue/db.py` (add `get_last_successful_run_time()`, new DEFAULTS entry)
- Modify: `ollama_queue/scheduler.py` (sort `due` list with AoI before promoting)
- Test: `tests/test_scheduler.py`

### Step 1: Add DEFAULTS entry

In `ollama_queue/db.py`, add to DEFAULTS:

```python
"aoi_weight": 0.3,  # PR3: fraction of scheduling score from information staleness (0=pure priority, 1=pure AoI)
```

### Step 2: Write failing tests

In `tests/test_db.py`:

```python
class TestLastSuccessfulRunTime:
    def test_returns_none_for_no_history(self, db):
        """Returns None when recurring job has never completed successfully."""
        rj_id = db.add_recurring_job("test-job", "echo test", interval_seconds=3600)
        result = db.get_last_successful_run_time(rj_id)
        assert result is None

    def test_returns_max_completed_at_for_successful_runs(self, db):
        """Returns timestamp of most recent successful completion."""
        import time
        rj_id = db.add_recurring_job("test-job", "echo test", interval_seconds=3600)
        now = time.time()
        # Two jobs: older failure, newer success
        job1 = db.submit_job("echo 1", "m", 5, 60, "s", recurring_job_id=rj_id)
        db.start_job(job1)
        db.complete_job(job1, exit_code=1, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 100, job1))

        job2 = db.submit_job("echo 2", "m", 5, 60, "s", recurring_job_id=rj_id)
        db.start_job(job2)
        db.complete_job(job2, exit_code=0, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 10, job2))
        db._connect().commit()

        result = db.get_last_successful_run_time(rj_id)
        assert result is not None
        assert abs(result - (now - 10)) < 1.0

    def test_ignores_failed_runs(self, db):
        """exit_code != 0 runs do not count as last successful run."""
        import time
        rj_id = db.add_recurring_job("test-job", "echo test", interval_seconds=3600)
        now = time.time()
        job1 = db.submit_job("echo 1", "m", 5, 60, "s", recurring_job_id=rj_id)
        db.start_job(job1)
        db.complete_job(job1, exit_code=1, stdout_tail="", stderr_tail="")
        db._connect().execute("UPDATE jobs SET completed_at=? WHERE id=?", (now - 5, job1))
        db._connect().commit()

        result = db.get_last_successful_run_time(rj_id)
        assert result is None
```

In `tests/test_scheduler.py`:

```python
class TestAoISorting:
    def test_stale_recurring_job_promoted_before_fresh_at_same_priority(self, db):
        """A stale (long-overdue) recurring job is promoted before a fresh one."""
        import time
        from ollama_queue.scheduler import Scheduler
        scheduler = Scheduler(db)
        now = time.time()

        # Two recurring jobs with same priority
        rj_stale = db.add_recurring_job("stale-job", "echo stale", interval_seconds=3600,
                                         priority=5, source="stale")
        rj_fresh = db.add_recurring_job("fresh-job", "echo fresh", interval_seconds=3600,
                                         priority=5, source="fresh")

        # Stale job: last successful run was 5 intervals ago
        job_old = db.submit_job("echo old", "m", 5, 60, "stale", recurring_job_id=rj_stale)
        db.start_job(job_old)
        db.complete_job(job_old, exit_code=0, stdout_tail="", stderr_tail="")
        db._connect().execute(
            "UPDATE jobs SET completed_at=? WHERE id=?", (now - 5*3600, job_old)
        )

        # Fresh job: last successful run was 0.5 intervals ago
        job_recent = db.submit_job("echo recent", "m", 5, 60, "fresh", recurring_job_id=rj_fresh)
        db.start_job(job_recent)
        db.complete_job(job_recent, exit_code=0, stdout_tail="", stderr_tail="")
        db._connect().execute(
            "UPDATE jobs SET completed_at=? WHERE id=?", (now - 0.5*3600, job_recent)
        )

        # Set both due now
        db._connect().execute("UPDATE recurring_jobs SET next_run=? WHERE id=?", (now - 1, rj_stale))
        db._connect().execute("UPDATE recurring_jobs SET next_run=? WHERE id=?", (now - 1, rj_fresh))
        db._connect().commit()

        promoted = scheduler.promote_due_jobs(now)
        assert len(promoted) == 2

        # Stale job should be promoted first (lower AoI sort key = higher urgency)
        jobs = [db.get_job(jid) for jid in promoted]
        # The stale job's source is "stale"
        first_source = jobs[0]["source"]
        assert first_source == "stale", f"Expected stale to be first, got {first_source}"

    def test_never_run_job_has_maximum_aoi_urgency(self, db):
        """A recurring job that never ran gets maximum urgency (staleness_norm=1.0)."""
        import time
        from ollama_queue.scheduler import Scheduler
        scheduler = Scheduler(db)
        now = time.time()

        rj_never = db.add_recurring_job("never-run", "echo x", interval_seconds=3600,
                                          priority=5, source="never")
        rj_ran = db.add_recurring_job("ran-once", "echo x", interval_seconds=3600,
                                       priority=5, source="ran")

        # rj_ran: completed 0.1 intervals ago (fresh)
        job_ran = db.submit_job("echo ran", "m", 5, 60, "ran", recurring_job_id=rj_ran)
        db.start_job(job_ran)
        db.complete_job(job_ran, exit_code=0, stdout_tail="", stderr_tail="")
        db._connect().execute(
            "UPDATE jobs SET completed_at=? WHERE id=?", (now - 0.1*3600, job_ran)
        )

        db._connect().execute("UPDATE recurring_jobs SET next_run=? WHERE id=?", (now - 1, rj_never))
        db._connect().execute("UPDATE recurring_jobs SET next_run=? WHERE id=?", (now - 1, rj_ran))
        db._connect().commit()

        promoted = scheduler.promote_due_jobs(now)
        assert len(promoted) == 2
        jobs = [db.get_job(jid) for jid in promoted]
        first_source = jobs[0]["source"]
        assert first_source == "never"
```

### Step 3: Run to verify failure

```bash
pytest tests/test_db.py::TestLastSuccessfulRunTime tests/test_scheduler.py::TestAoISorting -v
```

Expected: FAIL — missing method and wrong promotion order

### Step 4: Implement

In `ollama_queue/db.py`, add after `has_pending_or_running_recurring()`:

```python
def get_last_successful_run_time(self, recurring_job_id: int) -> float | None:
    """Return timestamp of most recent successful (exit_code=0) job for a recurring job.

    Uses exit_code=0 (not last_run which includes failures) for AoI accuracy.
    Returns None if the recurring job has never completed successfully.
    """
    conn = self._connect()
    row = conn.execute(
        """SELECT MAX(completed_at) as last_success
           FROM jobs
           WHERE recurring_job_id = ? AND exit_code = 0""",
        (recurring_job_id,),
    ).fetchone()
    if row is None or row["last_success"] is None:
        return None
    return float(row["last_success"])
```

In `ollama_queue/scheduler.py`, add `_aoi_sort_key()` method and modify `promote_due_jobs()`:

```python
def _aoi_sort_key(self, rj: dict, now: float) -> float:
    """Compute AoI-weighted scheduling urgency score. Lower = higher priority.

    Score = priority_norm * (1 - aoi_weight) + (1 - staleness_norm) * aoi_weight

    Both components normalized to [0, 1] so aoi_weight is semantically correct:
    aoi_weight=0.3 means exactly 30% of score comes from information staleness.
    priority_norm: 0=critical(p1), 1=background(p10)
    staleness_norm: 0=fresh, 1=maximally stale (>=5 intervals overdue)
    """
    aoi_weight = float(self.db.get_setting("aoi_weight") or 0.3)

    # Priority normalized to [0, 1]
    priority = int(rj.get("priority") or 5)
    priority_norm = (priority - 1) / 9.0  # 0 = p1 (critical), 1 = p10 (background)

    # Staleness normalized to [0, 1] — capped at 5× interval overdue
    last_success = self.db.get_last_successful_run_time(rj["id"])
    if last_success is not None:
        interval = float(rj.get("interval_seconds") or 3600)
        staleness_ratio = (now - last_success) / max(interval, 1.0)
        staleness_norm = min(1.0, staleness_ratio / 5.0)
    else:
        staleness_norm = 1.0  # never completed → maximum urgency

    return priority_norm * (1.0 - aoi_weight) + (1.0 - staleness_norm) * aoi_weight
```

In `promote_due_jobs()`, sort the `due` list before the promotion loop:

```python
def promote_due_jobs(self, now: float | None = None) -> list[int]:
    """Promote due recurring jobs to pending. Coalesces duplicates."""
    if now is None:
        now = time.time()
    due = self.db.get_due_recurring_jobs(now)

    # AoI sort: lower score = higher urgency. Ensures stale jobs promoted first
    # when multiple become due simultaneously.
    due.sort(key=lambda rj: self._aoi_sort_key(rj, now))

    new_ids = []
    for rj in due:
        # ... rest of existing loop unchanged ...
```

### Step 5: Run full test suite

```bash
pytest --timeout=120 -x -q
```

Expected: all tests pass

### Step 6: Lint

```bash
make lint
```

Expected: no errors

### Step 7: Commit

```bash
git add ollama_queue/db.py ollama_queue/scheduler.py tests/test_db.py tests/test_scheduler.py
git commit -m "feat: AoI tiebreaker for recurring job promotion (normalized staleness + priority sort)"
```

---

## PR 3 Complete — Verification

```bash
pytest --timeout=120 -q
```

Expected: all tests pass.

Verify SJF is active:
```bash
python3 -c "
import ollama_queue.db as d
from ollama_queue.estimator import DurationEstimator
db = d.Database(':memory:')
db.initialize()
est = DurationEstimator(db)
mean, cv_sq = est.estimate_with_variance('unknown-src')
print('mean:', mean, 'cv_squared:', cv_sq)
print('sjf_aging_factor:', db.get_setting('sjf_aging_factor'))
print('aoi_weight:', db.get_setting('aoi_weight'))
"
```

Expected:
```
mean: 600.0 cv_squared: 1.5
sjf_aging_factor: 3600
aoi_weight: 0.3
```
