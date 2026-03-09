# DLQ Auto-Reschedule & Deferred Jobs — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the queue smart enough to auto-reschedule failed (DLQ) and proactively defer jobs into slots where they'll actually succeed, using a Bayesian intelligence layer that learns from this machine's real performance.

**Architecture:** New `intelligence.py` module (RuntimeEstimator, SystemSnapshot, PerformanceCurve, LoadPatterns) consumed by `dlq_scheduler.py` and `deferral_scheduler.py`. Daemon hooks trigger event-driven sweeps on job completion + periodic fallback. Dashboard gets Performance tab with research-backed charts (uPlot scatter/regression, heatmap, sparklines).

**Tech Stack:** Python 3.12, SQLite (WAL), FastAPI, Preact 10 + @preact/signals + uPlot + Tailwind v4, numpy (new dep for regression), scipy (new dep for log-normal stats)

**Design Doc:** `docs/plans/2026-03-09-dlq-auto-reschedule-design.md`

**Execution:** Subagent-driven development with code review agents between batches. Parallel agents within batches where tasks are independent. Quality gates: `pytest --timeout=120 -x -q` + `npm run build` + lesson-scanner between every batch.

---

## Quality Gate (run between every batch)

```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q
cd ollama_queue/dashboard/spa && npm run build && cd ../../..
```

If either fails, fix before proceeding. After each batch, run code-review agent on changed files.

---

## Batch 1: Database Schema & Core Data Layer

Foundation — all other batches depend on this. No runtime behavior yet, just schema and CRUD.

### Task 1: job_metrics table

**Files:**
- Modify: `ollama_queue/db.py` (add table creation at ~line 320, after dlq table)
- Create: `tests/test_job_metrics.py`

**Step 1: Write failing tests**

```python
# tests/test_job_metrics.py
import pytest
from ollama_queue.db import Database
import tempfile, os

@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    d = Database(path)
    yield d
    os.unlink(path)

def test_store_job_metrics(db):
    job_id = db.submit_job("echo hi", "qwen3.5:9b", source="test")
    db.start_job(job_id)
    db.complete_job(job_id, 0)
    db.store_job_metrics(job_id, {
        "model": "qwen3.5:9b",
        "command": "echo hi",
        "resource_profile": "ollama",
        "load_duration_ns": 2154458,
        "prompt_eval_count": 26,
        "prompt_eval_duration_ns": 383809000,
        "eval_count": 298,
        "eval_duration_ns": 4799921000,
        "total_duration_ns": 5191217417,
        "model_size_gb": 5.4,
    })
    metrics = db.get_job_metrics(job_id)
    assert metrics is not None
    assert metrics["eval_count"] == 298
    assert metrics["model"] == "qwen3.5:9b"

def test_get_tok_per_min(db):
    """Derive tok/min from stored metrics."""
    job_id = db.submit_job("echo hi", "qwen3.5:9b", source="test")
    db.start_job(job_id)
    db.complete_job(job_id, 0)
    db.store_job_metrics(job_id, {
        "model": "qwen3.5:9b",
        "eval_count": 300,
        "eval_duration_ns": 5_000_000_000,  # 5 seconds
    })
    rates = db.get_tok_per_min("qwen3.5:9b")
    # 300 tokens / 5s = 60 tok/s = 3600 tok/min
    assert len(rates) == 1
    assert abs(rates[0] - 3600.0) < 0.1

def test_get_job_durations(db):
    """Historical wall-clock durations for a model."""
    for i in range(3):
        jid = db.submit_job(f"cmd{i}", "qwen3.5:9b", source="test")
        db.start_job(jid)
        db.complete_job(jid, 0)
    durations = db.get_job_durations("qwen3.5:9b")
    assert len(durations) == 3
    for d in durations:
        assert d >= 0

def test_get_load_durations(db):
    """Warmup times for a model."""
    job_id = db.submit_job("echo", "deepseek-r1:8b", source="test")
    db.start_job(job_id)
    db.complete_job(job_id, 0)
    db.store_job_metrics(job_id, {
        "model": "deepseek-r1:8b",
        "load_duration_ns": 1_800_000_000,  # 1.8 seconds
    })
    warmups = db.get_load_durations("deepseek-r1:8b")
    assert len(warmups) == 1
    assert abs(warmups[0] - 1.8) < 0.01

def test_get_model_stats(db):
    """Aggregate stats per model."""
    for i in range(5):
        jid = db.submit_job(f"cmd{i}", "qwen3.5:9b", source="test")
        db.start_job(jid)
        db.complete_job(jid, 0)
        db.store_job_metrics(jid, {
            "model": "qwen3.5:9b",
            "eval_count": 100 + i * 10,
            "eval_duration_ns": 2_000_000_000,
            "load_duration_ns": 1_500_000_000,
            "model_size_gb": 5.4,
        })
    stats = db.get_model_stats()
    assert "qwen3.5:9b" in stats
    s = stats["qwen3.5:9b"]
    assert s["run_count"] == 5
    assert s["avg_tok_per_min"] > 0
    assert s["avg_warmup_s"] > 0
    assert s["model_size_gb"] == 5.4

def test_metrics_missing_fields_stored_as_null(db):
    """Non-Ollama jobs store partial metrics."""
    job_id = db.submit_job("bash script.sh", "none", source="test")
    db.start_job(job_id)
    db.complete_job(job_id, 0)
    db.store_job_metrics(job_id, {"model": "none"})
    m = db.get_job_metrics(job_id)
    assert m is not None
    assert m["eval_count"] is None
```

