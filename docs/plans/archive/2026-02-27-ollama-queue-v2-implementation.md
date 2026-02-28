# ollama-queue v2 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Integrate a scheduler, DLQ, retry-with-backoff, stall detection, job tagging, and a full 6-tab dashboard into ollama-queue — eliminating all systemd timer units.

**Architecture:** Add `recurring_jobs`, `schedule_events`, and `dlq` tables to the existing SQLite DB. A new `Scheduler` class handles promotion + rebalancing; a new `DLQ` class handles failure routing. The daemon's `poll_once` gains 3 new pre-execution steps. The Preact SPA gains 2 new tabs (Schedule, DLQ) and updates to 4 existing tabs.

**Tech Stack:** Python 3.12, SQLite (WAL), FastAPI, Click, Preact 10, @preact/signals, Tailwind v4, uPlot. All existing patterns apply — synchronous SQLite, `sqlite3.Row`, `check_same_thread=False`.

**Design doc:** `docs/plans/2026-02-27-ollama-queue-v2-design.md`

---

## Task 1: Schema Migrations

**Files:**
- Modify: `ollama_queue/db.py`
- Modify: `tests/test_db.py`

### Step 1: Write failing tests for new tables

Add to `tests/test_db.py` inside `class TestInitialize`:

```python
def test_initialize_creates_v2_tables(self, db):
    tables = db.list_tables()
    expected = {
        "jobs", "duration_history", "health_log",
        "daemon_state", "settings",
        "recurring_jobs", "schedule_events", "dlq",  # new
    }
    assert expected == set(tables)

def test_jobs_has_v2_columns(self, db):
    db.submit_job("cmd", "m", 5, 60, "src",
                  tag="aria", max_retries=2, resource_profile="ollama")
    job = db.get_job(1)
    assert job["tag"] == "aria"
    assert job["max_retries"] == 2
    assert job["retry_count"] == 0
    assert job["retry_after"] is None
    assert job["stall_detected_at"] is None
    assert job["recurring_job_id"] is None
    assert job["resource_profile"] == "ollama"
```

### Step 2: Run to verify failure

```bash
cd ~/Documents/projects/ollama-queue && source .venv/bin/activate
pytest tests/test_db.py::TestInitialize::test_initialize_creates_v2_tables -v
```
Expected: FAIL — `recurring_jobs` table not found.

### Step 3: Add new tables + columns to `db.py`

In `initialize()`, append to the `executescript` SQL block:

```python
# After existing CREATE TABLE statements, add:
"""
CREATE TABLE IF NOT EXISTS recurring_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    command TEXT NOT NULL,
    model TEXT,
    priority INTEGER DEFAULT 5,
    timeout INTEGER DEFAULT 600,
    source TEXT,
    tag TEXT,
    resource_profile TEXT DEFAULT 'ollama',
    interval_seconds INTEGER NOT NULL,
    next_run REAL,
    last_run REAL,
    last_job_id INTEGER REFERENCES jobs(id),
    max_retries INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    recurring_job_id INTEGER REFERENCES recurring_jobs(id),
    job_id INTEGER REFERENCES jobs(id),
    details TEXT
);

CREATE TABLE IF NOT EXISTS dlq (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_job_id INTEGER NOT NULL,
    command TEXT NOT NULL,
    model TEXT,
    source TEXT,
    tag TEXT,
    priority INTEGER,
    resource_profile TEXT DEFAULT 'ollama',
    failure_reason TEXT,
    stdout_tail TEXT,
    stderr_tail TEXT,
    retry_count INTEGER DEFAULT 0,
    moved_at REAL NOT NULL,
    resolved_at REAL,
    resolution TEXT
);
"""
```

Also update `submit_job` signature to accept new columns, and update the `jobs` schema `CREATE TABLE` to include:

```sql
tag TEXT,
max_retries INTEGER DEFAULT 0,
retry_count INTEGER DEFAULT 0,
retry_after REAL,
stall_detected_at REAL,
recurring_job_id INTEGER REFERENCES recurring_jobs(id),
resource_profile TEXT DEFAULT 'ollama',
```

Update `submit_job` method signature:

```python
def submit_job(
    self,
    command: str,
    model: str,
    priority: int,
    timeout: int,
    source: str,
    tag: str | None = None,
    max_retries: int = 0,
    resource_profile: str = "ollama",
    recurring_job_id: int | None = None,
) -> int:
    conn = self._connect()
    cur = conn.execute(
        """INSERT INTO jobs
           (command, model, priority, timeout, source, submitted_at,
            tag, max_retries, resource_profile, recurring_job_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (command, model, priority, timeout, source, time.time(),
         tag, max_retries, resource_profile, recurring_job_id),
    )
    conn.commit()
    return cur.lastrowid
```

Also add to DEFAULTS dict:

```python
"default_max_retries": 0,
"retry_backoff_base_seconds": 60,
"retry_backoff_multiplier": 2.0,
"stall_multiplier": 2.0,
"priority_categories": '{"critical":[1,2],"high":[3,4],"normal":[5,6],"low":[7,8],"background":[9,10]}',
"priority_category_colors": '{"critical":"#ef4444","high":"#f97316","normal":"#3b82f6","low":"#6b7280","background":"#374151"}',
"resource_profiles": '{"ollama":{"check_vram":true,"check_ram":true,"check_load":true},"any":{"check_vram":false,"check_ram":false,"check_load":false}}',
```

### Step 4: Run tests to verify pass

```bash
pytest tests/test_db.py -v
```
Expected: All 22+ tests pass.

### Step 5: Commit

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat: add v2 schema — recurring_jobs, schedule_events, dlq tables + job columns"
```

---

## Task 2: Recurring Jobs DB Methods

**Files:**
- Modify: `ollama_queue/db.py`
- Modify: `tests/test_db.py`

### Step 1: Write failing tests

Add new class to `tests/test_db.py`:

```python
class TestRecurringJobs:
    def test_add_recurring_job(self, db):
        rj_id = db.add_recurring_job(
            name="aria-full",
            command="aria predict",
            interval_seconds=21600,
            model="qwen2.5:14b",
            priority=3,
            source="aria",
            tag="aria",
        )
        assert rj_id == 1
        rj = db.get_recurring_job(rj_id)
        assert rj["name"] == "aria-full"
        assert rj["interval_seconds"] == 21600
        assert rj["enabled"] == 1

    def test_get_due_recurring_jobs(self, db):
        now = time.time()
        db.add_recurring_job("job1", "cmd1", 3600, next_run=now - 1)
        db.add_recurring_job("job2", "cmd2", 3600, next_run=now + 3600)
        due = db.get_due_recurring_jobs(now)
        assert len(due) == 1
        assert due[0]["name"] == "job1"

    def test_get_due_skips_disabled(self, db):
        now = time.time()
        db.add_recurring_job("job1", "cmd1", 3600, next_run=now - 1)
        db.set_recurring_job_enabled("job1", False)
        due = db.get_due_recurring_jobs(now)
        assert len(due) == 0

    def test_update_next_run(self, db):
        rj_id = db.add_recurring_job("job1", "cmd1", 3600)
        completed_at = time.time()
        db.update_recurring_next_run(rj_id, completed_at)
        rj = db.get_recurring_job(rj_id)
        assert abs(rj["next_run"] - (completed_at + 3600)) < 0.01

    def test_list_recurring_jobs(self, db):
        db.add_recurring_job("a", "cmd_a", 3600)
        db.add_recurring_job("b", "cmd_b", 7200)
        jobs = db.list_recurring_jobs()
        assert len(jobs) == 2

    def test_log_schedule_event(self, db):
        db.log_schedule_event("promoted", details={"job_id": 1})
        events = db.get_schedule_events(limit=10)
        assert len(events) == 1
        assert events[0]["event_type"] == "promoted"
