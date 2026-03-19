# Fix: Proxy Sentinel, DLQ Atomicity, Scheduler TOCTOU, Flaky Tests

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 4 High + 1 Medium production bugs surfaced by senior dev review, plus 2 flaky tests under pytest-xdist.

**Architecture:** Four independent change groups (A–D) touching non-overlapping files — safe to execute in parallel sub-agents in a single worktree. Each group follows TDD: failing test → fix → green → commit.

**Tech Stack:** Python 3.12, FastAPI, SQLite (`threading.RLock`), subprocess, pytest

**Worktree:** `.worktrees/fix/proxy-dlq-scheduler-bugs`

---

## Summary of Bugs

| ID | File | Line | Description |
|----|------|------|-------------|
| H2 | `api/proxy.py` | 427-438 | Streaming `_released` stays False when `release_proxy_claim()` raises → BackgroundTask double-releases, can clear a new proxy's sentinel |
| H1 | `api/proxy.py` | 261-301 | Double `complete_job` when first call raises → job marked failed despite Ollama 200 |
| H4 | `daemon/executor.py` + `dlq.py` | 427-480 | `handle_failure` called inside `with db._lock:` → atomicity guarantee breaks (RLock re-entry = no-op guard) |
| H6 | `daemon/executor.py` | 414 | `proc.wait(timeout=5)` after SIGKILL unguarded → `TimeoutExpired` on D-state process leaks pipes |
| M4 | `scheduling/scheduler.py` | 110-134 | TOCTOU: `has_pending_or_running_recurring` check and `submit_job` are separate lock acquisitions → duplicate recurring jobs possible |
| F1 | `tests/test_api_cov_d.py` | 864 | `test_spa_static_serves_file` writes to package tree — races with parallel worker cleanup |
| F2 | `tests/test_api_eval_settings.py` | 700 | `test_valid_backend_url_accepted` shares backend DB state with parallel workers |

---

## Group A: proxy.py fixes (H2 + H1)

**Files:**
- Modify: `ollama_queue/api/proxy.py`
- Test: `tests/test_proxy.py`

### Task A1: Write failing test for H2 (streaming `_released` flag)

**Step 1: Write the failing test**

Add to `tests/test_proxy.py`:

```python
def test_streaming_release_flag_set_when_release_proxy_claim_raises(tmp_path):
    """_released must be set True even when release_proxy_claim() raises.

    Regression test for H2: if release_proxy_claim() raises, _released stays
    False and the BackgroundTask calls release_proxy_claim() a second time,
    potentially clearing a new proxy request's sentinel.
    """
    import threading
    from unittest.mock import MagicMock, patch

    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    # Track how many times release_proxy_claim is called
    release_call_count = 0
    original_release = db.release_proxy_claim

    def counting_release():
        nonlocal release_call_count
        release_call_count += 1
        raise RuntimeError("DB locked")

    db.release_proxy_claim = counting_release

    # Simulate _release() closure behavior directly
    _released = False

    def _release():
        nonlocal _released
        try:
            db.complete_job(1, exit_code=0, stdout_tail="(streaming)", stderr_tail="", outcome_reason=None)
        except Exception:
            pass
        try:
            db.release_proxy_claim()
        except Exception:
            return  # BUG: _released stays False
        _released = True

    _release()

    # After the fix: _released must be True even though release_proxy_claim raised
    assert _released is True, (
        "_released must be set True before early return so BackgroundTask "
        "does not call release_proxy_claim() a second time"
    )
```

**Step 2: Run to verify it fails**

```bash
cd ~/Documents/projects/ollama-queue/.worktrees/fix/proxy-dlq-scheduler-bugs
python3 -m pytest tests/test_proxy.py::test_streaming_release_flag_set_when_release_proxy_claim_raises -v
```
Expected: FAIL — `AssertionError: _released must be set True before early return`

**Step 3: Fix `_release()` in `ollama_queue/api/proxy.py` (lines 427-438)**

Change:
```python
        try:
            db.release_proxy_claim()
        except Exception:
            _log.exception("release_proxy_claim failed for streaming job %d", job_id)
            return
        _released = True
```

To:
```python
        try:
            db.release_proxy_claim()
        except Exception:
            _log.exception("release_proxy_claim failed for streaming job %d", job_id)
            _released = True  # attempt was made — prevent BackgroundTask double-release
            return
        _released = True
```

**Step 4: Run to verify it passes**

