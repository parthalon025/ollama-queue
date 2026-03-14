# DLQ Auto-Reschedule Audit Fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all 8 CRITICAL and 14 IMPORTANT issues found in the H+V code review of the DLQ auto-reschedule, deferral, intelligence, and metrics modules.

**Architecture:** Fixes organized into 7 batches by dependency and risk. Batch 1 (VRAM gate) is the highest-impact fix — restores the entire admission control system. Later batches address silent failures, parser correctness, scheduler safety, and estimator accuracy.

**Tech Stack:** Python 3.12, SQLite (WAL), pytest

**Design Doc:** Based on audit findings from 5 parallel code-review agents (2026-03-10).

**Execution:** Subagent-driven development with code review agents between batches. Quality gates: `pytest --timeout=120 -x -q` between every batch.

---

## Quality Gate (run between every batch)

```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q
```

If tests fail, fix before proceeding.

---

## Batch 1: VRAM Gate Restoration (C1 + C2 + C5)

The VRAM hard gate is completely inert due to three compounding failures: wrong key name in load map, hardcoded `job_vram_needed_gb=0`, and `_get_load_map` calling `load_map()` instead of `load_map_extended()`. This batch restores all three.

### Task 1: Fix `_get_load_map` to use `load_map_extended` and rename `vram_gb` key

**Files:**
- Modify: `ollama_queue/daemon.py:153-157`
- Modify: `ollama_queue/scheduler.py:359`
- Modify: `tests/test_integration_dlq_reschedule.py:31`

**Step 1: Fix daemon to use `load_map_extended()`**

In `ollama_queue/daemon.py`, change `_get_load_map`:

```python
def _get_load_map(self) -> list[dict]:
    """Load map accessor for DLQ/deferral schedulers."""
    if hasattr(self.scheduler, "load_map_extended"):
        return self.scheduler.load_map_extended()
    return []
```

**Step 2: Rename `vram_gb` to `vram_committed_gb` in `load_map_extended`**

In `ollama_queue/scheduler.py:359`, change the return dict:

```python
return [{"load": scores[i], "vram_committed_gb": round(vram[i], 1)} for i in range(self._SLOT_COUNT)]
```

**Step 3: Fix integration test `_empty_load_map`**

In `tests/test_integration_dlq_reschedule.py`, replace `_empty_load_map()`:

```python
def _empty_load_map():
    now = time.time()
    return [
        {
            "load": 0.0,
            "vram_committed_gb": 0.0,
            "timestamp": now + i * 1800,
            "is_pinned": False,
            "historical_quiet": True,
            "queue_depth": 0,
        }
        for i in range(48)
    ]
```

**Step 4: Run tests**

```bash
pytest tests/test_integration_dlq_reschedule.py tests/test_slot_scoring.py tests/test_scheduler.py -v --timeout=120
```

**Step 5: Commit**

```bash
git add ollama_queue/daemon.py ollama_queue/scheduler.py tests/test_integration_dlq_reschedule.py
git commit -m "fix: restore VRAM gate — use load_map_extended, rename vram_gb key

_get_load_map now calls load_map_extended() (not load_map()) so VRAM
commitment data is available to slot_scoring. Key renamed from vram_gb
to vram_committed_gb to match find_fitting_slot expectation.
Integration test updated with correct keys and timestamps.

Fixes: C1 (key mismatch), C5 (test wrong key)"
```

---

### Task 2: Wire real `job_vram_needed_gb` into DLQ and deferral schedulers

**Files:**
- Modify: `ollama_queue/dlq_scheduler.py:1,104-105`
- Modify: `ollama_queue/deferral_scheduler.py:1,82-83`
- Modify: `tests/test_dlq_scheduler.py`
- Modify: `tests/test_deferral_scheduler.py`

**Step 1: Write failing tests**

In `tests/test_dlq_scheduler.py`, add:

