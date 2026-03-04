# PR 1 — Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Harden SQLite PRAGMA configuration and replace exponential backoff in DLQ retries with decorrelated jitter.

**Architecture:** All changes in `db.py` and `dlq.py`. One schema migration (idempotent ALTER TABLE). No new files. No new dependencies.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), threading.RLock (existing pattern)

**Design doc:** `docs/plans/2026-03-04-queue-optimization-design.md` §PR1

**Quality Gates:**
- `pytest --timeout=120 -x -q` — must pass before every commit
- `make lint` — must pass before every commit
- Verify: `python3 -c "import ollama_queue.db; db = ollama_queue.db.Database(':memory:'); db.initialize(); print('OK')"`

---

## Task 1: SQLite PRAGMA Hardening

**Files:**
- Modify: `ollama_queue/db.py` (`_connect()` method, ~lines 50-57)
- Test: `tests/test_db.py`

### Step 1: Write failing test

Add to `tests/test_db.py` inside the `TestDatabase` class (or at module level with a fixture):

```python
def test_pragma_synchronous_normal(self, db):
    """PRAGMA synchronous should be NORMAL (1), not FULL (2)."""
    conn = db._connect()
    result = conn.execute("PRAGMA synchronous").fetchone()[0]
    assert result == 1  # 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA

def test_pragma_temp_store_memory(self, db):
    """PRAGMA temp_store should be MEMORY (2)."""
    conn = db._connect()
    result = conn.execute("PRAGMA temp_store").fetchone()[0]
    assert result == 2  # 0=DEFAULT, 1=FILE, 2=MEMORY

def test_pragma_busy_timeout(self, db):
    """PRAGMA busy_timeout should be 5000ms."""
    conn = db._connect()
    result = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert result == 5000

def test_pragma_wal_autocheckpoint(self, db):
    """PRAGMA wal_autocheckpoint should be 1000 pages."""
    conn = db._connect()
    result = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
    assert result == 1000
```

### Step 2: Run to verify failure

```bash
cd ~/Documents/projects/ollama-queue && source .venv/bin/activate
pytest tests/test_db.py::TestDatabase::test_pragma_synchronous_normal -v
```

Expected: FAIL — `assert 2 == 1`

### Step 3: Implement — add 6 PRAGMAs to `_connect()`

In `ollama_queue/db.py`, after the existing two PRAGMAs:

```python
def _connect(self) -> sqlite3.Connection:
    with self._lock:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            # Performance hardening (research: PR1 design doc)
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.execute("PRAGMA temp_store = MEMORY")
            self._conn.execute("PRAGMA mmap_size = 536870912")    # 512MB
            self._conn.execute("PRAGMA cache_size = -64000")       # 64MB page cache
            self._conn.execute("PRAGMA wal_autocheckpoint = 1000")
            self._conn.execute("PRAGMA busy_timeout = 5000")
    return self._conn
```

### Step 4: Run tests to verify pass

```bash
pytest tests/test_db.py -x -q
```

Expected: all existing tests pass + new PRAGMA tests pass

### Step 5: Commit

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "perf: add SQLite PRAGMA hardening (synchronous=NORMAL, mmap, cache, autocheckpoint, busy_timeout)"
```

---

## Task 2: Schema Migration — `last_retry_delay` Column

**Files:**
- Modify: `ollama_queue/db.py` (`_run_migrations()`, ~line 70)
- Test: `tests/test_db.py`

### Step 1: Write failing test

```python
def test_jobs_has_last_retry_delay_column(self, db):
    """jobs table must have last_retry_delay column for decorrelated jitter."""
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test")
    job = db.get_job(job_id)
    assert "last_retry_delay" in job
    assert job["last_retry_delay"] is None  # NULL by default
```

### Step 2: Run to verify failure

```bash
pytest tests/test_db.py::TestDatabase::test_jobs_has_last_retry_delay_column -v
```

Expected: FAIL — KeyError or missing key

### Step 3: Add migration in `_run_migrations()`

In `ollama_queue/db.py`, add to `_run_migrations()`:

```python
def _run_migrations(self, conn: sqlite3.Connection) -> None:
    """Apply all incremental schema migrations (idempotent)."""
    self._add_column_if_missing(conn, "recurring_jobs", "cron_expression", "TEXT")
    self._add_column_if_missing(conn, "recurring_jobs", "pinned", "INTEGER DEFAULT 0")
    self._add_column_if_missing(conn, "jobs", "pid", "INTEGER")
    self._add_column_if_missing(conn, "jobs", "stall_signals", "TEXT")
    self._add_column_if_missing(conn, "recurring_jobs", "check_command", "TEXT")
    self._add_column_if_missing(conn, "recurring_jobs", "max_runs", "INTEGER")
    self._add_column_if_missing(conn, "recurring_jobs", "outcome_reason", "TEXT")
    self._add_column_if_missing(conn, "jobs", "last_retry_delay", "REAL")  # PR1: jitter