```bash
python3 -m pytest tests/test_proxy.py::test_streaming_release_flag_set_when_release_proxy_claim_raises -v
```
Expected: PASS

**Step 5: Run full proxy test file to check no regressions**

```bash
python3 -m pytest tests/test_proxy.py -v --timeout=60
```
Expected: all pass

**Step 6: Commit**

```bash
git add ollama_queue/api/proxy.py tests/test_proxy.py
git commit -m "fix(proxy): set _released=True before early return when release_proxy_claim raises (H2)"
```

---

### Task A2: Write failing test for H1 (double `complete_job`)

**Step 1: Write the failing test**

Add to `tests/test_proxy.py`:

```python
def test_proxy_ollama_request_no_double_complete_job_when_first_raises(tmp_path):
    """complete_job must not be called twice when the first call raises.

    Regression test for H1: if db.complete_job raises on a successful Ollama
    response, the outer except Exception block calls complete_job again,
    marking the job failed despite Ollama returning 200.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch, call
    import httpx

    db = MagicMock()
    db.get_daemon_state.return_value = {"state": "running"}
    db.list_consumers.return_value = []
    db.try_claim_for_proxy.return_value = True
    db.submit_job.return_value = 42
    db.start_job.return_value = None

    complete_job_calls = []
    def raising_complete_job(**kwargs):
        complete_job_calls.append(kwargs)
        if len(complete_job_calls) == 1:
            raise RuntimeError("SQLITE_BUSY")
        # Second call should NOT happen — if it does, the bug is present

    db.complete_job.side_effect = raising_complete_job
    db.release_proxy_claim.return_value = None

    mock_response = MagicMock()
    mock_response.json.return_value = {"response": "hello", "eval_count": None}

    import ollama_queue.api as _api
    _api.db = db

    from ollama_queue.api.proxy import _proxy_ollama_request

    with patch("ollama_queue.api.proxy.select_backend", new=AsyncMock(return_value="http://localhost:11434")):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            import pytest
            with pytest.raises(Exception):  # expect HTTPException(502) from outer except
                asyncio.get_event_loop().run_until_complete(
                    _proxy_ollama_request(
                        endpoint="/api/generate",
                        command="proxy:/api/generate",
                        body={"model": "test", "stream": False},
                        resource_profile="ollama",
                        extract_stdout_fn=lambda r: str(r.get("response", ""))[:500],
                        error_prefix="test",
                    )
                )

    # The fix: complete_job should only be called once (the first call that raised)
    assert len(complete_job_calls) == 1, (
        f"complete_job was called {len(complete_job_calls)} times — "
        "must not call it twice when first call raises"
    )
    # And the one call must be the success path (exit_code=0)
    assert complete_job_calls[0]["exit_code"] == 0
```

**Step 2: Run to verify it fails**

```bash
python3 -m pytest tests/test_proxy.py::test_proxy_ollama_request_no_double_complete_job_when_first_raises -v
```
Expected: FAIL — `AssertionError: complete_job was called 2 times`

**Step 3: Fix `_proxy_ollama_request` in `ollama_queue/api/proxy.py` (lines 261-301)**

Add a `_job_completed` flag. Change:

```python
    try:
        backend = forced_backend if forced_backend and forced_backend != "auto" else await select_backend(model)
        async with httpx.AsyncClient(timeout=httpx.Timeout(float(req_timeout))) as client:
            resp = await client.post(f"{backend}{endpoint}", json=body)
            result = resp.json()

        db.complete_job(
            job_id=job_id,
            exit_code=0,
            stdout_tail=extract_stdout_fn(result),
            stderr_tail="",
            outcome_reason=None,
        )
        if result.get("eval_count"):
            try:
                db.store_backend_metrics(backend_url=backend, model=model, metrics=result)
            except Exception:
                _log.warning("store_backend_metrics failed for job %d", job_id, exc_info=True)
        result["_queue_job_id"] = job_id
        return result
    except httpx.ReadTimeout as e:
        # ReadTimeout is expected for slow models (e.g. deepseek-r1); log as WARNING not ERROR
        _log.warning("%s timed out for job %d after %ss (pass _timeout to override)", command, job_id, req_timeout)
        db.complete_job(
            job_id=job_id,
            exit_code=1,
            stdout_tail="",
            stderr_tail=str(e)[:500],
            outcome_reason=f"proxy timeout after {req_timeout}s",
        )
        raise HTTPException(status_code=504, detail=f"{error_prefix}: read timeout after {req_timeout}s") from e
    except Exception as e:
        _log.error("%s failed for job %d: %s", command, job_id, e, exc_info=True)
        db.complete_job(
            job_id=job_id,
            exit_code=1,
            stdout_tail="",
            stderr_tail=str(e)[:500],
            outcome_reason=f"proxy error: {e}",
        )
        raise HTTPException(status_code=502, detail=f"{error_prefix}: {e}") from e
```