```python
def test_sweep_passes_vram_estimate(self, db, estimator, sched):
    """Verify find_fitting_slot receives real VRAM estimate, not 0."""
    _add_dlq_entry(db, model="qwen2.5:14b")
    db.set_setting("dlq.auto_reschedule", True)
    with patch("ollama_queue.dlq_scheduler.find_fitting_slot") as mock_ffs:
        mock_ffs.return_value = {"slot_index": 2, "score": 10.0, "scheduled_time": time.time() + 3600}
        sched._do_sweep(db.list_dlq(unscheduled_only=True))
        call_kwargs = mock_ffs.call_args
        # 14b model → ~8.5 GB VRAM
        assert call_kwargs.kwargs.get("job_vram_needed_gb", call_kwargs[1].get("job_vram_needed_gb", 0)) > 0
```

**Step 2: Import `_estimate_model_vram` in both schedulers**

In `ollama_queue/dlq_scheduler.py`, add import:

```python
from ollama_queue.scheduler import _estimate_model_vram
```

In `ollama_queue/deferral_scheduler.py`, add import:

```python
from ollama_queue.scheduler import _estimate_model_vram
```

**Step 3: Replace hardcoded values**

In `ollama_queue/dlq_scheduler.py:102-109`, replace:

```python
            model = entry.get("model", "")
            job_vram = _estimate_model_vram(model)
            slot = find_fitting_slot(
                load_map,
                job_vram_needed_gb=job_vram,
                total_vram_gb=24.0,  # TODO: get from health monitor
                estimated_slots=estimated_slots,
                failure_category=failure_cat,
                job_model=model,
            )
```

In `ollama_queue/deferral_scheduler.py:79-86`, replace:

```python
            model = job.get("model", "")
            job_vram = _estimate_model_vram(model)
            estimated_slots = max(1, int(est.total_upper / 1800) + 1)
            slot = find_fitting_slot(
                load_map,
                job_vram_needed_gb=job_vram,
                total_vram_gb=24.0,  # TODO: get from health monitor
                estimated_slots=estimated_slots,
                job_model=model,
            )
```

**Step 4: Run tests**

```bash
pytest tests/test_dlq_scheduler.py tests/test_deferral_scheduler.py tests/test_integration_dlq_reschedule.py -v --timeout=120
```

**Step 5: Commit**

```bash
git add ollama_queue/dlq_scheduler.py ollama_queue/deferral_scheduler.py tests/test_dlq_scheduler.py tests/test_deferral_scheduler.py
git commit -m "fix: wire real VRAM estimates into DLQ/deferral schedulers

Replace hardcoded job_vram_needed_gb=0 with _estimate_model_vram()
from scheduler.py. VRAM hard gate and resource-failure headroom
(Factor 6) now function correctly.

Fixes: C2 (hardcoded VRAM)"
```

---

### Batch 1 Quality Gate

```bash
pytest --timeout=120 -x -q
```

---

## Batch 2: Silent Failure Logging (C3 + C6 + I1 + I4 + I5 + I6)

All Cluster A fixes — add logging where values are silently clamped or dropped.

### Task 3: Fix bare excepts in system_snapshot.py (C3)

**Files:**
- Modify: `ollama_queue/system_snapshot.py:53-68`

**Step 1: Add `exc_info=True` to all 4 bare excepts**

```python
            try:
                snap.ram_used_pct = health_monitor.get_ram_pct()  # type: ignore[attr-defined]
            except Exception:
                _log.debug("Failed to read RAM pct from health monitor", exc_info=True)
            try:
                snap.swap_used_pct = health_monitor.get_swap_pct()  # type: ignore[attr-defined]
            except Exception:
                _log.debug("Failed to read swap pct from health monitor", exc_info=True)
            try:
                snap.load_avg_1m = health_monitor.get_load_avg()  # type: ignore[attr-defined]
            except Exception:
                _log.debug("Failed to read load avg from health monitor", exc_info=True)
            try:
                vram = health_monitor.get_vram_pct()  # type: ignore[attr-defined]
                if vram is not None:
                    snap.vram_used_pct = vram
            except Exception:
                _log.debug("Failed to read VRAM pct from health monitor", exc_info=True)
```

