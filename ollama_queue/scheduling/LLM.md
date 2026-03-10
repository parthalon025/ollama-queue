# scheduling/ LLM Guide

## What You Must Know

Four components handle time-based job orchestration: the core `Scheduler` (recurring job promotion, load maps, rebalancing), `slot_scoring` (shared scoring logic), `DLQScheduler` (auto-reschedule failed jobs), and `DeferralScheduler` (resume deferred jobs).

## Scheduler (scheduler.py)

The primary class, used by the daemon's poll loop and the schedule API.

- `promote_due_jobs()` -- fires recurring jobs whose `next_run <= now`, coalesces duplicates
- `rebalance()` -- spreads interval jobs evenly, respects pinned cron jobs
- `load_map()` / `load_map_extended()` -- 48 half-hour slots showing scheduled load
- `suggest_time()` -- returns top-N lowest-load cron expressions for new jobs

**Pin enforcement**: Recurring jobs with `pinned=True` are protected during rebalance. Their `next_run` is computed from their cron expression, never shifted.

**Cache**: `_jobs_cache` with 10s TTL avoids redundant DB reads. Call `_invalidate_jobs_cache()` after any recurring job mutation.

## Slot Scoring (slot_scoring.py)

`score_slot()` returns a composite score from 10 factors. Returns -1 for infeasible slots.

Hard gates (automatic -1): VRAM overflow, pinned-slot collision.
Soft factors: hot-model bonus, recurring-conflict penalty, historical quiet bonus, queue depth, failure-category headroom.

`find_fitting_slot()` returns the highest-scoring feasible slot, or `None`.

## Deferral Sweep (deferral.py)

`DeferralScheduler.sweep()` is a **two-phase sweep** -- this is critical:

1. **Phase 1**: Fetch ALL deferred entries, resume any whose `scheduled_for` has passed
2. **Phase 2**: Fetch unscheduled-only entries, find fitting slots via `find_fitting_slot()`

The original single-call design filtered out scheduled entries, making phase-1 resumptions impossible. Tests must assert `list_deferred()` is called twice: once with no args, once with `unscheduled_only=True`.

**Non-blocking**: Uses `threading.Lock.acquire(blocking=False)` to return immediately if another sweep is in progress.

## DLQ Scheduler (dlq_scheduler.py)

`DLQScheduler` auto-reschedules failed jobs from the DLQ:

- **Failure classification**: `system_snapshot.classify_failure(reason)` tags entries as `resource`, `timeout`, `transient`, or `permanent`. Permanent failures are never auto-rescheduled.
- **Chronic skip**: Entries with `auto_reschedule_count >= chronic_failure_threshold` are skipped.
- **Two-step crash safety**: `mark_dlq_scheduling()` (marks, does NOT increment count) -> submit job -> `update_dlq_reschedule()` (increments count, sets resolution). Prevents double-counting on crash.
- **Event-driven + periodic**: Triggered by `on_job_completed()` and as fallback by `periodic_sweep()`.

## DLQ Priority Sort

**Ascending**: lower number = higher importance (1=critical, 10=background).

```python
# CORRECT
sorted(entries, key=lambda e: e.get("priority", 0))

# WRONG -- processes background before critical
sorted(entries, key=lambda e: e.get("priority", 0), reverse=True)
```

## Adding a New Scheduling Strategy

1. Create a new file in `scheduling/` with a class following the `DLQScheduler`/`DeferralScheduler` pattern
2. Accept `db`, `runtime_estimator`, `load_map_fn` in constructor
3. Use `find_fitting_slot()` from `slot_scoring.py` for slot selection
4. Use non-blocking lock pattern: `if not self._lock.acquire(blocking=False): return`
5. Wire into `Daemon.__init__` and call from `poll_once()`

## Testing

```bash
pytest tests/test_scheduler.py -x                   # core scheduler
pytest tests/test_slot_scoring.py -x                 # scoring logic
pytest tests/test_deferral_scheduler.py -x           # deferral sweep
pytest tests/test_dlq_scheduler.py -x                # DLQ auto-reschedule
pytest tests/test_integration_dlq_reschedule.py -x   # end-to-end DLQ flow
pytest tests/test_deferral.py -x                     # deferral DB operations
```

## Dependencies

- **Depends on**: db/, models/ (VRAM estimation for slot scoring), sensing/ (failure classification)
- **Depended on by**: daemon/ (promote + sweep), api/schedule.py (rebalance, load-map, suggest)
