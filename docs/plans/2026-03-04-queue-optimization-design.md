# ollama-queue Optimization Design

**Date:** 2026-03-04
**Status:** Approved
**Scope:** language:python, domain:ollama
**Research basis:** `tasks/research-ollama-queue-optimization.md` + `research/2026-03-04-ollama-queue-scheduling-research.md`

---

## Problem

The current ollama-queue is stable and correct (280 tests passing) but has ten validated gaps identified by comprehensive multi-perspective research (peer-reviewed queue theory + cross-disciplinary literature + codebase analysis). These gaps cause:

- SQLite writes ~10–100x slower than necessary (missing PRAGMAs)
- Thundering herd on DLQ retries (no jitter)
- No protection against Ollama backend crashes (no circuit breaker)
- No backpressure on unbounded queue growth (no 429)
- Long jobs block short same-priority jobs (no SJF)
- ETA estimates ignore variance — 50–100% inflation on mixed model workloads (M/G/1 model)
- AoI: recurring job staleness not factored into scheduling
- No queue health anomaly detection (entropy signal missing)
- ThreadPoolExecutor ceiling ignores available hardware capacity
- Burst detection reactive, not proactive (no Hawkes/EWMA)
- Preemption for critical jobs not implemented

---

## Solution: 4 PRs, Dependency-Ordered

### PR 1 — Foundation (db.py, dlq.py)
### PR 2 — Admission & Reliability (daemon.py, api.py, models.py)
### PR 3 — Scheduling Intelligence (daemon.py, estimator.py, scheduler.py, db.py)
### PR 4 — Observability + Strategic (api.py, daemon.py, new: burst.py)

Each PR is independently deployable and revertable. PR 1 is a prerequisite for all. PR 2 and PR 3 can be developed in parallel.

---

## PR 1 — Foundation

### 1.1 SQLite PRAGMA Hardening

**File:** `ollama_queue/db.py:_connect()`

Add six PRAGMAs after the existing WAL and foreign key setup:

```python
self._conn.execute("PRAGMA synchronous = NORMAL")
self._conn.execute("PRAGMA temp_store = MEMORY")
self._conn.execute("PRAGMA mmap_size = 536870912")    # 512MB
self._conn.execute("PRAGMA cache_size = -64000")       # 64MB page cache
self._conn.execute("PRAGMA wal_autocheckpoint = 1000")
self._conn.execute("PRAGMA busy_timeout = 5000")
```

