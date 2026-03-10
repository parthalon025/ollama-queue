# sensing/ LLM Guide

## What You Must Know

Four components monitor system state and detect anomalies. `HealthMonitor` reads hardware metrics and makes pause/resume/yield decisions. `StallDetector` uses Bayesian inference to detect frozen jobs. `BurstDetector` tracks submission rate regimes. `SystemSnapshot` provides point-in-time captures for slot scoring.

## Health Hysteresis

`HealthMonitor.evaluate()` uses separate pause and resume thresholds to prevent flapping:

```
Pause at 85% RAM -> resume only below 75% RAM
Pause at 90% VRAM -> resume only below 80% VRAM
```

The `currently_paused` parameter determines which threshold to compare. Without hysteresis, a system hovering at 84% would rapidly toggle pause/resume.

**VRAM cache**: `_vram_cache` has a 5s TTL. Read-only from the poll thread (no lock needed). `__init__` must be called to initialize it -- if subclassing in tests, call `super().__init__()`.

**Interactive yield**: When `yield_to_interactive=True` and Ollama shows a loaded model not in `recent_job_models` (10-minute window), the daemon yields to the interactive user.

## StallDetector Threading

`StallDetector` combines 4 weak signals into P(stuck) via naive Bayes:

1. Process state (D=disk-wait, Z=zombie)
2. CPU activity delta
3. Stdout silence duration
4. Ollama `/api/ps` model presence

**Thread safety boundary**: `_last_stdout` is written by worker threads (via `_drain_pipes_with_tracking`) and read by the poll thread. Protected by `_stdout_lock` (plain `Lock`).

```python
def update_stdout_activity(self, job_id, now):
    with self._stdout_lock:
        self._last_stdout[job_id] = now

def get_stdout_silence(self, job_id, now):
    with self._stdout_lock:
        last = self._last_stdout.get(job_id, 0)
    return now - last
```

`_cpu_prev` is single-threaded (poll only) and does not need a lock.

`forget(job_id)` must also acquire `_stdout_lock` before deleting from `_last_stdout`.

## BurstDetector Threading

`BurstDetector` uses EWMA of inter-arrival times vs. 75th-percentile baseline. 4 regimes: subcritical, moderate, warning, critical.

**Thread safety**: `record_submission()` is called from FastAPI threads (API submit); `regime()` is called from the daemon poll thread. The deque is shared -- `sorted(self._baseline_samples)` in `regime()` races with `append()` in `record_submission()`.

```python
# Both methods must acquire self._lock (threading.Lock)
def record_submission(self, ts):
    with self._lock:
        ...

def regime(self, now):
    with self._lock:
        ...
```

**Singleton**: Module-level `_default_detector` is shared between API and daemon. Tests must reset it (autouse fixture in conftest.py clears `_ewma`, `_baseline_samples`, `_last_ts`).

## SystemSnapshot

`SystemSnapshot.capture()` returns a frozen dataclass of system state (RAM, VRAM, GPU temp, load, swap, loaded models, queue depth). Used by `slot_scoring.py` for VRAM-aware scheduling.

`classify_failure(reason)` is a regex-based categorizer: `resource`, `timeout`, `transient`, or `permanent`. Used by `DLQScheduler` to decide whether to auto-reschedule.

## Adding a New Health Signal

1. Add the reading method to `HealthMonitor` (e.g., `get_gpu_temp()`)
2. Add pause/resume threshold settings to `DEFAULTS` in `db/schema.py`
3. Add the check to `evaluate()` with hysteresis (separate pause and resume thresholds)
4. Add to `check()` return dict so the API/dashboard can display it
5. Add to `SystemSnapshot` if needed for slot scoring

## Testing

```bash
pytest tests/test_health.py -x           # health monitor
pytest tests/test_stall.py -x            # stall detector
pytest tests/test_burst.py -x            # burst detector
pytest tests/test_system_snapshot.py -x  # snapshot + failure classification
```

Health tests mock `/proc/meminfo`, `nvidia-smi`, and Ollama `/api/ps`. Stall tests mock `/proc/<pid>/stat` and `os.kill()`. Burst tests use controlled timestamps.

## Dependencies

- **Depends on**: Nothing (reads /proc, nvidia-smi, Ollama /api/ps directly)
- **Depended on by**: daemon/ (health checks, stall detection, burst regime), scheduling/ (failure classification, system snapshot)
