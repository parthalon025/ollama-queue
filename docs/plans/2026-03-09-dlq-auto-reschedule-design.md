# DLQ Auto-Reschedule & Deferred Jobs — Design Document

**Date:** 2026-03-09
**Status:** Approved
**Repo:** `~/Documents/projects/ollama-queue/`

---

## In Plain English

When an AI job fails and lands in the dead letter queue, the system currently does nothing — it sits there until you manually hit Retry. This design makes the queue smart enough to figure out when that failed job can actually run successfully, and schedules it into a quiet slot where it has the resources it needs. It also adds the ability to proactively defer jobs that would fail (GPU too hot, not enough memory, system overloaded) instead of letting them fail first. Both systems share an intelligence layer that learns how long each model takes on your specific hardware, and uses that knowledge to make every scheduling decision across the entire queue smarter.

---

## Section 1: Bayesian Runtime Estimator

The core estimation engine. Predicts how long a job will take based on model size, historical performance, and token throughput — all learned from this machine's actual behavior.

### Estimation Model

Log-Normal distribution (standard for duration modeling — strictly positive, right-skewed). Conjugate prior on log-scale, updated by real observations.

### Input Signals (4-tier hierarchy)

| Tier | Signal | Source | Strength |
|------|--------|--------|----------|
| 1 | Resource profile prior | Job metadata (`light`/`medium`/`heavy`/`gpu_heavy`) | Weakest — generic bucket |
| 2 | Model size → tok/min curve | Cross-model regression on this machine | Medium — interpolated from other models |
| 3 | Model-level tok/min | Historical completed jobs for this model | Strong — direct observations |
| 4 | (Model, command pattern) durations | Historical completed jobs for this workload | Strongest — most specific |

### Token-Per-Minute Tracking

Ollama API responses include performance metrics. Capture after every job completion:

| Field | Source | Purpose |
|-------|--------|---------|
| `load_duration_ns` | Ollama `load_duration` | Warmup time (model load from disk) |
| `prompt_eval_count` | Ollama `prompt_eval_count` | Input tokens processed |
| `prompt_eval_duration_ns` | Ollama `prompt_eval_duration` | Prompt evaluation time |
| `eval_count` | Ollama `eval_count` | Output tokens generated |
| `eval_duration_ns` | Ollama `eval_duration` | Generation time |

Derived metric: `tok_per_min = (eval_count / eval_duration_ns) * 60e9`

### Cross-Model Estimation (Empirical Hardware Profile)

Instead of hardcoded performance tables, the system builds a performance model of your machine from every model it has ever run:

```
Observed data points (example on this machine):
  qwen3.5:9b     (5.4GB)  → 82 tok/min, 1.8s warmup
  qwen2.5-coder:14b (8.7GB)  → 48 tok/min, 2.9s warmup
  deepseek-r1:8b (4.9GB)  → 88 tok/min, 1.6s warmup

Fitted curves:
  tok/min = f(model_size_gb)   → log-linear regression
  warmup  = g(model_size_gb)   → linear regression
```

When a never-run model enters the queue, interpolate from fitted curves. 3+ observed models spanning different sizes is enough for reasonable estimates. The curves capture this machine's actual memory bandwidth, GPU speed, and disk throughput — no spec sheets needed.

| Observed models | Strategy |
|----------------|----------|
| 0 | Resource profile priors (weak, generic) |
| 1 | Single-point — scale linearly by size ratio |
| 2 | Linear interpolation between the two |
| 3+ | Log-linear regression with confidence intervals |

### Warmup Estimation

Model warmup (loading weights into VRAM) can dominate total runtime for large models. Estimated separately:

- **Cold model** (not loaded): `warmup = g(model_size_gb)` from fitted curve
- **Hot model** (already loaded per `ollama ps`): `warmup ≈ 0`
- **Future slots**: always assume cold (model may be evicted before scheduled time)

### Estimation Flow

```python
def estimate_runtime(model, command, resource_profile):
    # Tier 1: resource profile prior (weakest)
    prior = PROFILE_PRIORS[resource_profile]

    # Tier 2: cross-model curve (interpolate from this machine's history)
    model_size = get_model_size(model)  # from ollama show
    if performance_curve.fitted:
        size_prior = performance_curve.predict(model_size)
        prior = bayesian_update(prior, size_prior)

    # Tier 3: observed tok/min for this model (log-normal posterior)
    observed_rates = db.get_tok_per_min(model)
    if observed_rates:
        prior = bayesian_update(prior, observed_rates)

    # Tier 4: historical durations for (model, command pattern)
    historical = db.get_job_durations(model, command)
    if historical:
        prior = bayesian_update(prior, historical)

    # Warmup (separate estimate)
    warmup = estimate_warmup(model)

    generation_mean = exp(posterior_mean)
    generation_90th = exp(posterior_90th)

    return Estimate(
        warmup_mean=warmup.mean,
        warmup_upper=warmup.p90,
        generation_mean=generation_mean,
        generation_upper=generation_90th,
        total_mean=warmup.mean + generation_mean,
        total_upper=warmup.p90 + generation_90th,
        confidence=confidence_from_sample_size(n_observations),
    )
```

### Data Storage

New `job_metrics` table (separate from `jobs` — not all jobs produce Ollama metrics):

```sql
CREATE TABLE job_metrics (
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
);

CREATE INDEX idx_job_metrics_model ON job_metrics(model);
```

### Curve Caching

Refit the log-linear regression only when:
- A new model's data arrives (model not previously seen)
- Every 10 completions for an existing model
- Manual refresh via API

