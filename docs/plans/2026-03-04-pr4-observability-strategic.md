# PR 4 — Observability + Strategic Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add EWMA-based burst detection (no external deps), adaptive Shannon entropy queue health, and opt-in preemption (priority 1-2 jobs can preempt running lower-priority jobs; sends to PENDING, never DLQ).

**Architecture:** New file `burst.py`. Changes in `daemon.py` (entropy history, preemption), `scheduler.py` (entropy suspension parameter), `db.py` (new migration + DEFAULTS + method), `api.py` (`/api/health` surfacing). No new pip dependencies.

**Tech Stack:** Python 3.12, sqlite3, collections.deque, statistics (stdlib), signal/os (stdlib)

**Design doc:** `docs/plans/2026-03-04-queue-optimization-design.md` §PR4

**Prerequisite:** PR 1 must be merged. PR 2 and PR 3 can be merged in parallel.

**Quality Gates:**
- `pytest --timeout=120 -x -q` — must pass before every commit
- `make lint` — must pass before every commit

---

## Task 1: BurstDetector class in `burst.py`

**Files:**
- Create: `ollama_queue/burst.py`
- Create: `tests/test_burst.py`

### Step 1: Write failing tests

Create `tests/test_burst.py`:

```python
"""Tests for EWMA-based burst detection."""

import time
import pytest

from ollama_queue.burst import BurstDetector


class TestBurstDetector:
    def test_starts_unknown_before_10_samples(self):
        """Returns 'unknown' until 10 inter-arrival samples are collected."""
        detector = BurstDetector()
        now = time.time()
        for i in range(9):
            detector.record_submission(now + i * 10)
        assert detector.regime(now + 100) == "unknown"

    def test_subcritical_on_slow_steady_arrivals(self):
        """Slow, regular arrivals (100s apart) produce subcritical regime."""
        detector = BurstDetector()
        now = time.time()
        # 20 submissions 100 seconds apart — well-spaced
        for i in range(20):
            detector.record_submission(now + i * 100.0)
        regime = detector.regime(now + 2000)
        assert regime in ("subcritical", "moderate"), f"Expected subcritical, got {regime}"

    def test_critical_on_rapid_burst(self):
        """Rapid burst (0.1s apart) after steady baseline produces critical regime."""
        detector = BurstDetector()
        now = time.time()
        # Establish baseline: 20 submissions 60s apart
        for i in range(20):
            detector.record_submission(now + i * 60.0)
        # Burst: 30 submissions 0.1s apart
        burst_start = now + 1200.0
        for i in range(30):
            detector.record_submission(burst_start + i * 0.1)
        regime = detector.regime(burst_start + 3)
        assert regime in ("warning", "critical"), f"Expected warning/critical, got {regime}"

    def test_regime_transitions_on_resumed_normal(self):
        """Regime returns to subcritical after burst subsides (EWMA decays)."""
        detector = BurstDetector(alpha=0.5)  # faster decay for test
        now = time.time()
        # Baseline
        for i in range(20):
            detector.record_submission(now + i * 60.0)
        # Short burst
        burst = now + 1200
        for i in range(5):
            detector.record_submission(burst + i * 0.1)
        # Long recovery period — many normal arrivals
        recovery = burst + 100
        for i in range(40):
            detector.record_submission(recovery + i * 60.0)
        regime = detector.regime(recovery + 2400)
        assert regime in ("subcritical", "moderate"), f"Expected recovery, got {regime}"

    def test_single_submission_does_not_crash(self):
        """Handles single submission without error."""
        detector = BurstDetector()
        detector.record_submission(time.time())
        assert detector.regime(time.time()) == "unknown"

    def test_sparse_arrivals_stay_subcritical(self):
        """Hours-apart arrivals do not false-alarm as bursts."""
        detector = BurstDetector()
        now = time.time()
        # 15 submissions 3600s apart (one per hour)
        for i in range(15):
            detector.record_submission(now + i * 3600)
        regime = detector.regime(now + 15 * 3600)
        assert regime == "subcritical", f"Expected subcritical for sparse arrivals, got {regime}"

    def test_regime_returns_valid_value(self):
        """regime() always returns a valid string."""
        detector = BurstDetector()
        now = time.time()
        valid = {"unknown", "subcritical", "moderate", "warning", "critical"}
        for i in range(20):
            detector.record_submission(now + i * 10)
        assert detector.regime(now + 200) in valid
```

