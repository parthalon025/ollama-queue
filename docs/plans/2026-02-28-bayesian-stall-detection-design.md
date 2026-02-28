# Bayesian Multi-Signal Stall Detection for LLM Jobs

**Date:** 2026-02-28
**Status:** Approved
**Replaces:** time-based `stall_multiplier` stall detection

---

## Problem

LLM inference jobs are killed by a 600s hard timeout (`proc.communicate(timeout=job["timeout"])`) even when they are making legitimate progress on a long prompt. The timeout is not calibrated to LLM workloads — a large context job or a slow model may legitimately run for 20–60 minutes. Killing it wastes GPU time and misleads the DLQ.

The existing `stall_multiplier`-based stall detection only flags jobs; it cannot distinguish "slow but healthy" from "genuinely stuck" because it uses only one signal (elapsed time vs. estimated duration).

---

## Goal

Remove the hard timeout from LLM jobs (`resource_profile='ollama'`). Replace it with a Bayesian multi-signal stall detector that combines four independent evidence groups to compute a posterior stall probability. When the posterior crosses a configurable threshold, the daemon flags the job and optionally kills it.

Non-LLM jobs (`resource_profile='any'`) keep the hard timeout unchanged.

---

## Signal Design

### Evidence Groups (Independent)

Four groups chosen for independence — correlated signals (e.g., D-state and stdout silence) are in the same group, preventing double-counting.

| Group | Signal | P(s=1 \| stuck) | P(s=1 \| healthy) | Log LR |
|-------|--------|----------------|-------------------|--------|
| **Process state** | D-state (uninterruptible sleep) | 0.70 | 0.02 | +3.56 |
| | Z-state (zombie) | 0.95 | 0.001 | +6.86 |
| | R-state (running) | 0.05 | 0.60 | −2.48 |
| | S-state (normal sleep) | — | — | 0 (neutral) |
| **CPU activity** | CPU% < 1% for ≥60s | 0.80 | 0.10 | +2.08 |
| | CPU% ≥ 5% | 0.05 | 0.70 | −2.64 |
| **Stdout silence** | Silent 120–300s | 0.60 | 0.10 | +1.79 |
| | Silent > 300s | 0.90 | 0.02 | +3.81 |
| | Active < 30s | 0.05 | 0.50 | −2.30 |
| **Ollama model presence** | Model not in `/api/ps` | 0.50 | 0.10 | +1.61 |
| | Model loaded | 0.20 | 0.90 | −1.50 |

Within each group, take the signal with the highest absolute log LR. Groups are then combined additively (naïve Bayes over independent groups).

### Prior

`P(stuck) = 0.05` → prior log-odds = −2.94

Posterior = sigmoid(prior_log_odds + Σ group_log_lr)

**Example combinations:**

| Scenario | Posterior |
|----------|-----------|
| All healthy signals | ~0.01 |
| Only D-state | ~0.65 |
| D-state + CPU < 1% | ~0.92 |
| D-state + model not in ps + CPU < 1% | ~0.98 |
| Stdout silent 300s + CPU < 1% | ~0.88 |

---

## Architecture

### New Module: `ollama_queue/stall.py`

```
StallDetector
├── _last_stdout: dict[job_id → timestamp]   # updated by pipe reader
├── _cpu_prev: dict[job_id → (timestamp, ticks)]  # for delta CPU%
│
├── update_stdout_activity(job_id, now) → None
├── get_stdout_silence(job_id, now) → float | None   # None = no output ever
├── get_process_state(pid) → str             # 'R','S','D','Z','T','?'
├── get_cpu_pct(pid, job_id, now) → float | None    # None = first sample
├── get_ollama_ps_models() → set[str]        # ONE HTTP call, cached per cycle
├── compute_posterior(job_id, pid, model, now, ps_models) → (float, dict)
└── forget(job_id) → None                   # cleanup on job complete
```

`compute_posterior` returns `(posterior_float, signals_dict)` where `signals_dict` contains the log-LR contribution of each group — stored in the job record for debugging.

### Changes to `_run_job()` — LLM Jobs Only

Replace `proc.communicate(timeout=job["timeout"])` with a `select.select()`-based polling loop inside the worker thread. No additional threads.

```
while process is running or pipes have data:
    ready = select.select([stdout_fd, stderr_fd], [], [], 1.0)
    for each ready fd:
        read chunk → append to buffer
        if stdout chunk: stall_detector.update_stdout_activity(job_id, now)
    if process exited and select timeout: drain remaining bytes and break
```