Cached in memory on the `RuntimeEstimator` instance. Not persisted to DB (cheap to refit from `job_metrics`).

---

## Section 2: Slot Fitting & Sweep Logic

The `DLQScheduler` decides which DLQ entries fit into which future slots, triggered by job completion events.

### Sweep Triggers

| Trigger | When | Purpose |
|---------|------|---------|
| Event-driven | After any job completes | Primary — a slot just freed up |
| Periodic fallback | Every 30 min (configurable) | Safety net — daemon restart, missed events |

### Slot Fitting Algorithm

```python
def _sweep(self):
    entries = db.list_dlq(unscheduled_only=True)
    if not entries:
        return

    load = scheduler.load_map(now)  # 48 slots x 30 min = 24h

    for entry in sorted(entries, key=priority_then_age):
        est = estimator.estimate(entry.model, entry.command, entry.resource_profile)
        slots_needed = ceil(est.total_upper / 1800)  # 30-min slots

        slot = find_fitting_slot(load, slots_needed, entry.model)
        if slot is None:
            continue  # no room in next 24h — try next sweep

        retry_at = slot_to_datetime(slot)
        new_job_id = db.retry_dlq_entry(entry.id)
        db.set_retry_after(new_job_id, retry_at)
        db.update_dlq_reschedule(entry.id,
            rescheduled_job_id=new_job_id,
            rescheduled_for=retry_at,
            scoring_snapshot=score_reasons)
```

### VRAM-Aware Slot Fitting

```python
def find_fitting_slot(load, slots_needed, model):
    vram_required = get_model_vram(model)
    for window in contiguous_runs(slots_needed):
        if any(slot.score >= 999 for slot in window):
            continue  # pinned
        if any(slot.committed_vram + vram_required > available_vram for slot in window):
            continue  # won't fit
        if sum(slot.score for slot in window) > max_slot_load:
            continue  # too busy
        candidates.append((window, total_score))
    return min(candidates, key=total_score)  # lowest load wins
```

### Priority Ordering

DLQ entries swept in order:
1. Highest priority first (priority 10 before priority 1)
2. Oldest first within same priority (FIFO within tier)

### DLQ Entry State Tracking

New columns on `dlq` table:

```sql
ALTER TABLE dlq ADD COLUMN auto_reschedule_count INTEGER DEFAULT 0;
ALTER TABLE dlq ADD COLUMN auto_rescheduled_at REAL;
ALTER TABLE dlq ADD COLUMN rescheduled_job_id TEXT;
ALTER TABLE dlq ADD COLUMN rescheduled_for REAL;
ALTER TABLE dlq ADD COLUMN reschedule_reasoning TEXT;  -- JSON
```

Sweep filter: `WHERE resolved_at IS NULL AND auto_rescheduled_at IS NULL`

When a rescheduled job fails and re-enters DLQ, it becomes a new DLQ entry with `auto_reschedule_count` incremented from the previous entry.

### No Retry Cap

The system retries indefinitely. There is no hard limit on auto-reschedule attempts. The existing `StallDetector` handles hung jobs during execution — no special treatment for rescheduled jobs. A display-only "chronic failure" warning badge appears in the UI after a configurable number of attempts (default 5) to flag jobs that may need manual investigation.

---

## Section 3: Full-Awareness Decision Engine

The sweep doesn't just check "is there a free slot?" — it scores candidate slots across every dimension the system can observe.

### System Snapshot

Gathered before each sweep:

```python
@dataclass
class SystemSnapshot:
    # Hardware (real-time)
    cpu_load: float
    ram_available_gb: float
    gpu_util_pct: float
    gpu_temp_c: float
    gpu_vram_used_gb: float
    gpu_vram_total_gb: float
    disk_io_busy_pct: float

    # Ollama (live)
    loaded_models: list[str]         # hot models (no warmup)
    model_keep_alive_s: int

    # Queue (current + upcoming)
    running_jobs: list[Job]
    pending_jobs: list[Job]
    upcoming_recurring: list[Job]    # due within next 2h

    # Historical (learned patterns)
    hourly_load_profile: list[float]  # avg load by hour-of-day
    daily_load_profile: list[float]   # avg load by day-of-week
```

### Slot Scoring (10 factors)

```python
def score_slot(self, slot, entry, snapshot):
    score = 0.0
    reasons = []

    # 1. Load headroom
    load = load_map[slot]
    score += (10 - load) * 1.0

    # 2. VRAM fit (hard gate + margin bonus)
    vram_free = snapshot.gpu_vram_total_gb - committed_vram(slot)
    model_vram = get_model_vram(entry.model)
    if model_vram > vram_free: return -1, ["insufficient VRAM"]
    score += (vram_free - model_vram) / snapshot.gpu_vram_total_gb * 3.0

    # 3. Model already hot (skip warmup)
    if entry.model in snapshot.loaded_models:
        score += 2.0

    # 4. Recurring job conflicts
    conflicts = overlapping_recurring(slot, est.total_upper)
    score -= len(conflicts) * 3.0

    # 5. Historical quiet time
    hourly_avg = snapshot.hourly_load_profile[slot_to_hour(slot)]
    score += (1.0 - hourly_avg) * 2.0

    # 6. Thermal headroom
    if snapshot.gpu_temp_c > 80: score -= 2.0

    # 7. RAM pressure
    if snapshot.ram_available_gb < 4.0: score -= 3.0

    # 8. Failure-aware (see Failure Classification below)
    score += failure_bonus(entry, slot, snapshot)

    # 9. Disk I/O (affects warmup)
    if entry.model not in snapshot.loaded_models and snapshot.disk_io_busy_pct > 70:
        score -= 1.5

    # 10. Queue depth (don't starve normal jobs)
    if len(snapshot.pending_jobs) > 5: score -= 2.0

    return score, reasons
```

