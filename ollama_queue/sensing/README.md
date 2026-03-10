# sensing/ — System Monitoring + Anomaly Detection

## Purpose

Reads system metrics (RAM, VRAM, CPU, swap, GPU temperature) and detects
anomalies (stalled jobs, submission bursts, resource exhaustion). Provides the
daemon with pause/resume/yield decisions based on configurable thresholds.

## Architecture

Four components with distinct responsibilities:

```
health.py          -- Stateful: reads hardware metrics, evaluates pause/resume/yield
stall.py           -- Stateful: Bayesian multi-signal stall detection per job
burst.py           -- Stateful: EWMA-based submission rate regime detection
system_snapshot.py -- Stateless: point-in-time capture + failure classification
```

`HealthMonitor` is the primary class, instantiated by the Daemon. `StallDetector`
and `BurstDetector` are stateful singletons. `SystemSnapshot` and
`classify_failure` are utility functions used by the scheduling domain.

## Modules

| File | Key Exports | Role |
|------|-------------|------|
| `__init__.py` | `HealthMonitor` | Re-export |
| `health.py` | `HealthMonitor` | `check()` returns a snapshot dict (ram_pct, vram_pct, load_avg, swap_pct, ollama_model). `evaluate()` returns `{should_pause, should_yield, reason}` using hysteresis (separate pause/resume thresholds to prevent flapping). VRAM reading cached with 5s TTL. |
| `stall.py` | `StallDetector` | Bayesian P(stuck) from 4 independent signals: process state (D/Z), CPU activity delta, stdout silence duration, Ollama `/api/ps` model presence. `compute_posterior()` returns probability via naive Bayes over log-odds. Thread-safe: `_stdout_lock` protects writes from worker threads. |
| `burst.py` | `BurstDetector` | EWMA of inter-arrival times vs. 75th-percentile baseline. 4 regimes: subcritical, moderate, warning, critical. `record_submission()` called from API submit; `regime()` called from daemon poll. Thread-safe: `threading.Lock` protects deque iteration. Module-level `_default_detector` singleton shared between API and daemon. |
| `system_snapshot.py` | `SystemSnapshot`, `classify_failure` | `SystemSnapshot.capture()`: frozen dataclass of RAM, VRAM, GPU temp, load, swap, loaded models, queue depth. `classify_failure(reason)`: regex-based categorization into `resource`, `timeout`, `transient`, or `permanent`. |

## Key Patterns

- **Hysteresis**: `HealthMonitor.evaluate()` uses separate pause and resume
  thresholds (e.g., pause at 85% RAM, resume at 75%). The `currently_paused`
  parameter determines which threshold to compare against. This prevents rapid
  pause/resume flapping near a single threshold.

- **Interactive yield**: When `yield_to_interactive` is enabled and Ollama shows a
  loaded model that doesn't match recently-completed queue jobs, the daemon yields
  to the interactive user. `recent_job_models` (10-minute window) prevents the
  daemon from self-blocking on models it loaded.

- **Bayesian stall detection**: LLM jobs can't use simple timeouts (some prompts
  take 30+ minutes). The stall detector combines 4 weak signals into a posterior
  probability. The daemon flags stalls above `stall_posterior_threshold` (0.8) and
  optionally sends SIGTERM after a grace period.

- **Thread safety boundaries**: `StallDetector._last_stdout` is written by worker
  threads and read by the poll thread (uses `_stdout_lock`). `BurstDetector`
  deque is mutated by API threads and read by daemon (uses `threading.Lock`).
  `HealthMonitor._vram_cache` is read-only from the poll thread (no lock needed).

## Dependencies

**Depends on**: Nothing (reads /proc, nvidia-smi, Ollama /api/ps directly)
**Depended on by**: `daemon/` (health checks, stall detection, burst regime), `scheduling/` (failure classification, system snapshot)
