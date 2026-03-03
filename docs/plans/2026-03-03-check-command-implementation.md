# check_command + max_runs Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `check_command` (pre-flight gate with exit-code contract) and `max_runs` (convergence countdown) to recurring jobs in ollama-queue.

**Architecture:** Schema migration adds two nullable columns to `recurring_jobs`. Daemon's `_run_job()` runs the check in-thread before the main subprocess; exit codes 0/1/2 control proceed/skip/disable. `max_runs` decrements on successful main-command exits only. API, CLI, and dashboard expose both fields.

**Tech Stack:** Python 3.12, SQLite (stdlib), FastAPI, Click, Preact 10 JSX

---

## Setup

```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
git checkout docs/check-command-design
git checkout -b feature/check-command
```

Verify baseline passes before touching anything:
```bash
pytest --timeout=120 -x -q
```
Expected: 239 tests pass.

---

### Task 1: Schema Migration + DB Helpers

**Files:**
- Modify: `ollama_queue/db.py`
- Test: `tests/test_db.py`

**Step 1: Write failing tests**

Add to `tests/test_db.py`:

```python
def test_add_recurring_job_with_check_command(db):
    """check_command and max_runs are stored and retrievable."""
    rj_id = db.add_recurring_job(
        name="test-check",
        command="echo hi",
        interval_seconds=3600,
        check_command="exit 0",
        max_runs=5,
    )
    rj = db.get_recurring_job(rj_id)
    assert rj["check_command"] == "exit 0"
    assert rj["max_runs"] == 5


def test_add_recurring_job_without_check_command(db):
    """Existing jobs with no check_command default to None."""
    rj_id = db.add_recurring_job(
        name="no-check",
        command="echo hi",
        interval_seconds=3600,
    )
    rj = db.get_recurring_job(rj_id)
    assert rj["check_command"] is None
    assert rj["max_runs"] is None


def test_disable_recurring_job_with_reason(db):
    """disable_recurring_job sets enabled=0 and outcome_reason."""
    rj_id = db.add_recurring_job(
        name="test-disable",
        command="echo hi",
        interval_seconds=3600,
    )
    db.disable_recurring_job(rj_id, "check_command signaled complete")
    rj = db.get_recurring_job(rj_id)
    assert rj["enabled"] == 0
    assert rj["outcome_reason"] == "check_command signaled complete"


def test_enable_recurring_job_clears_reason(db):
    """set_recurring_job_enabled(True) clears outcome_reason."""
    rj_id = db.add_recurring_job(
        name="re-enable",
        command="echo hi",
        interval_seconds=3600,
    )
    db.disable_recurring_job(rj_id, "max_runs exhausted")
    db.set_recurring_job_enabled("re-enable", True)
    rj = db.get_recurring_job(rj_id)
    assert rj["enabled"] == 1
    assert rj["outcome_reason"] is None


def test_update_recurring_job_max_runs(db):
    """update_recurring_job allows max_runs and check_command updates."""
    rj_id = db.add_recurring_job(
        name="update-test",
        command="echo hi",
        interval_seconds=3600,
        max_runs=10,
    )
    db.update_recurring_job(rj_id, max_runs=9)
    rj = db.get_recurring_job(rj_id)
    assert rj["max_runs"] == 9


def test_recurring_job_schema_has_outcome_reason(db):
    """recurring_jobs table has outcome_reason column."""
    rj_id = db.add_recurring_job(
        name="schema-test",
        command="echo hi",
        interval_seconds=3600,
    )
    rj = db.get_recurring_job(rj_id)
    assert "outcome_reason" in rj
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_db.py -k "check_command or disable_recurring or enable_recurring or update_recurring_job_max_runs or schema_has_outcome" -v
```
Expected: 6 FAILED (AttributeError or KeyError)

**Step 3: Implement schema migration in `db.py`**

In `db.initialize()`, after the existing `stall_signals` migration block (around line 236), add three new migration blocks:

```python
        # Migrate: add check_command, max_runs, outcome_reason to recurring_jobs
        for col, defn in [
            ("check_command", "TEXT"),
            ("max_runs", "INTEGER"),
            ("outcome_reason", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE recurring_jobs ADD COLUMN {col} {defn}")
                conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    _log.debug("recurring_jobs.%s already exists — skipping migration", col)
                else:
                    raise
```

