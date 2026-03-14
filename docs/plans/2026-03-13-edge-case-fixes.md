# Edge Case Fixes — 27 Issues from Full Codebase Audit

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix all 27 edge cases identified in the 2026-03-13 codebase audit (`~/Documents/research/2026-03-13-ollama-queue-edge-case-audit.md`).

**Architecture:** Fixes are grouped into 10 batches by subsystem affinity and dependency order. Batch 1 (proxy streaming) is highest priority — 4 of 6 HIGH issues. Each batch is independently testable. All fixes are backwards-compatible (no schema changes, no API contract changes).

**Tech Stack:** Python 3.12, FastAPI, SQLite WAL, httpx, asyncio, Preact SPA

---

## Batch 1: Proxy Streaming & Sentinel Safety (HIGH — Issues #1, #2, #6)

### Task 1: Clear orphaned proxy sentinel on daemon restart

**Files:**
- Modify: `ollama_queue/daemon/loop.py:108-155` (`_recover_orphans`)
- Test: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
# In tests/test_daemon.py — add to existing orphan recovery tests

def test_recover_orphans_clears_proxy_sentinel(daemon_with_db):
    """Orphaned proxy sentinel (-1) must be cleared on restart."""
    daemon, db = daemon_with_db
    # Simulate crash-during-proxy: sentinel left in DB
    db.update_daemon_state(state="running", current_job_id=-1)

    daemon._recover_orphans()

    state = db.get_daemon_state()
    assert state["current_job_id"] is None, "Proxy sentinel should be cleared on restart"
    assert state["state"] == "idle" or state["current_job_id"] is None
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon.py::test_recover_orphans_clears_proxy_sentinel -xvs`
Expected: FAIL — sentinel remains -1

**Step 3: Write minimal implementation**

At the **end** of `_recover_orphans()` in `loop.py`, after the orphan loop, add:

```python
        # Clear orphaned proxy sentinel. If daemon crashed while a proxy held
        # the sentinel (-1), it persists and blocks all future proxy requests.
        with self.db._lock:
            conn = self.db._connect()
            conn.execute(
                "UPDATE daemon_state SET current_job_id = NULL "
                "WHERE id = 1 AND current_job_id = -1"
            )
            conn.commit()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon.py::test_recover_orphans_clears_proxy_sentinel -xvs`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/daemon/loop.py tests/test_daemon.py
git commit -m "fix: clear orphaned proxy sentinel on daemon restart (#6)"
```

---

### Task 2: Guarantee `release_fn()` in streaming proxy — handle client disconnect

**Files:**
- Modify: `ollama_queue/api/proxy.py:40-67` (`_iter_ndjson`), lines 261-298 (streaming path)
- Test: `tests/test_proxy.py`

**Step 1: Write the failing test**

```python
# In tests/test_proxy.py — add to streaming tests

import asyncio

async def test_iter_ndjson_releases_on_generator_close(mock_db):
    """release_fn must fire even if generator is closed mid-stream (client disconnect)."""
    released = {"called": False}

    def release():
        released["called"] = True

    class FakeResp:
        """Simulates an httpx streaming response that never sends done=true."""
        async def aiter_raw(self):
            yield b'{"response": "hello", "done": false}\n'
            # Client disconnects here — generator.close() called by Starlette
            await asyncio.sleep(999)  # never reached

    gen = _iter_ndjson(FakeResp(), release_fn=release)
    # Consume first chunk
    chunk = await gen.__anext__()
    assert b"hello" in chunk
    # Simulate client disconnect — close the generator
    await gen.aclose()

    assert released["called"], "release_fn must be called in finally block on generator close"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_proxy.py::test_iter_ndjson_releases_on_generator_close -xvs`
Expected: May PASS already (the `finally` block exists). If so, write a test for the httpx socket cleanup case instead.

**Step 3: Write implementation — add explicit socket cleanup to streaming path**

In `proxy.py`, modify the streaming path (lines 261-298) to wrap httpx resources in a cleanup guard:

```python
    # Replace the BackgroundTask cleanup with a more robust pattern:
    # Use an asyncio.Event to track whether _release was called inside
    # the generator, and force-call it in cleanup if it wasn't.

    _released = False
    _original_release = _release

    def _tracked_release():
        nonlocal _released
        _released = True
        _original_release()

    async def _cleanup_streaming_resources():
        """Guaranteed cleanup: close httpx resources + force-release if generator didn't."""
        try:
            await rp_resp.aclose()
        except Exception:
            _log.debug("rp_resp.aclose() failed during cleanup", exc_info=True)
        try:
            await async_client.aclose()
        except Exception:
            _log.debug("async_client.aclose() failed during cleanup", exc_info=True)
        if not _released:
            _log.warning("Streaming proxy release_fn not called by generator — forcing cleanup for job %d", job_id)
            _original_release()

    return StreamingResponse(
        _iter_ndjson(rp_resp, release_fn=_tracked_release),
        status_code=rp_resp.status_code,
        headers=headers,
        media_type="application/x-ndjson",
        background=BackgroundTask(_cleanup_streaming_resources),
    )
```

**Step 4: Run streaming proxy tests**

Run: `pytest tests/test_proxy.py -xvs -k streaming`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/api/proxy.py tests/test_proxy.py
git commit -m "fix: guarantee proxy claim release on client disconnect (#1, #2)"
```

---

## Batch 2: Daemon Concurrency (HIGH — Issues #3, #4)

### Task 3: Fix proxy admission gate mismatch — sync `_running` dict with proxy claims

**Files:**
- Modify: `ollama_queue/daemon/executor.py:217-329` (`_can_admit`)
- Test: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
def test_can_admit_blocks_when_proxy_claimed(daemon_with_db):
    """_can_admit must check DB for proxy sentinel, not just in-memory _running dict."""
    daemon, db = daemon_with_db
    # Simulate proxy claiming a slot (sentinel in DB but nothing in _running)
    db.update_daemon_state(state="running", current_job_id=-1)

    job = {"id": 1, "model": "qwen2.5:7b", "source": "test", "priority": 5}
    settings = db.get_all_settings()

    result = daemon._can_admit(job, settings)
    assert result is False, "_can_admit must block when proxy sentinel is active"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon.py::test_can_admit_blocks_when_proxy_claimed -xvs`