```

Also add `_set_job_retry_delay()` method to `Database`:

```python
def _set_job_retry_delay(self, job_id: int, delay: float) -> None:
    """Store the most recent retry delay for decorrelated jitter computation."""
    with self._lock:
        conn = self._connect()
        conn.execute("UPDATE jobs SET last_retry_delay = ? WHERE id = ?", (delay, job_id))
        conn.commit()
```

### Step 4: Run tests

```bash
pytest tests/test_db.py -x -q
```

Expected: all pass

### Step 5: Commit

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat: add last_retry_delay column to jobs for decorrelated jitter"
```

---

## Task 3: Decorrelated Jitter in DLQ Retries

**Files:**
- Modify: `ollama_queue/db.py` (`DEFAULTS`)
- Modify: `ollama_queue/dlq.py` (`_schedule_retry()`)
- Test: `tests/test_dlq.py`

### Step 1: Add new DEFAULTS entry

In `ollama_queue/db.py`, add to the `DEFAULTS` dict:

```python
"retry_backoff_cap_seconds": 3600,   # max DLQ retry interval (1 hour)
```

### Step 2: Write failing tests for jitter

In `tests/test_dlq.py`:

```python
import statistics

def test_retry_uses_decorrelated_jitter(self, db):
    """Multiple retries should produce randomized delays, not fixed exponential."""
    # Submit a job with 3 retries allowed
    job_id = db.submit_job("echo test", "qwen2.5:7b", 5, 60, "test", max_retries=3)
    dlq = DLQManager(db)

    delays = []
    for attempt in range(3):
        # Get current job state
        job = db.get_job(job_id)
        # Manually compute what the delay would be
        # (test the internal _schedule_retry behavior)
        result = dlq._schedule_retry(job_id, attempt)
        assert result == "retry"
        updated = db.get_job(job_id)
        retry_after = updated["retry_after"]
        delay = retry_after - updated.get("last_retry_delay", 60)  # approx
        delays.append(updated["last_retry_delay"])
        # Reset for next attempt
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
```

### Step 3: Run to verify failure

```bash
pytest tests/test_dlq.py -v
```

Expected: FAIL — delays are deterministic (no jitter)

### Step 4: Implement decorrelated jitter in `dlq.py`

Replace `_schedule_retry()` in `ollama_queue/dlq.py`:

```python
def _schedule_retry(self, job_id: int, retry_count: int) -> str:
    import random
    settings = self.db.get_all_settings()
    base = settings.get("retry_backoff_base_seconds", 60)
    cap = settings.get("retry_backoff_cap_seconds", 3600)

    # Decorrelated jitter: each delay is random in [base, prev_delay * 3]
    # Breaks synchronization between retrying jobs (prevents thundering herd)
    job = self.db.get_job(job_id)
    prev_delay = job.get("last_retry_delay") or base
    delay = min(cap, random.uniform(base, prev_delay * 3))

    retry_after = time.time() + delay
    self.db._set_job_retry_after(job_id, retry_after)
    self.db._set_job_retry_delay(job_id, delay)
    self.db.log_schedule_event(
        "retried",
        job_id=job_id,
        details={"retry_count": retry_count + 1, "retry_after": retry_after, "delay_seconds": delay},
    )
    _log.info("Scheduled retry for job #%d in %.0fs (attempt %d)", job_id, delay, retry_count + 1)
    return "retry"
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
git add ollama_queue/db.py ollama_queue/dlq.py tests/test_dlq.py
git commit -m "feat: replace exponential backoff with decorrelated jitter in DLQ retries"
```

---

## PR 1 Complete — Verification

```bash
pytest --timeout=120 -q
```

Expected: all tests pass (count will be higher than baseline by number of new tests added).

Check SQLite PRAGMAs are live on a real DB:
```bash
python3 -c "
import ollama_queue.db as d
db = d.Database('/tmp/test-pr1.db')
db.initialize()
conn = db._connect()
print('synchronous:', conn.execute('PRAGMA synchronous').fetchone()[0])  # expect 1
print('temp_store:', conn.execute('PRAGMA temp_store').fetchone()[0])    # expect 2
print('busy_timeout:', conn.execute('PRAGMA busy_timeout').fetchone()[0]) # expect 5000
print('wal_autocheckpoint:', conn.execute('PRAGMA wal_autocheckpoint').fetchone()[0])  # expect 1000
"
```