To:

```python
    _job_completed = False
    try:
        backend = forced_backend if forced_backend and forced_backend != "auto" else await select_backend(model)
        async with httpx.AsyncClient(timeout=httpx.Timeout(float(req_timeout))) as client:
            resp = await client.post(f"{backend}{endpoint}", json=body)
            result = resp.json()

        db.complete_job(
            job_id=job_id,
            exit_code=0,
            stdout_tail=extract_stdout_fn(result),
            stderr_tail="",
            outcome_reason=None,
        )
        _job_completed = True
        if result.get("eval_count"):
            try:
                db.store_backend_metrics(backend_url=backend, model=model, metrics=result)
            except Exception:
                _log.warning("store_backend_metrics failed for job %d", job_id, exc_info=True)
        result["_queue_job_id"] = job_id
        return result
    except httpx.ReadTimeout as e:
        # ReadTimeout is expected for slow models (e.g. deepseek-r1); log as WARNING not ERROR
        _log.warning("%s timed out for job %d after %ss (pass _timeout to override)", command, job_id, req_timeout)
        if not _job_completed:
            db.complete_job(
                job_id=job_id,
                exit_code=1,
                stdout_tail="",
                stderr_tail=str(e)[:500],
                outcome_reason=f"proxy timeout after {req_timeout}s",
            )
        raise HTTPException(status_code=504, detail=f"{error_prefix}: read timeout after {req_timeout}s") from e
    except Exception as e:
        _log.error("%s failed for job %d: %s", command, job_id, e, exc_info=True)
        if not _job_completed:
            db.complete_job(
                job_id=job_id,
                exit_code=1,
                stdout_tail="",
                stderr_tail=str(e)[:500],
                outcome_reason=f"proxy error: {e}",
            )
        raise HTTPException(status_code=502, detail=f"{error_prefix}: {e}") from e
```

**Step 4: Run to verify it passes**

```bash
python3 -m pytest tests/test_proxy.py::test_proxy_ollama_request_no_double_complete_job_when_first_raises -v
```
Expected: PASS

**Step 5: Run full proxy + embed proxy tests**

```bash
python3 -m pytest tests/test_proxy.py tests/test_embed_proxy.py -v --timeout=60
```
Expected: all pass

**Step 6: Commit**

```bash
git add ollama_queue/api/proxy.py tests/test_proxy.py
git commit -m "fix(proxy): guard complete_job with _job_completed flag to prevent double-complete on DB error (H1)"
```

---

## Group B: executor.py + dlq.py fixes (H4 + H6)

**Files:**
- Modify: `ollama_queue/dlq.py`
- Modify: `ollama_queue/daemon/executor.py`
- Test: `tests/test_dlq.py`
- Test: `tests/test_daemon.py`

### Task B1: Write failing test for H4 (DLQ atomicity)

**Context:** `handle_failure` wraps itself with `with self.db._lock:`, but is called from inside an existing `with self.db._lock:` block in `_run_job`. The RLock re-enters silently — the inner `with` is a no-op guard — breaking the atomicity guarantee.

**Fix approach:** Extract `handle_failure`'s body into `_handle_failure_locked(job_id, failure_reason)` (assumes lock already held). `handle_failure` acquires the lock and calls `_handle_failure_locked`. `executor.py` calls `_handle_failure_locked` directly (already inside the lock).

**Step 1: Write the failing test**

Add to `tests/test_dlq.py`:

```python
def test_handle_failure_locked_exists_and_matches_behavior(tmp_path):
    """DLQManager must expose _handle_failure_locked for callers already holding db._lock.

    Regression test for H4: executor.py calls handle_failure from inside
    with self.db._lock:. The RLock re-entry means handle_failure's own lock
    acquisition is a no-op, breaking the atomicity guarantee. The fix extracts
    _handle_failure_locked (assumes lock held) so executor can call it correctly.
    """
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    dlq = DLQManager(db)

    # _handle_failure_locked must exist and be callable
    assert hasattr(dlq, "_handle_failure_locked"), (
        "DLQManager must have _handle_failure_locked method for callers "
        "already holding db._lock (executor.py pattern)"
    )

    # Submit a job so there's something to fail
    job_id = db.submit_job(command="echo test", model="", priority=5, timeout=60, source="test")
    db.start_job(job_id)
    db.complete_job(job_id, exit_code=1, stdout_tail="", stderr_tail="", outcome_reason="test")

    # Calling _handle_failure_locked from inside db._lock must work without deadlock
    result = None
    with db._lock:
        result = dlq._handle_failure_locked(job_id, "test failure")

    assert result in ("retry", "dlq")
```

**Step 2: Run to verify it fails**

```bash
python3 -m pytest tests/test_dlq.py::test_handle_failure_locked_exists_and_matches_behavior -v
```
Expected: FAIL — `AssertionError: DLQManager must have _handle_failure_locked method`

**Step 3: Refactor `ollama_queue/dlq.py`**

Change `handle_failure` to delegate to `_handle_failure_locked`:

```python
    def handle_failure(self, job_id: int, failure_reason: str) -> str:
        """Route a failed job. Returns 'retry' or 'dlq'.

        Acquires db._lock for the full decision. Do NOT call this from inside
        an existing db._lock block — use _handle_failure_locked instead to
        preserve the single-lock atomicity guarantee.
        """
        with self.db._lock:
            return self._handle_failure_locked(job_id, failure_reason)

    def _handle_failure_locked(self, job_id: int, failure_reason: str) -> str:
        """Route a failed job — assumes db._lock is already held by the caller.

        Used by executor.py which calls this from inside its own db._lock block.
        The RLock would re-enter handle_failure's lock silently, breaking atomicity.
        """
        job = self.db.get_job(job_id)
        if not job:
            _log.warning("handle_failure: job #%d not found", job_id)
            return "dlq"
        retry_count = job.get("retry_count", 0)
        max_retries = job.get("max_retries", 0)
        if retry_count < max_retries:
            return self._schedule_retry(job_id, retry_count, job=job)
        else:
            return self._move_to_dlq(job_id, failure_reason)
```

**Step 4: Update `ollama_queue/daemon/executor.py` — replace `handle_failure` calls with `_handle_failure_locked`**

There are 4 call sites. All are inside `with self.db._lock:` blocks:

- Line 436: `self.dlq.handle_failure(job["id"], f"timeout after {job['timeout']}s")`
- Line 478: `self.dlq.handle_failure(job["id"], f"exit code {exit_code}")`
- Line 590: `self.dlq.handle_failure(job["id"], f"internal error: {type(exc).__name__}")`

Replace all three with `_handle_failure_locked`:
```python
self.dlq._handle_failure_locked(job["id"], f"timeout after {job['timeout']}s")
self.dlq._handle_failure_locked(job["id"], f"exit code {exit_code}")
self.dlq._handle_failure_locked(job["id"], f"internal error: {type(exc).__name__}")
```

Also update the comment at lines 425-426 and 465-466 from `# db._lock is RLock — complete_job and handle_failure re-acquire safely` to `# db._lock is held — call _handle_failure_locked (not handle_failure) to preserve atomicity`.

**Step 5: Run to verify tests pass**

```bash
python3 -m pytest tests/test_dlq.py tests/test_daemon.py -v --timeout=60
```
Expected: all pass including the new test

**Step 6: Commit**

```bash
git add ollama_queue/dlq.py ollama_queue/daemon/executor.py tests/test_dlq.py
git commit -m "fix(dlq): add _handle_failure_locked for callers inside db._lock; fix executor call sites (H4)"
```

---

### Task B2: Write failing test for H6 (unguarded proc.wait after SIGKILL)

**Step 1: Write the failing test**

Add to `tests/test_daemon.py`:

```python
def test_run_job_handles_timeout_expired_after_sigkill(tmp_path):
    """proc.wait(timeout=5) after SIGKILL must be guarded against TimeoutExpired.

    Regression test for H6: a D-state process ignores SIGKILL. proc.wait(5)
    raises TimeoutExpired, which previously propagated unhandled, leaving stdout
    and stderr pipes open indefinitely.
    """
    import subprocess
    from unittest.mock import MagicMock, patch, call

    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    job_id = db.submit_job(
        command="sleep 9999",
        model="",
        priority=5,
        timeout=1,
        source="test",
        resource_profile="ollama",
    )
    db.start_job(job_id)
    job = db.get_job(job_id)

    # Mock Popen so we can control proc.wait behavior
    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.returncode = -9
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()

    # _drain_pipes_with_tracking returns immediately with empty output
    wait_call_count = [0]

    def wait_side_effect(timeout=None):
        wait_call_count[0] += 1
        if wait_call_count[0] == 1:
            # First call (timeout=30): raise TimeoutExpired to trigger SIGKILL path
            raise subprocess.TimeoutExpired("sleep 9999", 30)
        if wait_call_count[0] == 2:
            # Second call (timeout=5 after SIGKILL): also raise (D-state)
            raise subprocess.TimeoutExpired("sleep 9999", 5)
        return 0

    mock_proc.wait.side_effect = wait_side_effect
    mock_proc.kill.return_value = None

    from ollama_queue.daemon import Daemon
    from unittest.mock import patch

    daemon = Daemon(db=db)

    with patch("subprocess.Popen", return_value=mock_proc):
        with patch("ollama_queue.daemon.executor._drain_pipes_with_tracking", return_value=(b"", b"")):
            # Must not raise — TimeoutExpired from proc.wait(5) must be caught
            try:
                daemon._run_job(job)
            except subprocess.TimeoutExpired:
                pytest.fail(
                    "TimeoutExpired from proc.wait(timeout=5) after SIGKILL must be caught, "
                    "not propagated. Pipes must be closed."
                )

    # After the fix: pipes must be explicitly closed on D-state timeout
    mock_proc.stdout.close.assert_called_once()
    mock_proc.stderr.close.assert_called_once()
```

**Step 2: Run to verify it fails**

```bash
python3 -m pytest tests/test_daemon.py::test_run_job_handles_timeout_expired_after_sigkill -v
```
Expected: FAIL — `TimeoutExpired` raised or `mock_proc.stdout.close` not called

**Step 3: Fix `ollama_queue/daemon/executor.py` line 414**

Change:
```python
                    proc.wait(timeout=5)  # reap zombie
```

To:
```python
                    try:
                        proc.wait(timeout=5)  # reap zombie
                    except subprocess.TimeoutExpired:
                        _log.warning(
                            "Job #%d still alive after SIGKILL (D-state?) — closing pipes",
                            job["id"],
                        )
                        with contextlib.suppress(Exception):
                            proc.stdout.close()
                        with contextlib.suppress(Exception):
                            proc.stderr.close()
```

**Step 4: Run to verify it passes**

```bash
python3 -m pytest tests/test_daemon.py::test_run_job_handles_timeout_expired_after_sigkill -v
```
Expected: PASS

**Step 5: Run full daemon test file**

```bash
python3 -m pytest tests/test_daemon.py -v --timeout=60
```
Expected: all pass

**Step 6: Commit**

```bash
git add ollama_queue/daemon/executor.py tests/test_daemon.py
git commit -m "fix(executor): guard proc.wait(5) after SIGKILL against TimeoutExpired; close pipes on D-state (H6)"
```

---

## Group C: scheduler.py TOCTOU fix (M4)

**Files:**
- Modify: `ollama_queue/scheduling/scheduler.py`
- Test: `tests/test_scheduler.py`

### Task C1: Write failing test for M4 (TOCTOU on recurring submit)

**Context:** `has_pending_or_running_recurring(rj["id"])` and `db.submit_job(...)` are individually atomic but not together. A concurrent `POST /api/schedule/{id}/run` can submit the same job between the check and the submit.

**Fix:** Wrap both the check and submit inside a single `with self.db._lock:` block.

**Step 1: Write the failing test**

Add to `tests/test_scheduler.py`:

```python
def test_promote_due_jobs_check_and_submit_atomic(tmp_path):
    """has_pending_or_running_recurring and submit_job must execute under one db._lock.

    Regression test for M4: without a shared lock, a concurrent API call can
    submit a duplicate recurring job between the coalesce check and the submit,
    causing two jobs for the same recurring job to run simultaneously.

    We verify atomicity by checking that db._lock is held continuously across
    both operations (no release between check and submit).
    """
    import threading

    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    rj_id = db.add_recurring_job(
        name="test-job",
        command="echo test",
        interval_seconds=60,
        model="",
        priority=5,
        timeout=60,
        source="test",
    )
    # Set next_run to the past so the job is due
    db._set_recurring_next_run(rj_id, time.time() - 10)

    scheduler = Scheduler(db)

    lock_held_during_submit = []
    original_submit = db.submit_job

    def tracking_submit(*args, **kwargs):
        # Check if db._lock is currently held (owner is current thread)
        # RLock._is_owned() is an internal method but available on CPython
        lock_held_during_submit.append(db._lock._is_owned())
        return original_submit(*args, **kwargs)

    db.submit_job = tracking_submit

    scheduler.promote_due_jobs()

    assert lock_held_during_submit, "submit_job was never called"
    assert all(lock_held_during_submit), (
        "db._lock must be held when submit_job is called — "
        "check and submit must be atomic to prevent duplicate job submissions"
    )
```