Expected: FAIL — returns True because `_running` dict is empty

**Step 3: Write minimal implementation**

Add a proxy sentinel check at the top of `_can_admit()` in `executor.py`, before the profile-based checks:

```python
    def _can_admit(self, job: dict, settings: dict | None = None) -> bool:
        # ... existing docstring ...

        # Check for in-flight proxy claim (sentinel -1 in daemon_state).
        # Proxy jobs aren't in the in-memory _running dict, only in the DB.
        state = self.db.get_daemon_state()
        if state.get("current_job_id") == -1:
            _log.debug("_can_admit: job #%d blocked — proxy claim in-flight (sentinel -1)", job["id"])
            return False

        profile = self._ollama_models.classify(job.get("model") or "")["resource_profile"]
        # ... rest of method unchanged ...
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon.py::test_can_admit_blocks_when_proxy_claimed -xvs`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/daemon/executor.py tests/test_daemon.py
git commit -m "fix: _can_admit checks proxy sentinel to prevent concurrent Ollama access (#3)"
```

---

### Task 4: Add timeout to `proc.wait()` to prevent worker deadlock

**Files:**
- Modify: `ollama_queue/daemon/executor.py:371`
- Test: `tests/test_daemon.py`

**Step 1: Write the failing test**

```python
def test_run_job_proc_wait_has_timeout(daemon_with_db, monkeypatch):
    """proc.wait() must not block forever if subprocess hangs after pipe drain."""
    daemon, db = daemon_with_db
    job = db.submit_job(command="sleep 999", model="", priority=5, timeout=600, source="test")
    job_row = db.get_job(job)

    class HangingProc:
        pid = 12345
        returncode = None
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE
        def poll(self):
            return 0  # drain loop sees exit
        def wait(self, timeout=None):
            if timeout is None:
                raise AssertionError("proc.wait() called without timeout — would deadlock")
            return 0
        def kill(self):
            self.returncode = -9

    monkeypatch.setattr("ollama_queue.daemon.executor.subprocess.Popen", lambda *a, **k: HangingProc())
    monkeypatch.setattr("ollama_queue.daemon.executor._drain_pipes_with_tracking",
                        lambda proc, jid, sd: (b"", b""))

    job_row["resource_profile"] = "ollama"
    # Should not raise AssertionError
    daemon._run_job(dict(job_row))
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_daemon.py::test_run_job_proc_wait_has_timeout -xvs`
Expected: FAIL with `AssertionError: proc.wait() called without timeout`

**Step 3: Write minimal implementation**

In `executor.py:371`, replace:

```python
                proc.wait()  # ensure returncode is set
```

With:

```python
                try:
                    proc.wait(timeout=30)
                except _TimeoutExpired:
                    _log.warning("Job #%d proc.wait() timed out after drain — sending SIGKILL", job["id"])
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    proc.wait(timeout=5)  # reap zombie
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_daemon.py::test_run_job_proc_wait_has_timeout -xvs`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/daemon/executor.py tests/test_daemon.py
git commit -m "fix: add timeout to proc.wait() to prevent worker thread deadlock (#4)"
```

---

## Batch 3: Health Monitor Robustness (HIGH+MEDIUM — Issues #5, #13)

### Task 5: Add max-pause-duration escape hatch to health hysteresis

**Files:**
- Modify: `ollama_queue/sensing/health.py:182-263` (`evaluate`)
- Modify: `ollama_queue/db/schema.py` (add default setting)
- Test: `tests/test_health.py`

**Step 1: Write the failing test**

```python
def test_evaluate_escapes_stuck_pause_after_max_duration(health_monitor):
    """If paused longer than max_pause_duration_seconds, force resume."""
    snap = {"ram_pct": 80.0, "swap_pct": 10.0, "load_avg": 2.0, "cpu_count": 8,
            "vram_pct": 50.0, "ollama_model": None, "ollama_loaded_models": []}
    settings = {
        "ram_pause_pct": 85, "ram_resume_pct": 75,  # RAM at 80 — in hysteresis band
        "swap_pause_pct": 80, "swap_resume_pct": 60,
        "load_pause_multiplier": 3.0, "load_resume_multiplier": 2.0,
        "yield_to_interactive": False,
        "max_pause_duration_seconds": 300,  # 5 min escape hatch
    }
    # Paused for 10 minutes — should force resume despite RAM in hysteresis band
    result = health_monitor.evaluate(snap, settings, currently_paused=True,
                                      paused_since=time.time() - 600)
    assert result["should_pause"] is False, "Should escape stuck pause after max_pause_duration"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_health.py::test_evaluate_escapes_stuck_pause_after_max_duration -xvs`
Expected: FAIL — returns `should_pause=True`

**Step 3: Write minimal implementation**

Add a `paused_since` parameter to `evaluate()` and an escape hatch check:

```python
    def evaluate(
        self,
        snap: dict,
        settings: dict,
        currently_paused: bool,
        queued_model: str | None = None,
        recent_job_models: set[str] | None = None,
        paused_since: float | None = None,
    ) -> dict:
        reasons: list[str] = []

        # --- Escape hatch: force resume if stuck too long ---
        max_pause = settings.get("max_pause_duration_seconds")
        if currently_paused and max_pause and paused_since:
            pause_duration = time.time() - paused_since
            if pause_duration >= float(max_pause):
                return {
                    "should_pause": False,
                    "should_yield": False,
                    "reason": f"Force resume: paused {pause_duration:.0f}s >= max {max_pause}s",
                }

        # ... rest of method unchanged ...
```

Update the call site in `loop.py:408` to pass `paused_since`:

```python
        evaluation = self.health.evaluate(
            snap,
            settings,
            currently_paused=currently_paused,
            queued_model=job["model"],
            recent_job_models=recent_models_snapshot,
            paused_since=state.get("paused_since"),
        )
```

Add default setting in `schema.py` seed data:

```python
("max_pause_duration_seconds", "600"),
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_health.py::test_evaluate_escapes_stuck_pause_after_max_duration -xvs`
Expected: PASS

**Step 5: Run full health test suite**

Run: `pytest tests/test_health.py -xvs`
Expected: All PASS (no regressions)

**Step 6: Commit**

```bash
git add ollama_queue/sensing/health.py ollama_queue/daemon/loop.py ollama_queue/db/schema.py tests/test_health.py
git commit -m "fix: add max_pause_duration escape hatch to prevent indefinite health pause (#5)"
```

---

### Task 6: SystemSnapshot marks "unknown" when Ollama is down

**Files:**
- Modify: `ollama_queue/sensing/system_snapshot.py`
- Test: `tests/test_system_snapshot.py`

**Step 1: Write the failing test**

```python
def test_snapshot_marks_vram_unknown_on_failure():
    """Snapshot must distinguish 'VRAM is 0%' from 'VRAM is unknown'."""
    class FailingMonitor:
        def get_ram_pct(self): return 50.0
        def get_swap_pct(self): return 10.0
        def get_load_avg(self): return 1.0
        def get_vram_pct(self): raise OSError("nvidia-smi not found")
        def get_cpu_count(self): return 8

    snap = SystemSnapshot.capture(health_monitor=FailingMonitor())
    assert snap.vram_known is False, "vram_known must be False when VRAM read fails"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_system_snapshot.py::test_snapshot_marks_vram_unknown_on_failure -xvs`
Expected: FAIL — `SystemSnapshot` has no `vram_known` attribute

**Step 3: Write minimal implementation**

Add `vram_known: bool = True` field to `SystemSnapshot` dataclass. In `capture()`, set `vram_known = False` when the VRAM read fails or returns None:

```python
@dataclasses.dataclass
class SystemSnapshot:
    timestamp: float = 0.0
    ram_used_pct: float = 0.0
    swap_used_pct: float = 0.0
    load_avg: float = 0.0
    vram_used_pct: float = 0.0
    vram_known: bool = True  # NEW: False if VRAM reading failed
    cpu_count: int = 1
    # ...

    @classmethod
    def capture(cls, health_monitor=None, ...):
        snap = cls(timestamp=time.time())
        if health_monitor is not None:
            # ... existing try/except for ram, swap, load ...
            try:
                vram = health_monitor.get_vram_pct()
                if vram is not None:
                    snap.vram_used_pct = vram
                else:
                    snap.vram_known = False
            except Exception:
                snap.vram_known = False
                _log.debug("VRAM read failed — marking as unknown", exc_info=True)
        return snap
```

Update `slot_scoring.py` to check `vram_known` before using VRAM data for scoring.

**Step 4: Run tests**

Run: `pytest tests/test_system_snapshot.py -xvs`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/sensing/system_snapshot.py ollama_queue/scheduling/slot_scoring.py tests/test_system_snapshot.py
git commit -m "fix: SystemSnapshot tracks vram_known to avoid phantom-zero scheduling (#13)"
```

---

## Batch 4: Eval Pipeline Safety (MEDIUM — Issues #7, #8, #18, #22, #27)

### Task 7: Add post-HTTP cancellation re-check in eval generate and judge

**Files:**
- Modify: `ollama_queue/eval/generate.py` (after `_generate_one()` returns, ~line 550)
- Modify: `ollama_queue/eval/judge.py` (after `_judge_one_target()` returns, ~line 725)
- Test: `tests/test_eval_generate.py`, `tests/test_eval_judge.py`

**Step 1: Write the failing test**

```python
def test_generate_stops_after_http_call_if_cancelled(eval_db, monkeypatch):
    """Generate phase must re-check run status AFTER each HTTP call, not just before."""
    call_count = {"n": 0}
    original_generate_one = _generate_one

    def counting_generate_one(*args, **kwargs):
        call_count["n"] += 1
        result = original_generate_one(*args, **kwargs)
        # Cancel the run after the first successful HTTP call
        if call_count["n"] == 1:
            eval_db.update_eval_run(run_id, status="cancelled", completed_at=time.time())
        return result

    monkeypatch.setattr("ollama_queue.eval.generate._generate_one", counting_generate_one)

    # Run with 5 items — should stop after 1, not continue to 5
    run_eval_generate(eval_db, run_id, ...)
    assert call_count["n"] == 1, f"Expected 1 call but got {call_count['n']} — cancel not checked after HTTP"
```

**Step 2: Run test to verify it fails**

Expected: FAIL — `call_count["n"]` is 5 (all items processed despite cancellation)

**Step 3: Write minimal implementation**

In `generate.py`, after the `_generate_one()` call returns (around line 550), add:

```python
            # Post-HTTP cancellation re-check: the blocking proxy call may have taken
            # 30+ seconds, during which the run could have been cancelled.
            _current = _eng.get_eval_run(db, run_id)
            if _current is None or _current.get("status") in ("failed", "cancelled"):
                _log.info("run_eval_generate: cancelled during HTTP call for run_id=%d", run_id)
                return
```

Apply the same pattern in `judge.py` after `_judge_one_target()` returns.

**Step 4: Run tests**

Run: `pytest tests/test_eval_generate.py tests/test_eval_judge.py -xvs`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/eval/generate.py ollama_queue/eval/judge.py tests/test_eval_generate.py tests/test_eval_judge.py
git commit -m "fix: eval generate/judge re-check cancellation after each HTTP call (#7)"
```

---

### Task 8: Mark eval run failed on background thread exception

**Files:**
- Modify: `ollama_queue/api/eval_runs.py:170-176`
- Test: `tests/test_eval_api.py`

**Step 1: Write the failing test**

