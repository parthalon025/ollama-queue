# DLQ Audit Round 2 Fixes — Implementation Plan

> **Status: COMPLETE** — All 8 tasks done. 26 commits on `fix/dlq-audit-fixes` branch (includes Round 1 fixes). Additional fixes beyond the plan: lock ordering in eval endpoints, precision-weighted Bayesian posterior, `performance_curve.fit()` stale state reset, streaming proxy connect timeout, falsy-zero eval_analysis guard, retry backoff float cast, `aoi_weight`/`last_success` sort hoist, `_free_vram_mb` exception narrowing, `_port_has_process` per-line check.

**Goal:** Fix all 7 IMPORTANT + 3 MINOR issues + 3 test coverage gaps found in the H+V code review of the DLQ audit fixes branch.

**Architecture:** Fixes organized into 4 batches by dependency. Batch 1 (DB method split + double-count fix) is highest-impact. Batch 2 enriches `load_map_extended` to populate all 7 scoring factors. Batch 3 fixes VRAM default + chronic threshold + daemon metrics path. Batch 4 adds missing test coverage.

**Tech Stack:** Python 3.12, SQLite (WAL), pytest

---

## Quality Gate (run between every batch)

```bash
cd ~/Documents/projects/ollama-queue/.worktrees/fix/dlq-audit-fixes
source ~/Documents/projects/ollama-queue/.venv/bin/activate
pytest --timeout=120 -x -q
```

---

## Batch 1: DLQ Double-Count Fix (I1) — DONE

### Task 1: Split `update_dlq_reschedule` into mark + finalize methods — DONE

Commits: `7117b0d`, `cb80d44`, `2a339c2`

**Files:**
- Modify: `ollama_queue/db.py:1589-1607`
- Modify: `ollama_queue/dlq_scheduler.py:136-160`
- Modify: `tests/test_dlq_scheduler.py`

**Step 1: Add `mark_dlq_scheduling` method to db.py**

Add a new method after `update_dlq_reschedule` (around line 1607) that only writes the crash-safety marker WITHOUT incrementing `auto_reschedule_count` or setting `resolution`:

```python
def mark_dlq_scheduling(
    self, dlq_id: int, rescheduled_for: float, reschedule_reasoning: str | None = None
) -> None:
    """Mark a DLQ entry as being rescheduled (crash-safety marker).

    Does NOT increment auto_reschedule_count or set resolution — those are
    written by update_dlq_reschedule once the job is confirmed created.
    """
    with self._lock:
        conn = self._connect()
        conn.execute(
            """UPDATE dlq SET auto_rescheduled_at = ?,
               rescheduled_for = ?,
               reschedule_reasoning = ?
               WHERE id = ?""",
            (time.time(), rescheduled_for, reschedule_reasoning, dlq_id),
        )
        conn.commit()
```

**Step 2: Update dlq_scheduler.py to use the new pattern**

Replace lines 135-160 in `dlq_scheduler.py`:

```python
            # Mark DLQ entry BEFORE submitting job (crash-safe ordering).
            # mark_dlq_scheduling does NOT increment count or set resolution.
            self.db.mark_dlq_scheduling(
                entry["id"],
                rescheduled_for=slot["scheduled_time"],
                reschedule_reasoning=reasoning,
            )

            # Create new job
            new_job_id = self.db.submit_job(
                command=entry["command"],
                model=entry.get("model", ""),
                priority=entry.get("priority", 0),
                timeout=entry.get("timeout", 600),
                source=f"dlq-reschedule:{entry.get('source', 'unknown')}",
                tag=entry.get("tag"),
                resource_profile=entry.get("resource_profile", "ollama"),
            )

            # Finalize: set job ID, increment count, mark resolved
            self.db.update_dlq_reschedule(
                entry["id"],
                rescheduled_job_id=new_job_id,
                rescheduled_for=slot["scheduled_time"],
                reschedule_reasoning=reasoning,
            )
```