**Step 4: Add `check_command` and `max_runs` to `add_recurring_job()`**

Change signature (add two new keyword-only params with defaults):

```python
    def add_recurring_job(
        self,
        name: str,
        command: str,
        interval_seconds: int | None = None,
        cron_expression: str | None = None,
        model: str | None = None,
        priority: int = 5,
        timeout: int = 600,
        source: str | None = None,
        tag: str | None = None,
        resource_profile: str = "ollama",
        max_retries: int = 0,
        next_run: float | None = None,
        pinned: bool = False,
        check_command: str | None = None,
        max_runs: int | None = None,
    ) -> int:
```

Update the INSERT to include them (add to both the column list and values tuple):

```python
        cur = conn.execute(
            """INSERT INTO recurring_jobs
               (name, command, model, priority, timeout, source, tag,
                resource_profile, interval_seconds, cron_expression, next_run,
                max_retries, pinned, check_command, max_runs, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name, command, model, priority, timeout, source, tag,
                resource_profile, interval_seconds, cron_expression, next_run,
                max_retries, 1 if pinned else 0,
                check_command, max_runs,
                now,
            ),
        )
```

**Step 5: Update `update_recurring_job()` allowed set**

Add `"check_command"`, `"max_runs"`, and `"outcome_reason"` to the `allowed` set (around line 749):

```python
        allowed = {
            "name", "command", "interval_seconds", "cron_expression",
            "model", "priority", "timeout", "source", "tag",
            "enabled", "next_run", "pinned", "max_retries",
            "check_command", "max_runs", "outcome_reason",   # NEW
        }
```

**Step 6: Add `disable_recurring_job()` method**

Add after `set_recurring_job_enabled()` (around line 730):

```python
    def disable_recurring_job(self, rj_id: int, reason: str) -> None:
        """Auto-disable a recurring job and record the reason."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE recurring_jobs SET enabled = 0, outcome_reason = ? WHERE id = ?",
                (reason, rj_id),
            )
            conn.commit()
```

**Step 7: Update `set_recurring_job_enabled()` to clear `outcome_reason` on re-enable**

Replace the existing method body:

```python
    def set_recurring_job_enabled(self, name: str, enabled: bool) -> bool:
        with self._lock:
            conn = self._connect()
            if enabled:
                cur = conn.execute(
                    "UPDATE recurring_jobs SET enabled = 1, outcome_reason = NULL WHERE name = ?",
                    (name,),
                )
            else:
                cur = conn.execute(
                    "UPDATE recurring_jobs SET enabled = 0 WHERE name = ?",
                    (name,),
                )
            conn.commit()
            return cur.rowcount > 0
```

**Step 8: Run tests to verify they pass**

```bash
pytest tests/test_db.py -k "check_command or disable_recurring or enable_recurring or update_recurring_job_max_runs or schema_has_outcome" -v
```
Expected: 6 PASSED

**Step 9: Run full test suite to confirm no regressions**

```bash
pytest --timeout=120 -x -q
```
Expected: 245 tests pass (239 + 6 new).

**Step 10: Commit**

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat: add check_command, max_runs, outcome_reason to recurring_jobs schema"
```

---

### Task 2: Daemon — check_command Pre-flight Logic

**Files:**
- Modify: `ollama_queue/daemon.py`
- Test: `tests/test_daemon.py`

**Step 1: Write failing tests**

Add to `tests/test_daemon.py`:

```python
import subprocess as _subprocess


def _make_recurring_and_job(db, check_command=None, max_runs=None):
    """Helper: create recurring job + pending queue job linked to it."""
    rj_id = db.add_recurring_job(
        name="check-test",
        command="echo main",
        interval_seconds=3600,
        check_command=check_command,
        max_runs=max_runs,
    )
    job_id = db.submit_job(
        command="echo main",
        model=None,
        priority=5,
        timeout=60,
        source="check-test",
        resource_profile="any",
        recurring_job_id=rj_id,
    )
    db.start_job(job_id)
    return rj_id, job_id