```python
def test_eval_run_marked_failed_on_background_exception(client, eval_db, monkeypatch):
    """Background thread exception must update run status to 'failed'."""
    def exploding_session(run_id, db):
        raise RuntimeError("unexpected crash")

    monkeypatch.setattr("ollama_queue.api.eval_runs.run_eval_session", exploding_session)

    resp = client.post("/api/eval/runs", json={...})
    run_id = resp.json()["run_id"]
    time.sleep(0.5)  # let background thread run

    run = eval_db.get_eval_run(run_id)
    assert run["status"] == "failed", "Background exception must mark run as failed"
    assert "unexpected crash" in (run.get("error") or "")
```

**Step 2: Run test to verify it fails**

Expected: FAIL — status remains "queued" (exception logged but status not updated)

**Step 3: Write minimal implementation**

In `eval_runs.py:170-176`, modify the background function:

```python
    def _run_session_in_background() -> None:
        try:
            run_eval_session(_captured_run_id, db)
        except Exception as exc:
            _log.exception("run_eval_session failed for run_id=%d", _captured_run_id)
            try:
                from datetime import UTC, datetime
                update_eval_run(
                    db, _captured_run_id,
                    status="failed",
                    error=f"background thread crash: {type(exc).__name__}: {exc}",
                    completed_at=datetime.now(UTC).isoformat(),
                )
            except Exception:
                _log.exception("Failed to mark run %d as failed after background crash", _captured_run_id)
```

**Step 4: Run test**

Run: `pytest tests/test_eval_api.py::test_eval_run_marked_failed_on_background_exception -xvs`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/api/eval_runs.py tests/test_eval_api.py
git commit -m "fix: mark eval run failed on background thread exception (#27)"
```

---

### Task 9: First-ever eval run requires higher threshold for auto-promote

**Files:**
- Modify: `ollama_queue/eval/promote.py:189-229` (Gate 2)
- Test: `tests/test_eval_promote.py`

**Step 1: Write the failing test**

```python
def test_auto_promote_skips_first_run_without_production_baseline(eval_db):
    """First-ever eval must not auto-promote without a production baseline to compare against."""
    # No production variant set — first run ever
    run = create_test_run(eval_db, winner_f1=0.50)  # mediocre but above default 0.75? No.
    # Actually: set threshold to 0.40 so 0.50 passes gate 1
    eval_db.set_setting("eval.f1_threshold", "0.40")

    check_auto_promote(eval_db, run["id"])

    # Should NOT have promoted because there's no production baseline
    variants = eval_db.list_eval_variants()
    production = [v for v in variants if v.get("is_production")]
    assert len(production) == 0, "First run should require explicit manual promote"
```

**Step 2: Run test to verify it fails**

Expected: FAIL — variant is auto-promoted (gate 2 skipped when no production baseline)

**Step 3: Write minimal implementation**

In `promote.py`, after the Gate 2 production lookup (around line 206), add:

```python
    if prod_row is None:
        # No production variant exists — this is the first eval ever.
        # Require explicit manual promotion for the first run to establish a baseline.
        _log.info(
            "check_auto_promote: run %d — no production baseline exists. "
            "First run requires manual promote to establish baseline.",
            run_id,
        )
        return
```

**Step 4: Run test**

Run: `pytest tests/test_eval_promote.py::test_auto_promote_skips_first_run_without_production_baseline -xvs`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/eval/promote.py tests/test_eval_promote.py
git commit -m "fix: require manual promote for first-ever eval run — no auto-promote without baseline (#8)"
```

---

### Task 10: Track judge parse failure rate

**Files:**
- Modify: `ollama_queue/eval/judge.py` (in `_judge_one_target`)
- Modify: `ollama_queue/eval/engine.py` (aggregate after judge phase)
- Test: `tests/test_eval_judge.py`

**Step 1: Write the failing test**

```python
def test_judge_parse_failures_counted_in_run(eval_db, monkeypatch):
    """Parse failures from LLM judge must be counted and stored on the run row."""
    # Mock _call_proxy to return unparseable responses
    monkeypatch.setattr(..., lambda *a, **k: "This is not JSON at all")

    run_eval_judge(eval_db, run_id, ...)

    run = eval_db.get_eval_run(run_id)
    # The run should record how many judge calls failed to parse
    assert run.get("judge_parse_failures", 0) > 0
```

**Step 2: Implementation**

Add a `parse_failures` counter in `run_eval_judge()`. After the judge loop, store it on the run:

```python
    parse_failures = 0
    # ... in the judge loop, after parse_judge_response returns:
    if scored.get("error") == "parse_failed":
        parse_failures += 1

    # After loop:
    if parse_failures > 0:
        _log.warning("run_eval_judge: %d/%d judge responses failed to parse for run %d",
                      parse_failures, total_judged, run_id)
    _eng.update_eval_run(db, run_id, judge_parse_failures=parse_failures)
```

Add `judge_parse_failures INTEGER DEFAULT 0` column to `eval_runs` via migration.

**Step 3: Commit**

```bash
git add ollama_queue/eval/judge.py ollama_queue/eval/engine.py ollama_queue/db/schema.py tests/test_eval_judge.py
git commit -m "fix: track and surface judge parse failure count per eval run (#22)"
```

---

## Batch 5: Stall Detection (MEDIUM — Issue #12)

### Task 11: Re-check posterior before stall kill

**Files:**
- Modify: `ollama_queue/daemon/executor.py:603-613`
- Test: `tests/test_stall.py`

**Step 1: Write the failing test**

```python
def test_stall_kill_rechecks_posterior_before_kill(daemon_with_db, monkeypatch):
    """Job should not be killed if posterior dropped below threshold since stall was first detected."""
    daemon, db = daemon_with_db
    # Job was flagged stalled 120s ago (grace=60 exceeded), but posterior now recovered
    posteriors = iter([0.9, 0.3])  # first call: flagged, second call: recovered
    monkeypatch.setattr(daemon.stall_detector, "compute_posterior",
                        lambda *a, **k: (next(posteriors), {}))

    killed = {"called": False}
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.__setitem__("called", True))

    # ... set up job with stall_detected_at 120s ago ...
    daemon._check_stalled_jobs(time.time())
    assert killed["called"] is False, "Should not kill when posterior recovered below threshold"
```