Fds set to non-blocking via `fcntl.F_SETFL | O_NONBLOCK` before the loop. After the loop, `out = b"".join(stdout_chunks)`, `err = b"".join(stderr_chunks)` — same tail recording as before.

Non-LLM jobs (`resource_profile != 'ollama'`) keep `proc.communicate(timeout=job["timeout"])`.

### Changes to `_check_stalled_jobs()`

```
1. Call get_ollama_ps_models() once → ps_models
2. For each running LLM job:
   a. compute_posterior(job_id, pid, model, now, ps_models) → (p, signals)
   b. if p >= stall_posterior_threshold and stall_detected_at is None:
        set_stall_detected(job_id, now, signals)
        log warning with signal breakdown
   c. if p >= threshold AND stall_action == "kill":
        stall_age = now - stall_detected_at
        if stall_age >= stall_kill_grace_seconds:
            os.kill(pid, SIGTERM)   # worker thread's proc.wait() returns
            log warning "killing stalled job #N"
```

`os.kill()` is wrapped in `contextlib.suppress(ProcessLookupError, PermissionError)` — the process may have exited between detection and kill.

### Kill Propagation

When `os.kill(pid, SIGTERM)` fires from the main loop:
- Worker thread's select loop sees the process exit (`proc.poll() → returncode`)
- Loop drains remaining pipe bytes and exits
- `exit_code = proc.returncode` (e.g., −15 for SIGTERM)
- Normal failure path: `complete_job(exit_code=−15)` → `dlq.handle_failure()`
- No special-case code needed — existing DLQ routing handles it

### Schema Change

One new column on `jobs`:
```sql
ALTER TABLE jobs ADD COLUMN stall_signals TEXT;
-- JSON: {"process": 3.56, "cpu": 2.08, "silence": 1.79, "ps": 1.61, "posterior": 0.98}
```

Migration guard in `initialize()` follows the established pattern:
```python
try:
    conn.execute("ALTER TABLE jobs ADD COLUMN stall_signals TEXT")
except sqlite3.OperationalError as e:
    if "duplicate column" not in str(e).lower():
        raise
```

### New Settings (db.py DEFAULTS)

| Key | Default | Notes |
|-----|---------|-------|
| `stall_posterior_threshold` | `0.8` | Posterior at which stall is declared |
| `stall_action` | `"log"` | `"log"` = flag only; `"kill"` = auto-terminate after grace |
| `stall_kill_grace_seconds` | `60` | Seconds between stall detection and auto-kill |

`stall_multiplier` is preserved in DEFAULTS but no longer used by `_check_stalled_jobs()`. It is deprecated.

---

## File Change Summary

| File | Change |
|------|--------|
| `ollama_queue/stall.py` | **New** — StallDetector class (~120 lines) |
| `ollama_queue/daemon.py` | Instantiate StallDetector; replace `communicate()` for LLM jobs with select loop; rewrite `_check_stalled_jobs()`; `stall_detector.forget()` in finally block |
| `ollama_queue/db.py` | Add `stall_signals TEXT` migration; add 3 new setting defaults; add `set_stall_detected()` method |
| `tests/test_stall.py` | **New** — unit tests for StallDetector (posterior math, signal extraction, cleanup) |
| `tests/test_daemon.py` | Update `_run_job()` tests for LLM jobs (select loop replaces communicate mock) |

---

## Testing Strategy

**`tests/test_stall.py`:**
- `test_posterior_all_healthy` — all signals negative → posterior < 0.2
- `test_posterior_d_state_only` — D-state alone → 0.5 < posterior < 0.8
- `test_posterior_two_signals` — D-state + silent 300s → posterior > 0.9
- `test_cpu_delta_computation` — two sequential calls produce correct CPU%
- `test_stdout_silence_tracking` — update/get cycle produces correct silence seconds
- `test_forget_clears_state` — forget() removes all job state
- `test_ollama_ps_once_per_cycle` — verify HTTP call count

**`tests/test_daemon.py` updates:**
- Replace `proc.communicate.return_value = (b"out", b"")` with mocked `select.select` returning readable fds, `os.read` returning chunks, and `proc.poll()` returning 0 eventually
- Add `test_stall_kill_action` — mock posterior > threshold + stall_action='kill' → assert `os.kill` called

---

## Out of Scope

- Non-LLM jobs (`resource_profile='any'`): timeout unchanged
- Dashboard UI changes: `stall_signals` JSON is stored but not yet visualized
- Stdout silence signal for `ollama pull` jobs: pull commands stream progress lines, silence threshold may need adjustment (future tuning)
- `stall_multiplier` removal: deprecated in place, removed in a future cleanup