**Step 3: Update tests**

In `tests/test_dlq_scheduler.py`, find tests that assert `update_dlq_reschedule` is called twice. Update to assert:
- First call is `mark_dlq_scheduling` (with `rescheduled_for` and `reschedule_reasoning`, no job_id)
- Second call is `update_dlq_reschedule` (with actual `rescheduled_job_id`)

**Step 4: Run tests**

```bash
pytest tests/test_dlq_scheduler.py tests/test_integration_dlq_reschedule.py -v --timeout=120
```

**Step 5: Commit**

```bash
git add ollama_queue/db.py ollama_queue/dlq_scheduler.py tests/test_dlq_scheduler.py
git commit -m "fix: split DLQ reschedule into mark + finalize — prevents double-count

mark_dlq_scheduling writes the crash-safety marker (auto_rescheduled_at,
rescheduled_for, reasoning) WITHOUT incrementing auto_reschedule_count
or setting resolution. update_dlq_reschedule does both only after the
job is confirmed created. Fixes I1 (double-count on atomic pattern)."
```

---

## Batch 2: Enrich load_map_extended (I3) — DONE

### Task 2: Add missing keys to load_map_extended output — DONE

Commit: `e1b7273`

**Files:**
- Modify: `ollama_queue/scheduler.py:329-359`
- Modify: `tests/test_scheduler.py`

**Step 1: Write failing test**

Add to `tests/test_scheduler.py`:

```python
def test_load_map_extended_has_all_scoring_keys(self):
    """load_map_extended must return all keys that find_fitting_slot consumes."""
    required_keys = {"load", "vram_committed_gb", "is_pinned", "recurring_ids", "timestamp"}
    result = self.scheduler.load_map_extended()
    assert len(result) == 48
    for entry in result:
        for key in required_keys:
            assert key in entry, f"Missing key: {key}"
    # Timestamps should be future (within 24h from now)
    import time
    now = time.time()
    for i, entry in enumerate(result):
        assert entry["timestamp"] >= now - 60, f"Slot {i} timestamp in the past"
        assert entry["timestamp"] <= now + 86400 + 60, f"Slot {i} timestamp too far in future"
```

**Step 2: Enrich load_map_extended**

Replace the return statement and add tracking arrays. The new `load_map_extended` should:

1. Track which recurring job IDs fire in each slot (`recurring_ids: list[int]`)
2. Track pinned slots from the scores array (`is_pinned: bool` — score >= PIN_SCORE)
3. Compute timestamps anchored to local midnight (`timestamp: float`)

Replace lines 329-359:

```python
def load_map_extended(self, now: float | None = None) -> list[dict]:
    """Build a 48-slot load map with VRAM estimates and scheduling metadata.

    Returns list of dicts with keys consumed by find_fitting_slot:
    - load: priority-weighted score
    - vram_committed_gb: estimated VRAM commitment
    - is_pinned: True if slot is pinned (score >= PIN_SCORE)
    - recurring_ids: list of recurring job IDs firing in this slot
    - timestamp: wall-clock time for this slot (anchored to local midnight)
    """
    import datetime as _dt

    if now is None:
        now = time.time()

    scores = self.load_map(now=now)
    vram: list[float] = [0.0] * self._SLOT_COUNT
    slot_rj_ids: list[list[int]] = [[] for _ in range(self._SLOT_COUNT)]

    local_midnight = _dt.datetime.combine(
        _dt.datetime.fromtimestamp(now).date(), _dt.time.min
    ).timestamp()

    for rj in self._get_recurring_jobs():
        if not rj["enabled"]:
            continue
        model = rj.get("model", "")
        model_vram = _estimate_model_vram(model)

        # Build a temporary score array to find which slots this job fires in
        tmp: list[float] = [0.0] * self._SLOT_COUNT
        if rj.get("cron_expression"):
            self._score_cron_job(tmp, rj, 1.0, now)
        elif rj.get("interval_seconds"):
            self._score_interval_job(tmp, rj, 1.0, now)

        for i in range(self._SLOT_COUNT):
            if tmp[i] > 0:
                slot_rj_ids[i].append(rj["id"])
                if model_vram > 0:
                    vram[i] += model_vram

    return [
        {
            "load": scores[i],
            "vram_committed_gb": round(vram[i], 1),
            "is_pinned": scores[i] >= self._PIN_SCORE,
            "recurring_ids": slot_rj_ids[i],
            "timestamp": local_midnight + i * self._SLOT_SECONDS,
        }
        for i in range(self._SLOT_COUNT)
    ]
```