**Step 2: Write minimal implementation**

In `executor.py:603-613`, re-check posterior before kill:

```python
                elif action == "kill":
                    stall_age = now - stall_detected_at
                    if stall_age >= grace:
                        # Re-check: posterior may have recovered since stall was first detected
                        if posterior < threshold:
                            _log.info(
                                "Job #%d stall recovered: posterior=%.2f < threshold=%.2f — clearing stall",
                                job_id, posterior, threshold,
                            )
                            self.db.clear_stall_detected(job_id)
                            continue
                        _log.warning(
                            "Killing stalled job #%d (stall_age=%.0fs posterior=%.2f)",
                            job_id, stall_age, posterior,
                        )
                        with contextlib.suppress(ProcessLookupError, PermissionError):
                            os.kill(pid, _signal.SIGTERM)
```

**Note:** Need to add `clear_stall_detected()` to `db/jobs.py`:

```python
    def clear_stall_detected(self, job_id: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("UPDATE jobs SET stall_detected_at = NULL WHERE id = ?", (job_id,))
            conn.commit()
```

**Step 3: Commit**

```bash
git add ollama_queue/daemon/executor.py ollama_queue/db/jobs.py tests/test_stall.py
git commit -m "fix: re-check posterior before stall kill to avoid false-positive kills (#12)"
```

---

## Batch 6: DLQ & Scheduling (MEDIUM — Issues #9, #10, #25)

### Task 12: Fix DLQ chronic failure race — atomic threshold check

**Files:**
- Modify: `ollama_queue/scheduling/dlq_scheduler.py:71-95`
- Test: `tests/test_dlq_scheduler.py`

**Step 1: Write the failing test**

```python
def test_dlq_chronic_check_atomic_with_reschedule(dlq_scheduler, db):
    """Threshold check and reschedule must be atomic to prevent double-reschedule at boundary."""
    # Entry at count=2, threshold=3 — exactly one reschedule remaining
    entry = create_dlq_entry(db, auto_reschedule_count=2)
    db.set_setting("dlq.chronic_failure_threshold", "3")

    # Simulate concurrent sweep: both read count=2 before either increments
    # After fix, the second attempt should be blocked by the lock
    results = dlq_scheduler._do_sweep()
    assert len(results) == 1, "Only one reschedule should succeed at threshold boundary"
```

**Step 2: Write implementation**

Move the chronic threshold check **inside** `_sweep_lock` and re-read the count after acquiring:

```python
    def _do_sweep(self):
        with self._sweep_lock:
            raw_threshold = self.db.get_setting("dlq.chronic_failure_threshold")
            chronic_threshold = int(raw_threshold) if raw_threshold is not None else 3

            entries = self._get_unscheduled_entries()
            results = []
            for entry in sorted(entries, key=lambda e: e.get("priority", 0)):
                # Re-read count inside lock to prevent race at boundary
                fresh = self.db.get_dlq_entry(entry["id"])
                if fresh and (fresh.get("auto_reschedule_count") or 0) >= chronic_threshold:
                    continue
                # ... rest of reschedule logic ...
```

**Step 3: Commit**

```bash
git add ollama_queue/scheduling/dlq_scheduler.py tests/test_dlq_scheduler.py
git commit -m "fix: DLQ chronic threshold check is atomic with reschedule to prevent race (#9)"
```

---

### Task 13: Validate cron expression at submission time

**Files:**
- Modify: `ollama_queue/db/schedule.py` (in `add_recurring_job`)
- Test: `tests/test_schedule.py`

**Step 1: Write the failing test**

```python
def test_add_recurring_job_rejects_invalid_cron(db):
    """Invalid cron expressions must raise ValueError at submission, not at promotion."""
    with pytest.raises(ValueError, match="Invalid cron expression"):
        db.add_recurring_job(name="bad", command="echo hi", cron_expression="invalid cron")
```

**Step 2: Write implementation**

In `add_recurring_job()`, validate before INSERT:

```python
    def add_recurring_job(self, ..., cron_expression=None, ...):
        if cron_expression:
            import datetime
            try:
                from croniter import croniter
                croniter(cron_expression, datetime.datetime.now())
            except (ValueError, KeyError) as e:
                raise ValueError(f"Invalid cron expression '{cron_expression}': {e}") from e
        # ... existing INSERT logic ...
```

Update the API endpoint to catch `ValueError` and return 400.

**Step 3: Commit**

```bash
git add ollama_queue/db/schedule.py ollama_queue/api/schedule.py tests/test_schedule.py
git commit -m "fix: validate cron expression at submission time, not promotion (#25)"
```

---

### Task 14: Add timezone-aware cron scheduling

**Files:**
- Modify: `ollama_queue/db/schedule.py:42-48`
- Modify: `ollama_queue/scheduling/scheduler.py:91-99`
- Test: `tests/test_scheduler.py`

**Step 1: Write the failing test**

```python
def test_cron_next_run_uses_local_timezone(db):
    """Cron next_run must use timezone-aware datetime to handle DST correctly."""
    import datetime
    # Manually compute next run and verify it matches timezone-aware calculation
    db.add_recurring_job(name="test", command="echo hi", cron_expression="0 2 * * *")
    job = db.list_recurring_jobs()[0]
    next_run = job["next_run"]

    # The next_run should be timezone-aware — verify by comparing to a known local calculation
    from zoneinfo import ZoneInfo
    local_tz = ZoneInfo("localtime") if hasattr(ZoneInfo, "__call__") else datetime.timezone.utc
    local_now = datetime.datetime.now(local_tz)
    assert next_run > 0, "next_run must be a positive timestamp"
```