**Step 2: Run tests — expect FAIL** (methods don't exist yet)

```bash
pytest tests/test_job_metrics.py -v
```

**Step 3: Implement in db.py**

Add to `initialize()` after dlq table (~line 320):

```python
# job_metrics table — Ollama performance metrics per completed job
cursor.execute("""
    CREATE TABLE IF NOT EXISTS job_metrics (
        job_id TEXT PRIMARY KEY,
        model TEXT NOT NULL,
        command TEXT,
        resource_profile TEXT,
        load_duration_ns INTEGER,
        prompt_eval_count INTEGER,
        prompt_eval_duration_ns INTEGER,
        eval_count INTEGER,
        eval_duration_ns INTEGER,
        total_duration_ns INTEGER,
        model_size_gb REAL,
        completed_at REAL,
        FOREIGN KEY (job_id) REFERENCES jobs(id)
    )
""")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_metrics_model ON job_metrics(model)")
```

Add CRUD methods after `clear_dlq()` (~line 1604):

```python
def store_job_metrics(self, job_id: str, metrics: dict) -> None:
    """Store Ollama performance metrics for a completed job."""
    with self._lock:
        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO job_metrics
               (job_id, model, command, resource_profile,
                load_duration_ns, prompt_eval_count, prompt_eval_duration_ns,
                eval_count, eval_duration_ns, total_duration_ns,
                model_size_gb, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, metrics.get("model"), metrics.get("command"),
             metrics.get("resource_profile"),
             metrics.get("load_duration_ns"), metrics.get("prompt_eval_count"),
             metrics.get("prompt_eval_duration_ns"),
             metrics.get("eval_count"), metrics.get("eval_duration_ns"),
             metrics.get("total_duration_ns"),
             metrics.get("model_size_gb"), time.time()),
        )
        conn.commit()

def get_job_metrics(self, job_id: str) -> dict | None:
    """Get stored metrics for a specific job."""
    with self._lock:
        conn = self._connect()
        row = conn.execute("SELECT * FROM job_metrics WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

def get_tok_per_min(self, model: str) -> list[float]:
    """Get historical tok/min rates for a model from stored metrics."""
    with self._lock:
        conn = self._connect()
        rows = conn.execute(
            """SELECT eval_count, eval_duration_ns FROM job_metrics
               WHERE model = ? AND eval_count IS NOT NULL
               AND eval_duration_ns IS NOT NULL AND eval_duration_ns > 0
               ORDER BY completed_at DESC LIMIT 50""",
            (model,),
        ).fetchall()
        return [(r["eval_count"] / r["eval_duration_ns"]) * 60e9 for r in rows]

def get_job_durations(self, model: str, command: str | None = None) -> list[float]:
    """Get historical wall-clock durations (seconds) for completed jobs."""
    with self._lock:
        conn = self._connect()
        if command:
            rows = conn.execute(
                """SELECT completed_at - started_at AS duration FROM jobs
                   WHERE model = ? AND command = ? AND status = 'complete'
                   AND completed_at IS NOT NULL AND started_at IS NOT NULL
                   ORDER BY completed_at DESC LIMIT 50""",
                (model, command),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT completed_at - started_at AS duration FROM jobs
                   WHERE model = ? AND status = 'complete'
                   AND completed_at IS NOT NULL AND started_at IS NOT NULL
                   ORDER BY completed_at DESC LIMIT 50""",
                (model,),
            ).fetchall()
        return [r["duration"] for r in rows]

def get_load_durations(self, model: str) -> list[float]:
    """Get historical warmup/load times (seconds) for a model."""
    with self._lock:
        conn = self._connect()
        rows = conn.execute(
            """SELECT load_duration_ns FROM job_metrics
               WHERE model = ? AND load_duration_ns IS NOT NULL
               AND load_duration_ns > 0
               ORDER BY completed_at DESC LIMIT 50""",
            (model,),
        ).fetchall()
        return [r["load_duration_ns"] / 1e9 for r in rows]

def get_model_stats(self) -> dict[str, dict]:
    """Get aggregate performance stats per model."""
    with self._lock:
        conn = self._connect()
        rows = conn.execute(
            """SELECT model,
                      COUNT(*) as run_count,
                      AVG(CASE WHEN eval_count IS NOT NULL AND eval_duration_ns > 0
                          THEN (eval_count * 1.0 / eval_duration_ns) * 60e9 END) as avg_tok_per_min,
                      AVG(CASE WHEN load_duration_ns IS NOT NULL AND load_duration_ns > 0
                          THEN load_duration_ns / 1e9 END) as avg_warmup_s,
                      MAX(model_size_gb) as model_size_gb,
                      MAX(completed_at) as last_run
               FROM job_metrics
               GROUP BY model"""
        ).fetchall()
        return {r["model"]: dict(r) for r in rows}
```

**Step 4: Run tests — expect PASS**

```bash
pytest tests/test_job_metrics.py -v
```

**Step 5: Commit**

```bash
git add ollama_queue/db.py tests/test_job_metrics.py
git commit -m "feat: add job_metrics table for Ollama performance tracking

Stores per-job: load_duration, prompt_eval, eval_count/duration,
model_size. Derived queries: tok/min, warmup, wall-clock duration,
aggregate model stats. Foundation for Bayesian runtime estimator."
```

---

### Task 2: DLQ schema additions

**Files:**
- Modify: `ollama_queue/db.py` (ALTER TABLE in initialize, ~line 320)
- Modify: `tests/test_dlq.py` (add tests for new columns)

**Step 1: Write failing tests**

Add to `tests/test_dlq.py`:

```python
def test_dlq_auto_reschedule_columns(db):
    """DLQ entries have auto-reschedule tracking columns."""
    job_id = db.submit_job("echo fail", "test-model", source="test")
    db.start_job(job_id)
    db.complete_job(job_id, 1)
    dlq_id = db.move_to_dlq(job_id, "exit code 1")
    entry = db.get_dlq_entry(dlq_id)
    assert entry["auto_reschedule_count"] == 0
    assert entry["auto_rescheduled_at"] is None
    assert entry["rescheduled_job_id"] is None
    assert entry["rescheduled_for"] is None
    assert entry["reschedule_reasoning"] is None

def test_update_dlq_reschedule(db):
    """Update DLQ entry with reschedule info."""
    job_id = db.submit_job("echo fail", "test-model", source="test")
    db.start_job(job_id)
    db.complete_job(job_id, 1)
    dlq_id = db.move_to_dlq(job_id, "exit code 1")

    import time, json
    now = time.time()
    reasoning = json.dumps({"score": 7.2, "reasons": ["load headroom: 8.0"]})
    db.update_dlq_reschedule(dlq_id,
        rescheduled_job_id="new-123",
        rescheduled_for=now + 3600,
        reschedule_reasoning=reasoning)

    entry = db.get_dlq_entry(dlq_id)
    assert entry["auto_rescheduled_at"] is not None
    assert entry["rescheduled_job_id"] == "new-123"
    assert entry["rescheduled_for"] > now
    assert "load headroom" in entry["reschedule_reasoning"]

def test_list_dlq_unscheduled_only(db):
    """list_dlq with unscheduled_only=True excludes already-rescheduled entries."""
    # Create two DLQ entries
    j1 = db.submit_job("cmd1", "m", source="test")
    db.start_job(j1)
    db.complete_job(j1, 1)
    dlq1 = db.move_to_dlq(j1, "fail1")

    j2 = db.submit_job("cmd2", "m", source="test")
    db.start_job(j2)
    db.complete_job(j2, 1)
    dlq2 = db.move_to_dlq(j2, "fail2")

    # Reschedule one
    db.update_dlq_reschedule(dlq1, rescheduled_job_id="new-1", rescheduled_for=9999999999.0)

    # Unscheduled only should return just the second
    unscheduled = db.list_dlq(unscheduled_only=True)
    assert len(unscheduled) == 1
    assert unscheduled[0]["id"] == dlq2
```

**Step 2: Run tests — expect FAIL**

**Step 3: Implement**

Add ALTER TABLE statements in `initialize()` (idempotent pattern matching existing migrations):

```python
# DLQ auto-reschedule columns (idempotent)
for col, typedef in [
    ("auto_reschedule_count", "INTEGER DEFAULT 0"),
    ("auto_rescheduled_at", "REAL"),
    ("rescheduled_job_id", "TEXT"),
    ("rescheduled_for", "REAL"),
    ("reschedule_reasoning", "TEXT"),
]:
    try:
        cursor.execute(f"ALTER TABLE dlq ADD COLUMN {col} {typedef}")
    except sqlite3.OperationalError:
        pass  # column already exists
```

Add new methods:

```python
def update_dlq_reschedule(self, dlq_id: int, rescheduled_job_id: str,
                          rescheduled_for: float,
                          reschedule_reasoning: str | None = None) -> None:
    """Mark a DLQ entry as auto-rescheduled."""
    with self._lock:
        conn = self._connect()
        conn.execute(
            """UPDATE dlq SET auto_rescheduled_at = ?, rescheduled_job_id = ?,
               rescheduled_for = ?, reschedule_reasoning = ?
               WHERE id = ?""",
            (time.time(), rescheduled_job_id, rescheduled_for,
             reschedule_reasoning, dlq_id),
        )
        conn.commit()

def list_dlq(self, include_resolved=False, unscheduled_only=False):
    """List DLQ entries. unscheduled_only excludes already auto-rescheduled."""
    with self._lock:
        conn = self._connect()
        if include_resolved:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM dlq ORDER BY moved_at DESC").fetchall()]
        where = "WHERE resolved_at IS NULL"
        if unscheduled_only:
            where += " AND auto_rescheduled_at IS NULL"
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM dlq {where} ORDER BY moved_at DESC").fetchall()]
```

**Step 4: Run tests — expect PASS**

**Step 5: Run full test suite to verify no regressions**

```bash
pytest --timeout=120 -x -q
```

**Step 6: Commit**

```bash
git add ollama_queue/db.py tests/test_dlq.py
git commit -m "feat: add DLQ auto-reschedule tracking columns

New columns: auto_reschedule_count, auto_rescheduled_at,
rescheduled_job_id, rescheduled_for, reschedule_reasoning.
list_dlq gains unscheduled_only parameter for sweep filtering."
```

---

### Task 3: Deferrals table

**Files:**
- Modify: `ollama_queue/db.py` (new table + CRUD)
- Modify: `ollama_queue/db.py` (add 'deferred' to valid job statuses if gated anywhere)
- Create: `tests/test_deferral.py`

**Step 1: Write failing tests**

```python
# tests/test_deferral.py
import pytest
from ollama_queue.db import Database
import tempfile, os, time, json

@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    d = Database(path)
    yield d
    os.unlink(path)

def test_defer_job(db):
    """Defer a pending job."""
    job_id = db.submit_job("echo hi", "qwen3.5:9b", source="test")
    deferral_id = db.defer_job(job_id, reason="resource",
                                context="needs 10GB, 4GB free")
    assert deferral_id is not None

    # Job status should be 'deferred'
    job = db.get_job(job_id)
    assert job["status"] == "deferred"

    # Deferral record exists
    d = db.get_deferral(deferral_id)
    assert d["job_id"] == job_id
    assert d["reason"] == "resource"
    assert d["deferred_at"] is not None
    assert d["resumed_at"] is None

def test_list_deferred(db):
    j1 = db.submit_job("cmd1", "m", source="test")
    j2 = db.submit_job("cmd2", "m", source="test")
    db.defer_job(j1, reason="burst")
    db.defer_job(j2, reason="thermal")

    deferred = db.list_deferred()
    assert len(deferred) == 2

def test_list_deferred_unscheduled_only(db):
    j1 = db.submit_job("cmd1", "m", source="test")
    j2 = db.submit_job("cmd2", "m", source="test")
    d1 = db.defer_job(j1, reason="burst")
    d2 = db.defer_job(j2, reason="thermal")

    # Schedule one
    db.update_deferral_schedule(d1, scheduled_for=time.time() + 3600,
                                scoring_snapshot='{"score": 5}')

    unscheduled = db.list_deferred(unscheduled_only=True)
    assert len(unscheduled) == 1
    assert unscheduled[0]["id"] == d2

def test_resume_deferred_job(db):
    job_id = db.submit_job("echo hi", "qwen3.5:9b", source="test")
    deferral_id = db.defer_job(job_id, reason="thermal")

    db.resume_deferred_job(deferral_id)

    # Job back to pending
    job = db.get_job(job_id)
    assert job["status"] == "pending"

    # Deferral marked resumed
    d = db.get_deferral(deferral_id)
    assert d["resumed_at"] is not None

def test_deferred_job_keeps_same_id(db):
    """Deferred jobs keep their original job ID — no new job created."""
    job_id = db.submit_job("echo hi", "qwen3.5:9b", source="test", priority=8)
    db.defer_job(job_id, reason="resource")

    # Still the same job
    job = db.get_job(job_id)
    assert job["priority"] == 8  # preserves original priority
```

**Step 2: Run tests — expect FAIL**

**Step 3: Implement**

Add deferrals table in `initialize()`:

```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS deferrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        deferred_at REAL NOT NULL,
        estimated_ready_at REAL,
        scheduled_for REAL,
        scoring_snapshot TEXT,
        resumed_at REAL,
        FOREIGN KEY (job_id) REFERENCES jobs(id)
    )
""")
```

Add methods:

```python
def defer_job(self, job_id: str, reason: str, context: str = "") -> int:
    """Defer a job — sets status to 'deferred' and creates deferral record."""
    with self._lock:
        conn = self._connect()
        now = time.time()
        conn.execute("UPDATE jobs SET status = 'deferred' WHERE id = ?", (job_id,))
        cursor = conn.execute(
            """INSERT INTO deferrals (job_id, reason, deferred_at)
               VALUES (?, ?, ?)""",
            (job_id, reason, now),
        )
        conn.commit()
        return cursor.lastrowid

def get_deferral(self, deferral_id: int) -> dict | None:
    with self._lock:
        conn = self._connect()
        row = conn.execute("SELECT * FROM deferrals WHERE id = ?",
                           (deferral_id,)).fetchone()
        return dict(row) if row else None

def list_deferred(self, unscheduled_only: bool = False) -> list[dict]:
    with self._lock:
        conn = self._connect()
        where = "WHERE resumed_at IS NULL"
        if unscheduled_only:
            where += " AND scheduled_for IS NULL"
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM deferrals {where} ORDER BY deferred_at ASC"
        ).fetchall()]

def update_deferral_schedule(self, deferral_id: int, scheduled_for: float,
                             scoring_snapshot: str | None = None) -> None:
    with self._lock:
        conn = self._connect()
        conn.execute(
            """UPDATE deferrals SET scheduled_for = ?, scoring_snapshot = ?
               WHERE id = ?""",
            (scheduled_for, scoring_snapshot, deferral_id),
        )
        conn.commit()

def resume_deferred_job(self, deferral_id: int) -> None:
    """Resume a deferred job — flip status back to pending, mark deferral as resumed."""
    with self._lock:
        conn = self._connect()
        row = conn.execute("SELECT job_id FROM deferrals WHERE id = ?",
                           (deferral_id,)).fetchone()
        if not row:
            return
        now = time.time()
        conn.execute("UPDATE jobs SET status = 'pending' WHERE id = ?",
                     (row["job_id"],))
        conn.execute("UPDATE deferrals SET resumed_at = ? WHERE id = ?",
                     (now, deferral_id))
        conn.commit()
```

**Step 4: Run tests — expect PASS**

**Step 5: Full suite**

```bash
pytest --timeout=120 -x -q
```

**Step 6: Commit**

```bash
git add ollama_queue/db.py tests/test_deferral.py
git commit -m "feat: add deferrals table and job deferral lifecycle

Deferred jobs keep their original ID (no new job created).
Status flips: pending → deferred → pending on resume.
Tracks: reason, scheduled_for, scoring_snapshot, resumed_at."
```

---

### Batch 1 Quality Gate

```bash
pytest --timeout=120 -x -q
```

Run code-review agent on: `ollama_queue/db.py`, `tests/test_job_metrics.py`, `tests/test_dlq.py`, `tests/test_deferral.py`

Run lesson-scanner: `lessons-db scan --target ~/Documents/projects/ollama-queue --baseline HEAD~3`

---

## Batch 2: Intelligence Layer — Core Estimation

The Bayesian runtime estimator and performance curve fitting. Pure computation — no daemon or API integration yet.

### Task 4: RuntimeEstimator — Bayesian core

**Files:**
- Create: `ollama_queue/intelligence.py`
- Create: `tests/test_intelligence.py`

**Step 1: Write failing tests**

```python
# tests/test_intelligence.py
import pytest
from unittest.mock import MagicMock
from ollama_queue.intelligence import RuntimeEstimator, Estimate

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_tok_per_min.return_value = []
    db.get_job_durations.return_value = []
    db.get_load_durations.return_value = []
    db.get_model_stats.return_value = {}
    return db

def test_estimate_with_no_history(mock_db):
    """Cold start — uses resource profile prior only."""
    est = RuntimeEstimator(mock_db)
    result = est.estimate("unknown-model", "echo hi", "medium")
    assert isinstance(result, Estimate)
    assert result.total_mean > 0
    assert result.total_upper > result.total_mean
    assert result.confidence == "low"

def test_estimate_with_tok_per_min_history(mock_db):
    """With observed tok/min, estimate is informed."""
    mock_db.get_tok_per_min.return_value = [80.0, 85.0, 78.0, 82.0, 84.0]
    mock_db.get_job_durations.return_value = [30.0, 32.0, 28.0, 35.0, 31.0]
    est = RuntimeEstimator(mock_db)
    result = est.estimate("qwen3.5:9b", "generate", "ollama")
    assert result.confidence in ("medium", "high")
    assert result.generation_mean > 0

def test_estimate_warmup_cold(mock_db):
    """Warmup estimate for cold model (not loaded)."""
    mock_db.get_load_durations.return_value = [1.8, 2.0, 1.7, 1.9]
    est = RuntimeEstimator(mock_db)
    result = est.estimate("qwen3.5:9b", "generate", "ollama",
                          loaded_models=[])
    assert result.warmup_mean > 0
    assert result.total_mean > result.generation_mean

def test_estimate_warmup_hot(mock_db):
    """Hot model — warmup should be ~0."""
    mock_db.get_load_durations.return_value = [1.8, 2.0, 1.7]
    est = RuntimeEstimator(mock_db)
    result = est.estimate("qwen3.5:9b", "generate", "ollama",
                          loaded_models=["qwen3.5:9b"])
    assert result.warmup_mean == 0.0

def test_confidence_scales_with_observations(mock_db):
    """More observations → higher confidence."""
    est = RuntimeEstimator(mock_db)

    mock_db.get_job_durations.return_value = [30.0]
    r1 = est.estimate("m", "c", "ollama")

    mock_db.get_job_durations.return_value = [30.0] * 10
    r2 = est.estimate("m", "c", "ollama")

    confidence_order = {"low": 0, "medium": 1, "high": 2}
    assert confidence_order[r2.confidence] >= confidence_order[r1.confidence]

def test_profile_priors_differ(mock_db):
    """Different resource profiles give different prior estimates."""
    est = RuntimeEstimator(mock_db)
    light = est.estimate("m", "c", "light")
    heavy = est.estimate("m", "c", "gpu_heavy")
    assert heavy.total_mean > light.total_mean
```

**Step 2: Run tests — expect FAIL**

**Step 3: Implement `intelligence.py`**

```python
"""Intelligence layer — Bayesian runtime estimation, system snapshot, load patterns.

Provides RuntimeEstimator (log-normal Bayesian estimation with hierarchical priors),
PerformanceCurve (cross-model regression), SystemSnapshot, and LoadPatterns.
Consumed by DLQScheduler, DeferralScheduler, and (future) daemon/scheduler.
"""
import math
import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Resource profile priors (log-normal parameters: log-mean, log-std, pseudo-observations)
PROFILE_PRIORS = {
    "light":     {"log_mean": math.log(30),  "log_std": 0.8, "n0": 2},
    "medium":    {"log_mean": math.log(120), "log_std": 0.7, "n0": 2},
    "heavy":     {"log_mean": math.log(600), "log_std": 0.6, "n0": 2},
    "gpu_heavy": {"log_mean": math.log(900), "log_std": 0.5, "n0": 2},
    "ollama":    {"log_mean": math.log(300), "log_std": 0.8, "n0": 1},
}

WARMUP_PRIOR = {"log_mean": math.log(3.0), "log_std": 1.0, "n0": 1}


@dataclass
class Estimate:
    """Runtime estimate with uncertainty."""
    warmup_mean: float = 0.0
    warmup_upper: float = 0.0
    generation_mean: float = 0.0
    generation_upper: float = 0.0
    total_mean: float = 0.0
    total_upper: float = 0.0
    confidence: str = "low"  # low, medium, high
    n_observations: int = 0


class RuntimeEstimator:
    """Bayesian runtime estimator using log-normal model with hierarchical priors."""

    def __init__(self, db):
        self.db = db
        self._curve = None
        self._curve_fitted_at = 0
        self._curve_refit_interval = 300  # 5 minutes

    def estimate(self, model: str, command: str | None,
                 resource_profile: str,
                 loaded_models: list[str] | None = None) -> Estimate:
        """Estimate runtime for a job.

        Uses 4-tier hierarchy:
        1. Resource profile prior (weakest)
        2. Cross-model performance curve
        3. Model-level tok/min history
        4. (Model, command) duration history (strongest)
        """
        # Tier 1: resource profile prior
        prior = PROFILE_PRIORS.get(resource_profile, PROFILE_PRIORS["ollama"]).copy()

        # Tier 2: cross-model curve (if fitted)
        # Implemented in Task 5 — PerformanceCurve

        # Tier 3: model-level historical durations
        durations = self.db.get_job_durations(model)
        n_obs = len(durations)

        # Tier 4: (model, command) specific durations
        if command:
            specific = self.db.get_job_durations(model, command)
            if len(specific) >= 3:
                durations = specific
                n_obs = len(specific)

        # Bayesian update
        if durations:
            log_durations = [math.log(max(d, 0.1)) for d in durations]
            n = len(log_durations)
            sample_mean = sum(log_durations) / n

            # Posterior mean (weighted average of prior and sample)
            n0 = prior["n0"]
            post_mean = (n0 * prior["log_mean"] + n * sample_mean) / (n0 + n)

            # Posterior variance
            if n > 1:
                sample_var = sum((x - sample_mean) ** 2 for x in log_durations) / (n - 1)
            else:
                sample_var = prior["log_std"] ** 2
            post_std = math.sqrt(
                (n0 * prior["log_std"] ** 2 + n * sample_var) / (n0 + n)
            )
        else:
            post_mean = prior["log_mean"]
            post_std = prior["log_std"]

        gen_mean = math.exp(post_mean)
        gen_upper = math.exp(post_mean + 1.28 * post_std)  # 90th percentile

        # Warmup estimate
        warmup_mean = 0.0
        warmup_upper = 0.0
        if loaded_models is None or model not in (loaded_models or []):
            warmup_mean, warmup_upper = self._estimate_warmup(model)

        # Confidence
        confidence = self._confidence_level(n_obs)

        return Estimate(
            warmup_mean=warmup_mean,
            warmup_upper=warmup_upper,
            generation_mean=gen_mean,
            generation_upper=gen_upper,
            total_mean=warmup_mean + gen_mean,
            total_upper=warmup_upper + gen_upper,
            confidence=confidence,
            n_observations=n_obs,
        )

    def _estimate_warmup(self, model: str) -> tuple[float, float]:
        """Estimate model warmup time from historical load_duration data."""
        warmups = self.db.get_load_durations(model)
        prior = WARMUP_PRIOR.copy()

        if warmups:
            log_warmups = [math.log(max(w, 0.01)) for w in warmups]
            n = len(log_warmups)
            sample_mean = sum(log_warmups) / n
            n0 = prior["n0"]
            post_mean = (n0 * prior["log_mean"] + n * sample_mean) / (n0 + n)

            if n > 1:
                sample_var = sum((x - sample_mean) ** 2 for x in log_warmups) / (n - 1)
            else:
                sample_var = prior["log_std"] ** 2
            post_std = math.sqrt(
                (n0 * prior["log_std"] ** 2 + n * sample_var) / (n0 + n)
            )
        else:
            post_mean = prior["log_mean"]
            post_std = prior["log_std"]

        mean = math.exp(post_mean)
        upper = math.exp(post_mean + 1.28 * post_std)
        return mean, upper

    def _confidence_level(self, n_observations: int) -> str:
        if n_observations >= 5:
            return "high"
        elif n_observations >= 2:
            return "medium"
        return "low"

    def refresh(self, job_id: str | None = None) -> None:
        """Called after job completion — update internal caches if needed."""
        # Curve refit is handled lazily in PerformanceCurve (Task 5)
        pass
```

**Step 4: Run tests — expect PASS**

**Step 5: Commit**

```bash
git add ollama_queue/intelligence.py tests/test_intelligence.py
git commit -m "feat: add RuntimeEstimator with log-normal Bayesian estimation

4-tier hierarchical estimation: resource profile prior → cross-model
curve → model-level history → (model, command) specifics.
Separate warmup estimation from generation.
Confidence: low (<2 obs), medium (2-4), high (5+)."
```

---

### Task 5: PerformanceCurve — cross-model regression

**Files:**
- Modify: `ollama_queue/intelligence.py` (add PerformanceCurve class)
- Modify: `tests/test_intelligence.py` (add curve tests)

**Step 1: Write failing tests**

Add to `tests/test_intelligence.py`:

```python
from ollama_queue.intelligence import PerformanceCurve

def test_performance_curve_no_data():
    """No data — predict returns None."""
    curve = PerformanceCurve()
    assert curve.predict_tok_per_min(5.0) is None

def test_performance_curve_single_point():
    """Single data point — linear extrapolation from that point."""
    curve = PerformanceCurve()
    curve.fit([{"model_size_gb": 5.0, "avg_tok_per_min": 80.0}])
    result = curve.predict_tok_per_min(5.0)
    assert result is not None
    assert abs(result - 80.0) < 1.0

def test_performance_curve_two_points():
    """Two points — linear interpolation."""
    curve = PerformanceCurve()
    curve.fit([
        {"model_size_gb": 5.0, "avg_tok_per_min": 80.0},
        {"model_size_gb": 10.0, "avg_tok_per_min": 45.0},
    ])
    # Midpoint should be between 45 and 80
    result = curve.predict_tok_per_min(7.5)
    assert result is not None
    assert 45 < result < 80

def test_performance_curve_regression():
    """3+ points — log-linear regression."""
    curve = PerformanceCurve()
    curve.fit([
        {"model_size_gb": 2.0, "avg_tok_per_min": 120.0},
        {"model_size_gb": 5.0, "avg_tok_per_min": 80.0},
        {"model_size_gb": 10.0, "avg_tok_per_min": 45.0},
        {"model_size_gb": 40.0, "avg_tok_per_min": 8.0},
    ])
    # Predict for 20GB — should be between 8 and 45
    result = curve.predict_tok_per_min(20.0)
    assert result is not None
    assert 5 < result < 45

def test_performance_curve_warmup():
    """Warmup curve — linear fit."""
    curve = PerformanceCurve()
    curve.fit([
        {"model_size_gb": 2.0, "avg_warmup_s": 0.8},
        {"model_size_gb": 5.0, "avg_warmup_s": 1.8},
        {"model_size_gb": 10.0, "avg_warmup_s": 3.2},
    ])
    result = curve.predict_warmup(7.5)
    assert result is not None
    assert 1.8 < result < 3.2

def test_performance_curve_confidence_interval():
    """Curve provides confidence interval."""
    curve = PerformanceCurve()
    curve.fit([
        {"model_size_gb": 2.0, "avg_tok_per_min": 120.0},
        {"model_size_gb": 5.0, "avg_tok_per_min": 80.0},
        {"model_size_gb": 10.0, "avg_tok_per_min": 45.0},
    ])
    mean, lower, upper = curve.predict_tok_per_min_ci(7.5)
    assert lower < mean < upper
```

**Step 2: Run tests — expect FAIL**

**Step 3: Implement PerformanceCurve**

Add to `intelligence.py`:

```python
class PerformanceCurve:
    """Cross-model performance curve fitted from empirical hardware data.

    Uses log-linear regression on (log(model_size), log(tok_per_min))
    to estimate performance for never-run models based on observed
    performance of other models on this machine.
    """

    def __init__(self):
        self._tok_slope = None
        self._tok_intercept = None
        self._tok_residual_std = None
        self._warmup_slope = None
        self._warmup_intercept = None
        self._points = []
        self.fitted = False

    def fit(self, model_stats: list[dict]) -> None:
        """Fit curves from model aggregate stats.

        Each entry: {model_size_gb, avg_tok_per_min, avg_warmup_s (optional)}
        """
        self._points = model_stats

        # tok/min curve: log-linear regression
        valid_tok = [s for s in model_stats
                     if s.get("avg_tok_per_min") and s.get("model_size_gb")]
        if len(valid_tok) >= 2:
            log_sizes = [math.log(s["model_size_gb"]) for s in valid_tok]
            log_rates = [math.log(s["avg_tok_per_min"]) for s in valid_tok]
            self._tok_slope, self._tok_intercept = _linear_regression(
                log_sizes, log_rates)
            # Residual std for confidence intervals
            predicted = [self._tok_slope * x + self._tok_intercept for x in log_sizes]
            residuals = [a - p for a, p in zip(log_rates, predicted)]
            if len(residuals) > 2:
                self._tok_residual_std = math.sqrt(
                    sum(r ** 2 for r in residuals) / (len(residuals) - 2))
            else:
                self._tok_residual_std = 0.3  # default uncertainty
            self.fitted = True
        elif len(valid_tok) == 1:
            # Single point — use typical slope
            s = valid_tok[0]
            self._tok_slope = -0.7  # typical power-law exponent
            self._tok_intercept = (math.log(s["avg_tok_per_min"])
                                   - self._tok_slope * math.log(s["model_size_gb"]))
            self._tok_residual_std = 0.5
            self.fitted = True

        # warmup curve: linear regression on (size, warmup)
        valid_warmup = [s for s in model_stats
                        if s.get("avg_warmup_s") and s.get("model_size_gb")]
        if len(valid_warmup) >= 2:
            sizes = [s["model_size_gb"] for s in valid_warmup]
            warmups = [s["avg_warmup_s"] for s in valid_warmup]
            self._warmup_slope, self._warmup_intercept = _linear_regression(
                sizes, warmups)

    def predict_tok_per_min(self, model_size_gb: float) -> float | None:
        """Predict tok/min for a model size."""
        if self._tok_slope is None:
            return None
        log_rate = self._tok_slope * math.log(model_size_gb) + self._tok_intercept
        return math.exp(log_rate)

    def predict_tok_per_min_ci(self, model_size_gb: float,
                                z: float = 1.28) -> tuple[float, float, float] | None:
        """Predict tok/min with confidence interval (default 90%)."""
        if self._tok_slope is None:
            return None
        log_rate = self._tok_slope * math.log(model_size_gb) + self._tok_intercept
        std = self._tok_residual_std or 0.3
        mean = math.exp(log_rate)
        lower = math.exp(log_rate - z * std)
        upper = math.exp(log_rate + z * std)
        return mean, lower, upper

    def predict_warmup(self, model_size_gb: float) -> float | None:
        """Predict warmup time (seconds) for a model size."""
        if self._warmup_slope is None:
            return None
        return max(0.1, self._warmup_slope * model_size_gb + self._warmup_intercept)

    def get_curve_data(self) -> dict:
        """Return fitted curve parameters for API/UI."""
        return {
            "tok_slope": self._tok_slope,
            "tok_intercept": self._tok_intercept,
            "tok_residual_std": self._tok_residual_std,
            "warmup_slope": self._warmup_slope,
            "warmup_intercept": self._warmup_intercept,
            "n_points": len(self._points),
            "points": self._points,
            "fitted": self.fitted,
        }


def _linear_regression(x: list[float], y: list[float]) -> tuple[float, float]:
    """Simple OLS linear regression. Returns (slope, intercept)."""
    n = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(a * b for a, b in zip(x, y))
    sum_x2 = sum(a ** 2 for a in x)

    denom = n * sum_x2 - sum_x ** 2
    if abs(denom) < 1e-10:
        return 0.0, sum_y / n if n else 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept
```

Note: Using hand-rolled `_linear_regression` instead of numpy/scipy — avoids adding heavy dependencies for simple OLS. If we need more sophisticated regression later, we can add numpy then.

**Step 4: Run tests — expect PASS**

**Step 5: Wire PerformanceCurve into RuntimeEstimator**

Update `RuntimeEstimator.estimate()` to use the curve as Tier 2. Add a `_maybe_refit_curve()` method that lazily refits from `db.get_model_stats()`.

**Step 6: Commit**

```bash
git add ollama_queue/intelligence.py tests/test_intelligence.py
git commit -m "feat: add PerformanceCurve for cross-model estimation

Log-linear regression on (model_size, tok/min) for interpolating
performance of never-run models. Linear regression for warmup.
Hand-rolled OLS — no numpy dependency needed.
Confidence intervals via residual standard deviation."
```

---

### Batch 2 Quality Gate

```bash
pytest --timeout=120 -x -q
```

Run code-review agent on: `ollama_queue/intelligence.py`, `tests/test_intelligence.py`

Run lesson-scanner: `lessons-db scan --target ~/Documents/projects/ollama-queue --baseline HEAD~3`

---

## Batch 3: Slot Scoring & Sweep Logic

Decision engine + DLQ/Deferral schedulers. Depends on Batch 1 (schema) and Batch 2 (estimator).

### Task 6: SystemSnapshot + failure classification

**Files:**
- Modify: `ollama_queue/intelligence.py` (add SystemSnapshot dataclass, classify_failure)
- Add tests to `tests/test_intelligence.py`

**Implementation:**

Add `SystemSnapshot` dataclass and `classify_failure()` function per design doc Section 3. `SystemSnapshot.capture(health_monitor, db, scheduler)` class method that gathers all real-time data. Tests should cover all 5 failure categories.

**Commit:** `feat: add SystemSnapshot and failure classification`

---

### Task 7: Slot scoring — score_slot()

**Files:**
- Modify: `ollama_queue/intelligence.py` (add `score_slot` function)
- Add tests to `tests/test_intelligence.py`

**Implementation:**

10-factor scoring function per design doc Section 3. Tests for:
- VRAM hard gate (returns -1 for insufficient VRAM)
- Hot model bonus
- Recurring job conflict penalty
- Historical quiet time bonus
- Failure-aware scoring (resource → extra headroom, timeout → open-ended slot)
- Queue depth penalty

**Commit:** `feat: add 10-factor slot scoring for scheduling decisions`

---

### Task 8: find_fitting_slot()

**Files:**
- Modify: `ollama_queue/intelligence.py`
- Add tests to `tests/test_intelligence.py`

**Implementation:**

VRAM-aware slot fitting per design doc Section 2. Scans load_map for contiguous runs of slots where:
- No slot is pinned (score >= 999)
- VRAM fits
- Aggregate load below threshold

Returns earliest qualifying window, sorted by total score ascending.

**Commit:** `feat: add VRAM-aware slot fitting for DLQ/deferral scheduling`

---

### Task 9: DLQScheduler

**Files:**
- Create: `ollama_queue/dlq_scheduler.py`
- Create: `tests/test_dlq_scheduler.py`

**Implementation:**

Per design doc Sections 2 and 4:
- `DLQScheduler.__init__(db, dlq_manager, scheduler, estimator)`
- `on_job_completed(job_id)` — event-driven trigger
- `periodic_sweep()` — fallback trigger
- `_sweep()` — core logic with sweep lock, priority ordering, slot fitting, rescheduling
- Stores reasoning JSON in `dlq.reschedule_reasoning`
- Copies `auto_reschedule_count` to new DLQ entries

Tests for: sweep with no entries, sweep finds slot, sweep skips pinned, priority ordering, sweep lock prevents concurrent execution, reschedule lineage tracking.

**Commit:** `feat: add DLQScheduler with event-driven sweep + slot fitting`

---

### Task 10: DeferralScheduler

**Files:**
- Create: `ollama_queue/deferral_scheduler.py`
- Create: `tests/test_deferral_scheduler.py`

**Implementation:**

Per design doc Section 5:
- `DeferralScheduler.__init__(db, scheduler, estimator)`
- `sweep()` — same pattern as DLQ but resumes existing jobs (no new job created)
- Same `score_slot()` and `find_fitting_slot()` from intelligence layer

Tests for: sweep resumes deferred jobs, deferred job keeps same ID, priority ordering, scoring with deferred-specific reasons.

**Commit:** `feat: add DeferralScheduler for proactive job postponement`

---

### Batch 3 Quality Gate

```bash
pytest --timeout=120 -x -q
```

Run code-review agent on: `ollama_queue/dlq_scheduler.py`, `ollama_queue/deferral_scheduler.py`, `ollama_queue/intelligence.py`

Run lesson-scanner: `lessons-db scan --target ~/Documents/projects/ollama-queue --baseline HEAD~5`

---

## Batch 4: Daemon Integration

Wiring the intelligence layer into the existing daemon. Most complex batch — touches daemon.py at 3 points plus adds deferral triggers. Changes must not break existing behavior.

### Task 11: Ollama metrics parsing

**Files:**
- Create: `ollama_queue/metrics_parser.py`
- Create: `tests/test_metrics_parser.py`

**Implementation:**

Parse the final `{"done": true, ...}` JSON line from Ollama stdout:

```python
def parse_ollama_metrics(stdout: str) -> dict | None:
    """Extract Ollama performance metrics from job stdout.

    Returns None for non-Ollama output (graceful fallback).
    """
```

Tests: valid Ollama JSON, partial output, non-Ollama output returns None, malformed JSON.

**Commit:** `feat: add Ollama response metrics parser`

---

### Task 12: Metrics capture on job completion

**Files:**
- Modify: `ollama_queue/daemon.py` (~line 820-840, in `_run_job`)
- Modify: `tests/test_daemon.py`

**Implementation:**

After `db.complete_job()` and before `dlq.handle_failure()` in `_run_job()`:

```python
# Capture Ollama metrics (graceful — non-Ollama jobs return None)
from ollama_queue.metrics_parser import parse_ollama_metrics
metrics = parse_ollama_metrics(stdout_capture)
if metrics:
    metrics["model"] = job["model"]
    metrics["command"] = job["command"]
    metrics["resource_profile"] = job.get("resource_profile", "ollama")
    self.db.store_job_metrics(job["id"], metrics)
```

Tests: mock subprocess output, verify metrics stored, verify non-Ollama jobs don't store metrics.

**Commit:** `feat: capture Ollama performance metrics on job completion`

---

### Task 13: Job completion hook + periodic sweep

**Files:**
- Modify: `ollama_queue/daemon.py` (add DLQScheduler/DeferralScheduler init, hook in _run_job, periodic in poll_once)
- Modify: `tests/test_daemon.py`

**Implementation:**

In `Daemon.__init__()`:
```python
self.dlq_scheduler = DLQScheduler(self.db, self.dlq, self.scheduler, self.estimator)
self.deferral_scheduler = DeferralScheduler(self.db, self.scheduler, self.estimator)
```

In `_run_job()` after DLQ routing (~line 840):
```python
self.dlq_scheduler.on_job_completed(job["id"])
```

In `poll_once()` periodic section (~line 1001):
```python
# DLQ/deferral periodic sweep (every dlq.sweep_fallback_minutes)
sweep_interval = self.db.get_setting("dlq.sweep_fallback_minutes") or 30
sweep_interval *= 60  # to seconds
if now - self._last_dlq_sweep >= sweep_interval:
    self.dlq_scheduler.periodic_sweep()
    self.deferral_scheduler.sweep()
    self._last_dlq_sweep = now
```

**Commit:** `feat: wire DLQ/deferral schedulers into daemon lifecycle`

---

### Task 14: Deferral triggers (admission, burst, thermal)

**Files:**
- Modify: `ollama_queue/daemon.py` (_can_admit, poll_once burst section)
- Modify: `tests/test_daemon.py`

**Implementation:**

In `_can_admit()` (~line 247): after VRAM check returns False, instead of just returning False:
```python
# Defer if won't fit soon
est_wait = self.dlq_scheduler.estimator.predict_next_opening(vram_needed)
wait_timeout = self.db.get_setting("defer.resource_wait_timeout_s") or 120
if est_wait is None or est_wait > wait_timeout:
    if self.db.get_setting("defer.enabled") is not False:
        self.db.defer_job(job["id"], reason="resource",
                          context=f"needs {vram_needed}GB, {vram_free}GB free")
        return False  # deferred, not just rejected
```

Burst deferral in `poll_once()` after burst regime update:
```python
if burst_regime == "burst" and self.db.get_setting("defer.enabled") is not False:
    threshold = self.db.get_setting("defer.burst_priority_threshold") or 3
    for job in pending_jobs:
        if job["priority"] < threshold:
            self.db.defer_job(job["id"], reason="burst")
```

Thermal deferral in health evaluation:
```python
if snap.get("gpu_temp_c") and snap["gpu_temp_c"] > (self.db.get_setting("defer.thermal_threshold_c") or 85):
    if self.db.get_setting("defer.enabled") is not False:
        for job in pending_gpu_jobs:
            self.db.defer_job(job["id"], reason="thermal")
```

**Commit:** `feat: add admission/burst/thermal deferral triggers in daemon`

---

### Batch 4 Quality Gate

```bash
pytest --timeout=120 -x -q
```

Run code-review agent on: `ollama_queue/daemon.py`, `ollama_queue/metrics_parser.py`

Run lesson-scanner: `lessons-db scan --target ~/Documents/projects/ollama-queue --baseline HEAD~4`

**Critical check:** Run existing daemon tests to verify no regressions:
```bash
pytest tests/test_daemon.py -v
```

---

## Batch 5: API Endpoints

New REST endpoints for DLQ scheduling, metrics, deferral, and settings.

### Task 15: DLQ schedule endpoints

**Files:**
- Modify: `ollama_queue/api.py` (add after existing DLQ endpoints ~line 890)
- Modify: `tests/test_api.py`

**Implementation:**

```python
@app.get("/api/dlq/schedule-preview")
# Returns what next sweep would do without executing

@app.post("/api/dlq/{dlq_id}/reschedule")
# Manually trigger reschedule for one entry
```

**Commit:** `feat: add DLQ schedule-preview and manual reschedule endpoints`

---

### Task 16: Metrics endpoints

**Files:**
- Modify: `ollama_queue/api.py`
- Modify: `tests/test_api.py`

**Implementation:**

```python
@app.get("/api/metrics/models")
# Returns per-model performance stats from db.get_model_stats()

@app.get("/api/metrics/performance-curve")
# Returns fitted curve data from PerformanceCurve.get_curve_data()
```

**Commit:** `feat: add model performance and curve endpoints`

---

### Task 17: Deferral endpoints

**Files:**
- Modify: `ollama_queue/api.py`
- Modify: `tests/test_api.py`

**Implementation:**

```python
@app.post("/api/jobs/{job_id}/defer")
# User-initiated deferral with optional 'until' parameter

@app.get("/api/deferred")
# List deferred jobs with scheduled resume times
```

**Commit:** `feat: add deferral endpoints`

---

### Task 18: Settings defaults

**Files:**
- Modify: `ollama_queue/db.py` (add defaults in initialize or default_settings)
- No new tests needed — settings system already tested

**Implementation:**

Add default settings per design doc Section 4:
```python
# DLQ auto-reschedule defaults
("dlq.auto_reschedule", True),
("dlq.max_slot_load", 5.0),
("dlq.sweep_fallback_minutes", 30),
("dlq.chronic_failure_threshold", 5),
("dlq.resource_failure_extra_margin", 0.3),
("defer.enabled", True),
("defer.burst_priority_threshold", 3),
("defer.thermal_threshold_c", 85),
("defer.resource_wait_timeout_s", 120),
```

**Commit:** `feat: add DLQ and deferral default settings`

---

### Batch 5 Quality Gate

```bash
pytest --timeout=120 -x -q
```

Run code-review agent on: `ollama_queue/api.py`

Run lesson-scanner: `lessons-db scan --target ~/Documents/projects/ollama-queue --baseline HEAD~4`

---

## Batch 6: Dashboard — Core Components

New Preact components for the UI. Can partially parallelize — WarmupBadge, DeferredPanel, and SystemHealth are independent.

### Task 19: Store updates

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/store.js`

**Implementation:**

Add signals and fetch functions:
```javascript
// New signals
export const deferredJobs = signal([]);
export const modelPerformance = signal([]);
export const performanceCurve = signal(null);

// New fetches
export async function fetchDeferred() { ... }
export async function fetchModelPerformance() { ... }
export async function fetchPerformanceCurve() { ... }
```

Add to polling loop (60s interval alongside history).

**Commit:** `feat: add store signals for deferred jobs and model performance`

---

### Task 20: WarmupBadge component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/WarmupBadge.jsx`

**Implementation:**

Per design doc Section 8:
- Shows "Warming up" before first token, "Generating" after
- Completed shows `Warmup: 5.2s | Generation: 38s` breakdown
- Used in CurrentJob and HistoryList

```jsx
// What it shows: Whether a running job is still loading the AI model (warming up)
//   or actively generating output.
// Decision it drives: Lets the user know if wait time is model loading vs actual
//   generation, so they can judge whether the job is progressing normally.
```

**Commit:** `feat: add WarmupBadge component for running job phase display`

---

### Task 21: DeferredPanel component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/DeferredPanel.jsx`

**Implementation:**

Per design doc Section 8:
- Shows between Active and Pending sections on Now page
- Each row: model, reason badge, scheduled time
- Expandable scoring reasoning

```jsx
// What it shows: Jobs that the system has paused because they can't run right now —
//   GPU too hot, not enough memory, system overloaded, or user-deferred.
// Decision it drives: Shows what work is waiting and why, so the user can understand
//   why their job isn't running and when it will resume.
```

**Commit:** `feat: add DeferredPanel component`

---

### Task 22: DLQ row enhancements

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/HistoryList.jsx` (or DLQ section in History page)

**Implementation:**

Enhance DLQ entries to show:
- Auto-reschedule status column (awaiting, scheduled, running, chronic)
- Estimate tooltip (warmup, generation, total, confidence, next slot)
- Expandable decision reasoning panel

**Commit:** `feat: enhance DLQ entries with reschedule status and reasoning`

---

### Task 23: SystemHealth panel

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/SystemHealth.jsx`

**Implementation:**

Real-time gauges: CPU, RAM, GPU util/temp/VRAM, disk I/O. Status badges. Data freshness opacity per research doc.

```jsx
// What it shows: Real-time health of the system — CPU, memory, GPU temperature,
//   available VRAM, and disk activity.
// Decision it drives: Tells the user whether the system is healthy enough to run
//   more jobs, and explains why jobs might be deferred (e.g., GPU overheating).
```

**Commit:** `feat: add SystemHealth panel with real-time gauges`

---

### Batch 6 Quality Gate

```bash
cd ollama_queue/dashboard/spa && npm run build && cd ../../..
pytest --timeout=120 -x -q
```

Run code-review agent on all new/modified SPA files.

---

## Batch 7: Dashboard — Performance Tab & Charts

The research-backed visualization layer. Depends on Batch 6 (store signals).

### Task 24: PerformanceTab page

**Files:**
- Create: `ollama_queue/dashboard/spa/src/pages/Performance.jsx`
- Modify: `ollama_queue/dashboard/spa/src/app.jsx` (add route)
- Modify: `ollama_queue/dashboard/spa/src/components/Sidebar.jsx` (add nav item)

**Implementation:**

New tab/page with 4 sections:
1. Model Performance Table (with sparklines)
2. Performance Curves (tok/min + warmup vs model size)
3. Load Heatmap (hour × day-of-week)
4. System Health Panel

Route: `performance`. Nav icon + label.

**Commit:** `feat: add Performance tab with model stats and charts`

---

### Task 25: PerformanceCurve chart

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/PerformanceCurveChart.jsx`

**Implementation:**

uPlot scatter plot with fitted regression line and confidence band per research doc:
- X: model size (GB), log scale
- Y: tok/min, linear
- Points: observed averages, size proportional to run count
- Line: fitted regression
- Band: 90% CI at `opacity: 0.15`
- Grid: horizontal only, ≤30% contrast

Second chart below: warmup vs model size (same X axis, separate Y).

**Commit:** `feat: add PerformanceCurve chart with scatter, regression, and CI band`

---

### Task 26: LoadHeatmap chart

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/LoadHeatmap.jsx`

**Implementation:**

Hour × day-of-week heatmap per research doc:
- Sequential lightness ramp (oklch)
- Hover: numeric load + job count
- DLQ scheduled markers as dot overlay
- Colorblind-safe (L-only ramp)

**Commit:** `feat: add LoadHeatmap component for usage pattern visualization`

---

### Task 27: Load map enhancements

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/LoadMapStrip.jsx`

**Implementation:**

Add to existing load map:
- VRAM utilization overlay (translucent secondary bar)
- DLQ scheduled job markers (icon above bar)
- Deferred job markers
- Tooltip showing committed VRAM + scheduled jobs

**Commit:** `feat: enhance load map with VRAM overlay and DLQ/deferred markers`

---

### Task 28: Settings panel additions

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Settings.jsx`

**Implementation:**

Add two new settings sections:
- "DLQ Auto-Reschedule": toggle, max slot load, sweep interval, chronic threshold, extra margin
- "Deferral": toggle, burst priority threshold, thermal threshold, resource timeout

**Commit:** `feat: add DLQ and deferral settings sections to Settings page`

---

### Batch 7 Quality Gate

```bash
cd ollama_queue/dashboard/spa && npm run build && cd ../../..
pytest --timeout=120 -x -q
```

Run code-review agent on all new SPA files.

---

## Batch 8: Integration, Polish & Verification

End-to-end integration, CLI commands, edge cases, final verification.

### Task 29: CLI commands for DLQ scheduling and deferral

**Files:**
- Modify: `ollama_queue/cli.py`
- Modify: `tests/test_cli.py`

**Implementation:**

```bash
ollama-queue dlq schedule-preview     # what next sweep would do
ollama-queue dlq reschedule <id>      # manually reschedule one entry
ollama-queue defer <job_id> [--until] # user-initiated deferral
ollama-queue metrics models           # show model performance stats
ollama-queue metrics curve            # show fitted curve parameters
```

**Commit:** `feat: add CLI commands for DLQ scheduling, deferral, and metrics`

---

### Task 30: LoadPatterns — hourly/daily profiles

**Files:**
- Modify: `ollama_queue/intelligence.py` (add LoadPatterns class)
- Add tests to `tests/test_intelligence.py`

**Implementation:**

```python
class LoadPatterns:
    """Learned load patterns by hour-of-day and day-of-week."""

    def compute(self, health_log: list[dict]) -> dict:
        """Aggregate health_log into hourly/daily load profiles."""

    def get_hourly_profile(self) -> list[float]:
        """24 floats: average load by hour."""

    def get_daily_profile(self) -> list[float]:
        """7 floats: average load by day-of-week."""
```

**Commit:** `feat: add LoadPatterns for learned usage profiles`

---

### Task 31: VRAM-aware load_map enhancement

**Files:**
- Modify: `ollama_queue/scheduler.py` (update `load_map()`)
- Modify: `tests/test_scheduler.py`

**Implementation:**

`load_map()` returns extended data: each slot includes VRAM commitment alongside load score.

**Commit:** `feat: enhance load_map with VRAM utilization per slot`

---

### Task 32: End-to-end integration test

**Files:**
- Create: `tests/test_integration_dlq_reschedule.py`

**Implementation:**

Full vertical trace:
1. Submit job → run → fail → enter DLQ
2. DLQ scheduler sweeps → finds slot → creates new pending job
3. New job has `retry_after` set to future slot
4. Verify DLQ entry marked with reschedule info
5. Verify reasoning stored

Second trace for deferral:
1. Submit job → defer (resource reason)
2. Deferral scheduler sweeps → resumes job
3. Verify same job ID, status back to pending

**Commit:** `test: add end-to-end integration tests for DLQ reschedule and deferral`

---

### Task 33: Final build + full test suite

**Step 1:** Full test suite
```bash
pytest --timeout=120 -x -q
```

**Step 2:** SPA build
```bash
cd ollama_queue/dashboard/spa && npm run build && cd ../../..
```

**Step 3:** Update CLAUDE.md with new files, gotchas, test counts

**Step 4:** Final commit
```bash
git add -A  # safe here — single agent, all files are ours
git commit -m "chore: update CLAUDE.md with DLQ auto-reschedule documentation"
```

---

### Batch 8 Quality Gate (Final)

```bash
pytest --timeout=120 -x -q
cd ollama_queue/dashboard/spa && npm run build && cd ../../..
```

Run full code-review agent on ALL changed files (git diff from feature branch base).

Run lesson-scanner: `lessons-db scan --target ~/Documents/projects/ollama-queue`

Run test coverage analysis agent.

---

## Regression Safeguards (Horizontal + Vertical)

Every batch must verify existing behavior is not broken. This is non-negotiable.

### Horizontal Sweep (after every batch)

Run ALL existing tests — not just new ones:

```bash
# Full existing test suite (748+ tests)
pytest --timeout=120 -x -q

# SPA builds without errors
cd ollama_queue/dashboard/spa && npm run build && cd ../../..
```

If ANY existing test fails after a batch, stop and fix before proceeding. The feature is additive — it should never break what already works.

### Vertical Trace (after batches 4 and 8)

After batch 4 (daemon wiring) and batch 8 (final), run the full pipeline trace:

**Trace 1: Normal job lifecycle (existing behavior)**
```bash
# Submit → run → complete → verify all endpoints reflect
ollama-queue submit --source test --model qwen2.5:7b --priority 3 --timeout 120 -- echo hello
ollama-queue status          # should show running/completed
ollama-queue history         # should show in history
curl -s localhost:7683/api/status | python3 -m json.tool  # API reflects
```

**Trace 2: DLQ lifecycle (existing behavior + new)**
```bash
# Submit job that fails → DLQ → verify auto-reschedule triggers
ollama-queue submit --source test --model qwen2.5:7b --priority 3 --timeout 5 -- sleep 10
# Wait for timeout → DLQ
ollama-queue dlq list        # should show entry
curl -s localhost:7683/api/dlq | python3 -m json.tool  # verify reschedule fields
```

**Trace 3: Recurring job lifecycle (existing behavior)**
```bash
ollama-queue schedule list   # should list all recurring jobs unchanged
# Verify promote_due_jobs still works (check logs)
```

**Trace 4: Proxy lifecycle (existing behavior)**
```bash
curl -s -X POST localhost:7683/api/generate -d '{"model":"qwen2.5:7b","prompt":"hi","stream":false}' | python3 -m json.tool
# Proxy should still work, sentinel job pattern unchanged
```

### Specific Regression Risks

| Risk | How It Breaks | Prevention |
|------|--------------|------------|
| `_run_job` touches → daemon loop changes | Added metrics capture or hook throws, job silently fails | Wrap all new code in try/except, log exceptions, never let new code block job completion |
| `poll_once` periodic sweep slows poll loop | Sweep takes >5s, daemon becomes unresponsive | Sweep acquires lock with timeout, runs in <100ms for empty DLQ |
| `list_dlq()` signature changed | Existing callers (API, CLI) break | New `unscheduled_only` parameter defaults to False — existing behavior unchanged |
| `_can_admit()` now defers → job never runs | Deferral incorrectly fires for normal admission failures | Only defer when `defer.enabled` setting is True AND resource condition is sustained beyond `resource_wait_timeout_s` |
| New `deferred` status not handled by `get_pending_jobs` | Deferred jobs appear in pending queue, get double-dispatched | `get_pending_jobs` WHERE clause already filters by `status='pending'` — `deferred` is excluded by default |
| Schema migration fails on existing production DB | ALTER TABLE on locked DB, or column already exists | Idempotent ALTER with try/except pattern (matching existing migrations) |
| New table `job_metrics` inserts slow down job completion | Lock contention between metrics store and daemon poll | Metrics store is a separate INSERT (not in the hot path for job dispatch), and uses the existing `_lock` RLock |
| Burst deferral defers ALL low-priority jobs permanently | Jobs stuck in deferred, never resume | Deferral sweep runs on every job completion — deferred jobs resume as soon as conditions clear |
| Thermal deferral fires at normal GPU temps | False positive deferral of working jobs | Default threshold is 85°C — well above normal operating range. Setting is configurable. |

### Integration Test Checklist (run after batch 8)

- [ ] Submit 10 normal jobs with various priorities — all complete in order
- [ ] Submit a job that times out — enters DLQ, auto-reschedule runs
- [ ] Verify proxy `/api/generate` works (sentinel pattern unaffected)
- [ ] Verify recurring job promotion works (schedule list, promote_due_jobs)
- [ ] Verify health monitoring pause/resume hysteresis still functions
- [ ] Verify stall detection still works
- [ ] Verify burst detection still works
- [ ] Verify eval pipeline still runs (if running)
- [ ] SPA loads and all existing tabs render
- [ ] New Performance tab renders
- [ ] Deferred panel appears on Now page when deferred jobs exist
- [ ] DLQ entries show reschedule status
- [ ] Settings page shows new DLQ/deferral controls
- [ ] No 500 errors in `ollama-queue serve` logs during any of the above

---

## Execution Summary

| Batch | Tasks | Focus | Parallel? | Quality Gate |
|-------|-------|-------|-----------|--------------|
| 1 | 1-3 | DB schema + CRUD | Tasks 1-3 parallel | pytest + code-review + lesson-scan |
| 2 | 4-5 | Intelligence core | Task 4 → 5 sequential | pytest + code-review |
| 3 | 6-10 | Scoring + Schedulers | Tasks 6-8 parallel, then 9-10 parallel | pytest + code-review + lesson-scan |
| 4 | 11-14 | Daemon wiring | Sequential (touches shared daemon.py) | pytest + code-review + lesson-scan |
| 5 | 15-18 | API endpoints | Tasks 15-17 parallel, 18 last | pytest + code-review |
| 6 | 19-23 | Dashboard core | Task 19 first, then 20-23 parallel | npm build + code-review |
| 7 | 24-28 | Charts + Performance tab | Task 24 first, then 25-28 parallel | npm build + code-review |
| 8 | 29-33 | Integration + polish | Sequential | Full suite + code-review + lesson-scan |

**Total: 33 tasks across 8 batches**

**New files:** 9 (intelligence.py, dlq_scheduler.py, deferral_scheduler.py, metrics_parser.py, 5 SPA components/pages)

**Modified files:** 8 (db.py, daemon.py, dlq.py, scheduler.py, api.py, cli.py, store.js, + SPA pages)

**New test files:** 6 (test_job_metrics.py, test_intelligence.py, test_dlq_scheduler.py, test_deferral_scheduler.py, test_deferral.py, test_metrics_parser.py, test_integration_dlq_reschedule.py)

**Estimated new tests:** ~120-150

**Agent dispatch strategy:**
- Batches 1, 3, 5, 6, 7: Parallel agents within batch (independent tasks)
- Batches 2, 4, 8: Sequential agents (shared file dependencies)
- Code-review agent after EVERY batch (non-negotiable)
- Lesson-scanner after batches 1, 3, 4, 8 (integration boundaries = Cluster B risk)
- Test coverage analysis after batch 8 (final)