### Failure Classification

Classify DLQ failure reasons to adapt retry strategy:

| Category | Patterns | Scoring Adaptation |
|----------|----------|-------------------|
| `resource` | OOM, CUDA error, memory alloc | Require 30% extra VRAM/RAM headroom |
| `timeout` | Job exceeded timeout | Prefer open-ended slots with no following jobs |
| `stall` | StallDetector killed it | Avoid concurrent heavy jobs in slot |
| `error` | Non-zero exit, model error | No special scoring — may be transient |
| `unknown` | Unclassified | Default scoring |

```python
def classify_failure(failure_reason: str) -> str:
    lower = failure_reason.lower()
    if any(k in lower for k in ('oom', 'memory', 'cuda', 'alloc')):
        return 'resource'
    if 'timeout' in lower:
        return 'timeout'
    if 'stall' in lower:
        return 'stall'
    if any(k in lower for k in ('exit code', 'error')):
        return 'error'
    return 'unknown'
```

### Decision Transparency

Every reschedule decision stores its full reasoning in `dlq.reschedule_reasoning` as JSON:

```json
{
    "score": 7.2,
    "slot": "2026-03-10T02:30:00",
    "reasons": [
        "load headroom: 8.0/10",
        "VRAM margin: 4.2GB",
        "model hot — no warmup needed",
        "historical load at 02:00 = 0.12 (quiet)",
        "no recurring conflicts",
        "timeout failure — open-ended slot selected"
    ],
    "estimate": {
        "warmup_mean": 0.0,
        "generation_mean": 45.2,
        "total_upper": 62.0,
        "confidence": "high"
    }
}
```

Skipped entries also get reasoning logged (stored on a `dlq.skip_reasoning` column or in daemon logs) so the UI can show why a job was deferred to the next sweep.

---

## Section 4: Daemon Integration & API

### Daemon Wiring (3 touch points)

**1. Metrics capture** (daemon.py, on job completion):
```python
metrics = parse_ollama_metrics(stdout)
if metrics:  # graceful fallback — non-Ollama jobs produce None
    db.store_job_metrics(job_id, metrics)
```

**2. Job completion hook** (daemon.py, after `dlq.handle_failure()`):
```python
self.dlq_scheduler.on_job_completed(job_id)
```

**3. Periodic fallback** (daemon.py poll loop):
```python
if now - last_dlq_sweep >= sweep_interval:
    self.dlq_scheduler.periodic_sweep()
    self.deferral_scheduler.sweep()
    last_dlq_sweep = now
```

### Sweep Lock

`on_job_completed()` and `periodic_sweep()` can fire concurrently. Single lock prevents double-scheduling:

```python
def _sweep(self):
    with self._sweep_lock:
        ...
```

### Ollama Response Parsing

Ollama JSON responses include metrics in the final streaming chunk:

```json
{"done": true, "total_duration": 5191217417, "load_duration": 2154458,
 "prompt_eval_count": 26, "prompt_eval_duration": 383809000,
 "eval_count": 298, "eval_duration": 4799921000}
```

Parser extracts the final `{"done": true, ...}` line from stdout. Non-Ollama jobs (no matching JSON) return `None` — fall back to wall-clock duration (`completed_at - started_at`).

### Reschedule Lineage

When a rescheduled job fails and re-enters DLQ:
1. New DLQ entry created (normal flow)
2. `auto_reschedule_count` copied from previous entry + 1
3. Job metadata carries `auto_reschedule_count` so the next DLQ entry can inherit it

This enables:
- UI showing retry history per original job
- Detection of chronically failing jobs (display-only, no hard cap)
- Estimator excluding chronic failures from curve fitting (poisoned data)

### New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dlq/schedule-preview` | What the next sweep would do, without executing |
| `POST` | `/api/dlq/{id}/reschedule` | Manually trigger reschedule for one entry |
| `GET` | `/api/metrics/models` | Per-model performance stats (tok/min, warmup, sample count) |
| `GET` | `/api/metrics/performance-curve` | Fitted regression curves (for UI chart) |
| `POST` | `/api/jobs/{id}/defer` | User-initiated deferral (see Section 6) |
| `GET` | `/api/deferred` | List deferred jobs with scheduled resume times |

### Settings

| Key | Default | Description |
|-----|---------|-------------|
| `dlq.auto_reschedule` | `true` | Master toggle for DLQ auto-reschedule |
| `dlq.max_slot_load` | `5.0` | Max aggregate load score for a candidate slot |
| `dlq.sweep_fallback_minutes` | `30` | Periodic sweep interval |
| `dlq.chronic_failure_threshold` | `5` | Display-only warning badge after N attempts |
| `dlq.resource_failure_extra_margin` | `0.3` | Extra VRAM headroom (fraction) for OOM failures |
| `defer.enabled` | `true` | Master toggle for system-initiated deferral |
| `defer.burst_priority_threshold` | `3` | Defer jobs below this priority during bursts |
| `defer.thermal_threshold_c` | `85` | Defer GPU jobs above this temperature |
| `defer.resource_wait_timeout_s` | `120` | Wait this long before deferring (vs holding in pending) |

---

## Section 5: Deferred Jobs