```

### Step 2: Run to verify failure

```bash
pytest tests/test_db.py::TestRecurringJobs -v
```
Expected: FAIL — `db.add_recurring_job` not defined.

### Step 3: Implement methods in `db.py`

Add after existing job methods:

```python
def add_recurring_job(
    self,
    name: str,
    command: str,
    interval_seconds: int,
    model: str | None = None,
    priority: int = 5,
    timeout: int = 600,
    source: str | None = None,
    tag: str | None = None,
    resource_profile: str = "ollama",
    max_retries: int = 0,
    next_run: float | None = None,
) -> int:
    conn = self._connect()
    now = time.time()
    cur = conn.execute(
        """INSERT INTO recurring_jobs
           (name, command, model, priority, timeout, source, tag,
            resource_profile, interval_seconds, next_run, max_retries, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, command, model, priority, timeout, source, tag,
         resource_profile, interval_seconds, next_run or now, max_retries, now),
    )
    conn.commit()
    return cur.lastrowid

def get_recurring_job(self, rj_id: int) -> dict | None:
    conn = self._connect()
    row = conn.execute(
        "SELECT * FROM recurring_jobs WHERE id = ?", (rj_id,)
    ).fetchone()
    return dict(row) if row else None

def get_recurring_job_by_name(self, name: str) -> dict | None:
    conn = self._connect()
    row = conn.execute(
        "SELECT * FROM recurring_jobs WHERE name = ?", (name,)
    ).fetchone()
    return dict(row) if row else None

def list_recurring_jobs(self) -> list[dict]:
    conn = self._connect()
    rows = conn.execute(
        "SELECT * FROM recurring_jobs ORDER BY priority ASC, name ASC"
    ).fetchall()
    return [dict(r) for r in rows]

def get_due_recurring_jobs(self, now: float) -> list[dict]:
    conn = self._connect()
    rows = conn.execute(
        """SELECT * FROM recurring_jobs
           WHERE enabled = 1 AND next_run <= ?
           ORDER BY priority ASC, next_run ASC""",
        (now,),
    ).fetchall()
    return [dict(r) for r in rows]

def update_recurring_next_run(
    self, rj_id: int, completed_at: float, job_id: int | None = None
) -> None:
    conn = self._connect()
    rj = self.get_recurring_job(rj_id)
    next_run = completed_at + rj["interval_seconds"]
    conn.execute(
        """UPDATE recurring_jobs
           SET next_run = ?, last_run = ?, last_job_id = ?
           WHERE id = ?""",
        (next_run, completed_at, job_id, rj_id),
    )
    conn.commit()

def set_recurring_job_enabled(self, name: str, enabled: bool) -> bool:
    conn = self._connect()
    cur = conn.execute(
        "UPDATE recurring_jobs SET enabled = ? WHERE name = ?",
        (1 if enabled else 0, name),
    )
    conn.commit()
    return cur.rowcount > 0

def delete_recurring_job(self, name: str) -> bool:
    conn = self._connect()
    cur = conn.execute(
        "DELETE FROM recurring_jobs WHERE name = ?", (name,)
    )
    conn.commit()
    return cur.rowcount > 0

def log_schedule_event(
    self,
    event_type: str,
    recurring_job_id: int | None = None,
    job_id: int | None = None,
    details: dict | None = None,
) -> None:
    import json
    conn = self._connect()
    conn.execute(
        """INSERT INTO schedule_events
           (timestamp, event_type, recurring_job_id, job_id, details)
           VALUES (?, ?, ?, ?, ?)""",
        (time.time(), event_type, recurring_job_id, job_id,
         json.dumps(details) if details else None),
    )
    conn.commit()

def get_schedule_events(self, limit: int = 100) -> list[dict]:
    conn = self._connect()
    rows = conn.execute(
        "SELECT * FROM schedule_events ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]

def has_pending_or_running_recurring(self, recurring_job_id: int) -> bool:
    conn = self._connect()
    row = conn.execute(
        """SELECT 1 FROM jobs
           WHERE recurring_job_id = ? AND status IN ('pending', 'running')
           LIMIT 1""",
        (recurring_job_id,),
    ).fetchone()
    return row is not None
```

### Step 4: Run tests to verify pass

```bash
pytest tests/test_db.py -v
```
Expected: All tests pass.

### Step 5: Commit

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat: add recurring_jobs + schedule_events DB methods"
```

---

## Task 3: DLQ DB Methods

**Files:**
- Modify: `ollama_queue/db.py`
- Modify: `tests/test_db.py`

### Step 1: Write failing tests

```python
class TestDLQ:
    def test_move_to_dlq(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="err",
                        outcome_reason="exit code 1")
        dlq_id = db.move_to_dlq(job_id, failure_reason="exit code 1")
        assert dlq_id is not None
        entry = db.get_dlq_entry(dlq_id)
        assert entry["original_job_id"] == job_id
        assert entry["failure_reason"] == "exit code 1"
        assert entry["resolution"] is None

    def test_list_dlq(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="")
        db.move_to_dlq(job_id, failure_reason="failed")
        entries = db.list_dlq()
        assert len(entries) == 1

    def test_dismiss_dlq_entry(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="")
        dlq_id = db.move_to_dlq(job_id, failure_reason="failed")
        db.dismiss_dlq_entry(dlq_id)
        entry = db.get_dlq_entry(dlq_id)
        assert entry["resolution"] == "dismissed"

    def test_retry_from_dlq_creates_new_job(self, db):
        job_id = db.submit_job("echo hello", "m", 5, 60, "src", tag="t")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="")
        dlq_id = db.move_to_dlq(job_id, failure_reason="failed")
        new_job_id = db.retry_dlq_entry(dlq_id)
        assert new_job_id is not None
        new_job = db.get_job(new_job_id)
        assert new_job["command"] == "echo hello"
        assert new_job["status"] == "pending"
        entry = db.get_dlq_entry(dlq_id)
        assert entry["resolution"] == "retried"

    def test_clear_dlq_removes_resolved(self, db):
        job_id = db.submit_job("cmd", "m", 5, 60, "src")
        db.start_job(job_id)
        db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="")
        dlq_id = db.move_to_dlq(job_id, failure_reason="failed")
        db.dismiss_dlq_entry(dlq_id)
        db.clear_dlq()
        assert db.list_dlq() == []
```

### Step 2: Run to verify failure

```bash
pytest tests/test_db.py::TestDLQ -v
```
Expected: FAIL.

### Step 3: Implement DLQ methods in `db.py`

```python
def move_to_dlq(self, job_id: int, failure_reason: str) -> int | None:
    conn = self._connect()
    job = self.get_job(job_id)
    if not job:
        return None
    cur = conn.execute(
        """INSERT INTO dlq
           (original_job_id, command, model, source, tag, priority,
            resource_profile, failure_reason, stdout_tail, stderr_tail,
            retry_count, moved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, job["command"], job["model"], job["source"], job.get("tag"),
         job["priority"], job.get("resource_profile", "ollama"), failure_reason,
         job.get("stdout_tail", ""), job.get("stderr_tail", ""),
         job.get("retry_count", 0), time.time()),
    )
    conn.execute("UPDATE jobs SET status = 'dead' WHERE id = ?", (job_id,))
    conn.commit()
    return cur.lastrowid

def get_dlq_entry(self, dlq_id: int) -> dict | None:
    conn = self._connect()
    row = conn.execute("SELECT * FROM dlq WHERE id = ?", (dlq_id,)).fetchone()
    return dict(row) if row else None