### Step 2: Run to verify failure

```bash
cd ~/Documents/projects/ollama-queue && source .venv/bin/activate
pytest tests/test_burst.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ollama_queue.burst'`

### Step 3: Implement

Create `ollama_queue/burst.py`:

```python
"""EWMA-based burst detection for ollama-queue.

Dependency-free implementation. Detects self-exciting submission bursts by
comparing current EWMA inter-arrival time against a stable 75th-percentile
baseline. Self-excitation manifests as rapidly shrinking inter-arrival times.

No external packages required (no 'tick', no scipy).
"""

from __future__ import annotations

import logging
from collections import deque

_log = logging.getLogger(__name__)

# Regime: (ratio_low_bound, ratio_high_bound)
# ratio = ewma_interval / p75_baseline (dimensionless)
# Burst → ratio shrinks → falls into lower brackets
_REGIMES: list[tuple[str, float, float]] = [
    ("critical",    0.00, 0.15),  # ewma < 15% of baseline → severe burst
    ("warning",     0.15, 0.30),  # ewma 15-30% of baseline → approaching saturation
    ("moderate",    0.30, 0.50),  # ewma 30-50% of baseline → elevated load
    ("subcritical", 0.50, float("inf")),  # ewma > 50% of baseline → normal
]


class BurstDetector:
    """Dependency-free burst detection via EWMA of inter-arrival times.

    Regime is determined by comparing the EWMA inter-arrival time against
    a 75th-percentile baseline computed from historical intervals.

    The 75th-percentile baseline is robust: during a burst, new short
    intervals are added to the sample, but they don't displace the p75
    until the burst dominates >25% of history. This gives a stable baseline
    that reflects normal traffic rather than being contaminated by bursts.

    Usage:
        detector = BurstDetector()
        # On each job submission:
        detector.record_submission(time.time())
        # In daemon poll:
        regime = detector.regime(time.time())
    """

    def __init__(self, alpha: float = 0.3, baseline_window: int = 100):
        """
        Args:
            alpha: EWMA smoothing factor (0-1). Higher = faster response to changes.
                   0.3 = good balance between responsiveness and noise rejection.
            baseline_window: Number of inter-arrival samples to keep for p75 baseline.
        """
        self._alpha = alpha
        self._ewma: float | None = None
        self._baseline_samples: deque[float] = deque(maxlen=baseline_window)
        self._last_ts: float | None = None

    def record_submission(self, ts: float) -> None:
        """Record a job submission timestamp. Call on every /api/submit."""
        if self._last_ts is not None:
            interval = ts - self._last_ts
            if interval > 0:
                self._baseline_samples.append(interval)
                if self._ewma is None:
                    self._ewma = interval
                else:
                    self._ewma = self._alpha * interval + (1 - self._alpha) * self._ewma
        self._last_ts = ts

    def regime(self, now: float) -> str:
        """Return current burst regime classification.

        Returns:
            "unknown"     — insufficient data (< 10 samples)
            "subcritical" — normal traffic
            "moderate"    — elevated submission rate
            "warning"     — approaching saturation
            "critical"    — burst in progress; consider engaging 429 gate

        Requires at least 10 inter-arrival samples for a reliable baseline.
        """
        if len(self._baseline_samples) < 10 or self._ewma is None:
            return "unknown"

        # 75th percentile baseline: robust against burst contamination
        sorted_samples = sorted(self._baseline_samples)
        p75_idx = int(0.75 * len(sorted_samples))
        baseline = sorted_samples[min(p75_idx, len(sorted_samples) - 1)]

        if baseline <= 0:
            return "unknown"

        ratio = self._ewma / baseline
        for name, low, high in _REGIMES:
            if low <= ratio < high:
                return name
        return "subcritical"
```