**Step 3: Run tests**

```bash
pytest tests/test_scheduler.py tests/test_slot_scoring.py tests/test_dlq_scheduler.py tests/test_deferral_scheduler.py tests/test_integration_dlq_reschedule.py -v --timeout=120
```

**Step 4: Commit**

```bash
git add ollama_queue/scheduler.py tests/test_scheduler.py
git commit -m "fix: enrich load_map_extended with pinned/recurring/timestamp keys

find_fitting_slot reads is_pinned, recurring_ids, and timestamp from
load map entries. Previously only load and vram_committed_gb were
populated — pinned-slot gate and recurring conflict penalty were
silently disabled. Now all scoring factors are active.

Fixes I3 (missing keys at integration boundary)."
```

---

## Batch 3: VRAM Default + Threshold + Daemon Metrics (I2 + I5 + I6) — DONE

### Task 3: Fix _estimate_model_vram to return safe default (I2 + I6 spec drift) — DONE

Commit: `ca246df`

**Files:**
- Modify: `ollama_queue/scheduler.py:410-418`
- Modify: `ollama_queue/dlq_scheduler.py:67`

**Step 1: Change `_estimate_model_vram` to return 4.0 for unknown models**

Replace lines 414-418 in `scheduler.py`:

```python
    """Estimate VRAM usage in GB from a model name like 'qwen2.5:7b'.

    Uses parameter count hints in the model name (e.g. '7b', '14b') and maps to
    approximate VRAM at Q4 quantization. Returns 4.0 GB default if no size hint found.
    """
    match = _SIZE_PATTERN.search(model)
    if not match:
        return 4.0  # Safe default for unknown models
```

**Step 2: Fix chronic threshold default**

In `dlq_scheduler.py` line 67, change `or 5` to `or 3`:

```python
chronic_threshold = self.db.get_setting("dlq.chronic_failure_threshold") or 3
```

**Step 3: Run tests**

```bash
pytest tests/test_scheduler.py tests/test_dlq_scheduler.py tests/test_deferral_scheduler.py tests/test_slot_scoring.py tests/test_integration_dlq_reschedule.py -v --timeout=120
```

Some tests may need adjustment if they assert `_estimate_model_vram("no-size") == 0.0`.

**Step 4: Commit**

```bash
git add ollama_queue/scheduler.py ollama_queue/dlq_scheduler.py
git commit -m "fix: _estimate_model_vram returns 4.0 GB default, chronic threshold 3

Models without a recognizable size pattern now return 4.0 GB instead
of 0.0, matching the CLAUDE.md documentation. Chronic failure threshold
default changed from 5 to 3, also matching docs.

Fixes I2 (VRAM zero for unknown models) and I6 (threshold spec drift)."
```

### Task 4: Initialize `out = b""` before try block in daemon.py (I5) — DONE

Commit: `81f3239`

**Files:**
- Modify: `ollama_queue/daemon.py`

**Step 1: Initialize `out` before the try block**

Find the line `try:` before `proc = subprocess.Popen(...)` (around line 805). Add `out = b""` before it:

```python
        out = b""  # Initialize so metrics capture works even if Popen raises
        try:
            proc = subprocess.Popen(
```

**Step 2: Move metrics capture into a finally-like pattern**