def list_dlq(self, include_resolved: bool = False) -> list[dict]:
    conn = self._connect()
    if include_resolved:
        rows = conn.execute(
            "SELECT * FROM dlq ORDER BY moved_at DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM dlq WHERE resolution IS NULL ORDER BY moved_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]

def dismiss_dlq_entry(self, dlq_id: int) -> bool:
    conn = self._connect()
    cur = conn.execute(
        "UPDATE dlq SET resolution = 'dismissed', resolved_at = ? WHERE id = ?",
        (time.time(), dlq_id),
    )
    conn.commit()
    return cur.rowcount > 0

def retry_dlq_entry(self, dlq_id: int) -> int | None:
    conn = self._connect()
    entry = self.get_dlq_entry(dlq_id)
    if not entry:
        return None
    new_job_id = self.submit_job(
        command=entry["command"],
        model=entry["model"],
        priority=entry["priority"] or 5,
        timeout=600,
        source=entry["source"] or "dlq-retry",
        tag=entry.get("tag"),
        resource_profile=entry.get("resource_profile", "ollama"),
    )
    conn.execute(
        """UPDATE dlq SET resolution = 'retried', resolved_at = ?,
           retry_count = retry_count + 1 WHERE id = ?""",
        (time.time(), dlq_id),
    )
    conn.commit()
    return new_job_id

def clear_dlq(self) -> int:
    conn = self._connect()
    cur = conn.execute("DELETE FROM dlq WHERE resolution IS NOT NULL")
    conn.commit()
    return cur.rowcount
```

### Step 4: Run tests

```bash
pytest tests/test_db.py -v
```
Expected: All tests pass.

### Step 5: Commit

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat: add DLQ DB methods — move, list, retry, dismiss, clear"
```

---

## Task 4: Scheduler Class

**Files:**
- Create: `ollama_queue/scheduler.py`
- Create: `tests/test_scheduler.py`

### Step 1: Write failing tests

Create `tests/test_scheduler.py`:

```python
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
```

### Step 2: Run to verify failure

```bash
pytest tests/test_scheduler.py -v
```
Expected: FAIL — `ollama_queue.scheduler` not found.

### Step 3: Implement `scheduler.py`

Create `ollama_queue/scheduler.py`:

```python
"""Scheduler: recurring job promotion and schedule rebalancing."""

from __future__ import annotations

import json
import logging
import time

from ollama_queue.db import Database

_log = logging.getLogger(__name__)


class Scheduler:
    """Manages recurring job promotion and schedule rebalancing."""

    def __init__(self, db: Database):
        self.db = db

    def promote_due_jobs(self, now: float | None = None) -> list[int]:
        """Promote due recurring jobs to pending. Coalesces duplicates.

        Returns list of new job IDs created.
        """
        if now is None:
            now = time.time()
        due = self.db.get_due_recurring_jobs(now)
        new_ids = []
        for rj in due:
            if self.db.has_pending_or_running_recurring(rj["id"]):
                self.db.log_schedule_event(
                    "skipped_duplicate",
                    recurring_job_id=rj["id"],
                    details={"name": rj["name"], "reason": "already pending or running"},
                )
                continue
            job_id = self.db.submit_job(
                command=rj["command"],
                model=rj["model"],
                priority=rj["priority"],
                timeout=rj["timeout"],
                source=rj["source"] or rj["name"],
                tag=rj.get("tag"),
                max_retries=rj.get("max_retries", 0),
                resource_profile=rj.get("resource_profile", "ollama"),
                recurring_job_id=rj["id"],
            )
            self.db.log_schedule_event(
                "promoted",
                recurring_job_id=rj["id"],
                job_id=job_id,
                details={"name": rj["name"]},
            )
            new_ids.append(job_id)
            _log.info("Promoted recurring job %r → job #%d", rj["name"], job_id)
        return new_ids

    def update_next_run(
        self, recurring_job_id: int, completed_at: float, job_id: int | None = None
    ) -> None:
        """Update next_run after job completion. Anchors to completed_at."""
        self.db.update_recurring_next_run(recurring_job_id, completed_at, job_id)
        rj = self.db.get_recurring_job(recurring_job_id)
        self.db.log_schedule_event(
            "next_run_updated",
            recurring_job_id=recurring_job_id,
            job_id=job_id,
            details={"name": rj["name"], "next_run": rj["next_run"]},
        )

    def rebalance(self, now: float | None = None) -> list[dict]:
        """Rebalance all enabled recurring jobs to spread load evenly.

        Higher priority jobs get earlier slots. Returns list of change dicts.
        """
        if now is None:
            now = time.time()
        rjs = [r for r in self.db.list_recurring_jobs() if r["enabled"]]
        if not rjs:
            return []

        # Sort by priority ascending (1 = highest = earliest slot)
        rjs.sort(key=lambda r: (r["priority"], r["name"]))

        # Window = shortest interval
        window = min(r["interval_seconds"] for r in rjs)
        n = len(rjs)
        changes = []

        for i, rj in enumerate(rjs):
            old_next_run = rj["next_run"]
            new_next_run = now + (window * i / n)
            conn = self.db._connect()
            conn.execute(
                "UPDATE recurring_jobs SET next_run = ? WHERE id = ?",
                (new_next_run, rj["id"]),
            )
            conn.commit()
            change = {
                "name": rj["name"],
                "old_next_run": old_next_run,
                "new_next_run": new_next_run,
            }
            changes.append(change)
            self.db.log_schedule_event(
                "rebalanced",
                recurring_job_id=rj["id"],
                details=change,
            )
            _log.info(
                "Rebalanced %r: next_run shifted by %.0fs",
                rj["name"],
                new_next_run - (old_next_run or now),
            )

        return changes
```

### Step 4: Run tests to verify pass

```bash
pytest tests/test_scheduler.py -v
```
Expected: All tests pass.

### Step 5: Commit

```bash
git add ollama_queue/scheduler.py tests/test_scheduler.py
git commit -m "feat: add Scheduler class — promote_due_jobs, update_next_run, rebalance"
```

---

## Task 5: DLQ Class

**Files:**
- Create: `ollama_queue/dlq.py`
- Create: `tests/test_dlq.py`

### Step 1: Write failing tests

Create `tests/test_dlq.py`:

```python
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
            db._connect().execute(
                "UPDATE jobs SET retry_count = ? WHERE id = ?", (retry_count, job_id)
            )
            db._connect().commit()
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
```

### Step 2: Run to verify failure

```bash
pytest tests/test_dlq.py -v
```
Expected: FAIL.

### Step 3: Implement `dlq.py`

Create `ollama_queue/dlq.py`:

```python
"""DLQ manager: routes failed jobs to retry queue or dead letter queue."""

from __future__ import annotations

import logging
import time

from ollama_queue.db import Database

_log = logging.getLogger(__name__)


class DLQManager:
    """Routes failed jobs to retry or DLQ based on retry budget."""

    def __init__(self, db: Database):
        self.db = db

    def handle_failure(self, job_id: int, failure_reason: str) -> str:
        """Route a failed job. Returns 'retry' or 'dlq'."""
        job = self.db.get_job(job_id)
        if not job:
            _log.warning("handle_failure: job #%d not found", job_id)
            return "dlq"

        retry_count = job.get("retry_count", 0)
        max_retries = job.get("max_retries", 0)

        if retry_count < max_retries:
            return self._schedule_retry(job_id, retry_count)
        else:
            return self._move_to_dlq(job_id, failure_reason)

    def _schedule_retry(self, job_id: int, retry_count: int) -> str:
        settings = self.db.get_all_settings()
        base = settings.get("retry_backoff_base_seconds", 60)
        multiplier = settings.get("retry_backoff_multiplier", 2.0)
        delay = base * (multiplier ** retry_count)
        retry_after = time.time() + delay

        conn = self.db._connect()
        conn.execute(
            """UPDATE jobs
               SET retry_count = retry_count + 1,
                   retry_after = ?,
                   status = 'pending'
               WHERE id = ?""",
            (retry_after, job_id),
        )
        conn.commit()
        self.db.log_schedule_event(
            "retried",
            job_id=job_id,
            details={"retry_count": retry_count + 1, "retry_after": retry_after, "delay_seconds": delay},
        )
        _log.info("Scheduled retry for job #%d in %.0fs (attempt %d)", job_id, delay, retry_count + 1)
        return "retry"

    def _move_to_dlq(self, job_id: int, failure_reason: str) -> str:
        dlq_id = self.db.move_to_dlq(job_id, failure_reason)
        self.db.log_schedule_event(
            "dlq_moved",
            job_id=job_id,
            details={"dlq_id": dlq_id, "failure_reason": failure_reason},
        )
        _log.warning("Job #%d moved to DLQ: %s", job_id, failure_reason)
        return "dlq"
```

### Step 4: Run tests to verify pass

```bash
pytest tests/test_dlq.py -v
```
Expected: All tests pass.

### Step 5: Commit

```bash
git add ollama_queue/dlq.py tests/test_dlq.py
git commit -m "feat: add DLQManager — handle_failure routes to retry or DLQ with backoff"
```

---

## Task 6: Daemon Integration

**Files:**
- Modify: `ollama_queue/daemon.py`
- Modify: `tests/test_daemon.py`

### Step 1: Write failing tests

Add to `tests/test_daemon.py`:

```python
class TestDaemonSchedulerIntegration:
    def test_poll_once_promotes_due_recurring_job(self, db):
        from ollama_queue.daemon import Daemon
        from unittest.mock import patch
        now = time.time()
        db.add_recurring_job("job1", "echo hi", 3600, next_run=now - 1, source="test")
        daemon = Daemon(db)
        with patch.object(daemon.health, 'check', return_value={
            'ram_pct': 10, 'vram_pct': 10, 'load_avg': 0.1,
            'swap_pct': 5, 'ollama_model': None
        }):
            daemon.poll_once()
        pending = db.get_pending_jobs()
        assert len(pending) == 1
        assert pending[0]["command"] == "echo hi"

    def test_poll_once_detects_stall(self, db):
        from ollama_queue.daemon import Daemon
        from unittest.mock import patch
        job_id = db.submit_job("slow", "m", 5, 600, "src")
        db.start_job(job_id)
        # Fake a job that started 1000s ago with estimated_duration=100s
        db._connect().execute(
            "UPDATE jobs SET started_at = ?, estimated_duration = 100 WHERE id = ?",
            (time.time() - 1000, job_id)
        )
        db._connect().commit()
        daemon = Daemon(db)
        with patch.object(daemon.health, 'check', return_value={
            'ram_pct': 10, 'vram_pct': 10, 'load_avg': 0.1,
            'swap_pct': 5, 'ollama_model': None
        }):
            daemon.poll_once()
        job = db.get_job(job_id)
        assert job["stall_detected_at"] is not None
```

### Step 2: Run to verify failure

```bash
pytest tests/test_daemon.py -v
```

### Step 3: Update `daemon.py`

Add imports at top:

```python
from ollama_queue.scheduler import Scheduler
from ollama_queue.dlq import DLQManager
```

Update `__init__`:

```python
def __init__(self, db: Database, health_monitor: HealthMonitor | None = None):
    self.db = db
    self.health = health_monitor or HealthMonitor()
    self.estimator = DurationEstimator(db)
    self.scheduler = Scheduler(db)
    self.dlq = DLQManager(db)
    self._last_prune: float = 0.0
    self._recent_job_models: dict[str, float] = {}
```

Add 3 new steps at the TOP of `poll_once()`, before step 1:

```python
def poll_once(self) -> None:
    now = time.time()

    # step 0: promote due recurring jobs
    try:
        self.scheduler.promote_due_jobs(now)
    except Exception:
        _log.exception("Scheduler promotion failed; continuing")

    # step 0b: detect stalled jobs
    try:
        self._check_stalled_jobs(now)
    except Exception:
        _log.exception("Stall detection failed; continuing")

    # step 0c: re-queue retryable jobs
    try:
        self._check_retryable_jobs(now)
    except Exception:
        _log.exception("Retry check failed; continuing")

    # ... existing step 1 onwards unchanged ...
```

Add helper methods:

```python
def _check_stalled_jobs(self, now: float) -> None:
    settings = self.db.get_all_settings()
    multiplier = settings.get("stall_multiplier", 2.0)
    conn = self.db._connect()
    running = conn.execute(
        "SELECT * FROM jobs WHERE status = 'running' AND stall_detected_at IS NULL"
    ).fetchall()
    for row in running:
        job = dict(row)
        estimated = job.get("estimated_duration")
        started = job.get("started_at")
        if estimated and started:
            elapsed = now - started
            if elapsed > multiplier * estimated:
                conn.execute(
                    "UPDATE jobs SET stall_detected_at = ? WHERE id = ?",
                    (now, job["id"]),
                )
                conn.commit()
                self.db.log_schedule_event(
                    "stall_detected",
                    job_id=job["id"],
                    details={"elapsed": elapsed, "estimated": estimated, "multiplier": multiplier},
                )
                _log.warning(
                    "Job #%d stalled: elapsed=%.0fs, estimated=%.0fs",
                    job["id"], elapsed, estimated,
                )

def _check_retryable_jobs(self, now: float) -> None:
    conn = self.db._connect()
    conn.execute(
        """UPDATE jobs SET retry_after = NULL
           WHERE status = 'pending' AND retry_after IS NOT NULL AND retry_after <= ?""",
        (now,),
    )
    conn.commit()
```

Update the failure path near step 10 (timeout/bad exit) to call DLQ routing:

```python
# After complete_job with exit_code != 0:
if exit_code != 0:
    failure_reason = f"exit code {exit_code}"
    self.dlq.handle_failure(job["id"], failure_reason)

# After completion with exit_code == 0, update recurring next_run:
if exit_code == 0 and job.get("recurring_job_id"):
    self.scheduler.update_next_run(
        job["recurring_job_id"],
        completed_at=time.time(),
        job_id=job["id"],
    )
```

### Step 4: Run all tests

```bash
pytest tests/ -v
```
Expected: All tests pass.

### Step 5: Commit

```bash
git add ollama_queue/daemon.py tests/test_daemon.py
git commit -m "feat: integrate Scheduler + DLQManager into daemon poll_once"
```

---

## Task 7: CLI — `schedule` and `dlq` Subcommands

**Files:**
- Modify: `ollama_queue/cli.py`
- Modify: `tests/test_cli.py`

### Step 1: Write failing tests

Add to `tests/test_cli.py`:

```python
class TestScheduleCLI:
    def test_schedule_add(self, runner, db_path):
        result = runner.invoke(main, [
            "--db", db_path, "schedule", "add",
            "--name", "test-job",
            "--interval", "6h",
            "--model", "qwen2.5:14b",
            "--priority", "3",
            "--tag", "aria",
            "--", "echo hello"
        ])
        assert result.exit_code == 0
        assert "test-job" in result.output

    def test_schedule_list(self, runner, db_path):
        runner.invoke(main, ["--db", db_path, "schedule", "add",
            "--name", "j1", "--interval", "1h", "--", "echo a"])
        result = runner.invoke(main, ["--db", db_path, "schedule", "list"])
        assert result.exit_code == 0
        assert "j1" in result.output

    def test_schedule_disable_enable(self, runner, db_path):
        runner.invoke(main, ["--db", db_path, "schedule", "add",
            "--name", "j1", "--interval", "1h", "--", "echo a"])
        result = runner.invoke(main, ["--db", db_path, "schedule", "disable", "j1"])
        assert result.exit_code == 0
        result = runner.invoke(main, ["--db", db_path, "schedule", "enable", "j1"])
        assert result.exit_code == 0

    def test_dlq_list(self, runner, db_path):
        result = runner.invoke(main, ["--db", db_path, "dlq", "list"])
        assert result.exit_code == 0


class TestSubmitWithNewFlags:
    def test_submit_with_tag_and_retries(self, runner, db_path):
        result = runner.invoke(main, [
            "--db", db_path, "submit",
            "--source", "test", "--model", "qwen2.5:7b",
            "--tag", "aria", "--max-retries", "2",
            "--", "echo hello"
        ])
        assert result.exit_code == 0
```

### Step 2: Run to verify failure

```bash
pytest tests/test_cli.py -v
```

### Step 3: Implement CLI additions in `cli.py`

Add `--tag` and `--max-retries` to `submit` command:

```python
@click.option("--tag", default=None, help="Job tag for grouping/filtering")
@click.option("--max-retries", default=0, type=int, help="Max retry attempts on failure")
```

Update `submit` call to `db.submit_job(... tag=tag, max_retries=max_retries)`.

Add `schedule` group after existing commands:

```python
@main.group()
def schedule():
    """Manage recurring scheduled jobs."""
    pass


def _parse_interval(interval_str: str) -> int:
    """Parse interval string like 6h, 30m, 90s, 1d → seconds."""
    unit_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if interval_str[-1] in unit_map:
        return int(interval_str[:-1]) * unit_map[interval_str[-1]]
    return int(interval_str)  # assume seconds


@schedule.command("add")
@click.option("--name", required=True, help="Unique job name")
@click.option("--interval", required=True, help="Interval: 6h, 30m, 90s, 1d")
@click.option("--model", default=None)
@click.option("--priority", default=5, type=int)
@click.option("--timeout", default=600, type=int)
@click.option("--tag", default=None)
@click.option("--source", default=None)
@click.option("--max-retries", default=0, type=int)
@click.option("--profile", default="ollama", help="Resource profile: ollama|any")
@click.argument("command", nargs=-1, required=True)
@click.pass_context
def schedule_add(ctx, name, interval, model, priority, timeout, tag, source, max_retries, profile, command):
    db = ctx.obj["db"]
    from ollama_queue.scheduler import Scheduler
    interval_seconds = _parse_interval(interval)
    rj_id = db.add_recurring_job(
        name=name, command=" ".join(command), interval_seconds=interval_seconds,
        model=model, priority=priority, timeout=timeout, source=source or name,
        tag=tag, resource_profile=profile, max_retries=max_retries,
    )
    Scheduler(db).rebalance()
    click.echo(f"Added recurring job '{name}' (id={rj_id}) — interval={interval}, rebalanced.")


@schedule.command("list")
@click.pass_context
def schedule_list(ctx):
    db = ctx.obj["db"]
    import datetime
    jobs = db.list_recurring_jobs()
    if not jobs:
        click.echo("No recurring jobs.")
        return
    click.echo(f"{'NAME':<20} {'INTERVAL':>10} {'PRIORITY':>8} {'TAG':<12} {'ENABLED':>7} {'NEXT RUN'}")
    click.echo("-" * 75)
    for rj in jobs:
        next_run = datetime.datetime.fromtimestamp(rj["next_run"]).strftime("%Y-%m-%d %H:%M") if rj["next_run"] else "—"
        interval_h = rj["interval_seconds"] // 3600
        interval_str = f"{interval_h}h" if rj["interval_seconds"] % 3600 == 0 else f"{rj['interval_seconds']}s"
        enabled = "yes" if rj["enabled"] else "no"
        click.echo(f"{rj['name']:<20} {interval_str:>10} {rj['priority']:>8} {rj.get('tag') or '—':<12} {enabled:>7}  {next_run}")


@schedule.command("enable")
@click.argument("name")
@click.pass_context
def schedule_enable(ctx, name):
    db = ctx.obj["db"]
    if db.set_recurring_job_enabled(name, True):
        from ollama_queue.scheduler import Scheduler
        Scheduler(db).rebalance()
        click.echo(f"Enabled '{name}' and rebalanced.")
    else:
        click.echo(f"Job '{name}' not found.", err=True)


@schedule.command("disable")
@click.argument("name")
@click.pass_context
def schedule_disable(ctx, name):
    db = ctx.obj["db"]
    if db.set_recurring_job_enabled(name, False):
        click.echo(f"Disabled '{name}'.")
    else:
        click.echo(f"Job '{name}' not found.", err=True)


@schedule.command("remove")
@click.argument("name")
@click.pass_context
def schedule_remove(ctx, name):
    db = ctx.obj["db"]
    if db.delete_recurring_job(name):
        click.echo(f"Removed '{name}'.")
    else:
        click.echo(f"Job '{name}' not found.", err=True)


@schedule.command("rebalance")
@click.pass_context
def schedule_rebalance(ctx):
    db = ctx.obj["db"]
    from ollama_queue.scheduler import Scheduler
    changes = Scheduler(db).rebalance()
    click.echo(f"Rebalanced {len(changes)} jobs.")
    for c in changes:
        click.echo(f"  {c['name']}: next_run shifted")
```

Add `dlq` group:

```python
@main.group()
def dlq():
    """Manage the dead letter queue."""
    pass


@dlq.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include resolved entries")
@click.pass_context
def dlq_list(ctx, show_all):
    db = ctx.obj["db"]
    entries = db.list_dlq(include_resolved=show_all)
    if not entries:
        click.echo("DLQ is empty.")
        return
    for e in entries:
        click.echo(f"[{e['id']}] {e['command'][:50]} — {e['failure_reason']} (retries={e['retry_count']})")


@dlq.command("retry")
@click.argument("dlq_id", type=int)
@click.pass_context
def dlq_retry(ctx, dlq_id):
    db = ctx.obj["db"]
    new_id = db.retry_dlq_entry(dlq_id)
    if new_id:
        click.echo(f"Retried DLQ entry {dlq_id} → new job #{new_id}")
    else:
        click.echo(f"DLQ entry {dlq_id} not found.", err=True)


@dlq.command("retry-all")
@click.pass_context
def dlq_retry_all(ctx):
    db = ctx.obj["db"]
    entries = db.list_dlq()
    count = 0
    for e in entries:
        if db.retry_dlq_entry(e["id"]):
            count += 1
    click.echo(f"Retried {count} DLQ entries.")


@dlq.command("dismiss")
@click.argument("dlq_id", type=int)
@click.pass_context
def dlq_dismiss(ctx, dlq_id):
    db = ctx.obj["db"]
    if db.dismiss_dlq_entry(dlq_id):
        click.echo(f"Dismissed DLQ entry {dlq_id}.")
    else:
        click.echo(f"DLQ entry {dlq_id} not found.", err=True)


@dlq.command("clear")
@click.pass_context
def dlq_clear(ctx):
    db = ctx.obj["db"]
    n = db.clear_dlq()
    click.echo(f"Cleared {n} resolved DLQ entries.")
```

### Step 4: Run all tests

```bash
pytest tests/ -v
```
Expected: All tests pass.

### Step 5: Commit

```bash
git add ollama_queue/cli.py tests/test_cli.py
git commit -m "feat: add schedule + dlq CLI subcommands; submit --tag --max-retries"
```

---

## Task 8: API Endpoints

**Files:**
- Modify: `ollama_queue/api.py`
- Modify: `tests/test_api.py`

### Step 1: Write failing tests

Add to `tests/test_api.py`:

```python
class TestScheduleAPI:
    def test_list_recurring_jobs(self, client):
        r = client.get("/api/schedule")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_add_recurring_job(self, client):
        r = client.post("/api/schedule", json={
            "name": "test-job",
            "command": "echo hello",
            "interval_seconds": 3600,
            "model": "qwen2.5:7b",
            "priority": 5,
            "tag": "test",
        })
        assert r.status_code == 200
        assert r.json()["name"] == "test-job"

    def test_update_recurring_job(self, client):
        client.post("/api/schedule", json={
            "name": "j1", "command": "echo hi", "interval_seconds": 3600
        })
        r = client.put("/api/schedule/1", json={"enabled": False})
        assert r.status_code == 200

    def test_delete_recurring_job(self, client):
        client.post("/api/schedule", json={
            "name": "j1", "command": "echo hi", "interval_seconds": 3600
        })
        r = client.delete("/api/schedule/1")
        assert r.status_code == 200

    def test_trigger_rebalance(self, client):
        r = client.post("/api/schedule/rebalance")
        assert r.status_code == 200

    def test_get_schedule_events(self, client):
        r = client.get("/api/schedule/events")
        assert r.status_code == 200


class TestDLQAPI:
    def test_list_dlq_empty(self, client):
        r = client.get("/api/dlq")
        assert r.status_code == 200
        assert r.json() == []

    def test_retry_all_dlq(self, client):
        r = client.post("/api/dlq/retry-all")
        assert r.status_code == 200

    def test_clear_dlq(self, client):
        r = client.delete("/api/dlq")
        assert r.status_code == 200
```

### Step 2: Run to verify failure

```bash
pytest tests/test_api.py -v
```

### Step 3: Implement API endpoints in `api.py`

Add Pydantic models:

```python
class RecurringJobCreate(BaseModel):
    name: str
    command: str
    interval_seconds: int
    model: str | None = None
    priority: int = 5
    timeout: int = 600
    source: str | None = None
    tag: str | None = None
    max_retries: int = 0
    resource_profile: str = "ollama"

class RecurringJobUpdate(BaseModel):
    enabled: bool | None = None
    priority: int | None = None
    interval_seconds: int | None = None
    tag: str | None = None
```

Add endpoints:

```python
@app.get("/api/schedule")
def list_schedule():
    return db.list_recurring_jobs()

@app.post("/api/schedule")
def add_schedule(body: RecurringJobCreate):
    from ollama_queue.scheduler import Scheduler
    rj_id = db.add_recurring_job(**body.model_dump())
    Scheduler(db).rebalance()
    return db.get_recurring_job(rj_id)

@app.put("/api/schedule/{rj_id}")
def update_schedule(rj_id: int, body: RecurringJobUpdate):
    conn = db._connect()
    if body.enabled is not None:
        conn.execute("UPDATE recurring_jobs SET enabled = ? WHERE id = ?",
                     (1 if body.enabled else 0, rj_id))
    if body.priority is not None:
        conn.execute("UPDATE recurring_jobs SET priority = ? WHERE id = ?",
                     (body.priority, rj_id))
    if body.interval_seconds is not None:
        conn.execute("UPDATE recurring_jobs SET interval_seconds = ? WHERE id = ?",
                     (body.interval_seconds, rj_id))
    if body.tag is not None:
        conn.execute("UPDATE recurring_jobs SET tag = ? WHERE id = ?",
                     (body.tag, rj_id))
    conn.commit()
    from ollama_queue.scheduler import Scheduler
    Scheduler(db).rebalance()
    return db.get_recurring_job(rj_id)

@app.delete("/api/schedule/{rj_id}")
def delete_schedule(rj_id: int):
    conn = db._connect()
    conn.execute("DELETE FROM recurring_jobs WHERE id = ?", (rj_id,))
    conn.commit()
    return {"deleted": rj_id}

@app.post("/api/schedule/rebalance")
def trigger_rebalance():
    from ollama_queue.scheduler import Scheduler
    changes = Scheduler(db).rebalance()
    return {"rebalanced": len(changes), "changes": changes}

@app.get("/api/schedule/events")
def get_schedule_events(limit: int = 100):
    return db.get_schedule_events(limit=limit)

@app.get("/api/dlq")
def list_dlq(include_resolved: bool = False):
    return db.list_dlq(include_resolved=include_resolved)

@app.post("/api/dlq/{dlq_id}/retry")
def retry_dlq(dlq_id: int):
    new_id = db.retry_dlq_entry(dlq_id)
    return {"new_job_id": new_id}

@app.post("/api/dlq/retry-all")
def retry_all_dlq():
    entries = db.list_dlq()
    new_ids = [db.retry_dlq_entry(e["id"]) for e in entries]
    return {"retried": len([x for x in new_ids if x])}

@app.post("/api/dlq/{dlq_id}/dismiss")
def dismiss_dlq(dlq_id: int):
    db.dismiss_dlq_entry(dlq_id)
    return {"dismissed": dlq_id}

@app.delete("/api/dlq")
def clear_dlq():
    n = db.clear_dlq()
    return {"cleared": n}
```

### Step 4: Run all tests

```bash
pytest tests/ -v
```
Expected: All tests pass.

### Step 5: Commit

```bash
git add ollama_queue/api.py tests/test_api.py
git commit -m "feat: add schedule + DLQ API endpoints (10 new routes)"
```

---

## Task 9: Dashboard — Schedule Tab (Tab 3)

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/ScheduleTab.jsx`
- Modify: `ollama_queue/dashboard/spa/src/App.jsx`
- Modify: `ollama_queue/dashboard/spa/src/store.js`

### Step 1: Add schedule state to store

In `store.js`, add:

```js
export const scheduleJobs = signal([]);
export const scheduleEvents = signal([]);

export async function fetchSchedule() {
  const [jobs, events] = await Promise.all([
    fetch('/api/schedule').then(r => r.json()),
    fetch('/api/schedule/events?limit=50').then(r => r.json()),
  ]);
  scheduleJobs.value = jobs;
  scheduleEvents.value = events;
}

export async function addScheduleJob(payload) {
  await fetch('/api/schedule', { method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload) });
  await fetchSchedule();
}