### Step 4: Run tests

```bash
pytest tests/test_burst.py -x -q
```

Expected: all tests pass

### Step 5: Commit

```bash
git add ollama_queue/burst.py tests/test_burst.py
git commit -m "feat: BurstDetector — EWMA burst detection with p75 baseline (no external deps)"
```

---

## Task 2: Schema Migration and DEFAULTS for Preemption

**Files:**
- Modify: `ollama_queue/db.py` (`DEFAULTS`, `_run_migrations()`, add `requeue_preempted_job()`)
- Test: `tests/test_db.py`

### Step 1: Write failing test

In `tests/test_db.py`:

```python
class TestPreemptionSupport:
    def test_jobs_has_preemption_count_column(self, db):
        """jobs table must have preemption_count column (default 0)."""
        job_id = db.submit_job("echo test", "m", 5, 60, "test")
        job = db.get_job(job_id)
        assert "preemption_count" in job
        assert job["preemption_count"] == 0

    def test_requeue_preempted_job_sets_pending(self, db):
        """requeue_preempted_job() sets status=pending and increments preemption_count."""
        job_id = db.submit_job("echo test", "m", 1, 60, "test")
        db.start_job(job_id)
        db.requeue_preempted_job(job_id)
        job = db.get_job(job_id)
        assert job["status"] == "pending"
        assert job["started_at"] is None
        assert job["pid"] is None
        assert job["preemption_count"] == 1

    def test_requeue_increments_preemption_count_each_call(self, db):
        """preemption_count increments on each requeue, not reset."""
        job_id = db.submit_job("echo test", "m", 1, 60, "test")
        db.start_job(job_id)
        db.requeue_preempted_job(job_id)
        db.start_job(job_id)
        db.requeue_preempted_job(job_id)
        job = db.get_job(job_id)
        assert job["preemption_count"] == 2

    def test_requeue_does_not_touch_dlq(self, db):
        """requeue_preempted_job() never creates a DLQ entry."""
        job_id = db.submit_job("echo test", "m", 1, 60, "test")
        db.start_job(job_id)
        db.requeue_preempted_job(job_id)
        assert db.list_dlq() == []  # DLQ must remain empty
```

### Step 2: Run to verify failure

```bash
pytest tests/test_db.py::TestPreemptionSupport -v
```

Expected: FAIL — missing column and method

### Step 3: Implement

In `ollama_queue/db.py`, add to `DEFAULTS`:

```python
"preemption_enabled": False,           # PR4: opt-in preemption; off by default
"preemption_window_seconds": 120,      # PR4: only preempt jobs running < N seconds
"max_preemptions_per_job": 2,          # PR4: prevent infinite preemption loops
"entropy_alert_window": 30,            # PR4: polls for rolling entropy baseline
"entropy_alert_sigma": 2.0,            # PR4: std deviations for anomaly detection
"entropy_suspend_low_priority": True,  # PR4: suspend p8-10 promotion on critical_backlog
```

In `_run_migrations()`, add:

```python
self._add_column_if_missing(conn, "jobs", "preemption_count", "INTEGER DEFAULT 0")
```

Add `requeue_preempted_job()` method after `reset_job_to_pending()`:

```python
def requeue_preempted_job(self, job_id: int) -> None:
    """Reset a preempted job to pending and increment preemption_count.

    IMPORTANT: Never touches DLQ. Preempted jobs are healthy work interrupted
    deliberately. DLQ means 'permanent failure requiring human review' — using
    it for preemption corrupts its semantic meaning and requires manual recovery.
    """
    with self._lock:
        conn = self._connect()
        conn.execute(
            """UPDATE jobs SET
                   status = 'pending',
                   started_at = NULL,
                   pid = NULL,
                   submitted_at = ?,
                   preemption_count = COALESCE(preemption_count, 0) + 1
               WHERE id = ?""",
            (time.time(), job_id),  # reset submitted_at so it re-sorts correctly
        )
        conn.commit()
```

### Step 4: Run tests

```bash
pytest tests/test_db.py -x -q
```

