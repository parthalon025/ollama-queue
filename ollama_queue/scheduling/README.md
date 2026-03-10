# scheduling/ — Time-Based Job Orchestration

## Purpose

Manages when recurring jobs fire, spreads them across time slots to avoid pileups,
and automatically reschedules failed (DLQ) and deferred jobs into optimal windows.

## Architecture

Four components handle different scheduling concerns:

```
scheduler.py      -- Core: promote due jobs, rebalance, load map, suggest slots
slot_scoring.py   -- Shared: 10-factor slot scoring with VRAM hard gates
dlq_scheduler.py  -- Sweep: auto-reschedule DLQ entries into fitting slots
deferral.py       -- Sweep: resume deferred jobs when conditions improve
```

`Scheduler` is the primary class, used by the daemon's poll loop and the API.
`DLQScheduler` and `DeferralScheduler` are sweep-based schedulers triggered both
event-driven (after job completion) and periodically (daemon poll fallback).

## Modules

| File | Key Exports | Role |
|------|-------------|------|
| `__init__.py` | `Scheduler` | Re-export |
| `scheduler.py` | `Scheduler` | Recurring job promotion (`promote_due_jobs`), `rebalance()` (spreads interval jobs + respects pins), `load_map()` / `load_map_extended()` (48 half-hour slots), `suggest_time()` (top-N lowest-load cron expressions) |
| `slot_scoring.py` | `score_slot`, `find_fitting_slot` | 10-factor scoring: VRAM hard gate, pinned-slot exclusion, hot-model bonus, recurring-conflict penalty, historical quiet bonus, queue depth factor, failure-category headroom |
| `deferral.py` | `DeferralScheduler` | Two-phase sweep: (1) resume entries whose `scheduled_for` has passed, (2) find fitting slots for unscheduled entries |
| `dlq_scheduler.py` | `DLQScheduler` | Failure classification + slot fitting + chronic-failure skip; event-driven (`on_job_completed`) and periodic (`periodic_sweep`) triggers |

## Key Patterns

- **Pin enforcement**: Recurring jobs with `pinned=True` are protected during
  `rebalance()`. Their `next_run` is computed from their cron expression, never
  shifted. Non-pinned interval jobs are spread evenly across their interval.

- **Two-phase deferral sweep**: Phase 1 fetches ALL deferred entries and resumes
  those whose `scheduled_for` has passed. Phase 2 fetches unscheduled-only entries
  and finds fitting slots. The original single-call design filtered out scheduled
  entries, making scheduled resumptions impossible.

- **Non-blocking sweeps**: Both `DLQScheduler._sweep()` and `DeferralScheduler.sweep()`
  use `threading.Lock.acquire(blocking=False)` to return immediately if another sweep
  is in progress.

- **Failure classification** (`system_snapshot.classify_failure`): Tags DLQ entries
  as `resource`, `timeout`, `transient`, or `permanent` based on failure reason
  patterns. Permanent failures are never auto-rescheduled. Chronic failures
  (reschedule count >= threshold) are also skipped.

- **VRAM-aware slot scoring**: `score_slot()` returns -1 for infeasible slots
  (VRAM overflow or pinned). Feasible slots get a composite score from 10 factors
  including hot-model bonus, recurring-conflict penalty, and queue depth.

## Dependencies

**Depends on**: `db/`, `models/` (VRAM estimation), `sensing/` (failure classification)
**Depended on by**: `daemon/` (promote + sweep), `api/schedule.py` (rebalance, load-map, suggest)