**Step 2: Run tests**

```bash
pytest tests/test_system_snapshot.py -v --timeout=120
```

**Step 3: Commit**

```bash
git add ollama_queue/system_snapshot.py
git commit -m "fix: add exc_info=True to system_snapshot bare excepts

All 4 health monitor reads now log the actual exception, not just
a generic message. Fixes Cluster A / lesson #7 violation."
```

---

### Task 4: Fix negative value crash in performance_curve.py (C6 + I4 + I5 + I6)

**Files:**
- Modify: `ollama_queue/performance_curve.py:51,72,90,100,23-24`
- Modify: `tests/test_performance_curve.py`

**Step 1: Write failing test for negative values in `fit()`**

Add to `tests/test_performance_curve.py`:

```python
def test_fit_ignores_negative_stats():
    """Negative model_size_gb or avg_tok_per_min must not crash math.log."""
    curve = PerformanceCurve()
    curve.fit([
        {"model_size_gb": -1.0, "avg_tok_per_min": 80.0},
        {"model_size_gb": 5.0, "avg_tok_per_min": -5.0},
        {"model_size_gb": 10.0, "avg_tok_per_min": 45.0},
    ])
    # Should fit from valid point only (single-point path)
    assert curve.fitted

def test_fit_degenerate_same_size():
    """All models same size — should not crash, slope=0."""
    curve = PerformanceCurve()
    curve.fit([
        {"model_size_gb": 7.0, "avg_tok_per_min": 80.0},
        {"model_size_gb": 7.0, "avg_tok_per_min": 75.0},
    ])
    assert curve.fitted
    result = curve.predict_tok_per_min(7.0)
    assert result is not None
```

**Step 2: Run tests — expect FAIL (C6 crashes)**

**Step 3: Fix filters and add logging**

In `performance_curve.py`, change the truthiness filters to explicit positive checks:

Line 51:
```python
valid_tok = [s for s in model_stats if (s.get("avg_tok_per_min") or 0) > 0 and (s.get("model_size_gb") or 0) > 0]
```

Line 72:
```python
valid_warmup = [s for s in model_stats if (s.get("avg_warmup_s") or 0) > 0 and (s.get("model_size_gb") or 0) > 0]
```

Line 23-24 (degenerate regression logging):
```python
    if abs(denom) < 1e-10:
        logger.debug("Linear regression degenerate (identical x-values): returning flat curve")
        return 0.0, sum_y / n if n else 0.0
```

Line 90 (silent fallback logging):
```python
        std = self._tok_residual_std or 0.3
        if not self._tok_residual_std:
            logger.debug("Using fallback residual_std=0.3 (zero or missing)")
```

Line 100 (warmup clamp logging):
```python
    def predict_warmup(self, model_size_gb: float) -> float | None:
        """Predict warmup time (seconds) for a model size."""
        if self._warmup_slope is None:
            return None
        raw = self._warmup_slope * model_size_gb + self._warmup_intercept
        if raw < 0.1:
            logger.debug("Warmup prediction clamped to 0.1 for size=%.1f (raw=%.2f)", model_size_gb, raw)
        return max(0.1, raw)
```

**Step 4: Run tests — expect PASS**

```bash
pytest tests/test_performance_curve.py -v --timeout=120
```

**Step 5: Commit**

```bash
git add ollama_queue/performance_curve.py tests/test_performance_curve.py
git commit -m "fix: guard against negative values in performance_curve fit()

Change truthiness filters to explicit >0 checks to prevent math.log
ValueError on negative model sizes or throughput. Add debug logging
for degenerate regression, residual_std fallback, and warmup clamp.

Fixes: C6 (negative crash), I4 (silent fallback), I5 (silent clamp), I6 (degenerate)"
```