Expected: all tests pass

### Step 5: Commit

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat: add preemption_count migration, requeue_preempted_job(), and PR4 DEFAULTS"
```

---

## Task 3: Adaptive Entropy Detection in daemon.py

**Files:**
- Modify: `ollama_queue/daemon.py` (`__init__`, add `_compute_queue_entropy()`, add `_check_entropy()`, modify `poll_once()`)
- Modify: `ollama_queue/scheduler.py` (`promote_due_jobs()` — add `suspend_low_priority` param)
- Test: `tests/test_daemon.py`

### Step 1: Write failing tests

In `tests/test_daemon.py`:

```python
class TestEntropyComputation:
    def test_empty_queue_entropy_is_zero(self, db):
        """Empty pending list gives entropy 0."""
        from ollama_queue.daemon import Daemon
        daemon = Daemon(db)
        import time
        result = daemon._compute_queue_entropy([], time.time())
        assert result == 0.0

    def test_uniform_priority_queue_has_high_entropy(self, db):
        """Queue with equal mix of priorities has maximum entropy."""
        from ollama_queue.daemon import Daemon
        import time
        daemon = Daemon(db)
        now = time.time()
        # 5 distinct priorities — mix
        jobs = [
            {"id": i, "priority": i, "submitted_at": now - 100}
            for i in range(1, 6)
        ]
        H = daemon._compute_queue_entropy(jobs, now)
        assert H > 1.0  # high entropy for diverse queue

    def test_single_priority_queue_has_low_entropy(self, db):
        """Queue with all same priority has entropy near 0."""
        from ollama_queue.daemon import Daemon
        import time
        daemon = Daemon(db)
        now = time.time()
        jobs = [
            {"id": i, "priority": 1, "submitted_at": now - 100}
            for i in range(10)
        ]
        H = daemon._compute_queue_entropy(jobs, now)
        assert H < 0.01  # near-zero: all same priority

    def test_entropy_history_accumulates(self, db):
        """_check_entropy() accumulates entropy readings over time."""
        from ollama_queue.daemon import Daemon
        import time
        daemon = Daemon(db)
        now = time.time()
        jobs = [{"id": i, "priority": 5, "submitted_at": now - 100} for i in range(5)]
        for _ in range(5):
            daemon._check_entropy(jobs, now)
        assert len(daemon._entropy_history) == 5
```

### Step 2: Run to verify failure

```bash
pytest tests/test_daemon.py::TestEntropyComputation -v
```

Expected: FAIL — `AttributeError: 'Daemon' object has no attribute '_compute_queue_entropy'`

### Step 3: Implement entropy in daemon.py

Add imports at the top of `ollama_queue/daemon.py`:

```python
import statistics
from collections import deque
from math import log, log2
```

Add entropy fields to `Daemon.__init__()`:

```python
# Adaptive entropy tracking (in-memory rolling baseline)
self._entropy_history: deque[float] = deque(maxlen=30)  # updated from DEFAULTS at runtime
self._entropy_suspend_until: float = 0.0
```

Add `_compute_queue_entropy()` method:

```python
def _compute_queue_entropy(self, pending_jobs: list[dict], now: float) -> float:
    """Compute age-weighted Shannon entropy of the pending queue's priority distribution.

    Age-weighted: older jobs count more (log(1 + wait_seconds)).
    Higher entropy = diverse priority mix = healthy queue.
    Lower entropy = priority collapse = backlog or flood.
    Returns 0.0 for empty queue.
    """
    if not pending_jobs:
        return 0.0

    from collections import defaultdict
    weights = {j["id"]: log(1.0 + max(0.0, now - (j.get("submitted_at") or now)))
               for j in pending_jobs}
    total_w = sum(weights.values()) or 1.0

    priority_weights: dict[int, float] = defaultdict(float)
    for j in pending_jobs:
        priority_weights[j["priority"]] += weights[j["id"]] / total_w

    return -sum(w * log2(w) for w in priority_weights.values() if w > 0)