**Step 2: Write implementation**

Replace all `datetime.datetime.fromtimestamp(now)` with timezone-aware versions:

```python
import datetime
from zoneinfo import ZoneInfo

def _local_dt(ts: float) -> datetime.datetime:
    """Convert unix timestamp to timezone-aware local datetime for cron evaluation."""
    try:
        return datetime.datetime.fromtimestamp(ts, tz=ZoneInfo("localtime"))
    except Exception:
        return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
```

Apply in all 3 locations: `db/schedule.py:42`, `db/schedule.py:120`, `scheduling/scheduler.py:91`.

**Step 3: Commit**

```bash
git add ollama_queue/db/schedule.py ollama_queue/scheduling/scheduler.py tests/test_scheduler.py
git commit -m "fix: use timezone-aware datetime for cron scheduling to handle DST (#10)"
```

---

## Batch 7: Input Validation & API Bounds (LOW — Issues #19, #20)

### Task 15: Add priority bounds and query limit caps

**Files:**
- Modify: `ollama_queue/api/jobs.py:123-132` (set_priority)
- Modify: `ollama_queue/api/schedule.py:200, 215` (events, suggest)
- Test: `tests/test_api_jobs.py`, `tests/test_api_schedule.py`

**Step 1: Write tests**

```python
def test_set_priority_rejects_out_of_range(client):
    """Priority must be 0-10."""
    resp = client.put("/api/queue/1/priority", json={"priority": -5})
    assert resp.status_code == 400

    resp = client.put("/api/queue/1/priority", json={"priority": 99})
    assert resp.status_code == 400

def test_schedule_events_caps_limit(client):
    """Limit must be capped at 1000."""
    resp = client.get("/api/schedule/events?limit=999999")
    assert resp.status_code == 200  # succeeds but capped internally
```

**Step 2: Write implementation**

In `jobs.py:126-128`:
```python
    if not isinstance(priority, int) or priority < 0 or priority > 10:
        raise HTTPException(status_code=400, detail="priority must be an integer 0-10")
```

In `schedule.py:200`:
```python
def get_schedule_events(limit: int = 100):
    limit = max(1, min(limit, 1000))
    # ...
```

In `schedule.py:215`:
```python
def suggest_schedule_time(priority: int = 5, top_n: int = 3):
    priority = max(0, min(priority, 10))
    top_n = max(1, min(top_n, 20))
    # ...
```

**Step 3: Commit**

```bash
git add ollama_queue/api/jobs.py ollama_queue/api/schedule.py tests/test_api_jobs.py tests/test_api_schedule.py
git commit -m "fix: add bounds checking for priority (0-10) and query limits (cap 1000) (#19)"
```

---

### Task 16: Return 404 for zero-match batch operations

**Files:**
- Modify: `ollama_queue/api/schedule.py:229-265`
- Test: `tests/test_api_schedule.py`

**Step 1: Write test**

```python
def test_batch_toggle_returns_404_for_unknown_tag(client, db):
    """Batch toggle with non-existent tag should return 404."""
    resp = client.post("/api/schedule/batch-toggle", json={"tag": "nonexistent", "enabled": True})
    assert resp.status_code == 404
```

**Step 2: Write implementation**

In `schedule.py:237-240`:
```python
    matched = [rj for rj in jobs if rj.get("tag") == tag]
    if not matched:
        raise HTTPException(status_code=404, detail=f"No recurring jobs found with tag '{tag}'")
```

Same pattern for `batch_run_schedule`.

**Step 3: Commit**

```bash
git add ollama_queue/api/schedule.py tests/test_api_schedule.py
git commit -m "fix: batch operations return 404 when no jobs match tag (#20)"
```

---

## Batch 8: Models & Estimation (MEDIUM — Issues #11, #14, #15, #21)

### Task 17: Improve VRAM fallback — log warning for unknown models

**Files:**
- Modify: `ollama_queue/models/client.py:229-257`
- Test: `tests/test_models.py`

**Step 1: Write test**

```python
def test_estimate_vram_logs_warning_for_unknown_model(caplog):
    """Unknown models falling back to 4GB default must log a warning."""
    models = OllamaModels()
    with caplog.at_level(logging.WARNING):
        result = models.estimate_vram_mb("totally-unknown-model", db=mock_db)
    assert result == 4000  # fallback
    assert "falling back to default" in caplog.text.lower()
```

**Step 2: Write implementation**

At the fallback return in `client.py:257`:
```python
        _log.warning(
            "VRAM estimate for model '%s' using 4000MB default — "
            "model not in registry or local list. Consider adding to model_registry.",
            model,
        )
        return 4000
```

**Step 3: Commit**

```bash
git add ollama_queue/models/client.py tests/test_models.py
git commit -m "fix: log warning when VRAM estimation falls back to 4GB default (#11)"
```

---

### Task 18: Guard RuntimeEstimator against negative durations

**Files:**
- Modify: `ollama_queue/models/runtime_estimator.py:77-105`
- Test: `tests/test_runtime_estimator.py`

**Step 1: Write test**

```python
def test_negative_durations_excluded_not_clamped(estimator):
    """Negative durations from clock skew must be excluded, not clamped to 0.1."""
    durations = [30.0, 45.0, -5.0, 60.0, -1.0]
    result = estimator._compute_posterior("test-model", durations)
    # Only 3 valid durations should be used (30, 45, 60), not 5 with 2 clamped
    assert result["sample_count"] == 3
```

**Step 2: Write implementation**

Replace the clamping strategy with exclusion:

```python
    if durations:
        valid = [d for d in durations if d > 0]
        excluded = len(durations) - len(valid)
        if excluded:
            logger.warning("Excluded %d non-positive durations for model=%r", excluded, model)
        if not valid:
            return self._prior(model)
        log_durations = [math.log(d) for d in valid]
```

**Step 3: Commit**

```bash
git add ollama_queue/models/runtime_estimator.py tests/test_runtime_estimator.py
git commit -m "fix: exclude negative durations instead of clamping to prevent estimate corruption (#14)"
```