---

### Task 5: Log silent clamps in runtime_estimator.py (I1)

**Files:**
- Modify: `ollama_queue/runtime_estimator.py:77,101,123`

**Step 1: Add logging for clamped durations**

At line 76-77, replace:
```python
        if durations:
            log_durations = [math.log(max(d, 0.1)) for d in durations]
```

With:
```python
        if durations:
            bad = [d for d in durations if d <= 0]
            if bad:
                logger.warning("Clamping %d non-positive durations for model=%r: %s", len(bad), model, bad[:5])
            log_durations = [math.log(max(d, 0.1)) for d in durations]
```

At line 101, simplify the redundant `or []`:
```python
        if loaded_models is None or model not in loaded_models:
```

At line 122-123, add logging for warmup clamps:
```python
        if warmups:
            bad = [w for w in warmups if w <= 0]
            if bad:
                logger.warning("Clamping %d non-positive warmup values for model=%r", len(bad), model)
            log_warmups = [math.log(max(w, 0.01)) for w in warmups]
```

**Step 2: Run tests**

```bash
pytest tests/test_runtime_estimator.py -v --timeout=120
```

**Step 3: Commit**

```bash
git add ollama_queue/runtime_estimator.py
git commit -m "fix: log non-positive durations before clamping in RuntimeEstimator

Cluster A fix: silent max(d, 0.1) clamps now emit logger.warning
with count and sample values. Also remove redundant 'or []' guard."
```

---

### Batch 2 Quality Gate

```bash
pytest --timeout=120 -x -q
```

---

## Batch 3: Metrics Parser (C8 + I7 + I8)

### Task 6: Replace regex with line-by-line JSON parsing (C8 + I7)

**Files:**
- Modify: `ollama_queue/metrics_parser.py`
- Modify: `tests/test_metrics_parser.py`

**Step 1: Write failing test for nested JSON (/api/chat format)**

Add to `tests/test_metrics_parser.py`:

```python
def test_chat_format_nested_json():
    """Ollama /api/chat wraps response in message object — regex would miss this."""
    stdout = '{"model":"llama3","message":{"role":"assistant","content":"Hi"},"done":true,"eval_count":50,"eval_duration":1000000000,"total_duration":2000000000,"load_duration":500000000}\n'
    result = parse_ollama_metrics(stdout)
    assert result is not None
    assert result["eval_count"] == 50
```

**Step 2: Replace regex with line-by-line JSON scan**

```python
def parse_ollama_metrics(stdout: str) -> dict | None:
    """Extract Ollama performance metrics from job stdout.

    Scans for the final ``{"done": true, ...}`` JSON object that Ollama
    emits at the end of generate/chat responses. Returns a dict with
    standardized field names, or None if no metrics found.
    """
    if not stdout:
        return None

    last_done = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("done") is True:
            last_done = data

    if last_done is None:
        return None

    # Extract and normalize fields
    metrics = {}
    for field in (
        "load_duration",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
        "total_duration",
    ):
        val = last_done.get(field)
        if val is not None:
            key = f"{field}_ns" if field.endswith("_duration") else field
            metrics[key] = val

    if "model" in last_done:
        metrics["response_model"] = last_done["model"]

    return metrics if metrics else None
```

Remove the `_DONE_PATTERN` regex and the `re` import.

**Step 3: Run tests**

```bash
pytest tests/test_metrics_parser.py -v --timeout=120
```

**Step 4: Commit**

```bash
git add ollama_queue/metrics_parser.py tests/test_metrics_parser.py
git commit -m "fix: replace regex parser with line-by-line JSON scan

Regex [^{}]* broke on /api/chat nested JSON (message object).
New approach: iterate lines, json.loads each, keep last done=true.
Handles both /api/generate (flat) and /api/chat (nested) formats.

Fixes: C8 (nested JSON), I7 (response_model still parsed for future use)"
```