Deferred is the proactive twin of DLQ — it prevents failures instead of recovering from them. A deferred job is not failed; it is parked with intent to resume when conditions are right.

### What Triggers Deferral

| Trigger | Description | Initiator |
|---------|-------------|-----------|
| User-initiated | "Run this later when quiet" | User via UI/API/CLI |
| Resource | VRAM full, RAM low — job can't start safely | Daemon admission check |
| Contention | Queue overloaded, low-priority yields to higher | Daemon dequeue |
| Burst | Burst detected — defer non-critical work | BurstDetector |
| Thermal | GPU temp exceeds threshold | System snapshot |
| Schedule | Recurring job fires during peak | Scheduler + intelligence |

### Job States (Revised)

```
pending ──→ running ──→ completed
   │            │
   │            ├──→ failed ──→ dead (DLQ)
   │            │
   │            └──→ deferred (system: resource during run)
   │
   └──→ deferred (system: can't admit / burst / thermal / user)
           │
           └──→ pending (intelligence layer finds slot)
```

A deferred job keeps its original job ID — it flips `deferred → pending` when resumed. No new job created. This is the key difference from DLQ retry (which creates a new job).

### Deferral Record

```sql
CREATE TABLE deferrals (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    reason TEXT NOT NULL,           -- 'user', 'resource', 'contention', 'burst', 'thermal'
    deferred_at REAL NOT NULL,
    estimated_ready_at REAL,        -- intelligence layer's best guess
    scheduled_for REAL,             -- when the sweep assigned a slot
    scoring_snapshot TEXT,           -- JSON: full score_slot() reasoning
    resumed_at REAL,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);
```

### System-Initiated Deferral Points

**Admission deferral** (daemon `_can_admit()`):

```python
def _can_admit(self, job):
    vram_needed = get_model_vram(job.model)
    vram_free = snapshot.gpu_vram_total_gb - snapshot.gpu_vram_used_gb

    if vram_needed <= vram_free:
        return "admit"

    est_wait = intelligence.predict_next_opening(vram_needed)
    if est_wait and est_wait < defer_wait_timeout:
        return "wait"  # hold in pending — opens soon

    defer(job, reason='resource',
          context=f"needs {vram_needed}GB, {vram_free}GB free")
    return "deferred"
```

**Burst deferral**:

```python
def on_burst_detected(self):
    threshold = db.get_setting('defer.burst_priority_threshold', 3)
    for job in snapshot.pending_jobs:
        if job.priority < threshold:
            defer(job, reason='burst',
                  context=f"priority {job.priority} < burst threshold {threshold}")
```

**Thermal deferral**:

```python
if snapshot.gpu_temp_c > db.get_setting('defer.thermal_threshold_c', 85):
    for job in pending_gpu_jobs:
        defer(job, reason='thermal',
              context=f"GPU {snapshot.gpu_temp_c}C > threshold")
```

### User-Initiated Deferral

- **UI:** "Defer" button on pending/running jobs → modal with options: next quiet slot, specific time, or condition-based
- **API:** `POST /api/jobs/{id}/defer` with optional `until` parameter
- **CLI:** `ollama-queue defer <job_id> [--until "02:00"]`

### Deferred Job Sweep

Uses the same intelligence layer as DLQ sweep — `score_slot()` works identically:

```python
class DeferralScheduler:
    def sweep(self):
        deferred = db.list_deferred(unscheduled_only=True)
        for entry in sorted(deferred, key=priority_then_age):
            est = intelligence.estimate(entry.model, entry.command, entry.resource_profile)
            slot = intelligence.find_fitting_slot(load, est, entry.model)
            if slot:
                db.update_job_status(entry.job_id, 'pending')
                db.set_retry_after(entry.job_id, slot_to_datetime(slot))
                db.update_deferral(entry.id, scheduled_for=slot, scoring=reasons)
```

Same triggers: event-driven on job completion + periodic fallback.

### Deferred vs DLQ

| Aspect | Deferred | DLQ |
|--------|----------|-----|
| Why | Can't run now, will work later | Failed, might work later |
| Job status | `deferred` | `dead` |
| Creates new job? | No — same job ID, flips back to `pending` | Yes — new job from DLQ entry |
| Lineage | Not needed — same job | Yes — `auto_reschedule_count` |
| Failure classification | Deferral reason (resource/burst/thermal/user) | Failure category (OOM/timeout/error/stall) |
| Scoring | Same `score_slot()` | Same `score_slot()` + failure-aware bonuses |
| UI location | Deferred section in queue view | DLQ tab |

---

## Section 6: Cross-System Intelligence Layer

The infrastructure built for DLQ auto-reschedule is a general-purpose intelligence layer that improves the entire queue.

### Architecture

```
┌─────────────────────────────────────────────────┐
│              IntelligenceLayer                    │
│  ┌────────────────┐  ┌──────────────────────┐   │
│  │ RuntimeEstim.  │  │  SystemSnapshot      │   │
│  │ (Bayesian,     │  │  (real-time state,   │   │
│  │  cross-model)  │  │   hardware, Ollama)  │   │
│  └───────┬────────┘  └──────────┬───────────┘   │
│          │    ┌────────────────┐ │               │
│          │    │ LoadPatterns   │ │               │
│          │    │ (hour-of-day,  │ │               │
│          │    │  day-of-week)  │ │               │
│          │    └───────┬────────┘ │               │
│          └────┬───────┘──────────┘               │
│               │                                  │
│   ┌───────────┴────────────────────┐             │
│   │  estimate(model, cmd, profile) │             │
│   │  score_slot(job, slot)         │             │
│   │  find_fitting_slot(load, est)  │             │
│   │  predict_completion(job)       │             │
│   │  suggest_timeout(model)        │             │
│   │  snapshot()                    │             │
│   └────────────────────────────────┘             │
└───────────┬───────────┬───────────┬──────────────┘
            │           │           │
     ┌──────┴──┐  ┌─────┴───┐  ┌───┴──────┐
     │DLQ Sched│  │Scheduler│  │ Daemon   │
     │Deferral │  │(suggest, │  │(admit,   │
     │ Sched   │  │ rebal)  │  │ stall,   │
     │         │  │         │  │ dequeue) │
     └─────────┘  └─────────┘  └──────────┘
```