---

### Task 19: Fix PerformanceCurve overflow on degenerate fits

**Files:**
- Modify: `ollama_queue/models/performance_curve.py:44-90`
- Test: `tests/test_performance_curve.py`

**Step 1: Write test**

```python
def test_predict_tok_per_min_no_overflow_on_bad_fit(curve):
    """Prediction must not return inf on degenerate regression."""
    # Two points with nearly identical x values
    stats = [
        {"model_size_gb": 7.0, "avg_tok_per_min": 100},
        {"model_size_gb": 7.001, "avg_tok_per_min": 200},
    ]
    curve.fit(stats)
    result = curve.predict_tok_per_min(14.0)
    assert result is not None
    assert math.isfinite(result), f"Prediction must be finite, got {result}"
    assert result > 0
```

**Step 2: Write implementation**

Add a guard after `_linear_regression`:

```python
    if abs(self._tok_slope) > 10.0:
        _log.warning("PerformanceCurve: degenerate fit (slope=%.2f) — using single-point fallback", self._tok_slope)
        # Fall back to single-point logic with hardcoded slope
        self._tok_slope = -0.7
        self._tok_intercept = math.log(valid_tok[0]["avg_tok_per_min"]) - self._tok_slope * math.log(valid_tok[0]["model_size_gb"])
```

Also cap `predict_tok_per_min` output:

```python
    def predict_tok_per_min(self, model_size_gb: float) -> float | None:
        if self._tok_slope is None:
            return None
        log_rate = self._tok_slope * math.log(max(model_size_gb, 0.01)) + self._tok_intercept
        return min(math.exp(log_rate), 100_000)  # cap at 100k tok/min — no GPU is faster
```

**Step 3: Commit**

```bash
git add ollama_queue/models/performance_curve.py tests/test_performance_curve.py
git commit -m "fix: cap PerformanceCurve predictions to prevent overflow on degenerate fits (#15)"
```

---

### Task 20: Reduce model list cache TTL and add manual invalidation

**Files:**
- Modify: `ollama_queue/models/client.py:61-82`
- Test: `tests/test_models.py`

**Step 1: Write test**

```python
def test_invalidate_cache_returns_fresh_data(models, monkeypatch):
    """After invalidation, next list_local() must fetch fresh data."""
    monkeypatch.setattr(models, "_fetch_list_local", lambda: [{"name": "old"}])
    models.list_local()  # populate cache

    monkeypatch.setattr(models, "_fetch_list_local", lambda: [{"name": "new"}])
    OllamaModels._invalidate_list_cache()

    result = models.list_local()
    assert result[0]["name"] == "new"
```

**Step 2: Write implementation**

Reduce TTL from 60s to 15s (less stale, still avoids hammering Ollama):

```python
_LIST_LOCAL_TTL = 15.0  # was 60.0
```

**Step 3: Commit**

```bash
git add ollama_queue/models/client.py tests/test_models.py
git commit -m "fix: reduce model list cache TTL from 60s to 15s (#21)"
```

---

## Batch 9: DB Resilience (MEDIUM — Issue #16)

### Task 21: Add SQLITE_BUSY retry logic for write operations

**Files:**
- Modify: `ollama_queue/db/__init__.py`
- Test: `tests/test_db.py`

**Step 1: Write test**

```python
def test_write_retries_on_sqlite_busy(db, monkeypatch):
    """Write operations must retry once on SQLITE_BUSY instead of failing."""
    import sqlite3
    call_count = {"n": 0}
    original_execute = db._connect().execute

    def flaky_execute(sql, params=()):
        call_count["n"] += 1
        if call_count["n"] == 1 and "INSERT" in sql:
            raise sqlite3.OperationalError("database is locked")
        return original_execute(sql, params)

    # ... monkeypatch and test ...
```

**Step 2: Write implementation**

Add a retry wrapper in `db/__init__.py`:

```python
import sqlite3

def _retry_on_busy(fn, max_retries=2, backoff=0.1):
    """Retry a DB operation on SQLITE_BUSY (WAL checkpoint contention)."""
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_retries:
                _log.warning("SQLITE_BUSY on attempt %d/%d — retrying after %.1fs",
                             attempt + 1, max_retries, backoff)
                time.sleep(backoff * (2 ** attempt))
            else:
                raise
```

Apply to `log_health()`, `complete_job()`, `submit_job()` — the most write-heavy methods.

**Step 3: Commit**

```bash
git add ollama_queue/db/__init__.py tests/test_db.py
git commit -m "fix: retry write operations on SQLITE_BUSY from WAL checkpoint contention (#16)"
```

---

## Batch 10: Config Safety & Cleanup (LOW — Issues #17, #23, #24, #26)

### Task 22: Add mtime check before patching consumer config

**Files:**
- Modify: `ollama_queue/config/patcher.py:85-108`
- Test: `tests/test_patcher.py`

**Step 1: Write test**

```python
def test_patch_rejects_if_file_modified_since_scan(patcher, tmp_path):
    """Patching must abort if config file was modified after scan."""
    config_file = tmp_path / "service.conf"
    config_file.write_text("original")
    scan_result = patcher.scan_file(str(config_file))

    # Simulate admin editing the file after scan
    time.sleep(0.1)
    config_file.write_text("admin changed this")

    with pytest.raises(ValueError, match="modified since scan"):
        patcher.patch_consumer(scan_result)
```

**Step 2: Write implementation**

In `patcher.py`, store `mtime` during scan, check before patch:

```python
    def scan_file(self, path: str) -> dict:
        result = self._do_scan(path)
        result["scanned_mtime"] = os.path.getmtime(path)
        return result

    def patch_consumer(self, scan_result: dict) -> dict:
        path = scan_result["path"]
        current_mtime = os.path.getmtime(path)
        if scan_result.get("scanned_mtime") and current_mtime != scan_result["scanned_mtime"]:
            raise ValueError(
                f"Config file '{path}' modified since scan "
                f"(scanned={scan_result['scanned_mtime']}, current={current_mtime}). "
                f"Re-scan before patching."
            )
        # ... existing patch logic ...
```