---

### Task 7: Capture metrics on non-zero exit codes (I8)

**Files:**
- Modify: `ollama_queue/daemon.py:907-924`

**Step 1: Move metrics capture outside exit_code guard**

Move lines 914-924 to run unconditionally (before the `if exit_code == 0` block or after it). The `parse_ollama_metrics` already returns `None` for non-Ollama output, so there is no false-positive risk.

After the `out` variable is available (around line 900), but before the `if exit_code == 0:` block:

```python
            # Always capture Ollama metrics — job may have produced output before failing
            full_stdout = out.decode("utf-8", errors="replace")
            metrics = parse_ollama_metrics(full_stdout)
            if metrics:
                metrics["model"] = job.get("model", "")
                metrics["command"] = job.get("command", "")
                metrics["resource_profile"] = job.get("resource_profile", "ollama")
                try:
                    self.db.store_job_metrics(job["id"], metrics)
                except Exception:
                    _log.exception("Failed to store metrics for job #%d", job["id"])

            if exit_code == 0:
                self.db.record_duration(...)
```

Remove the duplicate metrics block from inside the `if exit_code == 0` block.

**Step 2: Run tests**

```bash
pytest tests/test_daemon.py -v --timeout=120
```

**Step 3: Commit**

```bash
git add ollama_queue/daemon.py
git commit -m "fix: capture Ollama metrics on all exits, not just exit_code=0

Jobs that partially succeed (streaming response then failing) now
have their tok/min and eval metrics captured. parse_ollama_metrics
returns None for non-Ollama output, so no false positives."
```

---

### Batch 3 Quality Gate

```bash
pytest --timeout=120 -x -q
```

---

## Batch 4: Scheduler Safety (C4 + I12 + I13 + I14)

### Task 8: Add idempotency guard to deferral Phase 1 (I12)

**Files:**
- Modify: `ollama_queue/deferral_scheduler.py:47-53`

**Step 1: Phase 1 already has the guard (lines 49-51)**

Reading the code again: Phase 1 at lines 49-50 already has:
```python
                job = self.db.get_job(entry["job_id"])
                if not job or job["status"] != "deferred":
                    continue
```

This is correct. The agent was wrong — the guard IS present. **Skip this task.**

---

### Task 9: Wire `defer.enabled` setting in DeferralScheduler (I13)

**Files:**
- Modify: `ollama_queue/deferral_scheduler.py:40`
- Modify: `tests/test_deferral_scheduler.py`

**Step 1: Write failing test**

Add to `tests/test_deferral_scheduler.py`:

```python
def test_sweep_respects_disabled_setting(self):
    """When defer.enabled is false, sweep should return empty."""
    db = MagicMock()
    db.get_setting.return_value = False
    est = MagicMock()
    sched = DeferralScheduler(db, est, lambda: [])
    result = sched.sweep()
    assert result == []
    db.list_deferred.assert_not_called()
```

**Step 2: Add the guard at top of `_do_sweep`**

In `ollama_queue/deferral_scheduler.py:40`, after the method signature:

```python
    def _do_sweep(self) -> list[dict]:
        """Process deferred jobs — resume scheduled-past entries and find slots for unscheduled."""
        if not self.db.get_setting("defer.enabled"):
            return []
        resumed = []
```

**Step 3: Run tests**

```bash
pytest tests/test_deferral_scheduler.py -v --timeout=120
```

**Step 4: Commit**

```bash
git add ollama_queue/deferral_scheduler.py tests/test_deferral_scheduler.py
git commit -m "fix: wire defer.enabled setting — toggle now actually works

DeferralScheduler._do_sweep now exits early when defer.enabled is
false, matching DLQScheduler's dlq.auto_reschedule guard pattern."
```

---