def test_check_command_exit0_proceeds(daemon):
    """check_command exit 0 → main job runs normally."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="exit 0")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        mock_sub.run.return_value = MagicMock(returncode=0)
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    # check_command ran (subprocess.run called), main Popen also called
    mock_sub.run.assert_called_once()
    mock_sub.Popen.assert_called_once()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_check_command_exit1_skips(daemon):
    """check_command exit 1 → job skipped, next_run advanced, no Popen."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="exit 1")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=1)
        daemon._run_job(job)

    mock_sub.Popen.assert_not_called()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"
    assert "skip" in (completed["outcome_reason"] or "").lower()
    # next_run advanced
    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["next_run"] > time.time()  # advanced past now


def test_check_command_exit2_disables(daemon):
    """check_command exit 2 → recurring job auto-disabled, no Popen."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="exit 2")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=2)
        daemon._run_job(job)

    mock_sub.Popen.assert_not_called()
    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["enabled"] == 0
    assert "check_command" in (rj["outcome_reason"] or "").lower()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_check_command_unknown_exit_failopen(daemon):
    """check_command exit 99 → warning logged, main job proceeds (fail-open)."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="exit 99")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        mock_sub.run.return_value = MagicMock(returncode=99)
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.Popen.assert_called_once()  # main job ran
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_check_command_timeout_failopen(daemon):
    """check_command TimeoutExpired → warning logged, main job proceeds."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, check_command="sleep 999")
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.run.side_effect = _subprocess.TimeoutExpired("sleep 999", 30)
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.Popen.assert_called_once()
    completed = daemon.db.get_job(job_id)
    assert completed["status"] == "completed"


def test_no_check_command_skips_check(daemon):
    """Job with no check_command skips check, runs normally."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, check_command=None)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    mock_sub.run.assert_not_called()  # no check_command call
    mock_sub.Popen.assert_called_once()
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_daemon.py -k "check_command" -v
```
Expected: 6 FAILED

**Step 3: Implement `_run_check_command()` in `daemon.py`**

Add this private method to the `Daemon` class after `_check_retryable_jobs()` and before `run()`:

```python
    def _run_check_command(self, job: dict, recurring_job: dict) -> str:
        """Run check_command for a recurring job before the main command.

        Returns:
            'proceed'  — exit 0 or fail-open: run main job
            'skip'     — exit 1: advance next_run, complete job as skipped
            'disable'  — exit 2: auto-disable recurring job, complete job
        """
        check_cmd = recurring_job["check_command"]
        rj_id = recurring_job["id"]
        try:
            result = subprocess.run(
                check_cmd,
                shell=True,
                capture_output=True,
                timeout=30,
            )
            code = result.returncode
        except subprocess.TimeoutExpired:
            _log.warning(
                "check_command timed out for recurring job id=%d — proceeding (fail-open)",
                rj_id,
            )
            return "proceed"

        if code == 0:
            return "proceed"
        elif code == 1:
            _log.info(
                "check_command exit 1 for recurring job id=%d (%s) — no work, skipping",
                rj_id, recurring_job.get("name", ""),
            )
            with self.db._lock:
                self.db.complete_job(
                    job["id"],
                    exit_code=0,
                    stdout_tail="",
                    stderr_tail="",
                    outcome_reason="check_command: no work (skipped)",
                )
            try:
                self.scheduler.update_next_run(rj_id, completed_at=time.time(), job_id=job["id"])
            except Exception:
                _log.exception("Failed to advance next_run for recurring job id=%d after skip", rj_id)
            return "skip"
        elif code == 2:
            _log.info(
                "check_command exit 2 for recurring job id=%d (%s) — permanently done, auto-disabling",
                rj_id, recurring_job.get("name", ""),
            )
            self.db.disable_recurring_job(rj_id, "check_command signaled complete")
            with self.db._lock:
                self.db.complete_job(
                    job["id"],
                    exit_code=0,
                    stdout_tail="",
                    stderr_tail="",
                    outcome_reason="check_command: permanently done (auto-disabled)",
                )
            return "disable"
        else:
            _log.warning(
                "check_command returned unknown exit code %d for recurring job id=%d — "
                "proceeding (fail-open)",
                code, rj_id,
            )
            return "proceed"