**Step 2: Run to verify it fails**

```bash
python3 -m pytest tests/test_scheduler.py::test_promote_due_jobs_check_and_submit_atomic -v
```
Expected: FAIL — `lock_held_during_submit` contains `False`

**Step 3: Fix `ollama_queue/scheduling/scheduler.py` (lines 110-151)**

Wrap the `has_pending_or_running_recurring` check and `submit_job` call in a single lock:

Change:
```python
            if self.db.has_pending_or_running_recurring(rj["id"]):
                # A previous run is still pending or running — skip this trigger.
                ...
                continue

            job_id = self.db.submit_job(
                command=rj["command"],
                ...
            )
```

To:
```python
            with self.db._lock:
                # Atomic check-and-submit: no concurrent API call can submit a duplicate
                # between the coalesce check and the actual submit. (M4)
                if self.db.has_pending_or_running_recurring(rj["id"]):
                    # A previous run is still pending or running — skip this trigger.
                    ...
                    continue

                job_id = self.db.submit_job(
                    command=rj["command"],
                    ...
                )
```

Note: `has_pending_or_running_recurring` internally acquires `db._lock` (it's an RLock, so re-entry is safe). `submit_job` also acquires `db._lock` internally. The outer `with self.db._lock:` makes the pair atomic.

The `log_schedule_event` and `new_ids.append` calls after `submit_job` can remain outside the lock (they don't need to be in the atomic window).

Full replacement for lines 110-151:

```python
            with self.db._lock:
                # Atomic check-and-submit prevents duplicate promotion under concurrent
                # POST /api/schedule/{id}/run races. db._lock is RLock — reentrant. (M4)
                if self.db.has_pending_or_running_recurring(rj["id"]):
                    # A previous run is still pending or running — skip this trigger.
                    # Advance next_run to suppress poll re-evaluations until completion.
                    self.db.log_schedule_event(
                        "skipped_duplicate",
                        recurring_job_id=rj["id"],
                        details={"name": rj["name"], "reason": "already pending or running"},
                    )
                    try:
                        next_run_updates[rj["id"]] = _compute_next_run()
                    except CroniterBadCronError as exc:
                        _log.warning(
                            "promote_due_jobs: skipping next_run update for recurring job %r (id=%s)"
                            " — bad cron expression %r: %s",
                            rj.get("name"),
                            rj.get("id"),
                            rj.get("cron_expression"),
                            exc,
                        )
                    continue

                job_id = self.db.submit_job(
                    command=rj["command"],
                    model=rj["model"],
                    priority=rj["priority"],
                    timeout=rj["timeout"],
                    source=rj["source"] or rj["name"],
                    tag=rj.get("tag"),
                    max_retries=rj.get("max_retries", 0),
                    resource_profile=rj.get("resource_profile", "ollama"),
                    recurring_job_id=rj["id"],
                )
            # log_schedule_event and append outside the lock (not part of the atomic window)
            self.db.log_schedule_event(
                "promoted",
                recurring_job_id=rj["id"],
                job_id=job_id,
                details={"name": rj["name"]},
            )
            new_ids.append(job_id)
            _log.info("Promoted recurring job %r → job #%d", rj["name"], job_id)
```

**Step 4: Run to verify it passes**

```bash
python3 -m pytest tests/test_scheduler.py::test_promote_due_jobs_check_and_submit_atomic -v
```
Expected: PASS

**Step 5: Run full scheduler test file**

```bash
python3 -m pytest tests/test_scheduler.py tests/test_schedule.py tests/test_schedule_db.py -v --timeout=60
```
Expected: all pass

**Step 6: Commit**

```bash
git add ollama_queue/scheduling/scheduler.py tests/test_scheduler.py
git commit -m "fix(scheduler): make has_pending_or_running + submit_job atomic under db._lock (M4)"
```

---

## Group D: Flaky test fixes (F1 + F2)

**Files:**
- Modify: `tests/test_api_cov_d.py`
- Modify: `tests/test_api_eval_settings.py`

### Task D1: Fix `test_spa_static_serves_file` (F1)

**Problem:** The test writes `index.html` and `app.js` directly into `ollama_queue/dashboard/spa/dist/` (the real package path). Under xdist, another parallel worker's teardown calls `shutil.rmtree` on this directory while this test's setup is still creating files, causing a 404.

**Fix:** Use `tmp_path` for the dist directory, patch `app_mod.__file__` to point there, never touch the package tree.

**Step 1: Read the full test to understand its imports**

Verify the test imports `create_app` and `Database` — look for the import block at the top of `tests/test_api_cov_d.py`.

**Step 2: Replace `test_spa_static_serves_file` in `tests/test_api_cov_d.py`**

Find the existing function (starts at line 864) and replace the entire function with:

```python
def test_spa_static_serves_file(tmp_path):
    """GET /ui/{path} serves files from the SPA dist directory.

    Creates an isolated dist directory in tmp_path (not the package tree) so
    this test is safe under pytest-xdist — no shared filesystem state with
    other parallel workers.
    """
    import ollama_queue.app as app_mod
    from unittest.mock import patch

    # Build an isolated dist directory — never write to the real package tree
    dist_dir = tmp_path / "spa" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>test</html>")
    (dist_dir / "app.js").write_text("console.log('test');")

    # Patch __file__ so create_app resolves the dist path to our tmp directory.
    # app.py computes: Path(__file__).parent / "dashboard" / "spa" / "dist"
    # We patch __file__ to point two levels up from our dist dir:
    #   patched_file = tmp_path / "spa" / "dist" / ".." / ".." / "app.py"
    #   → Path(patched_file).parent = tmp_path (our app.py's "parent" = tmp_path)
    # So: tmp_path / "dashboard" / "spa" / "dist" — but that's a different path.
    # Simpler: patch Path in app.py's computation directly.
    fake_app_file = str(tmp_path / "ollama_queue" / "app.py")
    fake_dist = tmp_path / "ollama_queue" / "dashboard" / "spa" / "dist"
    fake_dist.mkdir(parents=True)
    (fake_dist / "index.html").write_text("<html>test</html>")
    (fake_dist / "app.js").write_text("console.log('test');")

    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    with patch.object(app_mod, "__file__", fake_app_file):
        app = create_app(db)

    client = TestClient(app)

    # Test serving an existing file
    resp = client.get("/ui/app.js")
    assert resp.status_code == 200

    # Test index fallback for unknown SPA route
    resp = client.get("/ui/nonexistent/route")
    assert resp.status_code == 200
    assert b"<html>test</html>" in resp.content

    # Test null-byte guard
    resp = client.get("/ui/app%00.js")
    assert resp.status_code == 400
```

**Step 3: Run the test in isolation**

```bash
python3 -m pytest tests/test_api_cov_d.py::test_spa_static_serves_file -v --timeout=30
```
Expected: PASS

**Step 4: Run it with xdist to verify no flakiness**

```bash
python3 -m pytest tests/test_api_cov_d.py -n 4 --timeout=60 -v 2>&1 | tail -20
```
Expected: all pass

**Step 5: Commit**

```bash
git add tests/test_api_cov_d.py
git commit -m "test: isolate test_spa_static_serves_file to tmp_path — fix xdist flakiness (F1)"
```

---

### Task D2: Fix `test_valid_backend_url_accepted` (F2)

**Problem:** The test uses the `client_and_db` fixture which may share backend registry state with parallel workers. `db.add_backend(...)` writes to a shared in-memory registry that other workers could reset.

**Fix:** The test already uses `client_and_db` (which provides a fresh isolated DB). The race is in the `BACKENDS` dict in `api/backend_router.py` which is module-level shared state. The test needs to patch the `BACKENDS` dict or ensure isolation.

Look at how `add_backend` and backend validation work in `api/eval_settings.py`:

```bash
grep -n "BACKENDS\|add_backend\|generator_backend_url" ~/Documents/projects/ollama-queue/ollama_queue/api/eval_settings.py | head -20
```

**Step 1: Investigate the BACKENDS shared state**

```bash
grep -n "BACKENDS" ~/Documents/projects/ollama-queue/ollama_queue/api/backend_router.py | head -10
grep -n "BACKENDS\|add_backend" ~/Documents/projects/ollama-queue/ollama_queue/api/eval_settings.py | head -10
```

**Step 2: Patch BACKENDS in the test to avoid shared state**

The fix is to patch `ollama_queue.api.eval_settings.BACKENDS` (or wherever `generator_backend_url` validation reads it) with a dict containing our test backend URL. This isolates the test from any parallel worker modifying the module-level `BACKENDS` dict.

Replace `test_valid_backend_url_accepted` in `tests/test_api_eval_settings.py`:

```python
def test_valid_backend_url_accepted(client_and_db):
    """PUT with a registered backend URL should be accepted.

    Uses patch to inject the backend into BACKENDS dict directly — avoids
    shared in-memory module-level state that races with xdist parallel workers.
    """
    from unittest.mock import patch
    client, db = client_and_db
    test_url = "http://100.114.197.57:11434"

    # Patch the BACKENDS dict used by eval_settings validation — avoids
    # xdist race where another worker resets module-level BACKENDS state
    with patch("ollama_queue.api.eval_settings.BACKENDS", {test_url: {"weight": 1.0}}):
        resp = client.put(
            "/api/eval/settings",
            json={"eval.generator_backend_url": test_url},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["eval.generator_backend_url"] == test_url
```

**Step 3: Verify the patch target is correct**

If `BACKENDS` is imported differently in `eval_settings.py`, adjust the patch path. Check:

```bash
head -30 ~/Documents/projects/ollama-queue/ollama_queue/api/eval_settings.py | grep -E "import|BACKENDS"
```

Use the actual import path for the patch.

**Step 4: Run the test in isolation and with xdist**

```bash
python3 -m pytest tests/test_api_eval_settings.py::test_valid_backend_url_accepted -v --timeout=30
python3 -m pytest tests/test_api_eval_settings.py -n 4 --timeout=60 2>&1 | tail -10
```
Expected: both pass

**Step 5: Commit**

```bash
git add tests/test_api_eval_settings.py
git commit -m "test: patch BACKENDS dict in test_valid_backend_url_accepted — fix xdist flakiness (F2)"
```

---

## Final Verification

After all groups complete:

**Step 1: Run the full test suite serially**

```bash
cd ~/Documents/projects/ollama-queue/.worktrees/fix/proxy-dlq-scheduler-bugs
python3 -m pytest --timeout=120 -q 2>&1 | tail -20
```
Expected: 1,938+ pass, 0 fail

**Step 2: Run with xdist to verify no new flakiness**

```bash
python3 -m pytest -n auto --timeout=120 -q 2>&1 | tail -20
```
Expected: all pass

**Step 3: Run quality gate**

```bash
make lint
```

**Step 4: Create PR**

```bash
gh pr create \
  --title "fix: proxy sentinel double-release, DLQ atomicity, scheduler TOCTOU, pipe leak, flaky tests" \
  --body "## Summary

Fixes 4 High + 1 Medium production bugs found in senior dev review + 2 flaky test isolation issues.

### Bugs Fixed
- **H2** (proxy.py): Streaming \`_released\` flag not set when \`release_proxy_claim()\` raises — BackgroundTask could double-release sentinel, clearing a new proxy request's claim
- **H1** (proxy.py): Double \`complete_job\` when first DB call raises — job marked failed despite Ollama returning 200
- **H4** (dlq.py + executor.py): \`handle_failure\` called inside \`db._lock\` — atomicity guarantee broken via RLock re-entry; extracted \`_handle_failure_locked\` for locked callers
- **H6** (executor.py): \`proc.wait(timeout=5)\` after SIGKILL unguarded — \`TimeoutExpired\` on D-state process left pipes open; now caught + pipes closed
- **M4** (scheduler.py): \`has_pending_or_running_recurring\` and \`submit_job\` not atomic — duplicate recurring job submissions possible; wrapped in single \`db._lock\`

### Flaky Tests Fixed
- **F1** (test_api_cov_d.py): \`test_spa_static_serves_file\` now uses isolated \`tmp_path\` + \`patch(__file__)\` instead of writing to package tree
- **F2** (test_api_eval_settings.py): \`test_valid_backend_url_accepted\` now patches \`BACKENDS\` dict directly instead of relying on shared module-level state

### Test Coverage
Each bug fix includes a new regression test. Full suite: 1,938+ tests passing."
```