```

Add `_check_entropy()` method:

```python
def _check_entropy(self, pending_jobs: list[dict], now: float) -> None:
    """Compute entropy, update rolling baseline, log anomalies, set suspension."""
    H = self._compute_queue_entropy(pending_jobs, now)
    self._entropy_history.append(H)

    # Need at least 10 samples for a meaningful baseline
    if len(self._entropy_history) < 10:
        return

    sigma = float(self.db.get_setting("entropy_alert_sigma") or 2.0)
    mean_H = statistics.mean(self._entropy_history)
    std_H = statistics.stdev(self._entropy_history) if len(self._entropy_history) > 1 else 0.1
    if std_H == 0:
        std_H = 0.1

    if H < mean_H - sigma * std_H:
        # Determine anomaly type
        high_priority_count = sum(1 for j in pending_jobs if j["priority"] <= 4)
        if high_priority_count / max(len(pending_jobs), 1) > 0.7:
            alert_type = "critical_backlog"
        else:
            alert_type = "background_flood"

        self.db.log_schedule_event("entropy_alert", details={
            "H": H, "mean_H": mean_H, "sigma": sigma, "type": alert_type
        })
        _log.warning(
            "Queue entropy anomaly detected: H=%.2f mean=%.2f type=%s",
            H, mean_H, alert_type
        )

        suspend_enabled = self.db.get_setting("entropy_suspend_low_priority")
        if alert_type == "critical_backlog" and suspend_enabled:
            self._entropy_suspend_until = now + 60.0  # suspend p8-10 for 60s
            _log.info("Suspended low-priority (p8-10) promotion for 60s due to critical_backlog")
```

In `poll_once()`, add entropy check after step 3 (after health logging):

```python
# 3b. Entropy anomaly detection
try:
    self._check_entropy(pending_jobs, now)
except Exception:
    _log.exception("Entropy check failed; continuing")
```

In `poll_once()`, modify the scheduler call in step 0 to pass entropy suspension flag:

```python
# 0. Promote due recurring jobs (runs even while a job is running)
try:
    suspend_low_priority = now < self._entropy_suspend_until
    self.scheduler.promote_due_jobs(now, suspend_low_priority=suspend_low_priority)
except Exception:
    _log.exception("Scheduler promotion failed; continuing")
```

In `ollama_queue/scheduler.py`, modify `promote_due_jobs()` signature:

```python
def promote_due_jobs(
    self,
    now: float | None = None,
    suspend_low_priority: bool = False,
) -> list[int]:
    """Promote due recurring jobs to pending. Coalesces duplicates.

    Args:
        suspend_low_priority: If True, skip promotion of priority 8-10 jobs.
            Set by daemon when entropy anomaly indicates critical_backlog.
    """
    if now is None:
        now = time.time()
    due = self.db.get_due_recurring_jobs(now)
    due.sort(key=lambda rj: self._aoi_sort_key(rj, now))

    new_ids = []
    for rj in due:
        # Entropy suspension: skip low-priority promotion during backlog
        if suspend_low_priority and int(rj.get("priority") or 5) >= 8:
            _log.debug(
                "Skipping promotion of %r (priority=%d) — entropy suspension active",
                rj["name"], rj["priority"]
            )
            continue
        # ... rest of existing loop unchanged ...
```

### Step 4: Run tests

```bash
pytest tests/test_daemon.py tests/test_scheduler.py -x -q
```

Expected: all tests pass

### Step 5: Commit

```bash
git add ollama_queue/daemon.py ollama_queue/scheduler.py tests/test_daemon.py
git commit -m "feat: adaptive Shannon entropy detection with suspension of low-priority promotion"
```

---

## Task 4: BurstDetector Integration and `/api/health` Surfacing

**Files:**
- Modify: `ollama_queue/daemon.py` (integrate BurstDetector into `poll_once()`)
- Modify: `ollama_queue/api.py` (`/api/health` response)
- Test: `tests/test_daemon.py`, `tests/test_api.py`

### Step 1: Write failing test

In `tests/test_daemon.py`:

```python
def test_daemon_has_burst_detector(self, db):
    """Daemon initializes with a BurstDetector instance."""
    from ollama_queue.daemon import Daemon
    from ollama_queue.burst import BurstDetector
    daemon = Daemon(db)
    assert hasattr(daemon, "_burst_detector")
    assert isinstance(daemon._burst_detector, BurstDetector)