```

**Step 4: Integrate check_command into `_run_job()`**

At the start of `_run_job()`, before the `try:` block's `subprocess.Popen` call, add the check_command guard. Insert after `vram_before = self._free_vram_mb()` and before the `try:` block:

```python
        # Pre-flight check_command: gate job on external signal
        if job.get("recurring_job_id"):
            _rj = self.db.get_recurring_job(job["recurring_job_id"])
            if _rj and _rj.get("check_command"):
                _check_result = self._run_check_command(job, _rj)
                if _check_result in ("skip", "disable"):
                    return
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/test_daemon.py -k "check_command" -v
```
Expected: 6 PASSED

**Step 6: Run full suite**

```bash
pytest --timeout=120 -x -q
```
Expected: 251 tests pass.

**Step 7: Commit**

```bash
git add ollama_queue/daemon.py tests/test_daemon.py
git commit -m "feat: add check_command pre-flight gate to _run_job executor thread"
```

---

### Task 3: Daemon — max_runs Countdown

**Files:**
- Modify: `ollama_queue/daemon.py`
- Test: `tests/test_daemon.py`

**Step 1: Write failing tests**

Add to `tests/test_daemon.py`:

```python
def test_max_runs_decrements_on_success(daemon):
    """Successful main job decrements max_runs by 1."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=3)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["max_runs"] == 2


def test_max_runs_no_decrement_on_failure(daemon):
    """Failed main job does NOT decrement max_runs."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=3)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 1  # failure
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"", b"err")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["max_runs"] == 3  # unchanged