### Task 10: Tag auto-rescheduled jobs distinctly (I14)

**Files:**
- Modify: `ollama_queue/dlq_scheduler.py:121`

**Step 1: Change source attribution**

Replace line 121:
```python
                source=entry.get("source", "dlq-reschedule"),
```

With:
```python
                source=f"dlq-reschedule:{entry.get('source', 'unknown')}",
```

**Step 2: Run tests**

```bash
pytest tests/test_dlq_scheduler.py tests/test_integration_dlq_reschedule.py -v --timeout=120
```

**Step 3: Commit**

```bash
git add ollama_queue/dlq_scheduler.py
git commit -m "fix: tag auto-rescheduled jobs with distinct source prefix

Jobs rescheduled from DLQ now use source 'dlq-reschedule:<original>'
instead of carrying the original source, making them distinguishable
in metrics and history from organic submissions."
```

---

### Task 11: Make submit + update_dlq_reschedule atomic (C4)

**Files:**
- Modify: `ollama_queue/dlq_scheduler.py:115-149`

**Step 1: Mark DLQ entry as in-flight before submitting job**

Move `update_dlq_reschedule` before `submit_job`, or use a flag approach. The simplest safe approach: update first (marks as "being rescheduled"), then submit. If submit fails, the entry stays marked but with `rescheduled_job_id=None` — which is recoverable. The alternative (submit then update) leaves a dangling job on crash.

Replace lines 115-149:
```python
            # Mark DLQ entry as being rescheduled BEFORE creating the job
            # (prevents duplicate reschedules on crash between submit and update)
            reasoning = json.dumps(
                {
                    "failure_category": failure_cat,
                    "estimate": {
                        "mean": est.total_mean,
                        "upper": est.total_upper,
                        "confidence": est.confidence,
                    },
                    "slot": {
                        "index": slot["slot_index"],
                        "score": slot["score"],
                    },
                    "reschedule_count": (entry.get("auto_reschedule_count") or 0) + 1,
                }
            )

            self.db.update_dlq_reschedule(
                entry["id"],
                rescheduled_job_id=None,
                rescheduled_for=slot["scheduled_time"],
                reschedule_reasoning=reasoning,
            )

            # Now create the job — DLQ entry is already marked, safe from re-sweep
            new_job_id = self.db.submit_job(
                command=entry["command"],
                model=entry.get("model", ""),
                priority=entry.get("priority", 0),
                timeout=entry.get("timeout", 600),
                source=f"dlq-reschedule:{entry.get('source', 'unknown')}",
                tag=entry.get("tag"),
                resource_profile=entry.get("resource_profile", "ollama"),
            )

            # Backfill the job ID
            self.db.update_dlq_reschedule(
                entry["id"],
                rescheduled_job_id=new_job_id,
                rescheduled_for=slot["scheduled_time"],
                reschedule_reasoning=reasoning,
            )

            logger.info(
                "DLQ #%s: rescheduled as job #%s at slot %s (score=%.1f, cat=%s)",
                entry["id"],
                new_job_id,
                slot["slot_index"],
                slot["score"],
                failure_cat,
            )
            rescheduled.append({"dlq_id": entry["id"], "new_job_id": new_job_id})
```

**Step 2: Run tests**

```bash
pytest tests/test_dlq_scheduler.py tests/test_integration_dlq_reschedule.py -v --timeout=120
```

**Step 3: Commit**

```bash
git add ollama_queue/dlq_scheduler.py
git commit -m "fix: make DLQ reschedule atomic — mark entry before submitting job

update_dlq_reschedule now runs BEFORE submit_job. If the process
crashes between the two, the DLQ entry is already marked (preventing
re-sweep duplicates) and rescheduled_job_id=None signals incomplete.
Job ID is backfilled after successful submit.

Fixes: C4 (non-atomic submit+update)"
```

---

### Batch 4 Quality Gate

