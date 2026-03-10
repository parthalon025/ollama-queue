# daemon/ LLM Guide

## What You Must Know

The daemon is a single-threaded polling loop (`poll_once()` every 5s) that dispatches jobs to a `ThreadPoolExecutor`. It is split across two files using the mixin pattern -- `LoopMixin` (poll, schedule, circuit breaker) and `ExecutorMixin` (run jobs, admission, stall checks). Both compose into one `Daemon` class in `__init__.py`.

## Mixin Pattern

```python
class Daemon(LoopMixin, ExecutorMixin):
    def __init__(self, db, health_monitor=None):
        # ALL shared state initialized here: db, health, scheduler,
        # estimator, stall_detector, burst_detector, circuit breaker,
        # _running dict, _running_lock, etc.
```

When adding methods, put scheduling/loop logic in `loop.py`, execution/admission logic in `executor.py`. Both access `self.db`, `self.health`, `self._running`, etc.

## Lock Ordering

The daemon has multiple locks. Never nest them in the wrong order:

| Lock | Protects | Thread |
|------|----------|--------|
| `db._lock` (RLock) | All SQLite writes | Any |
| `_running_lock` | `_running`, `_running_models` dicts | Poll + worker |
| `_cb_lock` | Circuit breaker state (`_cb_*`) | Poll only |
| `_recent_models_lock` | `_recent_job_models` dict | Poll + worker |

Rule: Never hold `_cb_lock` while calling `db` methods. `_running_lock` is only held for dict snapshots, never across DB calls.

## Proxy Sentinel Guard

The proxy sets `current_job_id = -1` as a sentinel. Every "set idle" transition in `poll_once()` must check:

```python
# WRONG -- clobbers proxy sentinel
db.update_daemon_state(state='idle', current_job_id=None)

# RIGHT
state = db.get_daemon_state()
if state.get("current_job_id") != -1:
    db.update_daemon_state(state='idle', current_job_id=None)
```

Without this guard, the daemon clears the sentinel every 5s poll, allowing concurrent proxy requests.

## Circuit Breaker

3 consecutive Ollama failures open the circuit. States: CLOSED -> OPEN -> HALF_OPEN (one probe) -> CLOSED. Exponential cooldown (30s base, 10min cap). Shell scripts that exit non-zero count as failures -- scripts must `exit 0` for "nothing to do".

## Admission Gate (`_can_admit`)

Three factors checked from `poll_once()` (single-threaded, consistent snapshot):

1. **Concurrency**: embed=4 slots, heavy (70B+)=alone, standard=serialize per-model
2. **VRAM budget**: committed VRAM (from model estimates) within `max_vram_mb * 0.8`
3. **Health**: RAM/VRAM/load/swap thresholds with hysteresis

## stdout Capture

`_drain_pipes_with_tracking()` uses `select()` with non-blocking fds. 128KB sliding window (`_MAX_STDOUT_BYTES`) -- pops oldest chunks when over budget. Without this, chatty jobs OOM the 512M service.

## Orphan Recovery

`_recover_orphans()` runs on startup. Marks running/generating/judging/pending jobs as failed. Must skip proxy sentinels (`command LIKE 'proxy:%'`) -- marking them pending would cause shell execution (exit 127).

## Patching Targets for Tests

```python
# Subprocess (job execution)
@patch("ollama_queue.daemon.executor.subprocess")

# Time
@patch("ollama_queue.daemon.loop.time")

# Health monitor
@patch.object(HealthMonitor, "evaluate")
@patch.object(HealthMonitor, "check")

# Stall detector
@patch.object(StallDetector, "compute_posterior")
```

Note: `executor.py` imports `subprocess` twice -- `import subprocess` and `import subprocess as _subprocess`. Mocks targeting `subprocess` do NOT replace `_subprocess`. The `_subprocess` alias is used for real calls that must not be mocked (e.g., `_subprocess.PIPE`).

## Testing

```bash
pytest tests/test_daemon.py -x
pytest tests/test_daemon.py -k "test_poll" -x  # subset
```

## Dependencies

- **Depends on**: db/, models/, sensing/, scheduling/, dlq.py, metrics_parser.py
- **Depended on by**: cli.py (starts daemon in `serve` command)