**Step 3: Commit**

```bash
git add ollama_queue/config/patcher.py tests/test_patcher.py
git commit -m "fix: reject consumer config patch if file modified since scan (TOCTOU guard) (#17)"
```

---

### Task 23: Clean up partial eval results on orphan recovery

**Files:**
- Modify: `ollama_queue/daemon/loop.py:108-131` (orphan recovery for eval)
- Test: `tests/test_daemon.py`

**Step 1: Write test**

```python
def test_orphan_recovery_cleans_partial_eval_results(daemon_with_db):
    """Orphaned eval runs should have their partial results annotated."""
    daemon, db = daemon_with_db
    # Create a run with partial results
    run_id = db.create_eval_run(...)
    db.insert_eval_result(run_id, ...)  # partial result

    daemon._recover_orphans()

    run = db.get_eval_run(run_id)
    assert run["status"] == "failed"
    assert "daemon restart" in run["error"]
```

This test should already pass (orphan recovery marks status=failed). The improvement is adding a `partial_results` annotation to the error so operators know results exist:

```python
            for row in stuck:
                # Count partial results for the error message
                result_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM eval_results WHERE run_id = ?",
                    (row["id"],)
                ).fetchone()["cnt"]
                error_msg = "daemon restart: session abandoned"
                if result_count > 0:
                    error_msg += f" ({result_count} partial results remain)"
                conn.execute(
                    "UPDATE eval_runs SET status='failed', error=?,"
                    " completed_at=? WHERE id=?",
                    (error_msg, now, row["id"]),
                )
```

**Step 2: Commit**

```bash
git add ollama_queue/daemon/loop.py tests/test_daemon.py
git commit -m "fix: annotate orphaned eval runs with partial result count (#23)"
```

---

### Task 24: Log warning for NULL-PID orphan recovery

**Files:**
- Modify: `ollama_queue/daemon/loop.py:148-155`
- Test: `tests/test_daemon.py`

**Step 1: Write test**

```python
def test_orphan_recovery_warns_on_null_pid(daemon_with_db, caplog):
    """Orphans with NULL PID must log a warning about potential duplicate execution."""
    daemon, db = daemon_with_db
    # Create a running job with no PID (crash between spawn and PID write)
    job_id = db.submit_job(command="echo hi", model="", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    # PID is NULL — crash happened before PID was recorded

    with caplog.at_level(logging.WARNING):
        daemon._recover_orphans()

    assert "null pid" in caplog.text.lower() or "no pid" in caplog.text.lower()
```

**Step 2: Write implementation**

```python
            if job.get("pid") and job["pid"] > 0:
                try:
                    os.kill(job["pid"], _signal.SIGTERM)
                    _log.info("Sent SIGTERM to orphaned pid=%d (job #%d)", job["pid"], job["id"])
                except ProcessLookupError:
                    pass
            else:
                _log.warning(
                    "Orphan job #%d has no PID — process may still be running. "
                    "Resetting to pending; check for duplicate execution.",
                    job["id"],
                )
            self.db.reset_job_to_pending(job["id"])
```

**Step 3: Commit**

```bash
git add ollama_queue/daemon/loop.py tests/test_daemon.py
git commit -m "fix: log warning for orphan jobs with no PID — possible duplicate execution (#24)"
```

---

### Task 25: Lower BurstDetector baseline threshold for low-traffic systems

**Files:**
- Modify: `ollama_queue/sensing/burst.py:78-82`
- Test: `tests/test_burst.py`

**Step 1: Write test**

```python
def test_burst_detector_activates_with_fewer_samples(detector):
    """BurstDetector should activate regime detection after 5 samples, not 10."""
    for i in range(5):
        detector.record_submission(float(i))
    result = detector.regime(5.0)
    assert result != "unknown", "Should activate after 5 samples"
```

**Step 2: Write implementation**

Change the threshold from 10 to 5 in `burst.py:80`:

```python
        if len(self._baseline_samples) < 5 or self._ewma is None:  # was 10
            return "unknown"
```

**Step 3: Commit**

```bash
git add ollama_queue/sensing/burst.py tests/test_burst.py
git commit -m "fix: lower BurstDetector activation threshold from 10 to 5 samples (#26)"
```

---

## Final: Full Regression Suite

### Task 26: Run full test suite + build

Run:
```bash
cd ~/Documents/projects/ollama-queue
pytest --timeout=120 -x -q
cd ollama_queue/dashboard/spa && npm run build
```

Expected: All 1,677+ tests PASS (new tests added), SPA builds clean.

### Task 27: Update CLAUDE.md gotchas

Add entries for the new behaviors:
- `max_pause_duration_seconds` setting (default 600)
- `vram_known` field on SystemSnapshot
- First-ever eval run requires manual promote
- Model list cache TTL reduced to 15s
- Priority bounds 0-10 enforced

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with edge case fix behaviors"
```

---

## Dependency Graph

```
Batch 1 (proxy) ─── no deps ──────────────────────────┐
Batch 2 (daemon) ── no deps ──────────────────────────┤
Batch 3 (health) ── no deps ──────────────────────────┤
Batch 4 (eval) ──── no deps ──────────────────────────┤── Task 26 (full suite)
Batch 5 (stall) ─── no deps ──────────────────────────┤── Task 27 (docs)
Batch 6 (sched) ─── no deps ──────────────────────────┤
Batch 7 (api) ───── no deps ──────────────────────────┤
Batch 8 (models) ── no deps ──────────────────────────┤
Batch 9 (db) ────── no deps ──────────────────────────┤
Batch 10 (misc) ─── no deps ──────────────────────────┘
```

All 10 batches are independent — can run in parallel via separate worktrees.

**Recommended execution order** (if sequential): Batch 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 26 → 27