export async function toggleScheduleJob(id, enabled) {
  await fetch(`/api/schedule/${id}`, { method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ enabled }) });
  await fetchSchedule();
}

export async function triggerRebalance() {
  await fetch('/api/schedule/rebalance', { method: 'POST' });
  await fetchSchedule();
}
```

### Step 2: Create `ScheduleTab.jsx`

```jsx
import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import { scheduleJobs, scheduleEvents, fetchSchedule,
         toggleScheduleJob, triggerRebalance } from '../store';

function formatCountdown(next_run) {
  const diff = next_run - Date.now() / 1000;
  if (diff < 0) return 'overdue';
  const h = Math.floor(diff / 3600);
  const m = Math.floor((diff % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function formatInterval(seconds) {
  if (seconds % 86400 === 0) return `${seconds / 86400}d`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

const CATEGORY_COLORS = {
  critical: '#ef4444', high: '#f97316',
  normal: '#3b82f6', low: '#6b7280', background: '#374151',
};

function priorityCategory(p) {
  if (p <= 2) return 'critical';
  if (p <= 4) return 'high';
  if (p <= 6) return 'normal';
  if (p <= 8) return 'low';
  return 'background';
}

function TimelineBar({ jobs }) {
  // 24h bar showing when each job fires
  const now = Date.now() / 1000;
  const daySeconds = 86400;
  return (
    <div style={{ position: 'relative', height: 48, background: '#1e293b',
                  borderRadius: 4, overflow: 'hidden', margin: '1rem 0' }}>
      {jobs.map(rj => {
        const pct = ((rj.next_run - now) % daySeconds) / daySeconds * 100;
        const color = CATEGORY_COLORS[priorityCategory(rj.priority)];
        return (
          <div key={rj.id} title={`${rj.name} — ${formatCountdown(rj.next_run)}`}
               style={{
                 position: 'absolute', left: `${Math.max(0, pct)}%`,
                 width: 3, top: 0, bottom: 0, background: color, opacity: 0.8,
               }} />
        );
      })}
    </div>
  );
}

export function ScheduleTab() {
  useEffect(() => { fetchSchedule(); }, []);
  const jobs = scheduleJobs.value;
  const events = scheduleEvents.value;

  return (
    <div style={{ padding: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Schedule</h2>
        <button onClick={triggerRebalance}
                style={{ padding: '0.4rem 1rem', background: '#3b82f6',
                         color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
          Rebalance Now
        </button>
      </div>

      <TimelineBar jobs={jobs} />

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #334155' }}>
            <th style={{ textAlign: 'left', padding: '0.4rem' }}>Name</th>
            <th>Tag</th>
            <th>Interval</th>
            <th>Priority</th>
            <th>Next Run</th>
            <th>Enabled</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map(rj => {
            const cat = priorityCategory(rj.priority);
            const color = CATEGORY_COLORS[cat];
            return (
              <tr key={rj.id} style={{ borderBottom: '1px solid #1e293b' }}>
                <td style={{ padding: '0.5rem', borderLeft: `3px solid ${color}` }}>
                  {rj.name}
                </td>
                <td style={{ textAlign: 'center', color: '#94a3b8' }}>{rj.tag || '—'}</td>
                <td style={{ textAlign: 'center' }}>{formatInterval(rj.interval_seconds)}</td>
                <td style={{ textAlign: 'center' }}>
                  <span style={{ background: color, color: '#fff',
                                 padding: '0.1rem 0.5rem', borderRadius: 4, fontSize: 12 }}>
                    {cat} ({rj.priority})
                  </span>
                </td>
                <td style={{ textAlign: 'center' }}>{formatCountdown(rj.next_run)}</td>
                <td style={{ textAlign: 'center' }}>
                  <input type="checkbox" checked={!!rj.enabled}
                         onChange={e => toggleScheduleJob(rj.id, e.target.checked)} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <h3 style={{ marginTop: '2rem' }}>Rebalance Log</h3>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #334155' }}>
            <th style={{ textAlign: 'left' }}>Time</th>
            <th>Event</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {events.slice(0, 20).map(ev => (
            <tr key={ev.id} style={{ borderBottom: '1px solid #1e293b', fontSize: 12 }}>
              <td style={{ padding: '0.3rem', color: '#94a3b8' }}>
                {new Date(ev.timestamp * 1000).toLocaleTimeString()}
              </td>
              <td><code>{ev.event_type}</code></td>
              <td style={{ color: '#94a3b8' }}>{ev.details || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

### Step 3: Add Schedule tab to `App.jsx`

```jsx
// Add import
import { ScheduleTab } from './components/ScheduleTab';

// Add tab to tab list: ['Dashboard', 'Queue', 'Schedule', 'History', 'DLQ', 'Settings']
// Add case in tab renderer:
case 'Schedule': return <ScheduleTab />;
```

### Step 4: Build and verify

```bash
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build
```
Expected: Build succeeds, no errors.

### Step 5: Commit

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/
git commit -m "feat: add Schedule tab — 24h timeline, recurring jobs table, rebalance log"
```

---

## Task 10: Dashboard — DLQ Tab (Tab 5)

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/DLQTab.jsx`
- Modify: `ollama_queue/dashboard/spa/src/store.js`
- Modify: `ollama_queue/dashboard/spa/src/App.jsx`

### Step 1: Add DLQ state to store

```js
export const dlqEntries = signal([]);
export const dlqCount = signal(0);

export async function fetchDLQ() {
  const entries = await fetch('/api/dlq').then(r => r.json());
  dlqEntries.value = entries;
  dlqCount.value = entries.length;
}

export async function retryDLQEntry(id) {
  await fetch(`/api/dlq/${id}/retry`, { method: 'POST' });
  await fetchDLQ();
}

export async function retryAllDLQ() {
  await fetch('/api/dlq/retry-all', { method: 'POST' });
  await fetchDLQ();
}

export async function dismissDLQEntry(id) {
  await fetch(`/api/dlq/${id}/dismiss`, { method: 'POST' });
  await fetchDLQ();
}

export async function clearDLQ() {
  await fetch('/api/dlq', { method: 'DELETE' });
  await fetchDLQ();
}
```

### Step 2: Create `DLQTab.jsx`

```jsx
import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import { dlqEntries, fetchDLQ, retryDLQEntry, retryAllDLQ,
         dismissDLQEntry, clearDLQ } from '../store';

export function DLQTab() {
  const [expanded, setExpanded] = useState(null);
  useEffect(() => { fetchDLQ(); }, []);
  const entries = dlqEntries.value;

  if (entries.length === 0) {
    return (
      <div style={{ padding: '3rem', textAlign: 'center', color: '#64748b' }}>
        <div style={{ fontSize: 48 }}>✓</div>
        <div style={{ marginTop: '0.5rem' }}>No failed jobs</div>
      </div>
    );
  }

  return (
    <div style={{ padding: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Dead Letter Queue <span style={{ fontSize: 14, color: '#ef4444' }}>
          ({entries.length})
        </span></h2>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button onClick={retryAllDLQ}
                  style={{ padding: '0.4rem 1rem', background: '#3b82f6',
                           color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
            Retry All
          </button>
          <button onClick={clearDLQ}
                  style={{ padding: '0.4rem 1rem', background: '#374151',
                           color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
            Clear Resolved
          </button>
        </div>
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: '1rem' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #334155' }}>
            <th style={{ textAlign: 'left', padding: '0.4rem' }}>Command</th>
            <th>Source</th>
            <th>Tag</th>
            <th>Failure</th>
            <th>Retries</th>
            <th>Moved</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(e => (
            <>
              <tr key={e.id} style={{ borderBottom: '1px solid #1e293b', cursor: 'pointer' }}
                  onClick={() => setExpanded(expanded === e.id ? null : e.id)}>
                <td style={{ padding: '0.5rem', fontFamily: 'monospace',
                             color: '#94a3b8', maxWidth: 200, overflow: 'hidden',
                             textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {e.command}
                </td>
                <td style={{ textAlign: 'center' }}>{e.source || '—'}</td>
                <td style={{ textAlign: 'center' }}>{e.tag || '—'}</td>
                <td style={{ textAlign: 'center', color: '#ef4444' }}>{e.failure_reason}</td>
                <td style={{ textAlign: 'center' }}>{e.retry_count}</td>
                <td style={{ textAlign: 'center', color: '#94a3b8', fontSize: 11 }}>
                  {new Date(e.moved_at * 1000).toLocaleString()}
                </td>
                <td style={{ textAlign: 'center' }}>
                  <button onClick={ev => { ev.stopPropagation(); retryDLQEntry(e.id); }}
                          style={{ marginRight: 4, padding: '0.2rem 0.6rem',
                                   background: '#3b82f6', color: '#fff',
                                   border: 'none', borderRadius: 3, cursor: 'pointer' }}>
                    Retry
                  </button>
                  <button onClick={ev => { ev.stopPropagation(); dismissDLQEntry(e.id); }}
                          style={{ padding: '0.2rem 0.6rem', background: '#374151',
                                   color: '#fff', border: 'none', borderRadius: 3, cursor: 'pointer' }}>
                    Dismiss
                  </button>
                </td>
              </tr>
              {expanded === e.id && (
                <tr key={`${e.id}-detail`}>
                  <td colSpan={7} style={{ padding: '0.5rem 1rem',
                                           background: '#0f172a', fontFamily: 'monospace',
                                           fontSize: 11, color: '#94a3b8' }}>
                    <div><strong>stdout:</strong> {e.stdout_tail || '(empty)'}</div>
                    <div><strong>stderr:</strong> {e.stderr_tail || '(empty)'}</div>
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

### Step 3: Wire into `App.jsx`

```jsx
import { DLQTab } from './components/DLQTab';
import { dlqCount } from './store';

// Update tab labels to show badge:
const tabLabel = tab === 'DLQ' && dlqCount.value > 0
  ? `DLQ (${dlqCount.value})`
  : tab;

// Add case:
case 'DLQ': return <DLQTab />;
```

### Step 4: Build and verify

```bash
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build
```

### Step 5: Commit

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/
git commit -m "feat: add DLQ tab — failed jobs table, retry/dismiss, bulk actions, badge count"
```

---

## Task 11: Dashboard — Update Existing Tabs

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/QueueTab.jsx` (or equivalent)
- Modify: `ollama_queue/dashboard/spa/src/components/SettingsTab.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/HistoryTab.jsx`

### Step 1: Queue Tab additions

Add tag filter chips above pending jobs list:

```jsx
// Tag filter state
const [tagFilter, setTagFilter] = useState(null);
const tags = [...new Set(pendingJobs.value.map(j => j.tag).filter(Boolean))];

// Above the list:
<div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.5rem' }}>
  <span style={{ padding: '0.2rem 0.6rem', borderRadius: 12, cursor: 'pointer',
                 background: tagFilter === null ? '#3b82f6' : '#334155', color: '#fff' }}
        onClick={() => setTagFilter(null)}>All</span>
  {tags.map(tag => (
    <span key={tag} style={{ padding: '0.2rem 0.6rem', borderRadius: 12, cursor: 'pointer',
                              background: tagFilter === tag ? '#3b82f6' : '#334155', color: '#fff' }}
          onClick={() => setTagFilter(tag)}>{tag}</span>
  ))}
</div>
```

Add priority category color band to job rows:

```jsx
// In job row, add left border by priority:
const cat = priorityCategory(job.priority);  // same function as ScheduleTab
style={{ borderLeft: `3px solid ${CATEGORY_COLORS[cat]}` }}
```

Add retry badge:

```jsx
{job.retry_count > 0 && (
  <span style={{ fontSize: 10, background: '#f97316', color: '#fff',
                 padding: '0.1rem 0.3rem', borderRadius: 3, marginLeft: 4 }}>
    retry {job.retry_count}
  </span>
)}
```

### Step 2: Settings Tab additions

Add Priority Categories section:

```jsx
<h3>Priority Categories</h3>
<p style={{ fontSize: 12, color: '#94a3b8' }}>
  Changes trigger automatic rebalance of scheduled jobs.
</p>
{Object.entries(categories).map(([name, [min, max]]) => (
  <div key={name} style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: 8 }}>
    <input value={name} readOnly style={{ width: 100 }} />
    <span style={{ color: '#94a3b8' }}>Priority</span>
    <input type="number" min={1} max={10} value={min}
           onChange={e => updateCategory(name, Number(e.target.value), max)} style={{ width: 50 }} />
    <span>–</span>
    <input type="number" min={1} max={10} value={max}
           onChange={e => updateCategory(name, min, Number(e.target.value))} style={{ width: 50 }} />
    <div style={{ width: 20, height: 20, borderRadius: '50%',
                  background: CATEGORY_COLORS[name] }} />
  </div>
))}

<h3>Retry Defaults</h3>
<SettingRow label="Max Retries (default)" settingKey="default_max_retries" type="number" />
<SettingRow label="Backoff Base (seconds)" settingKey="retry_backoff_base_seconds" type="number" />
<SettingRow label="Backoff Multiplier" settingKey="retry_backoff_multiplier" type="number" step="0.1" />

<h3>Stall Detection</h3>
<SettingRow label="Stall Multiplier (×estimated)" settingKey="stall_multiplier" type="number" step="0.1" />
```

### Step 3: History Tab — tag filter + retry chain grouping

Add tag filter chips (same pattern as Queue tab).

Add stall indicator:

```jsx
{job.stall_detected_at && (
  <span title="Stall detected" style={{ color: '#f97316', marginLeft: 4 }}>⚠</span>
)}
```

### Step 4: Dashboard Tab — stall alert banner

```jsx
const stalledJobs = jobs.filter(j => j.stall_detected_at && j.status === 'running');
{stalledJobs.length > 0 && (
  <div style={{ background: '#7c2d12', color: '#fff', padding: '0.5rem 1rem',
                borderRadius: 4, marginBottom: '1rem' }}>
    ⚠ {stalledJobs.length} job(s) may be stalled. Check Queue tab.
  </div>
)}
```

### Step 5: Build and verify

```bash
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build
```

### Step 6: Commit

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/
git commit -m "feat: update existing tabs — tag filters, priority colors, retry badges, stall alerts, settings sections"
```

---

## Task 12: Migration Script

**Files:**
- Create: `scripts/migrate_timers.py`

### Step 1: Implement migration script

Create `scripts/migrate_timers.py`:

```python
#!/usr/bin/env python3
"""Migrate systemd timer units to ollama-queue recurring jobs.

Usage:
    python3 scripts/migrate_timers.py --dry-run   # preview only
    python3 scripts/migrate_timers.py --execute   # run migration
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Map timer name → (interval_seconds, model, priority, tag, command_suffix)
# Edit this mapping to match your actual timer ExecStart commands
TIMER_MAP = {
    "aria-full": {
        "interval": "24h", "model": "qwen2.5:14b", "priority": 3, "tag": "aria",
        "command": "aria predict --mode full",
    },
    "aria-intraday": {
        "interval": "4h", "model": "qwen2.5:7b", "priority": 4, "tag": "aria",
        "command": "aria predict --mode intraday",
    },
    # Add remaining timers here
}

UNIT_DIR = Path.home() / ".config/systemd/user"

def parse_interval(s: str) -> int:
    unit_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return int(s[:-1]) * unit_map[s[-1]]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--db", default=str(Path.home() / ".local/share/ollama-queue/queue.db"))
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("Specify --dry-run or --execute")
        sys.exit(1)

    for name, cfg in TIMER_MAP.items():
        timer_file = UNIT_DIR / f"{name}.timer"
        service_file = UNIT_DIR / f"{name}.service"

        if not timer_file.exists():
            print(f"SKIP {name} — timer file not found")
            continue

        interval_seconds = parse_interval(cfg["interval"])
        cmd = [
            "ollama-queue", "--db", args.db, "schedule", "add",
            "--name", name,
            "--interval", cfg["interval"],
            "--model", cfg["model"],
            "--priority", str(cfg["priority"]),
            "--tag", cfg["tag"],
            "--source", name,
            "--", cfg["command"],
        ]

        print(f"{'DRY RUN: ' if args.dry_run else ''}Register {name} "
              f"(every {cfg['interval']}, priority={cfg['priority']})")

        if args.execute:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  ERROR: {result.stderr.strip()}")
                continue
            print(f"  OK: {result.stdout.strip()}")

            # Disable and delete timer unit
            subprocess.run(["systemctl", "--user", "disable", "--now", f"{name}.timer"],
                           capture_output=True)
            timer_file.unlink(missing_ok=True)
            service_file.unlink(missing_ok=True)
            print(f"  Deleted: {timer_file}")

    if args.execute:
        subprocess.run(["systemctl", "--user", "daemon-reload"])
        subprocess.run(["ollama-queue", "--db", args.db, "schedule", "rebalance"])
        print("\nMigration complete. Run: ollama-queue schedule list")

if __name__ == "__main__":
    main()
```

### Step 2: Dry-run to verify

```bash
cd ~/Documents/projects/ollama-queue
python3 scripts/migrate_timers.py --dry-run
```
Expected: Preview of all timers with no changes applied.

### Step 3: Commit

```bash
git add scripts/migrate_timers.py
git commit -m "feat: add migrate_timers.py — convert systemd timers to recurring jobs"
```

---

## Task 13: Full Test Suite + Integration Verification

**Files:**
- Run: all tests
- Run: vertical pipeline trace

### Step 1: Run full test suite

```bash
cd ~/Documents/projects/ollama-queue && source .venv/bin/activate
pytest --timeout=120 -x -q
```
Expected: All tests pass. Note count — should be 71+ (existing) + new.

### Step 2: Build dashboard

```bash
cd ollama_queue/dashboard/spa && npm run build
```
Expected: `dist/` updated, no errors.

### Step 3: Vertical pipeline trace

Start the server in background, then trace a recurring job end-to-end:

```bash
# Terminal 1:
ollama-queue serve --port 7683 &

# Add a test recurring job
ollama-queue schedule add --name test-trace --interval 5s --source test -- echo "trace-ok"

# Wait for daemon to promote and run it (~5s)
sleep 10

# Verify end-to-end
ollama-queue history --all | grep trace-ok
ollama-queue schedule list  # next_run should have advanced

# Check API
curl -s http://localhost:7683/api/schedule | python3 -m json.tool
curl -s http://localhost:7683/api/schedule/events | python3 -m json.tool
```
Expected: Job appears in history with exit_code=0, next_run advanced by 5s from completion.

### Step 4: Commit final state

```bash
git add -A
git commit -m "feat: ollama-queue v2 complete — scheduler, DLQ, retry, stall, tags, 6-tab dashboard"
```

---

## Task 14: Migration Execution

**Run only after Task 13 passes.**

### Step 1: Back up current DB

```bash
cp ~/.local/share/ollama-queue/queue.db ~/.local/share/ollama-queue/queue.db.pre-v2
```

### Step 2: Update TIMER_MAP in migrate_timers.py

Edit `scripts/migrate_timers.py` to fill in the correct commands for all 10 Ollama timer units. Cross-reference with:

```bash
cat ~/.config/systemd/user/aria-full.service
cat ~/.config/systemd/user/aria-intraday.service
# ... for each timer
```

### Step 3: Dry-run

```bash
python3 scripts/migrate_timers.py --dry-run
```
Review output. Verify all 10 timers are covered.

### Step 4: Stop ollama-queue service

```bash
systemctl --user stop ollama-queue
```

### Step 5: Execute migration

```bash
python3 scripts/migrate_timers.py --execute
```

### Step 6: Restart service

```bash
systemctl --user start ollama-queue
systemctl --user status ollama-queue
```

### Step 7: Verify

```bash
ollama-queue schedule list
ollama-queue status
```
Expected: All 10 jobs listed, systemd timers gone, queue running.

### Step 8: Final commit

```bash
git add scripts/migrate_timers.py
git commit -m "chore: populate TIMER_MAP with all 10 Ollama timer migrations"
```

---

## Summary

| Task | Feature | Tests |
|---|---|---|
| 1 | Schema migrations | +4 tests |
| 2 | Recurring job DB methods | +6 tests |
| 3 | DLQ DB methods | +5 tests |
| 4 | Scheduler class | +8 tests |
| 5 | DLQManager class | +4 tests |
| 6 | Daemon integration | +2 tests |
| 7 | CLI — schedule + dlq | +6 tests |
| 8 | API — 10 new endpoints | +9 tests |
| 9 | Dashboard — Schedule tab | build |
| 10 | Dashboard — DLQ tab | build |
| 11 | Dashboard — existing tab updates | build |
| 12 | Migration script | dry-run |
| 13 | Integration verification | pipeline |
| 14 | Migration execution | live |