```bash
pytest --timeout=120 -x -q
```

---

## Batch 5: RuntimeEstimator Correctness (C7 + I2 + I3)

### Task 12: Update RuntimeEstimator docstring to 3-tier (C7)

**Files:**
- Modify: `ollama_queue/runtime_estimator.py:1-11,62`

**Step 1: Fix docstring and comment**

Update module docstring to describe the actual 3-tier hierarchy:
```python
"""Bayesian runtime estimator using log-normal model with hierarchical priors.

Predicts how long a job will take based on model size, historical performance,
and token throughput — all learned from this machine's actual behavior.

Uses 3-tier hierarchy:
1. Resource profile prior (weakest — generic bucket)
2. Model-level duration history (direct observations)
3. (Model, command) duration history (strongest — exact match)

Note: Cross-model PerformanceCurve is a separate module used externally
by callers who want to interpolate from other models' performance.
"""
```

Update line 62 comment:
```python
        # (PerformanceCurve is used by callers externally, not within this estimator)
```

**Step 2: Fix misleading test name (I3)**

In `tests/test_runtime_estimator.py`, rename `test_estimate_with_tok_per_min_history` to `test_estimate_with_duration_history` and remove the unused `get_tok_per_min` mock setup.

**Step 3: Run tests**

```bash
pytest tests/test_runtime_estimator.py -v --timeout=120
```

**Step 4: Commit**

```bash
git add ollama_queue/runtime_estimator.py tests/test_runtime_estimator.py
git commit -m "fix: correct RuntimeEstimator docs to 3-tier (not 4-tier)

PerformanceCurve is used externally, not inside the estimator. Update
docstring and comment to match reality. Rename misleading test that
set up unused get_tok_per_min mock."
```

---

### Batch 5 Quality Gate

```bash
pytest --timeout=120 -x -q
```

---

## Batch 6: Intelligence & Slot Scoring (I9 + I10)

### Task 13: Fix quietest_hour for sparse data (I9)

**Files:**
- Modify: `ollama_queue/intelligence.py:47-55`
- Modify: `tests/test_intelligence.py`

**Step 1: Write failing test**

Add to `tests/test_intelligence.py`:

```python
def test_quietest_hour_ignores_unobserved():
    """quietest_hour should only consider hours with actual data."""
    lp = LoadPatterns()
    # Only hours 10 and 14 have data
    log = [
        {"recorded_at": 1709301600 + 10 * 3600, "load": 5.0},  # hour 10
        {"recorded_at": 1709301600 + 14 * 3600, "load": 2.0},  # hour 14
    ]
    result = lp.compute(log)
    # quietest_hour should be 14 (load=2.0), not hour 0 (no data = 0.0)
    assert result["quietest_hour"] == 14
```

**Step 2: Fix by tracking observed hours**

Replace lines 47-55 in `intelligence.py`:

```python
        self._hourly = [hourly_sums[h] / hourly_counts[h] if hourly_counts[h] > 0 else 0.0 for h in range(24)]
        self._daily = [daily_sums[d] / daily_counts[d] if daily_counts[d] > 0 else 0.0 for d in range(7)]
        self._computed = True

        observed_hours = [(self._hourly[h], h) for h in range(24) if hourly_counts[h] > 0]

        return {
            "hourly_points": sum(hourly_counts),
            "daily_points": sum(daily_counts),
            "peak_hour": max(observed_hours)[1] if observed_hours else None,
            "quietest_hour": min(observed_hours)[1] if observed_hours else None,
        }
```

**Step 3: Run tests**

```bash
pytest tests/test_intelligence.py -v --timeout=120
```

**Step 4: Commit**

```bash
git add ollama_queue/intelligence.py tests/test_intelligence.py
git commit -m "fix: quietest_hour only considers observed hours

Previously resolved to hour 0 (unobserved, load=0.0) in sparse logs.
Now tracks which hours have data and computes min/max only over those."
```