### Consumers (current scope + future)

**DLQ Scheduler** (this design):
- `estimate()` → slot fitting
- `score_slot()` → multi-factor decision
- `snapshot()` → awareness inputs

**Deferral Scheduler** (this design):
- Same interfaces as DLQ scheduler

**Normal Job Scheduling** (future — immediate benefit):
- `suggest_time()` upgraded: finds slots where the specific job fits, not just low-load slots
- `estimate()` → VRAM-aware and duration-aware slot suggestions for recurring jobs

**VRAM Admission** (future):
- `predict_completion()` → "current job finishes in ~30s, wait instead of rejecting"
- Predictive admission instead of reactive admit/reject

**Stall Detection** (future):
- `estimate()` → per-job stall thresholds
- "7B job expected 30s, stalled at 75s" vs "70B job expected 10min, not stalled at 75s"

**Queue Ordering** (future):
- Model-affinity batching: among equal-priority jobs, prefer the one using the currently loaded model
- Avoids unnecessary model swap (load new model = evict current = warmup penalty)

**Burst Detection** (future):
- Hourly load profiles distinguish real bursts from normal daily patterns
- "10 jobs at 2 AM = burst" vs "10 jobs at 2 PM = normal Tuesday"

**Auto-Timeout Suggestion** (future):
- `suggest_timeout()` → 3x upper bound estimate
- UI auto-suggests when creating recurring jobs

### Implementation Approach

Build the intelligence layer for DLQ/deferral first (this scope). Design interfaces so other systems adopt them incrementally:
- `intelligence.py` — `RuntimeEstimator`, `SystemSnapshot`, `LoadPatterns`, `PerformanceCurve`
- `dlq_scheduler.py` — consumes intelligence, implements DLQ sweep
- `deferral_scheduler.py` — consumes intelligence, implements deferral sweep
- Daemon, scheduler, burst detector → adopt intelligence methods in follow-up PRs

---

## Section 7: Visualization Design (Research-Backed)

Every chart and data presentation in this feature is grounded in peer-reviewed research. References: Cleveland & McGill (1984) perceptual hierarchy, Bertin (1967) visual variables, Tufte (1983/2006) data-ink ratio and sparklines, Ware (2004) preattentive features, Hullman et al. (2015) uncertainty visualization, Stevens (1957) power law. Full synthesis: `~/Documents/research/2026-03-02-data-visualization-design-research.md`.

### Chart Type Selection (Decision Tree Applied)

| Data Question | Chart Type | Rationale (Cleveland & McGill) |
|--------------|-----------|-------------------------------|
| "How fast is each model?" | Horizontal bar chart (tok/min by model) | Position on common scale = Rank 1 accuracy (±1.3%). Bar chart, not pie. Horizontal because model names are long labels. |
| "How does model size relate to speed?" | Scatter plot + fitted line (size vs tok/min) | Two continuous variables = scatter. Fitted regression line shows the learned curve. Confidence band shows uncertainty. |
| "How does warmup relate to size?" | Scatter plot + fitted line (size vs warmup) | Same rationale. Separate chart (not dual-axis) per Tufte — dual Y-axes distort perception. |
| "When is the system busy?" | Heatmap: hour × day-of-week | Periodic temporal pattern = heatmap calendar (taxonomy §3.1). Color saturation = Rank 6 accuracy — supplement with numeric labels on hover. |
| "What's the load over next 24h?" | Bar chart (48 slots) with overlay markers | Comparison among items over time = bar chart. DLQ/deferred markers as annotations, not a second data series. |
| "How is a model trending?" | Sparkline (inline in table) | Tufte sparkline rules: no axes, no legend, 2.5:1–4:1 aspect ratio, end-point annotation only. Communicates trend direction and volatility, not precise values. |
| "How confident is the estimate?" | Confidence interval band | Hullman et al. Rank 1 for uncertainty: shaded band at `--chart-ci-opacity: 0.15`. Always show, never hide uncertainty. |

### Encoding Rules

**Primary variable → position.** tok/min, warmup, load score all use position on a common scale (bar length, scatter position). Never encode the most important variable in color alone.

**Secondary variable → color saturation.** Heatmap cells, load map slot intensity. Sequential ramp must vary in lightness (oklch L channel), not hue — hue is categorical only (Bertin: hue is selective and associative but NOT ordered).

**Category → color hue.** Model names in scatter plot, failure categories in DLQ. Max 8 distinct hues for preattentive pop-out (Ware). The ollama-queue palette inherits from expedition33-ui theme tokens.

**Uncertainty → always explicit.** Never hide it. Confidence bands on fitted curves. Sample count shown next to every estimate. "Low confidence" label when < 3 observations.

### Specific Chart Specifications

**1. Performance Curve — tok/min vs model size**