After the `except Exception as exc:` block (around line 964-979), add metrics capture for the partial output:

```python
        except Exception as exc:
            _log.exception("Unhandled exception in worker thread for job #%d; marking failed", job["id"])
            with self.db._lock:
                try:
                    self.db.complete_job(
                        job["id"],
                        exit_code=-1,
                        stdout_tail="",
                        stderr_tail="",
                        outcome_reason="internal error",
                    )
                except Exception:
                    _log.exception("Failed to mark job #%d failed after worker exception", job["id"])
                self._record_ollama_failure()
                # ... existing handle_failure code ...

            # Attempt to capture any partial metrics from output before crash
            if out:
                try:
                    full_stdout = out.decode("utf-8", errors="replace")
                    metrics = parse_ollama_metrics(full_stdout)
                    if metrics:
                        metrics["model"] = job.get("model", "")
                        metrics["command"] = job.get("command", "")
                        metrics["resource_profile"] = job.get("resource_profile", "ollama")
                        self.db.store_job_metrics(job["id"], metrics)
                except Exception:
                    _log.debug("Failed to capture partial metrics for job #%d", job["id"])
```

**Step 3: Run tests**

```bash
pytest tests/test_daemon.py -v --timeout=120
```

**Step 4: Commit**

```bash
git add ollama_queue/daemon.py
git commit -m "fix: capture partial metrics even when Popen raises

Initialize out=b'' before the try block so the except handler
can attempt to parse any partial Ollama output. Prevents silent
loss of metrics data on OOM-kills and broken pipes.

Fixes I5 (metrics capture unreachable on exception path)."
```

---

## Batch 4: Test Coverage Gaps (T1 + T2 + T3 + minor fixes) — DONE

### Task 5: Add deferral VRAM wiring test (T1) — DONE

Commit: `d8e5e39`

**Files:**
- Modify: `tests/test_deferral_scheduler.py`

**Step 1: Add test**

```python
def test_sweep_passes_vram_estimate(self):
    """find_fitting_slot should receive a non-zero VRAM estimate for known models."""
    db = MagicMock()
    db.get_setting.return_value = True
    db.list_deferred.side_effect = [
        [],  # Phase 1: no scheduled entries
        [{"id": 1, "job_id": 100, "scheduled_for": None}],  # Phase 2
    ]
    db.get_job.return_value = {
        "id": 100, "status": "deferred", "model": "qwen2.5:14b",
        "command": "echo test", "resource_profile": "ollama",
    }
    est = MagicMock()
    est_result = MagicMock()
    est_result.total_upper = 300.0
    est_result.total_mean = 200.0
    est.estimate.return_value = est_result

    load_map = [{"load": 0.0, "vram_committed_gb": 0.0, "is_pinned": False,
                 "recurring_ids": [], "timestamp": time.time() + i * 1800}
                for i in range(48)]

    sched = DeferralScheduler(db, est, lambda: load_map)
    with patch("ollama_queue.deferral_scheduler.find_fitting_slot") as mock_ffs:
        mock_ffs.return_value = {"slot_index": 5, "score": 10.0,
                                 "scheduled_time": time.time() + 9000}
        sched.sweep()
        mock_ffs.assert_called_once()
        call_kwargs = mock_ffs.call_args
        # VRAM estimate for 14b model should be > 0
        assert call_kwargs.kwargs.get("job_vram_needed_gb", call_kwargs[1].get("job_vram_needed_gb", 0)) > 0
```

**Step 2: Run test**

```bash
pytest tests/test_deferral_scheduler.py -v --timeout=120
```

**Step 3: Commit**

```bash
git add tests/test_deferral_scheduler.py
git commit -m "test: add deferral VRAM wiring test (T1)"
```

### Task 6: Add non-positive duration test (T2) — DONE

Commit: `4423311`

**Files:**
- Modify: `tests/test_runtime_estimator.py`

