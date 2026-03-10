# daemon/ — Polling Loop + Job Executor

## Purpose

The daemon is the heartbeat of ollama-queue. Every 5 seconds it wakes up, checks
system health, evaluates whether a job can run, and either dispatches it to a worker
thread or goes back to sleep with a logged reason.

## Architecture

Uses the **mixin pattern** to split a large class across files while presenting a
single `Daemon` API. Python's MRO resolves methods from both mixins into one class:

```python
class Daemon(LoopMixin, ExecutorMixin):
    def __init__(self, db, health_monitor=None):
        # Holds all shared state: db, health, scheduler, estimator,
        # stall_detector, burst_detector, circuit breaker, running jobs
```

The `__init__.py` assembles the class and imports all collaborators (Scheduler,
DLQManager, HealthMonitor, StallDetector, BurstDetector, estimators).

## Modules

| File | Class/Function | Role |
|------|----------------|------|
| `__init__.py` | `Daemon` | Assembled class; `__init__` initializes all state |
| `loop.py` | `LoopMixin` | `poll_once()`, `run()`, `shutdown()`, orphan recovery, circuit breaker, entropy detection, dequeue (SJF + aging) |
| `executor.py` | `ExecutorMixin` | `_run_job()`, `_can_admit()` (3-factor admission gate), stall checks, preemption, retries, resource helpers |
| `executor.py` | `_drain_pipes_with_tracking()` | Standalone function: select()-based stdout/stderr drain with 128KB sliding window |

## Key Patterns

- **Single-threaded poll, multi-threaded execution**: `poll_once()` runs on the main
  thread. Jobs execute in a `ThreadPoolExecutor`. `_can_admit()` is only called from
  `poll_once()` so the `_running_lock` snapshot is consistent within a single poll.

- **3-factor admission gate** (`_can_admit`):
  1. **Concurrency type**: embed jobs get 4 slots; heavy models (70B+) run alone;
     standard models serialize per-model.
  2. **Resource budget**: committed VRAM (from model estimates, not live GPU reads)
     must fit within `max_vram_mb * 0.8`.
  3. **Health evaluation**: RAM, VRAM, load, swap thresholds with hysteresis.

- **Circuit breaker**: 3 consecutive Ollama failures open the circuit. Exponential
  cooldown (30s base, 10min cap). HALF_OPEN allows one probe job through. Lock
  ordering: `_cb_lock` is never held while calling `db` methods.

- **Proxy sentinel guard**: The proxy sets `current_job_id = -1` as a sentinel.
  Every "set idle" transition in `poll_once()` checks for this sentinel and skips
  clearing `current_job_id` if a proxy request is in flight.

- **Cooperative job lifecycle**: `_run_job()` logs the real PID, drains pipes via
  `select()`, records metrics, updates duration history, triggers DLQ sweep, and
  handles preemption requeue. All DB mutations are atomic under `db._lock`.

## Dependencies

**Depends on**: `db/`, `models/`, `sensing/`, `scheduling/`, `dlq.py`, `metrics_parser.py`
**Depended on by**: `cli.py` (starts the daemon in `serve`)