```

In `tests/test_api.py`:

```python
def test_health_includes_burst_regime(self, client):
    """GET /api/health includes burst_regime field."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "burst_regime" in data
    assert data["burst_regime"] in {"unknown", "subcritical", "moderate", "warning", "critical"}
```

### Step 2: Run to verify failure

```bash
pytest tests/test_daemon.py::TestDaemon::test_daemon_has_burst_detector -v
pytest tests/test_api.py::TestHealth::test_health_includes_burst_regime -v
```

Expected: FAIL — missing attribute and missing health field

### Step 3: Implement

In `ollama_queue/daemon.py`, add import at top:

```python
from ollama_queue.burst import BurstDetector
```

In `Daemon.__init__()`, add:

```python
self._burst_detector = BurstDetector()
self._burst_regime: str = "unknown"  # cached for /api/health
```

In `poll_once()`, after entropy check (step 3b), add:

```python
# 3c. Update burst regime every poll (BurstDetector is cheap to query)
try:
    self._burst_regime = self._burst_detector.regime(now)
    if self._burst_regime in ("warning", "critical"):
        _log.info("Burst regime: %s", self._burst_regime)
except Exception:
    _log.exception("Burst regime check failed; continuing")
```

In `ollama_queue/api.py`, find the `/api/health` endpoint and add `burst_regime` to its response dict. The daemon object is accessible via the module-level singleton or dependency injection (look for how the existing `/api/health` handler accesses daemon state).

The response dict should include:
```python
"burst_regime": daemon._burst_regime,
```

Also integrate `record_submission()` into the submit handler — wherever `db.submit_job()` is called successfully, add:

```python
daemon._burst_detector.record_submission(time.time())
```

### Step 4: Run tests

```bash
pytest --timeout=120 -x -q
```

Expected: all tests pass

### Step 5: Commit

```bash
git add ollama_queue/daemon.py ollama_queue/api.py tests/test_daemon.py tests/test_api.py
git commit -m "feat: integrate BurstDetector into daemon; surface burst_regime in /api/health"
```

---

## Task 5: Preemption Logic in daemon.py

**Files:**
- Modify: `ollama_queue/daemon.py` (add `_check_preemption()`, `_preempt_job()`, call from `poll_once()`)
- Test: `tests/test_daemon.py`

### Step 1: Write failing tests

In `tests/test_daemon.py`:

```python
class TestPreemption:
    def _setup_running_job(self, db, daemon, model="qwen2.5:7b", priority=5):
        """Helper: submit a job, mark it running with a fake PID."""
        import time
        job_id = db.submit_job("echo run", model, priority, 600, "test")
        db.start_job(job_id)
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE jobs SET pid=99999 WHERE id=?", (job_id,))
            conn.commit()
        return job_id

    def test_preemption_disabled_by_default(self, db):
        """No preemption when preemption_enabled=False."""
        from ollama_queue.daemon import Daemon
        import time
        daemon = Daemon(db)
        # preemption_enabled defaults to False
        p1_job = {"id": 99, "priority": 1, "model": "qwen2.5:7b", "source": "test"}
        result = daemon._check_preemption(p1_job, time.time())
        assert result is None

    def test_preemption_skips_high_priority_requester(self, db):
        """Only priority 1-2 jobs can trigger preemption."""
        from ollama_queue.daemon import Daemon
        import time
        daemon = Daemon(db)
        db.set_setting("preemption_enabled", True)

        p5_job = {"id": 99, "priority": 5, "model": "qwen2.5:7b", "source": "test"}
        result = daemon._check_preemption(p5_job, time.time())
        assert result is None  # priority 5 cannot preempt

    def test_preempt_job_sends_to_pending_not_dlq(self, db):
        """_preempt_job() sets status=pending and leaves DLQ empty."""
        from ollama_queue.daemon import Daemon
        from unittest.mock import patch
        import time
        daemon = Daemon(db)
        db.set_setting("preemption_enabled", True)

        job_id = db.submit_job("echo low", "qwen2.5:7b", 5, 600, "test")
        db.start_job(job_id)
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE jobs SET pid=99999 WHERE id=?", (job_id,))
            conn.commit()

        with patch("os.kill", return_value=None):  # suppress SIGTERM to fake PID
            daemon._preempt_job(job_id)

        job = db.get_job(job_id)
        assert job["status"] == "pending", f"Expected pending, got {job['status']}"
        assert db.list_dlq() == [], "DLQ must be empty after preemption"

    def test_preempt_increments_preemption_count(self, db):
        """preemption_count is incremented after preemption."""
        from ollama_queue.daemon import Daemon
        from unittest.mock import patch
        daemon = Daemon(db)

        job_id = db.submit_job("echo low", "qwen2.5:7b", 5, 600, "test")
        db.start_job(job_id)
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE jobs SET pid=99999 WHERE id=?", (job_id,))
            conn.commit()

        with patch("os.kill", return_value=None):
            daemon._preempt_job(job_id)

        job = db.get_job(job_id)
        assert job["preemption_count"] == 1

    def test_job_at_max_preemptions_is_immune(self, db):
        """Job with preemption_count >= max_preemptions_per_job cannot be preempted again."""
        from ollama_queue.daemon import Daemon
        import time
        daemon = Daemon(db)
        db.set_setting("preemption_enabled", True)
        db.set_setting("max_preemptions_per_job", 2)

        job_id = db.submit_job("echo low", "qwen2.5:7b", 5, 600, "test")
        db.start_job(job_id)
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE jobs SET pid=99999, preemption_count=2 WHERE id=?", (job_id,))
            conn.commit()

        now = time.time()
        # Inject fake running job into daemon's tracking
        from concurrent.futures import Future
        fake_future = Future()
        with daemon._running_lock:
            daemon._running[job_id] = fake_future
            daemon._running_models[job_id] = "qwen2.5:7b"

        p1_job = {
            "id": 999, "priority": 1, "model": "qwen2.5:7b",
            "source": "test", "submitted_at": now
        }
        result = daemon._check_preemption(p1_job, now)
        assert result is None  # immune due to max preemptions
```

### Step 2: Run to verify failure

```bash
pytest tests/test_daemon.py::TestPreemption -v
```

Expected: FAIL — missing methods

### Step 3: Implement preemption

Add `_check_preemption()` to `Daemon` in `ollama_queue/daemon.py`:

```python
def _check_preemption(self, new_job: dict, now: float) -> int | None:
    """Find a running job to preempt for new_job. Returns job_id or None.

    Preemption only occurs when:
    1. preemption_enabled=True (opt-in)
    2. new_job priority is 1 or 2
    3. A running job has run < preemption_window_seconds
    4. That running job has < max_preemptions_per_job preemptions
    5. Running job has been silent > 30s (likely not near completion)
    6. Running job's VRAM >= new_job's VRAM (would free enough headroom)
    7. Running job has more estimated time remaining than new_job's total duration
    """
    if int(new_job.get("priority") or 10) > 2:
        return None
    if not self.db.get_setting("preemption_enabled"):
        return None

    preempt_window = float(self.db.get_setting("preemption_window_seconds") or 120)
    max_preemptions = int(self.db.get_setting("max_preemptions_per_job") or 2)
    new_duration = self.estimator.estimate(new_job.get("source") or "", new_job.get("model"))
    new_vram = self._ollama_models.estimate_vram_mb(new_job.get("model") or "", self.db)

    with self._running_lock:
        candidates = list(self._running.keys())

    for jid in candidates:
        # Get job record for preemption_count and estimated_duration
        job = self.db.get_job(jid)
        if job is None:
            continue
        if (job.get("preemption_count") or 0) >= max_preemptions:
            continue  # immune

        # Check how long it's been running
        started_at = job.get("started_at") or now
        elapsed = now - started_at
        if elapsed >= preempt_window:
            continue  # too far into execution

        # Skip recently active jobs (stdout in last 30s = likely near completion)
        silence = self.stall_detector.get_stdout_silence(jid, now)
        if silence is not None and silence < 30.0:
            continue

        # Check VRAM: only preempt if running job uses >= new job's VRAM
        running_vram = self._ollama_models.estimate_vram_mb(
            self._running_models.get(jid) or "", self.db
        )
        if running_vram < new_vram:
            continue  # wouldn't free enough VRAM

        # Check remaining time: only preempt if running job has more remaining than new job total
        estimated_duration = job.get("estimated_duration") or self.estimator.estimate(
            job.get("source") or "", job.get("model")
        )
        remaining = estimated_duration - elapsed
        if remaining <= new_duration:
            continue  # running job nearly done; not worth preempting

        return jid  # found a candidate

    return None
```

Add `_preempt_job()` to `Daemon`:

```python
def _preempt_job(self, job_id: int) -> None:
    """SIGTERM the running job and requeue as pending.

    NEVER sends to DLQ. Preempted jobs are healthy work interrupted deliberately.
    DLQ is for permanent failures requiring human review.
    """
    import contextlib
    import os
    import signal as _sig

    job = self.db.get_job(job_id)
    pid = job.get("pid") if job else None
    if pid and pid > 0:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, _sig.SIGTERM)
            _log.info("Sent SIGTERM to job #%d pid=%d for preemption", job_id, pid)

    self.db.requeue_preempted_job(job_id)
    self.db.log_schedule_event("preempted", job_id=job_id, details={"reason": "priority_preemption"})
    _log.warning("Preempted job #%d — requeued as pending", job_id)

    # Remove from running tracking (worker thread will notice SIGTERM and exit)
    with self._running_lock:
        self._running.pop(job_id, None)
        self._running_models.pop(job_id, None)
```

In `poll_once()`, add preemption check in step 8 after `_can_admit()` passes (before dispatching to executor):

```python
# 8. Admit and dispatch
if not self._can_admit(job):
    self.db.update_daemon_state(state="idle", last_poll_at=now, current_job_id=None)
    return

# Preemption: if new job is priority 1-2, check if we should preempt a running job
preempt_id = self._check_preemption(job, now)
if preempt_id is not None:
    self._preempt_job(preempt_id)
    # Continue — now there's a free slot for the new job

# ... rest of executor submission unchanged ...
```

### Step 4: Run full test suite

```bash
pytest --timeout=120 -x -q
```

Expected: all tests pass

### Step 5: Lint

```bash
make lint
```

Expected: no errors

### Step 6: Commit

```bash
git add ollama_queue/daemon.py tests/test_daemon.py
git commit -m "feat: opt-in preemption for priority 1-2 jobs (requeues to pending, never DLQ)"
```

---

## PR 4 Complete — Verification

```bash
pytest --timeout=120 -q
```

Expected: all tests pass.

Verify new settings are seeded:
```bash
python3 -c "
import ollama_queue.db as d
db = d.Database(':memory:')
db.initialize()
for key in ['preemption_enabled', 'max_preemptions_per_job', 'preemption_window_seconds',
            'entropy_alert_window', 'entropy_alert_sigma', 'entropy_suspend_low_priority']:
    print(f'{key}: {db.get_setting(key)}')
"
```

Expected:
```
preemption_enabled: False
max_preemptions_per_job: 2
preemption_window_seconds: 120
entropy_alert_window: 30
entropy_alert_sigma: 2.0
entropy_suspend_low_priority: True
```

Verify BurstDetector works standalone:
```bash
python3 -c "
from ollama_queue.burst import BurstDetector
import time
d = BurstDetector()
now = time.time()
for i in range(20):
    d.record_submission(now + i * 60)
print('Steady regime:', d.regime(now + 1200))
for i in range(30):
    d.record_submission(now + 1200 + i * 0.1)
print('Burst regime:', d.regime(now + 1205))
"
```

Expected: steady → subcritical, burst → warning/critical