**Step 1: Add test**

```python
def test_estimate_clamps_negative_durations(self):
    """Non-positive durations should be clamped, not crash."""
    db = MagicMock()
    db.get_durations.return_value = [0.0, -1.0, 30.0, 60.0]
    db.get_warmup_durations.return_value = []
    est = RuntimeEstimator(db)
    result = est.estimate("test-model", "echo test", "ollama")
    # Should produce a valid estimate without raising
    assert result.total_mean > 0
    assert result.confidence in ("low", "medium", "high")
```

**Step 2: Run test**

```bash
pytest tests/test_runtime_estimator.py -v --timeout=120
```

**Step 3: Commit**

```bash
git add tests/test_runtime_estimator.py
git commit -m "test: add non-positive duration clamping test (T2)"
```

### Task 7: Add mid-stream malformed JSON test (T3) — DONE

Commit: `095d1e0`

**Files:**
- Modify: `tests/test_metrics_parser.py`

**Step 1: Add test**

```python
def test_malformed_line_mid_stream_still_finds_done(self):
    """A malformed JSON line mid-stream should not prevent finding the done response."""
    output = (
        '{"model":"qwen2.5:7b","response":"hello"}\n'
        '{CORRUPTED LINE\n'
        '{"model":"qwen2.5:7b","done":true,"total_duration":1000000000,"eval_count":50,"eval_duration":500000000}\n'
    )
    result = parse_ollama_metrics(output)
    assert result is not None
    assert result["eval_count"] == 50
```

**Step 2: Run test**

```bash
pytest tests/test_metrics_parser.py -v --timeout=120
```

**Step 3: Commit**

```bash
git add tests/test_metrics_parser.py
git commit -m "test: add mid-stream malformed JSON resilience test (T3)"
```

### Task 8: Fix minor issues (M2 + M3) — DONE

Commit: `26cf806`

**Files:**
- Modify: `ollama_queue/performance_curve.py:63`

**Step 1: Fix residual std boundary (M2)**

Change line 63 from `> 2` to `>= 2`:

```python
residual_std = float(np.std(residuals, ddof=1)) if len(residuals) >= 2 else 0.3
```

Note: `ddof=1` requires at least 2 values (Bessel's correction), which is exactly the threshold. This is safe.

**Step 2: Run tests**

```bash
pytest tests/test_performance_curve.py -v --timeout=120
```

**Step 3: Commit**

```bash
git add ollama_queue/performance_curve.py
git commit -m "fix: residual std uses >= 2 threshold (matches fit acceptance)"
```

---

## Summary

| Batch | Tasks | Fixes | Impact | Status |
|-------|-------|-------|--------|--------|
| 1 | 1 | I1 | DLQ double-count eliminated | DONE |
| 2 | 2 | I3 | All 7 scoring factors active in slot scoring | DONE |
| 3 | 3-4 | I2, I5, I6 | VRAM default + threshold + daemon metrics | DONE |
| 4 | 5-8 | T1, T2, T3, M2 | Test gaps + minor fix | DONE |

## Additional Fixes (beyond plan scope)

These fixes were discovered and resolved during implementation but were not in the original plan:

| Commit | Fix | Category |
|--------|-----|----------|
| `2a339c2` | Lock ordering in eval endpoints + precision-weighted Bayesian posterior | CRITICAL |
| `5afe272` | Narrow `_free_vram_mb` exception handling | MINOR |
| `ddb3d6b` | `_port_has_process` checks per-line, not full output | IMPORTANT |
| `1acfc1c` | Explicit None check instead of falsy-zero in eval_analysis | IMPORTANT |
| `3d5335c` | `float()` cast to retry backoff settings | MINOR |
| `627293d` | Hoist `aoi_weight` + `last_success` reads above sort (O(1) vs O(N)) | MINOR |
| `dd5e4cd` | Add 10s connect timeout to streaming proxy | IMPORTANT |