---

### Task 14: Default slot timestamp to computed future time (I10)

**Files:**
- Modify: `ollama_queue/slot_scoring.py:125-129`

**Step 1: Use computed timestamp when missing**

Replace line 128:
```python
        "scheduled_time": load_map[best_start].get("timestamp", 0.0),
```

With:
```python
        "scheduled_time": load_map[best_start].get("timestamp") or (time.time() + best_start * 1800),
```

Add `import time` at top of file.

**Step 2: Run tests**

```bash
pytest tests/test_slot_scoring.py tests/test_integration_dlq_reschedule.py -v --timeout=120
```

**Step 3: Commit**

```bash
git add ollama_queue/slot_scoring.py
git commit -m "fix: default slot timestamp to computed future time

When load_map entries lack a timestamp field, compute it from
slot_index × 30min instead of defaulting to epoch 0. Prevents
deferral scheduler from immediately resuming all entries."
```

---

### Batch 6 Quality Gate

```bash
pytest --timeout=120 -x -q
```

---

## Batch 7: Final Sweep (I2 + M1 + M2)

### Task 15: Fix Bayesian posterior formula (I2)

**Files:**
- Modify: `ollama_queue/runtime_estimator.py:90,130`

**Step 1: Replace weighted-avg-of-variances with precision-weighted update**

Replace line 90:
```python
            post_std = math.sqrt((n0 * prior["log_std"] ** 2 + n * sample_var) / (n0 + n))
```

With:
```python
            prior_precision = 1.0 / (prior["log_std"] ** 2)
            sample_precision = n / sample_var if sample_var > 1e-10 else n / (prior["log_std"] ** 2)
            post_precision = prior_precision + sample_precision
            post_std = math.sqrt(1.0 / post_precision)
```

Apply same fix at line 130 for warmup:
```python
            prior_precision = 1.0 / (prior["log_std"] ** 2)
            sample_precision = n / sample_var if sample_var > 1e-10 else n / (prior["log_std"] ** 2)
            post_precision = prior_precision + sample_precision
            post_std = math.sqrt(1.0 / post_precision)
```

**Step 2: Run tests — some may need threshold adjustments**

```bash
pytest tests/test_runtime_estimator.py -v --timeout=120
```

Tests that assert confidence intervals may need minor assertion range updates due to tighter posteriors.

**Step 3: Commit**

```bash
git add ollama_queue/runtime_estimator.py
git commit -m "fix: use precision-weighted Bayesian posterior for runtime estimation

Previous formula (weighted average of variances) over-estimated
uncertainty when sample data was abundant. Now uses proper conjugate
normal-normal posterior: precision = 1/σ², posterior precision =
prior_precision + sample_precision."
```

---

### Task 16: Minor fixes (M1 + M2)

**Files:**
- Modify: `ollama_queue/performance_curve.py:111`

**Step 1: Return copy of points**

Line 111:
```python
            "points": list(self._points),
```

**Step 2: Commit**

```bash
git add ollama_queue/performance_curve.py
git commit -m "fix: return copy of points in get_curve_data"
```

---

### Batch 7 Quality Gate

```bash
pytest --timeout=120 -x -q
```

---

## Summary

| Batch | Tasks | Fixes | Impact |
|-------|-------|-------|--------|
| 1 | 1-2 | C1, C2, C5 | VRAM gate fully functional |
| 2 | 3-5 | C3, C6, I1, I4, I5, I6 | Silent failures eliminated |
| 3 | 6-7 | C8, I7, I8 | Metrics captured for all job types |
| 4 | 8-11 | C4, I12, I13, I14 | Scheduler race conditions + dead controls fixed |
| 5 | 12 | C7, I3 | Documentation matches reality |
| 6 | 13-14 | I9, I10 | Intelligence accuracy for sparse data |
| 7 | 15-16 | I2, M1 | Bayesian math corrected |