What it shows: A scatter plot where each dot is a model you've run, positioned by its file size (horizontal) and how many tokens per minute it generates on your machine (vertical). A fitted curve connects them, and a shaded band shows how confident the system is in the curve. Bigger dots mean more data points — the system has seen that model run many times.

What decision it drives: When a failed or deferred job needs rescheduling, you can see whether the system's estimate for that model is trustworthy. If the dot is large and sits right on the curve, the estimate is solid. If it's small and far from the curve, the system is guessing — you might want to manually review the scheduled slot. Also tells you at a glance which models are fast and which are slow on your hardware, so you know what to expect when submitting jobs.

```
Encoding:
  X-axis: model size (GB) — log scale (data spans >2 orders: 1GB–40GB)
  Y-axis: tok/min — linear scale
  Points: observed model averages (position = Rank 1)
  Point size: proportional to observation count (area, Rank 5 — include numeric label)
  Line: log-linear regression fit
  Band: 90% confidence interval (--chart-ci-opacity: 0.15)
  Color: single series — use --chart-cat-1 (primary blue in expedition33)

Grid: horizontal only, no vertical (Tufte rule 1). Grid opacity ≤30% of data opacity.
Reference line: none (no meaningful threshold for tok/min).
Axes: tick labels at sensible intervals (auto-tick for log scale: 1, 2, 5, 10, 20, 50).
```

**2. Performance Curve — warmup vs model size**

What it shows: Same scatter layout, but the vertical axis is how long it takes to load the model from disk into GPU memory before it can start generating. Small models load in 1-2 seconds; large models can take 30+ seconds. The fitted line shows the pattern on your hardware — which is a function of your disk speed (NVMe vs SATA) and VRAM capacity.

What decision it drives: Tells you whether warmup time is a significant factor for a given model. If warmup is 2 seconds on a 30-second job, it barely matters. If warmup is 25 seconds on a 40-second job, over half the slot is just loading — the scheduler needs to account for that, and you can see that it does. Also helps you understand why certain jobs take longer than expected: it might not be the AI being slow, it's the model loading.

```
Encoding:
  X-axis: model size (GB) — log scale (same as tok/min chart for alignment)
  Y-axis: warmup (seconds) — linear scale
  Points + line + band: same as tok/min chart
  Color: --chart-cat-2 (secondary — orange in expedition33)

These two charts are small multiples (non-aligned common scale = Rank 2, ±2.1%).
Same X-axis, separate Y-axes. Vertically stacked, not side-by-side (Tufte: small
multiples should share the comparison axis for easy scanning).
```

**3. Load Heatmap — hour × day-of-week**

What it shows: A grid of colored cells — one for every hour of every day of the week. Dark cells mean the system was busy during that hour. Light cells mean it was idle. The pattern emerges over time: you'll see that Tuesday afternoons are busy (maybe several recurring jobs overlap), but Sunday 2 AM is always empty.

What decision it drives: This is how the scheduler learns when your "quiet hours" are — not from configuration, but from observation. When a DLQ job needs rescheduling, the system picks a light-colored cell (historically quiet). You can also spot scheduling problems: if a cell is unexpectedly dark, maybe two recurring jobs overlap and should be spread out. The DLQ scheduled markers (small dots on cells) show you where the system plans to retry failed jobs, so you can see whether it's picking sensible times.

```
Encoding:
  X-axis: hour of day (0–23) — categorical
  Y-axis: day of week (Mon–Sun) — categorical
  Cell color: sequential lightness ramp (--chart-seq-*), oklch L from 0.25 (dark/busy) to 0.85 (light/idle)
  Cell size: fixed (--chart-cell-min-size)

Hover: numeric load value + job count. Required because color saturation is Rank 6 (±18%).
DLQ scheduled markers: small dot overlay on cells where DLQ jobs are scheduled.
Grid gap: --chart-cell-gap (Gestalt: proximity groups cells within day, gap separates days).
Colorblind: sequential L-only ramp is safe for all CVD types (no hue discrimination needed).
```

**4. Load Map — 48-slot bar chart (next 24 hours)**