**Rationale:**
- `synchronous=NORMAL`: eliminates per-commit fsync. Safe for local queue — crash loses at most 5 seconds of writes, recoverable from DLQ. 10–100x write speedup.
- `temp_store=MEMORY`: temp tables in RAM, avoids disk I/O for intermediate sorts
- `mmap_size`: OS-managed memory mapping reduces syscall overhead
- `cache_size`: 64MB page cache, reduces repeated disk reads
- `wal_autocheckpoint=1000`: auto-checkpoint every 1000 pages. Without this, long-running reads prevent WAL file from being truncated, causing unbounded growth and read performance degradation (lesson #1570 surface area)
- `busy_timeout=5000`: if a thread encounters a locked DB, retry for 5 seconds before raising `SQLITE_BUSY`. Prevents silent failures under concurrent FastAPI + daemon writes.

### 1.2 Decorrelated Jitter on DLQ Retries

**Files:** `ollama_queue/db.py` (schema), `ollama_queue/dlq.py:_schedule_retry()`

**Schema change:** add `last_retry_delay REAL` column to `jobs` via `_run_migrations()`:
```python
self._add_column_if_missing(conn, "jobs", "last_retry_delay", "REAL")
```

**dlq.py change:** replace `delay = base * (multiplier**retry_count)` with decorrelated jitter:
```python
import random

prev = job.get("last_retry_delay") or base
cap = settings.get("retry_backoff_cap_seconds", 3600)
delay = min(cap, random.uniform(base, prev * 3))
```

Store delay: `self.db._set_job_retry_delay(job_id, delay)` (new db method, sets `last_retry_delay`).

**New DEFAULTS:** `"retry_backoff_cap_seconds": 3600` (1 hour max retry interval).

**Rationale:** Pure exponential backoff with no jitter synchronizes retrying jobs — they all retry at the same time, creating a thundering herd against a recovering Ollama. Decorrelated jitter (AWS empirical recommendation) breaks synchronization. Each retry delay is randomized between `[base, prev * 3]`, distributing retry load over time. This is strictly better than exponential backoff with no jitter in all scenarios.

---

## PR 2 — Admission & Reliability

### 2.1 Circuit Breaker on Ollama Client

**File:** `ollama_queue/daemon.py`

3-state in-memory circuit breaker added to `Daemon` class. State resets on daemon restart (correct — Ollama likely recovered after restart).

**States:**
- `CLOSED` → normal operation
- `OPEN` → fast-reject `ollama` resource_profile jobs; `any` profile unaffected
- `HALF_OPEN` → probe with `/api/ps`; success → CLOSED, failure → OPEN

**Fields on `Daemon.__init__`:**
```python
self._cb_state: str = "closed"
self._cb_failure_count: int = 0
self._cb_opened_at: float | None = None
self._cb_open_attempt_count: int = 0  # for exponential cooldown backoff
```

**New DEFAULTS:**
```python
"cb_failure_threshold": 5,        # consecutive Ollama failures before tripping
"cb_base_cooldown_seconds": 60,   # first cooldown; doubles per successive OPEN
"cb_max_cooldown_seconds": 600,   # cap cooldown at 10 minutes
```

**What counts as Ollama failure:** `connection refused` on health check, or `subprocess exit_code` attributable to Ollama infrastructure (not user content). Tracked in `_run_job()` failure path.

**Cooldown backoff:** prevents probe storm on a flapping Ollama process:
```python
cooldown = min(
    cb_max_cooldown,
    cb_base_cooldown * (2 ** self._cb_open_attempt_count)
)
```

**Auditability:** every state transition calls:
```python
self._db.log_schedule_event("circuit_breaker", details={
    "state": new_state,
    "failure_count": self._cb_failure_count,
    "reason": reason
})
```
Circuit state visible in History tab via `schedule_events`.

**Effect on `_can_admit()`:** add check at top:
```python
if job["resource_profile"] == "ollama" and self._cb_state == "open":
    return False, "circuit_open"
```

### 2.2 HTTP 429 Backpressure

**File:** `ollama_queue/api.py`

Two new DEFAULTS:
```python
"max_queue_depth": 200,              # 0 = disabled
"max_acceptable_wait_seconds": 0,    # 0 = disabled; >0 gates by ETA
```

In `POST /api/submit` handler — check before insert:
```python
max_depth = db.get_setting("max_queue_depth")
if max_depth > 0:
    pending_count = db.count_pending_jobs()
    if pending_count >= max_depth:
        etas = estimator.queue_etas(db.get_pending_jobs())
        drain = max((e["estimated_start_offset"] + e["estimated_duration"])
                    for e in etas) if etas else 60
        raise HTTPException(
            status_code=429,
            detail=f"Queue full ({pending_count}/{max_depth} pending)",
            headers={"Retry-After": str(int(drain))}
        )
```

`Retry-After` uses actual estimated drain time — not a hardcoded constant. No `--force` bypass flag — priority 1 is the existing escape hatch for urgent submissions.

Also add `db.count_pending_jobs()` method (simple `SELECT COUNT(*) WHERE status='pending'`).

### 2.3 Resource-Aware ThreadPoolExecutor Sizing

**Files:** `ollama_queue/daemon.py`, `ollama_queue/models.py`

**New method on `OllamaModels`:** `min_estimated_vram_mb() -> float`
- Queries `model_registry` for `MIN(vram_observed_mb)` where `resource_profile='ollama'`
- Falls back to `min(MODEL_VRAM_DEFAULTS.values())`
- Returns a safe floor (never 0)

**New DEFAULTS:**
```python
"cpu_offload_efficiency": 0.3,  # fraction of RAM usable for Ollama CPU layer offload
```

**Executor sizing in `Daemon.__init__`** (after health check):
```python
health = self._health.check()
total_vram_mb = health.get("vram_total_mb") or 0
total_ram_mb = health.get("ram_total_mb") or 0
ram_resume_pct = self._db.get_setting("ram_resume_pct") / 100
cpu_offload_eff = self._db.get_setting("cpu_offload_efficiency")
vram_safety = self._db.get_setting("vram_safety_factor")

vram_available = total_vram_mb * (1.0 - (1.0 / vram_safety))
ram_available = total_ram_mb * ram_resume_pct
effective_capacity = vram_available + (ram_available * cpu_offload_eff)

min_model_mb = self._models.min_estimated_vram_mb()
theoretical_max = max(1, int(effective_capacity / min_model_mb)) if min_model_mb > 0 else 4
self._executor = ThreadPoolExecutor(max_workers=theoretical_max + 3)
```

**How to understand it:** The executor ceiling equals "how many models could physically fit in GPU + CPU RAM simultaneously, at minimum model size" plus 3 threads for IO/monitoring headroom. On a 24GB GPU + 64GB RAM with 4GB minimum model: `(18.5 + 14.4) / 4 + 3 = ~11 threads`. On CPU-only: `(0 + 48*0.3) / 4 + 3 = ~7 threads`. The `_can_admit()` gate still controls actual concurrency — this is just the ceiling.

---

## PR 3 — Scheduling Intelligence

### 3.1 SJF Tiebreaker With Aging and Bulk Estimates

**File:** `ollama_queue/daemon.py`

**New db method:** `db.estimate_duration_bulk(sources: list[str]) -> dict[str, float]`
- Single `SELECT source, AVG(duration) FROM duration_history WHERE exit_code=0 GROUP BY source` query filtered to the given sources
- Returns `{source: mean_duration}` dict

**New DEFAULTS:**
```python
"sjf_aging_factor": 3600,  # seconds; 0 = disable aging (pure SJF)
```

**Dequeue sort in `Daemon._dequeue_next_job()`:**
```python
pending = self._db.get_pending_jobs()
now = time.time()
estimates = self._db.estimate_duration_bulk([j["source"] for j in pending])

def sort_key(j):
    duration, cv_sq = self._estimator.estimate_with_variance(
        j["source"], model=j.get("model"),
        cached=estimates  # use bulk-fetched mean
    )
    std_dev = duration * (cv_sq ** 0.5)
    risk_adjusted = duration + 0.5 * std_dev   # penalize high-variance estimates

    aging_factor = self._db.get_setting("sjf_aging_factor")
    wait = now - j["submitted_at"]
    effective_duration = risk_adjusted / (1 + wait / aging_factor) if aging_factor > 0 else risk_adjusted

    return (j["priority"], effective_duration)

pending.sort(key=sort_key)
```

**Rationale:**
- Bulk query: 1 DB hit instead of N per poll cycle
- Risk-adjusted: `duration + 0.5 * std_dev` uses cv_squared so noisy estimates don't give false SJF precision
- Aging: `risk_adjusted / (1 + wait/3600)` — after 1 hour waiting, effective duration halves. Prevents starvation of long jobs. Set `sjf_aging_factor=0` to disable.
- Embed jobs bypass this sort (already handled in `_can_admit()` concurrency profile check)

### 3.2 Variance Tracking in DurationEstimator

**Files:** `ollama_queue/estimator.py`, `ollama_queue/db.py`

**New db method:** `db.estimate_duration_stats(source: str) -> tuple[float, float] | None`
```python
# Returns (mean, variance) from last 10 successful runs
SELECT AVG(duration), AVG(duration * duration) - AVG(duration) * AVG(duration)
FROM duration_history
WHERE source = ? AND exit_code = 0
ORDER BY recorded_at DESC LIMIT 10
```

**New estimator method:**
```python
def estimate_with_variance(
    self, source: str, model: str | None = None,
    cached: dict | None = None
) -> tuple[float, float]:
    """Returns (mean_seconds, cv_squared).

    cv_squared = Var(S) / Mean(S)^2
    Interpretation:
      cv_squared < 0.5: highly predictable (same model, same prompt size)
      cv_squared 0.5-1.5: normal variance
      cv_squared > 1.5: unreliable estimate (mixed workloads, treat with skepticism)
    """
    stats = self._db.estimate_duration_stats(source)
    if stats:
        mean, variance = stats
        cv_sq = variance / (mean ** 2) if mean > 0 else 1.5
        return mean, cv_sq

    mean = cached.get(source) if cached else None
    if mean is None:
        mean = self._model_default(model) if model else self.GENERIC_DEFAULT
    return mean, 1.5  # unknown variance → conservative default
```

**Surfaces in API:** `/api/queue` response adds `estimated_duration_cv_squared` per job. Dashboard ETA confidence intervals widen when cv_squared > 1.5.

### 3.3 Age of Information Tiebreaker for Recurring Jobs

**File:** `ollama_queue/scheduler.py`

**New db method:** `db.get_last_successful_run_time(recurring_job_id: int) -> float | None`
```python
SELECT MAX(completed_at) FROM jobs
WHERE recurring_job_id = ? AND exit_code = 0
```
Uses `exit_code=0` (successful), not `last_run` which includes failures.

**New DEFAULTS:**
```python
"aoi_weight": 0.3,  # fraction of scheduling score from information staleness
```

**AoI score in `Scheduler.promote_due_jobs()`** — sorts `due` list before promoting:
```python
def _aoi_sort_key(self, rj: dict, now: float) -> float:
    aoi_weight = self._db.get_setting("aoi_weight")

    # Priority normalized to [0, 1] — 0=critical, 1=background
    priority_norm = (rj["priority"] - 1) / 9

    # Staleness normalized to [0, 1] — 1=max urgency (5x overdue)
    last_success = self._db.get_last_successful_run_time(rj["id"])
    if last_success:
        interval = rj.get("interval_seconds") or 3600
        staleness = (now - last_success) / max(interval, 1)
        staleness_norm = min(1.0, staleness / 5.0)
    else:
        staleness_norm = 1.0  # never completed → maximum urgency

    # Lower score = higher scheduling priority
    return priority_norm * (1 - aoi_weight) + (1 - staleness_norm) * aoi_weight
```

**Why normalize:** raw priority (1–10) and raw `1/staleness` (0–∞) are on incompatible scales. Without normalization, `aoi_weight=0.3` doesn't actually mean "30% from staleness" — it means whatever the raw staleness ratio happens to be. Normalization makes the weight semantically correct.

---

## PR 4 — Observability + Strategic

### 4.1 Adaptive Shannon Entropy Queue Health

**File:** `ollama_queue/api.py` (and daemon.py for action)

**New DEFAULTS:**
```python
"entropy_alert_window": 30,           # polls for rolling baseline
"entropy_alert_sigma": 2.0,           # standard deviations for anomaly
"entropy_suspend_low_priority": True, # act on critical_backlog by suspending p8-10 promotion
```

**Entropy computation (age-weighted):**
```python
def _queue_entropy(pending_jobs: list[dict], now: float) -> float:
    if not pending_jobs:
        return 0.0
    # Weight each job by log(1 + wait_seconds) — older jobs matter more
    weights = {j["id"]: log(1 + (now - j["submitted_at"])) for j in pending_jobs}
    total_w = sum(weights.values()) or 1.0
    priority_weights: dict[int, float] = defaultdict(float)
    for j in pending_jobs:
        priority_weights[j["priority"]] += weights[j["id"]] / total_w
    return -sum(w * log2(w) for w in priority_weights.values() if w > 0)
```

**Adaptive anomaly detection in daemon poll:**
```python
self._entropy_history: deque[float] = deque(maxlen=entropy_alert_window)

# After computing current H:
self._entropy_history.append(H)
if len(self._entropy_history) >= 10:
    mean_H = statistics.mean(self._entropy_history)
    std_H = statistics.stdev(self._entropy_history) or 0.1
    if H < mean_H - sigma * std_H:
        alert_type = "critical_backlog" if high_priority_dominant else "background_flood"
        self._db.log_schedule_event("entropy_alert", details={"H": H, "type": alert_type})
        if alert_type == "critical_backlog" and entropy_suspend_low_priority:
            self._entropy_suspend_until = now + 60  # suspend p8-10 promotion for 60s
```

**In `Scheduler.promote_due_jobs()`:** check `daemon._entropy_suspend_until` before promoting priority 8–10 jobs. Passed via a flag on the Scheduler or checked in daemon before calling promote.

### 4.2 EWMA Burst Detection (No External Dependencies)

**New file:** `ollama_queue/burst.py`

**Design:** EWMA of inter-arrival times, normalized against a baseline. Self-excitation (burst) manifests as shrinking inter-arrival times.

```python
class BurstDetector:
    """Dependency-free burst detection via EWMA of inter-arrival times.

    Regime is determined by comparing current EWMA inter-arrival time
    against a stable baseline. A burst causes inter-arrival times to
    shrink rapidly (jobs arrive faster than usual).
    """

    REGIMES = {
        "subcritical": (0.5, float("inf")),   # ratio > 0.5 of baseline
        "moderate":    (0.3, 0.5),
        "warning":     (0.15, 0.3),
        "critical":    (0.0, 0.15),
    }

    def __init__(self, alpha: float = 0.3, baseline_window: int = 100):
        self._alpha = alpha
        self._ewma: float | None = None
        self._baseline: float | None = None
        self._baseline_samples: deque[float] = deque(maxlen=baseline_window)
        self._last_ts: float | None = None
        self._last_regime_compute: float = 0.0
        self._cached_regime: str = "unknown"

    def record_submission(self, ts: float) -> None:
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
        """Recompute at most every 60s or every 10 new events."""
        if len(self._baseline_samples) < 10:
            return "unknown"
        # Baseline: 75th percentile of historical intervals (robust to bursts)
        sorted_samples = sorted(self._baseline_samples)
        p75_idx = int(0.75 * len(sorted_samples))
        baseline = sorted_samples[p75_idx]
        ratio = self._ewma / baseline if baseline > 0 else 1.0
        for regime, (low, high) in self.REGIMES.items():
            if low <= ratio < high:
                return regime
        return "subcritical"
```

**Integration in daemon:**
- `record_submission()` called in `POST /api/submit` handler
- `regime()` computed in `poll_once()` every 60s
- When `warning`: log schedule_event, surface in `/api/health`
- When `critical` AND `queue_depth > 0.5 * max_queue_depth`: engage 429 (both signals required — regime alone is insufficient)

**Time-based window:** `deque(maxlen=100)` combined with 75th-percentile baseline automatically handles sparse queues. Sparse arrivals have large intervals → high baseline → ratio stays > 0.5 → subcritical. No artificial event-count problems.

### 4.3 DBMS-Inspired Preemption

**File:** `ollama_queue/daemon.py`

**New DEFAULTS:**
```python
"preemption_enabled": False,            # opt-in; off by default
"preemption_window_seconds": 120,       # only preempt jobs running < 120s
"max_preemptions_per_job": 2,           # prevent infinite preemption loops
```

**Schema change:** add `preemption_count INTEGER DEFAULT 0` to `jobs` via `_run_migrations()`.

**Preemption check — triggered when a priority 1–2 job is admitted:**
```python
def _check_preemption(self, new_job: dict, now: float) -> int | None:
    """Returns job_id to preempt, or None."""
    if new_job["priority"] > 2:
        return None
    if not self._db.get_setting("preemption_enabled"):
        return None

    preempt_window = self._db.get_setting("preemption_window_seconds")
    max_preemptions = self._db.get_setting("max_preemptions_per_job")
    new_duration = self._estimator.estimate(new_job["source"], new_job.get("model"))

    with self._running_lock:
        for jid, meta in self._running.items():
            # Skip immune jobs (already preempted max times)
            if (meta.get("preemption_count") or 0) >= max_preemptions:
                continue
            elapsed = now - meta["started_at"]
            if elapsed >= preempt_window:
                continue
            # Skip if recently active (likely near completion)
            silence = self._stall_detector.get_stdout_silence(jid, now)
            if silence is not None and silence < 30:
                continue
            # Only preempt if it creates enough VRAM headroom
            running_vram = self._models.estimate_vram_mb(meta.get("model") or "")
            new_vram = self._models.estimate_vram_mb(new_job.get("model") or "")
            if running_vram < new_vram:
                continue
            remaining = meta["estimated_duration"] - elapsed
            if remaining > new_duration:
                return jid
    return None
```

**Preemption execution — NOT via DLQ:**
```python
def _preempt_job(self, job_id: int) -> None:
    """SIGTERM the running job and requeue it as pending. Never touches DLQ."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(self._running[job_id]["pid"], signal.SIGTERM)

    self._db.requeue_preempted_job(job_id)  # sets status='pending', increments preemption_count
    self._db.log_schedule_event("preempted", job_id=job_id,
        details={"reason": "priority_preemption"})
    _log.warning("Preempted job #%d for higher-priority work", job_id)
```

**New db method:** `db.requeue_preempted_job(job_id)`:
```python
UPDATE jobs SET status='pending', started_at=NULL, pid=NULL,
    preemption_count = COALESCE(preemption_count, 0) + 1,
    submitted_at = ?   -- reset to now so it re-sorts correctly
WHERE id = ?
```

**Why NOT DLQ:** DLQ means "permanent failure requiring human review." Preempted jobs are healthy work interrupted deliberately. Sending them to DLQ corrupts the DLQ's semantic meaning, pollutes the failure metric, and requires manual `dlq retry` to recover what was never broken.

---

## File Change Summary

| File | PR | Changes |
|------|----|---------|
| `db.py` | 1, 2, 3, 4 | 6 PRAGMAs; `last_retry_delay` migration; `estimate_duration_bulk()`; `estimate_duration_stats()`; `get_last_successful_run_time()`; `count_pending_jobs()`; `requeue_preempted_job()`; `preemption_count` migration; new DEFAULTS entries |
| `dlq.py` | 1 | Replace exponential backoff with decorrelated jitter; store `last_retry_delay` |
| `daemon.py` | 2, 3, 4 | Circuit breaker (3-state); resource-aware executor; 429 check; preemption logic; entropy suspension; burst regime check |
| `api.py` | 2, 4 | 429 gate in submit handler; entropy + burst_regime in `/api/health` response |
| `models.py` | 2 | Add `min_estimated_vram_mb()` method |
| `estimator.py` | 3 | Add `estimate_with_variance()`; accept `cached` bulk dict |
| `scheduler.py` | 3, 4 | AoI sort key; entropy suspension check |
| `burst.py` | 4 | **New** — `BurstDetector` class (zero new dependencies) |

---

## New DEFAULTS Summary

| Key | Default | PR | Purpose |
|-----|---------|----|---------|
| `retry_backoff_cap_seconds` | `3600` | 1 | Max DLQ retry interval |
| `cb_failure_threshold` | `5` | 2 | Circuit breaker trip count |
| `cb_base_cooldown_seconds` | `60` | 2 | Circuit breaker first cooldown |
| `cb_max_cooldown_seconds` | `600` | 2 | Circuit breaker max cooldown |
| `max_queue_depth` | `200` | 2 | 429 gate; 0=disabled |
| `max_acceptable_wait_seconds` | `0` | 2 | ETA-based 429 gate; 0=disabled |
| `cpu_offload_efficiency` | `0.3` | 2 | CPU RAM fraction for model offload |
| `sjf_aging_factor` | `3600` | 3 | Starvation prevention; 0=pure SJF |
| `aoi_weight` | `0.3` | 3 | AoI vs priority in recurring scheduling |
| `entropy_alert_window` | `30` | 4 | Rolling baseline window (polls) |
| `entropy_alert_sigma` | `2.0` | 4 | Anomaly detection threshold |
| `entropy_suspend_low_priority` | `True` | 4 | Act on backlog by suspending p8-10 |
| `preemption_enabled` | `False` | 4 | Opt-in preemption |
| `preemption_window_seconds` | `120` | 4 | Only preempt jobs running < N seconds |
| `max_preemptions_per_job` | `2` | 4 | Prevent infinite preemption loops |

---

## Testing Strategy

Each PR adds tests to its affected test files. No test counts hardcoded (lesson #177).

**PR 1:**
- `test_db.py`: verify all 6 PRAGMAs are set on connect; verify `last_retry_delay` column exists
- `test_dlq.py`: verify jitter produces values in `[base, prev*3]` range; verify multiple retries don't synchronize (statistical test: std_dev of N delays > base*0.1)

**PR 2:**
- `test_daemon.py`: circuit breaker state transitions (closed→open→half_open→closed); verify `any` profile bypasses circuit; verify exponential cooldown
- `test_api.py`: 429 response with correct `Retry-After` header; verify disabled when `max_queue_depth=0`
- `test_models.py`: `min_estimated_vram_mb()` returns floor > 0 on empty registry

**PR 3:**
- `test_daemon.py`: SJF ordering (shorter job ahead of longer in same priority); aging (long-waiting job eventually promoted)
- `test_estimator.py`: `estimate_with_variance()` returns correct cv_squared; high-variance history → cv_sq > 1
- `test_scheduler.py`: AoI sort key normalization; stale source promoted above fresh same-priority source

**PR 4:**
- `test_burst.py` (new): EWMA converges on burst; regime classifications; handles sparse arrivals
- `test_daemon.py`: preemption sends to pending (not DLQ); preemption_count incremented; immune job not preempted; recently-active job not preempted
- Entropy: adaptive threshold doesn't alarm on stable queue; anomaly detected when priority distribution collapses

---

## Non-Goals

- No changes to dashboard SPA (metrics surfaced in API; dashboard updates are a separate design)
- No learning-to-rank ML (month 3 roadmap item; insufficient job history today)
- No changes to Bayesian stall detection (already correct)
- No changes to 48-slot load map (already correct)