def test_max_runs_zero_disables_job(daemon):
    """When max_runs reaches 0 after success, recurring job is auto-disabled."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=1)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["enabled"] == 0
    assert "max_runs" in (rj["outcome_reason"] or "").lower()


def test_no_max_runs_no_decrement(daemon):
    """Job with max_runs=None doesn't touch the field."""
    rj_id, job_id = _make_recurring_and_job(daemon.db, max_runs=None)
    job = daemon.db.get_job(job_id)

    with patch("ollama_queue.daemon.subprocess") as mock_sub:
        proc = MagicMock()
        proc.pid = 9999
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        with patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"ok", b"")):
            daemon._run_job(job)

    rj = daemon.db.get_recurring_job(rj_id)
    assert rj["max_runs"] is None
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_daemon.py -k "max_runs" -v
```
Expected: 4 FAILED

**Step 3: Implement max_runs logic in `_run_job()`**

After the `if exit_code == 0:` block that records duration (around line 519), add the max_runs check. The block will be inside the existing `if exit_code == 0:` block but AFTER `record_duration()`:

```python
            # max_runs countdown: decrement on success, auto-disable at 0
            if job.get("recurring_job_id"):
                _rj_for_maxruns = self.db.get_recurring_job(job["recurring_job_id"])
                if _rj_for_maxruns and _rj_for_maxruns.get("max_runs") is not None:
                    remaining = _rj_for_maxruns["max_runs"] - 1
                    if remaining <= 0:
                        self.db.disable_recurring_job(
                            job["recurring_job_id"], "max_runs exhausted"
                        )
                        _log.info(
                            "Recurring job id=%d auto-disabled: max_runs exhausted",
                            job["recurring_job_id"],
                        )
                    else:
                        self.db.update_recurring_job(
                            job["recurring_job_id"], max_runs=remaining
                        )
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_daemon.py -k "max_runs" -v
```
Expected: 4 PASSED

**Step 5: Run full suite**

```bash
pytest --timeout=120 -x -q
```
Expected: 255 tests pass.

**Step 6: Commit**

```bash
git add ollama_queue/daemon.py tests/test_daemon.py
git commit -m "feat: add max_runs countdown with auto-disable on exhaustion"
```

---

### Task 4: API — Models + /enable Endpoint

**Files:**
- Modify: `ollama_queue/api.py`
- Test: `tests/test_api.py`

**Step 1: Write failing tests**

Add to `tests/test_api.py` (within `TestScheduleAPI` class or as standalone):

```python
def test_add_recurring_job_with_check_command(client):
    r = client.post("/api/schedule", json={
        "name": "check-job",
        "command": "echo hi",
        "interval_seconds": 3600,
        "check_command": "exit 0",
        "max_runs": 5,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["check_command"] == "exit 0"
    assert data["max_runs"] == 5


def test_update_recurring_job_check_command(client):
    client.post("/api/schedule", json={
        "name": "upd-job",
        "command": "echo hi",
        "interval_seconds": 3600,
    })
    r = client.put("/api/schedule/1", json={"check_command": "exit 1"})
    assert r.status_code == 200


def test_enable_endpoint_clears_disabled_job(client):
    # Create and disable a job
    client.post("/api/schedule", json={
        "name": "disabled-job",
        "command": "echo hi",
        "interval_seconds": 3600,
    })
    client.put("/api/schedule/1", json={"enabled": False})
    # Re-enable via POST endpoint
    r = client.post("/api/schedule/jobs/disabled-job/enable")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Verify it's enabled in list
    jobs = client.get("/api/schedule").json()
    job = next(j for j in jobs if j["name"] == "disabled-job")
    assert job["enabled"] == 1


def test_enable_endpoint_not_found(client):
    r = client.post("/api/schedule/jobs/nonexistent/enable")
    assert r.status_code == 404
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api.py -k "check_command or enable_endpoint" -v
```
Expected: 4 FAILED

**Step 3: Update Pydantic models in `api.py`**

Add two fields to `RecurringJobCreate` (after `pinned: bool = False`):

```python
class RecurringJobCreate(BaseModel):
    name: str
    command: str
    interval_seconds: int | None = None
    cron_expression: str | None = None
    model: str | None = None
    priority: int = 5
    timeout: int = 600
    source: str | None = None
    tag: str | None = None
    max_retries: int = 0
    resource_profile: str = "ollama"
    pinned: bool = False
    check_command: str | None = None    # NEW
    max_runs: int | None = None         # NEW
```

Add two fields to `RecurringJobUpdate` (after `pinned: bool | None = None`):

```python
class RecurringJobUpdate(BaseModel):
    enabled: bool | None = None
    priority: int | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    tag: str | None = None
    command: str | None = None
    name: str | None = None
    model: str | None = None
    timeout: int | None = None
    max_retries: int | None = None
    pinned: bool | None = None
    check_command: str | None = None    # NEW
    max_runs: int | None = None         # NEW
```

**Step 4: Add `/enable` endpoint in `api.py`**

Add after the `@app.post("/api/schedule/{rj_id}/run-now")` block (around line 448), BEFORE the parameterized `GET /api/schedule/{rj_id}/runs` to avoid route shadowing:

```python
    @app.post("/api/schedule/jobs/{name}/enable")
    def enable_schedule_by_name(name: str):
        """Re-enable a recurring job that was auto-disabled, clearing outcome_reason."""
        if not db.set_recurring_job_enabled(name, True):
            raise HTTPException(status_code=404, detail="Recurring job not found")
        return {"ok": True}
```

**IMPORTANT:** This route uses the job *name* (string) rather than id to make it easy to call from CLI and scripts. It must be placed before any `/{rj_id}` parameterized route that would match first.

**Step 5: Run tests to verify they pass**

```bash
pytest tests/test_api.py -k "check_command or enable_endpoint" -v
```
Expected: 4 PASSED

**Step 6: Run full suite**

```bash
pytest --timeout=120 -x -q
```
Expected: 259 tests pass.

**Step 7: Commit**

```bash
git add ollama_queue/api.py tests/test_api.py
git commit -m "feat: add check_command/max_runs to API models, add /enable endpoint"
```

---

### Task 5: CLI — Flags + enable Clears Reason

**Files:**
- Modify: `ollama_queue/cli.py`
- Test: `tests/test_cli.py`

**Step 1: Write failing tests**

Add to `tests/test_cli.py`:

```python
from click.testing import CliRunner
from ollama_queue.cli import main


def test_schedule_add_check_command(tmp_path):
    """schedule add accepts --check-command and --max-runs flags."""
    db_path = str(tmp_path / "q.db")
    runner = CliRunner()
    result = runner.invoke(main, [
        "--db", db_path,
        "schedule", "add",
        "--name", "cc-test",
        "--interval", "1h",
        "--check-command", "exit 0",
        "--max-runs", "10",
        "echo", "hello",
    ])
    assert result.exit_code == 0, result.output
    assert "cc-test" in result.output


def test_schedule_edit_check_command(tmp_path):
    """schedule edit accepts --check-command flag."""
    db_path = str(tmp_path / "q.db")
    runner = CliRunner()
    runner.invoke(main, [
        "--db", db_path, "schedule", "add",
        "--name", "edit-test", "--interval", "1h", "echo", "hi",
    ])
    result = runner.invoke(main, [
        "--db", db_path,
        "schedule", "edit", "edit-test",
        "--check-command", "exit 1",
    ])
    assert result.exit_code == 0, result.output


def test_schedule_enable_clears_outcome_reason(tmp_path):
    """schedule enable clears outcome_reason after auto-disable."""
    from ollama_queue.db import Database
    db_path = str(tmp_path / "q.db")
    db = Database(db_path)
    db.initialize()
    rj_id = db.add_recurring_job(
        name="re-enable-test", command="echo hi", interval_seconds=3600
    )
    db.disable_recurring_job(rj_id, "max_runs exhausted")

    runner = CliRunner()
    result = runner.invoke(main, [
        "--db", db_path, "schedule", "enable", "re-enable-test",
    ])
    assert result.exit_code == 0
    rj = db.get_recurring_job(rj_id)
    assert rj["enabled"] == 1
    assert rj["outcome_reason"] is None
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cli.py -k "check_command or edit_check or enable_clears" -v
```
Expected: 3 FAILED

**Step 3: Add `--check-command` and `--max-runs` to `schedule_add` in `cli.py`**

Add two new options before `@click.argument("command", nargs=-1, required=True)`:

```python
@click.option("--check-command", "check_command", default=None,
              help="Shell command to run before job; exit 0=run, 1=skip, 2=disable")
@click.option("--max-runs", "max_runs", default=None, type=int,
              help="Auto-disable after N successful completions")
```

Add the params to the `schedule_add` function signature:

```python
def schedule_add(
    ctx, name, interval, at, days, cron, model, priority, timeout,
    tag, source, max_retries, profile, pin,
    check_command, max_runs,   # NEW
    command,
):
```

Add them to the `db.add_recurring_job(...)` call:

```python
    rj_id = db.add_recurring_job(
        name=name,
        command=" ".join(command),
        interval_seconds=interval_seconds,
        cron_expression=cron_expression,
        model=model,
        priority=priority,
        timeout=timeout,
        source=source or name,
        tag=tag,
        resource_profile=profile,
        max_retries=max_retries,
        pinned=pin,
        check_command=check_command,   # NEW
        max_runs=max_runs,             # NEW
    )
```

**Step 4: Add `--check-command` to `schedule_edit` in `cli.py`**

Add option before `@click.pass_context` on `schedule_edit`:

```python
@click.option("--check-command", "check_command", default=None,
              help="New check_command (empty string to clear)")
@click.option("--max-runs", "max_runs", default=None, type=int,
              help="New max_runs countdown")
```

Add to function signature and update the `fields` dict inside `schedule_edit`:

```python
def schedule_edit(ctx, name, priority, interval, new_command, pin, check_command, max_runs):
    ...
    if check_command is not None:
        fields["check_command"] = check_command if check_command else None
    if max_runs is not None:
        fields["max_runs"] = max_runs
```

**Step 5: `schedule enable` already exists** — it calls `set_recurring_job_enabled(name, True)` which (after Task 1) already clears `outcome_reason`. No code change needed; the test in Step 1 verifies the wiring.

**Step 6: Run tests to verify they pass**

```bash
pytest tests/test_cli.py -k "check_command or edit_check or enable_clears" -v
```
Expected: 3 PASSED

**Step 7: Run full suite**

```bash
pytest --timeout=120 -x -q
```
Expected: 262 tests pass.

**Step 8: Commit**

```bash
git add ollama_queue/cli.py tests/test_cli.py
git commit -m "feat: add --check-command and --max-runs CLI flags to schedule add/edit"
```

---

### Task 6: Dashboard — Check + Runs Columns

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Plan.jsx`

No unit tests for this task — verify visually after `npm run build`.

**Step 1: Update `COLUMNS` array (line 122)**

Change:
```javascript
const COLUMNS = ['Name', 'Model', 'VRAM', 'Schedule', 'Priority', 'Next Run', 'ETA', '\u2605', 'Enabled', ''];
```

To:
```javascript
const COLUMNS = ['Name', 'Model', 'VRAM', 'Schedule', 'Priority', 'Next Run', 'ETA', 'Check', 'Runs', '\u2605', 'Enabled', ''];
```

**Step 2: Add two new `<td>` cells in the job row renderer**

After the `ETA` cell (the `rj.estimated_duration` cell, around line 532-535) and before the pin `★` cell:

```jsx
                <td style={{ textAlign: 'center', fontSize: 'var(--type-label)',
                             color: 'var(--status-success)' }}>
                    {rj.check_command ? '\u2713' : ''}
                </td>
                <td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)',
                             fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                    {rj.max_runs != null ? `${rj.max_runs} left` : ''}
                </td>
```

**Step 3: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build
cd ../../..
```
Expected: Build succeeds, no JSX errors.

Open dashboard → Plan tab → verify two new columns appear.

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Plan.jsx
git add ollama_queue/dashboard/spa/dist/
git commit -m "feat: add Check and Runs columns to Schedule tab"
```

---

### Task 7: Update Lessons-DB Wrapper Scripts

Once the feature is live, update the three wrapper scripts so their short-circuit logic exits with proper codes instead of returning `exit 0`.

**Files:**
- Modify: `~/Documents/projects/lessons-db/scripts/meta-extract-principles.sh`
- Modify: `~/Documents/projects/lessons-db/scripts/meta-generate-meta-lessons.sh`
- Modify: `~/Documents/projects/lessons-db/scripts/meta-eval-generate.sh`

**Step 1: Update `meta-extract-principles.sh`**

Replace:
```bash
if [ "$remaining" -eq 0 ]; then
    log "All lessons have principles — nothing to do"
    exit 0
fi
```

With:
```bash
if [ "$remaining" -eq 0 ]; then
    log "All lessons have principles — nothing to do"
    exit 1   # check_command: no work this interval
fi
```

Note: for extract-principles, exit 1 (not 2) is correct — more lessons may be added later, so the job should remain enabled and simply skip when caught up.

**Step 2: Update `meta-generate-meta-lessons.sh`**

Replace:
```bash
if [ -z "$pending" ] || [ "$pending" -eq 0 ] 2>/dev/null; then
    log "All clusters have meta-lessons — nothing to do"
    exit 0
fi
```

With:
```bash
if [ -z "$pending" ] || [ "$pending" -eq 0 ] 2>/dev/null; then
    log "All clusters have meta-lessons — nothing to do"
    exit 1   # check_command: no work (more clusters may form later)
fi
```

**Step 3: Update `meta-eval-generate.sh`**

Replace the short-circuit block:
```bash
    if [ "$completed" -ge "$max_expected" ] 2>/dev/null; then
        log "All $completed pairs complete — nothing to do"
        exit 0
    fi
```

With:
```bash
    if [ "$completed" -ge "$max_expected" ] 2>/dev/null; then
        log "All $completed pairs complete — signaling done"
        exit 2   # check_command: permanently done — auto-disable
    fi
```

Note: eval-generate uses exit 2 (auto-disable) because once all 180 (variant, lesson) pairs are done, the job should stop running until manually re-enabled.

**Step 4: Update recurring job registrations in ollama-queue**

Wire `check_command` into the existing registered jobs (remove old in-script short-circuit by delegating it to the queue):

```bash
ollama-queue schedule edit lessons-db-extract-principles \
    --check-command "bash $HOME/Documents/projects/lessons-db/scripts/meta-extract-principles.sh --check-only"
```

Actually, since the wrapper scripts already contain the check logic, the simplest approach is to **use the wrapper script itself as the check_command**. The existing check logic at the top of each script (before the LLM invocation) is the check; the LLM invocation is the main command.

Refactor approach: Split each wrapper into a `check` function (exits 0/1/2) and an `exec` function (runs LLM), then register:
```
check_command = "wrapper.sh --check"
command       = "wrapper.sh --run"
```

OR: Keep wrapper scripts as-is (they return exit codes now), register the whole script as `check_command`, and write thin `exec` scripts. This is a refactoring task to do alongside or after the feature lands — the current scripts are functional.

**Simple approach for now:** Register each script's check logic inline as the `check_command`. The wrapper script's LLM invocation remains as the `command`. Use `update` on existing jobs:

```bash
# Extract principles: check_command exits 1 when nothing to do
ollama-queue schedule edit lessons-db-extract-principles \
    --check-command \
    'python3 ~/Documents/projects/lessons-db/.venv/bin/python3 -c "import sqlite3; c=sqlite3.connect(\"$HOME/.local/share/lessons-db/lessons.db\"); r=c.execute(\"SELECT COUNT(*) FROM lessons WHERE principle IS NULL\").fetchone()[0]; exit(1 if r == 0 else 0)"'
```

This inline approach eliminates the need for separate check scripts. The wrapper scripts then become pure executors (remove the short-circuit preamble).

**Alternatively (recommended):** Keep the existing wrapper scripts for now — they work correctly with exit codes. Register them as the `check_command` with a lighter sub-check script. Leave the detailed refactor for a follow-up PR.

**Step 5: Commit (lessons-db repo)**

```bash
cd ~/Documents/projects/lessons-db
git add scripts/meta-extract-principles.sh scripts/meta-generate-meta-lessons.sh scripts/meta-eval-generate.sh
git commit -m "feat: update wrapper scripts to emit check_command exit codes (1=skip, 2=done)"
```

---

### Task 8: Final Integration Test + PR

**Step 1: Run full test suite one more time**

```bash
cd ~/Documents/projects/ollama-queue
pytest --timeout=120 -q
```
Expected: 262 tests pass.

**Step 2: Build dashboard**

```bash
cd ollama_queue/dashboard/spa && npm run build && cd ../../..
```

**Step 3: Quick integration verify**

```bash
# Start the server in background
ollama-queue serve --port 7684 &
sleep 2

# Register a test job with check_command
ollama-queue --db /tmp/test-queue.db schedule add \
    --name "test-check" \
    --interval 30m \
    --check-command "exit 1" \
    echo hello

# Verify it shows check_command in list
ollama-queue schedule list

kill %1 2>/dev/null
```

**Step 4: Push branch and create PR**

```bash
git push -u origin feature/check-command
gh pr create \
    --base docs/check-command-design \
    --title "feat: check_command + max_runs for recurring jobs" \
    --body "$(cat <<'EOF'
## Summary
- Adds `check_command` pre-flight gate: exit 0=run, 1=skip, 2=auto-disable
- Adds `max_runs` countdown: auto-disables after N successful completions
- Failed runs do NOT count toward max_runs (convergence measure, not attempts)
- check_command runs in executor thread (not poll thread) — never blocks scheduling
- API, CLI, and dashboard updated
- Lessons-db wrapper scripts updated with proper exit codes

## Test plan
- [x] 262 tests pass (up from 239)
- [x] Dashboard builds without JSX errors
- [x] `schedule add --check-command` / `--max-runs` accepted
- [x] check_command exit 1 skips, exit 2 auto-disables
- [x] max_runs reaches 0 → auto-disabled
- [x] `schedule enable` re-enables and clears outcome_reason

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Execution Notes

- **venv:** Always `source .venv/bin/activate` before running pytest or ollama-queue CLI
- **Dashboard dist/ is gitignored** — must `npm run build` before committing dist changes
- **check_same_thread=False** — SQLite calls from executor thread are safe (WAL mode, RLock)
- **db._lock is RLock** — worker threads can acquire it while poll thread holds it. Never change to Lock.
- **`subprocess.run` vs `subprocess.Popen`** — use `subprocess.run` for check_command (short-lived, capture output) and keep `subprocess.Popen` for the main job (streaming output via pipe drain)