What it shows: A bar for each 30-minute slot over the next 24 hours. Tall bars = busy slots (lots of scheduled work). Short bars = open slots. Red bars are pinned (hard-scheduled, can't be moved). A vertical "now" needle shows where you currently are in the timeline. Icons above bars mark where DLQ and deferred jobs have been scheduled. A translucent overlay shows how much GPU memory is committed in each slot.

What decision it drives: This is the scheduling dashboard — it answers "when is there room to run more jobs?" at a glance. If all bars are tall, the queue is full and DLQ retries will have to wait. If there are clear valleys (low bars with no pinned jobs and plenty of VRAM), the system has good options for rescheduling. You can also manually submit jobs into low-load slots by clicking them. The DLQ/deferred icons let you verify the system's scheduling decisions against your own judgment.

```
Encoding:
  X-axis: time slots (30-min intervals over 24h)
  Y-axis: load score — linear, 0–10
  Bars: priority-weighted load (position on common scale = Rank 1)
  Color: bars colored by intensity (sequential ramp)
  Pinned slots: distinct hue (--chart-cat-4, red) — categorical distinction
  Annotations:
    - DLQ scheduled jobs: icon marker above the bar
    - Deferred jobs: different icon marker
    - "Now" needle: vertical reference line (dashed, --chart-ref-line-style)

VRAM overlay: secondary bar (lighter opacity) showing committed VRAM as fraction of total.
Not a dual axis — normalized to same 0–1 scale as load score ratio.
```

**5. Model Performance Table with sparklines**

What it shows: A table listing every AI model you've run, with columns for how many times it's run, its average speed (tokens per minute), average warmup time, average total duration, and when it last ran. Each row also has a tiny inline chart (sparkline) showing the trend of that model's tok/min over the last 20 runs — is it getting faster, slower, or staying consistent?

What decision it drives: At a glance, you can see which models are well-understood (many runs, tight variance) and which are uncertain (few runs, wide ± range). The sparkline trend matters: a downward trend in tok/min might indicate GPU thermal throttling over time or model contention. An upward trend after a driver update confirms the upgrade helped. This table is also where you verify the estimator's data — if the numbers don't match your expectations, something is wrong with the metrics capture.

```
Per Tufte sparkline rules:
  - No axes, no labels, no legend
  - 24px height (--chart-spark-height), ~80px width (aspect ratio ~3.3:1)
  - End-point dot: current (latest) value (--chart-spark-dot-radius: 2.5px)
  - Color: --chart-spark-color (theme accent)
  - Inline in the "Avg tok/min" column cell
  - Shows last 20 observations — trend direction and volatility at a glance
```

**6. System Health Panel**

What it shows: Real-time gauges for CPU load, available RAM, GPU utilization percentage, GPU temperature, GPU VRAM usage (used/total), and disk I/O busy percentage. Each metric has a status badge: green (healthy), yellow (warm), red (throttling/critical). Values fade in opacity as they age — if the last update was 30+ seconds ago, the numbers desaturate to signal staleness.

What decision it drives: Tells you whether the system is healthy enough to run more jobs right now. If GPU temp is 87°C with a red "Throttling" badge, you know why jobs are being deferred — and you know not to submit more until it cools. If VRAM is 11/12 GB used, you understand why a 14B model can't start. This panel is the "why" behind every deferral and scheduling decision. It also serves as a quick health check when accessing the dashboard remotely from your phone.

**7. Decision Reasoning Panel (expandable on DLQ/Deferred entries)**

What it shows: A tree of scored factors explaining exactly why the system chose a specific time slot for a rescheduled or deferred job. Each factor shows its name, its value, and its contribution to the overall score. For skipped entries, it shows why no slot was chosen and when the next sweep will try again.

What decision it drives: Eliminates "why did it do that?" — the most common frustration with autonomous systems. If the system scheduled a job for 2:30 AM and you think midnight would be better, you can see the reasoning: maybe midnight had a recurring job conflict and VRAM contention. If the reasoning is wrong (maybe that recurring job was deleted), you know the system is working with stale data and can trigger a manual resweep. Transparency builds trust in the autonomous scheduling.

### Data Freshness (Weiser)

All real-time data follows the freshness encoding from the research:

| Age | Visual Treatment |
|-----|-----------------|
| < 1 min | Full opacity, full saturation |
| 1–5 min | 90% opacity |
| 5–30 min | 70% opacity, slight desaturation |
| > 30 min | 50% opacity, desaturated + "Last updated" timestamp |

Applied to: system health panel values, model stats, load map, performance curve data points.

### Uncertainty Visualization (Hullman et al.)

Every estimate shown in the UI includes uncertainty:

| Element | Uncertainty Display | Method (Hullman rank) |
|---------|-------------------|----------------------|
| Performance curve | Shaded confidence band | Rank 1 — CI band |
| Runtime estimate tooltip | `~45s ± 12s (8 observations)` | Numeric ± with sample size |
| Slot score | Score + list of factors | Full decision transparency |
| Low-confidence estimate | `Low confidence` label + wider band | Visual + textual cue |

Never present a point estimate without its uncertainty. Sample size (observation count) appears alongside every estimate.

### Responsive Behavior

| Breakpoint | Adaptation |
|-----------|-----------|
| Desktop (≥1024px) | Full performance tab: 2-column (curves left, table right), heatmap below |
| Tablet (768–1023px) | Single column, curves stacked, heatmap full-width |
| Mobile (<768px) | Table only (with sparklines). Curves accessible via "Show charts" toggle. Heatmap hidden (too small for 24×7 cells). |

Charts use `--chart-sm-min-width: 120px` and `--chart-sm-min-height: 60px` minimum dimensions. Below minimum, chart collapses to a numeric summary.

### Animation & Streaming

```css
/* New data points animate in (300ms ease-out) */
--chart-stream-duration: 300ms;
--chart-stream-easing: var(--ease-out);

@media (prefers-reduced-motion: reduce) {
  --chart-stream-duration: 0ms;
}
```

Warmup → generating phase transition on running jobs uses a subtle color shift (not animation), respecting reduced-motion preferences.

---

## Section 8: Dashboard UI Implementation

### DLQ Tab Enhancements

**Auto-Reschedule Status Column:**

| State | Display |
|-------|---------|
| Awaiting sweep | `Queued for auto-reschedule` |
| Scheduled | `Rescheduled → 02:30 AM (slot load: 1.2)` |
| Running | `Running (attempt #3) — Warming up` or `— Generating` |
| Chronic | Warning badge after N attempts — display only |

**Estimate Tooltip** on each DLQ entry:
```
Est. warmup:     ~3.0s  (from 12 observations)
Est. generation: ~45s   (48 tok/min, 8 observations)
Est. total:      ~48s
Confidence:      High
Next slot:       02:30 AM (load: 1.2/5.0)
```

**Decision Reasoning** (expandable per entry):
```
Rescheduled → 02:30 AM (score: 7.2)
  Load headroom: 8.0/10
  VRAM margin: 4.2GB
  Model hot — no warmup needed
  Historical load at 02:00 = 0.12 (quiet)
  No recurring conflicts
  Timeout failure — open-ended slot selected
```

Skipped entries show why:
```
Deferred — no qualifying slot
  Best candidate: 14:00 (score: 1.3)
  3 pending jobs ahead
  GPU temp: 82C
  Will retry at next sweep
```

### Warmup Indicator (all running jobs)

| Phase | Display |
|-------|---------|
| Before first token | `Warming up` badge |
| After first token | `Generating` badge |
| Completed | `Warmup: 5.2s | Generation: 38s` breakdown |

Detection: parse stdout during execution. First output chunk = transition from warmup to generating.

### Deferred Panel (queue view)

New section between Active and History:

```
-- Active (2) -----------------------------------------------
  qwen3.5:9b     Generating       12s / ~30s est.
  deepseek:8b    Warming up       2s / ~4s est.
-- Deferred (3) ---------------------------------------------
  llama3:70b     Resource — needs 40GB, 12GB free    → 02:30 AM
  qwen:14b       Thermal — GPU 87C                   → cooling
  codestral      Burst — priority 2 deferred          → 11:45 PM
-- Pending (1) ----------------------------------------------
  qwen3.5:9b     priority 8    queued 3s ago
```

### Performance Tab

**Model Performance Table:**

| Model | Runs | Avg tok/min | Avg warmup | Avg duration | Last run |
|-------|------|------------|-----------|-------------|----------|
| qwen3.5:9b | 47 | 82 +/- 6 | 1.8s | 34s | 2h ago |
| qwen2.5-coder:14b | 23 | 48 +/- 4 | 2.9s | 52s | 5h ago |

**Performance Curves (2 charts):**
- Scatter + log-linear fit: model size (GB) vs tok/min
- Scatter + linear fit: model size (GB) vs warmup (seconds)
- Shaded confidence interval band
- These are the estimator's internals made visible — builds trust in scheduling decisions

**Load Heatmap:**
- Hour-of-day x day-of-week → average load
- Learned over time — shows when the system is naturally quiet
- DLQ scheduled markers overlaid on heatmap

**System Health Panel:**
- Real-time: CPU, RAM, GPU util/temp/VRAM, disk I/O
- Status badges: GPU Cool / GPU Warm / GPU Throttling

### Load Map Enhancement

Existing 24h/48-slot load map gets:
- VRAM utilization overlay per slot
- DLQ scheduled job markers with tooltip (which job, estimated duration)
- Deferred job markers (when scheduled for a future slot)

### Settings Panel Additions

"DLQ Auto-Reschedule" section:

| Control | Type | Default |
|---------|------|---------|
| Enable auto-reschedule | Toggle | On |
| Max slot load threshold | Number | 5.0 |
| Sweep fallback interval | Minutes | 30 |
| Chronic failure warning | Number | 5 |
| Resource failure extra margin | % | 30 |

"Deferral" section:

| Control | Type | Default |
|---------|------|---------|
| Enable auto-deferral | Toggle | On |
| Burst defer priority threshold | Number | 3 |
| Thermal defer threshold | C | 85 |
| Resource defer timeout | Seconds | 120 |

---

## Project Structure (new/modified files)

```
ollama_queue/
├── intelligence.py          # NEW — RuntimeEstimator, SystemSnapshot, LoadPatterns, PerformanceCurve
├── dlq_scheduler.py         # NEW — DLQ sweep logic, scoring, reschedule
├── deferral_scheduler.py    # NEW — Deferral sweep logic, resume
├── daemon.py                # MODIFIED — metrics capture, completion hook, periodic sweep, deferral triggers
├── db.py                    # MODIFIED — job_metrics table, dlq columns, deferrals table, new queries
├── dlq.py                   # MODIFIED — failure classification
├── scheduler.py             # MODIFIED — VRAM-aware load_map
├── api.py                   # MODIFIED — new endpoints
├── health.py                # MODIFIED — snapshot data
└── dashboard/spa/src/
    ├── components/
    │   ├── DeferredPanel.jsx    # NEW
    │   ├── PerformanceTab.jsx   # NEW
    │   ├── PerformanceCurve.jsx # NEW
    │   ├── LoadHeatmap.jsx      # NEW
    │   ├── WarmupBadge.jsx      # NEW
    │   ├── DLQRow.jsx           # MODIFIED — reschedule status, reasoning
    │   └── SystemHealth.jsx     # NEW
    ├── pages/
    │   └── Dashboard.jsx        # MODIFIED — deferred panel, warmup badges
    └── hooks/
        └── usePerformance.js    # NEW — model stats, curves
tests/
├── test_intelligence.py     # NEW
├── test_dlq_scheduler.py    # NEW
├── test_deferral.py         # NEW
└── test_job_metrics.py      # NEW
```

---

## Success Criteria

1. DLQ entries auto-reschedule to available slots without manual intervention
2. Rescheduled jobs succeed at a higher rate than their original run (right-sized slot)
3. Deferred jobs resume automatically when conditions clear (thermal, burst, resources)
4. Runtime estimates within 30% of actual after 5 model observations
5. Cross-model estimation produces usable estimates for never-run models (after 3+ other models observed)
6. Warmup phase visible in UI for all running jobs
7. Performance curves visible and updating in dashboard
8. Decision reasoning visible for every auto-reschedule and deferral
9. No impact on normal job throughput — DLQ/deferral sweeps complete in <100ms
10. All existing tests continue to pass; new features covered by dedicated test files
