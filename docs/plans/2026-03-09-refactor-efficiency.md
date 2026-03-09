# Efficiency Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce per-poll overhead in the daemon and improve consistency across scanner/patcher/health modules by eliminating redundant DB queries, subprocess calls, and object instantiations.

**Architecture:** Five independent task groups with non-overlapping file ownership — safe for parallel sub-agent execution. Changes are internal-only (no API surface changes, no schema changes).

**Tech Stack:** Python 3.12, SQLite (threading.RLock pattern), subprocess, threading.

---

## Pre-flight

```bash
cd ~/Documents/projects/ollama-queue/.worktrees/refactor
source .venv/bin/activate
pytest --timeout=120 -x -q   # must be green before starting
```

Expected: 696 tests, all pass.

---

## Task Group A — daemon.py + estimator.py
### Items 1, 2, 13: Settings batch-fetch, model classification cache, OllamaModels reuse

**Files:**
- Modify: `ollama_queue/daemon.py`
- Modify: `ollama_queue/estimator.py`
- Test: `tests/test_daemon.py`, `tests/test_estimator.py`

**Context:**
`poll_once()` currently calls `self.db.get_setting()` individually 4-6 times per cycle (each acquires `db._lock`, opens connection, queries). `get_all_settings()` already exists — one call fetches all. Additionally, `_can_admit()` classifies the same model 1-3 times; classify() does regex on every call. Finally, `DurationEstimator.queue_etas()` instantiates `OllamaModels()` fresh every call — daemon already has `self._ollama_models`.

### Step A1: Write failing test for settings pass-through

In `tests/test_daemon.py`, add:

```python
def test_poll_once_calls_get_all_settings_once(daemon_fixture):
    """get_all_settings called once per poll, not per sub-method."""
    with patch.object(daemon_fixture.db, "get_all_settings", wraps=daemon_fixture.db.get_all_settings) as mock_gs, \
         patch.object(daemon_fixture.db, "get_setting") as mock_single:
        daemon_fixture.poll_once()
    # get_setting should NOT be called for settings that are now batch-fetched
    calls = [c.args[0] for c in mock_single.call_args_list]
    batch_fetched = {"entropy_alert_sigma", "entropy_suspend_low_priority",
                     "cpu_offload_efficiency", "min_model_vram_mb"}
    for key in batch_fetched:
        assert key not in calls, f"get_setting('{key}') called individually — should use batch"
    assert mock_gs.call_count >= 1
```

Run: `pytest tests/test_daemon.py::test_poll_once_calls_get_all_settings_once -v`
Expected: FAIL (get_setting still called individually).

### Step A2: Refactor daemon.py — batch settings in poll_once

In `poll_once()` (around line 534), settings are fetched at line 634 (`self.db.get_all_settings()`). Move this call EARLIER — before `_check_entropy` and before `_can_admit`. Then:

**a) Pass settings to `_check_entropy`:**

Change signature:
```python
def _check_entropy(self, pending_jobs: list[dict], now: float, settings: dict) -> None:
```

Inside `_check_entropy`, replace:
```python
sigma = float(self.db.get_setting("entropy_alert_sigma") or 2.0)
```
with:
```python
sigma = float(settings.get("entropy_alert_sigma") or 2.0)
```

And replace:
```python
suspend_enabled = self.db.get_setting("entropy_suspend_low_priority")
```
with:
```python
suspend_enabled = settings.get("entropy_suspend_low_priority")
```

Update the call site in `poll_once()` (currently at line ~606):
```python
self._check_entropy(pending_jobs, now, settings)
```

**b) Pass settings to `_can_admit`:**

Change signature:
```python
def _can_admit(self, job: dict, settings: dict) -> bool:
```

Inside `_can_admit`, replace:
```python
settings = self.db.get_all_settings()  # line 320 — remove this
```
(Delete that line — settings now passed in.)

Replace these individual setting reads:
```python
# _max_slots() calls get_setting("max_concurrent_jobs")
# _shadow_hours() calls get_setting("concurrent_shadow_hours")
# _compute_max_workers() calls get_setting("cpu_offload_efficiency") and get_setting("min_model_vram_mb")
```

Change `_max_slots()` to accept optional settings:
```python
def _max_slots(self, settings: dict | None = None) -> int:
    if settings is not None:
        return max(1, int(settings.get("max_concurrent_jobs") or 1))
    return max(1, int(self.db.get_setting("max_concurrent_jobs") or 1))
```

Change `_shadow_hours()` similarly:
```python
def _shadow_hours(self, settings: dict | None = None) -> float:
    if settings is not None:
        return float(settings.get("concurrent_shadow_hours") or 24)
    return float(self.db.get_setting("concurrent_shadow_hours") or 24)
```

Pass `settings` through `_in_shadow_mode()` → `_shadow_hours()` and through the `_max_slots()` call inside `_can_admit`.

Also read `max_vram_mb` from settings:
```python
# Replace:
max_vram_raw = self.db.get_setting("max_vram_mb")
# With:
max_vram_raw = settings.get("max_vram_mb")
```

Update the two `_can_admit(job)` call sites in `poll_once()`:
```python
if not self._can_admit(job, settings):
```

**c) Cache model classification in `_can_admit`:**

At the top of `_can_admit`, after the `profile` line (line 260), the `classify()` call is already stored in `profile`. But `classify()` is called AGAIN at line 268 for the embed count loop:

```python
# line 268 - currently:
if self._ollama_models.classify(self._running_models.get(jid, ""))["resource_profile"] == "embed"
```

This is fine — that's classifying *running* jobs (different models). The redundancy to fix is line 296:
```python
# _max_slots() is called twice (line 294 and implicitly). Already fixed by passing settings.
```

The real fix for item 2: `profile` is computed once at line 260. Ensure it's not recomputed. Check line 307:
```python
model_vram = self._ollama_models.estimate_vram_mb(model, self.db) if model else 0.0
```
This is `estimate_vram_mb`, not `classify` — OK. No duplicate classification after the `profile` variable is set. ✓

### Step A3: Fix estimator.py — pass OllamaModels instance

In `estimator.py`, change `queue_etas()`:

```python
def queue_etas(self, queue_jobs: list[dict], om: OllamaModels | None = None) -> list[dict]:
    """...(existing docstring)..."""
    results = []
    cumulative_offset: float = 0.0
    if om is None:
        om = OllamaModels()

    for job in queue_jobs:
        ...
```

In `daemon.py`, find where `DurationEstimator(self.db).queue_etas(...)` is called (check around line 633):
```python
# Change from:
estimator = DurationEstimator(self.db)
etas = estimator.queue_etas(pending_jobs)
# To:
estimator = DurationEstimator(self.db)
etas = estimator.queue_etas(pending_jobs, om=self._ollama_models)
```

Also check `api.py` for any `DurationEstimator(...).queue_etas(...)` calls — pass `om=None` (will instantiate its own, acceptable for API path which doesn't have a shared instance).

### Step A4: Run tests

```bash
pytest tests/test_daemon.py tests/test_estimator.py -v --timeout=120
```
Expected: all pass.

### Step A5: Run full suite

```bash
pytest --timeout=120 -x -q
```
Expected: 696 tests, all pass.

### Step A6: Commit

```bash
git add ollama_queue/daemon.py ollama_queue/estimator.py tests/test_daemon.py
git commit -m "perf: batch settings fetch in poll_once, pass OllamaModels to estimator"
```

---

## Task Group B — health.py + models.py
### Item 4: TTL caching for subprocess calls (nvidia-smi, ollama list)

**Files:**
- Modify: `ollama_queue/health.py`
- Modify: `ollama_queue/models.py`
- Test: `tests/test_health.py`, `tests/test_models.py`

**Context:**
`HealthMonitor.get_vram_pct()` runs `nvidia-smi` every call (called every 5s poll). `OllamaModels.list_local()` runs `ollama list` every call (called on every model check). Add simple time-based TTL caches using `time.monotonic()`.

### Step B1: Write failing test for VRAM cache

In `tests/test_health.py`, add:

```python
def test_get_vram_pct_cached(monkeypatch):
    """nvidia-smi subprocess called at most once per TTL window."""
    import time
    from ollama_queue.health import HealthMonitor

    call_count = 0
    def fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return type("R", (), {"returncode": 0, "stdout": "1024\n"})()

    monkeypatch.setattr("ollama_queue.health.subprocess.run", fake_run)
    h = HealthMonitor()
    h.get_vram_pct()
    h.get_vram_pct()
    h.get_vram_pct()
    assert call_count == 1, "nvidia-smi should be called once within TTL window"
```

Run: `pytest tests/test_health.py::test_get_vram_pct_cached -v`
Expected: FAIL.

### Step B2: Add VRAM cache to HealthMonitor

In `health.py`, add instance state to `__init__`:
```python
def __init__(self) -> None:
    self._vram_cache: tuple[float, float | None] | None = None  # (timestamp, value)
    self._VRAM_TTL = 5.0  # seconds
```

Wrap `get_vram_pct()`:
```python
def get_vram_pct(self) -> float | None:
    now = time.monotonic()
    if self._vram_cache is not None:
        ts, val = self._vram_cache
        if now - ts < self._VRAM_TTL:
            return val
    result = self._fetch_vram_pct()
    self._vram_cache = (now, result)
    return result

def _fetch_vram_pct(self) -> float | None:
    """Query nvidia-smi for VRAM usage percentage (uncached)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        line = result.stdout.strip().split("\n")[0]
        parts = line.split(",")
        used = float(parts[0].strip())
        total = float(parts[1].strip())
        if total == 0:
            return 0.0
        return round(used / total * 100, 1)
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, UnicodeDecodeError):
        return None
```

Add `import time` if not present.

### Step B3: Write failing test for model list cache

In `tests/test_models.py`, add:

```python
def test_list_local_cached(monkeypatch):
    """ollama list subprocess called at most once per TTL window."""
    from ollama_queue.models import OllamaModels

    call_count = 0
    def fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return type("R", (), {"returncode": 0, "stdout": "NAME\nqwen2.5:7b\n"})()

    monkeypatch.setattr("ollama_queue.models.subprocess.run", fake_run)
    om = OllamaModels()
    om.list_local()
    om.list_local()
    om.list_local()
    assert call_count == 1, "ollama list should be called once within TTL window"
```

Run: `pytest tests/test_models.py::test_list_local_cached -v`
Expected: FAIL.

### Step B4: Add model list TTL cache to OllamaModels

Find `list_local()` in `models.py`. Add class-level cache (shared across instances since models rarely change):

```python
_list_local_cache: tuple[float, list[dict]] | None = None
_LIST_LOCAL_TTL = 60.0  # seconds — models change rarely

@classmethod
def _invalidate_list_cache(cls) -> None:
    """Call after pulling/deleting a model."""
    cls._list_local_cache = None
```

Wrap the existing `list_local()` body:
```python
def list_local(self) -> list[dict]:
    """Return locally available Ollama models with 60s TTL cache."""
    import time
    now = time.monotonic()
    if OllamaModels._list_local_cache is not None:
        ts, val = OllamaModels._list_local_cache
        if now - ts < OllamaModels._LIST_LOCAL_TTL:
            return val
    result = self._fetch_list_local()
    OllamaModels._list_local_cache = (now, result)
    return result

def _fetch_list_local(self) -> list[dict]:
    """Run 'ollama list' and parse output (uncached)."""
    # ... move existing list_local() body here ...
```

Call `OllamaModels._invalidate_list_cache()` in `models.py` after any pull/delete operations.

### Step B5: Fix models.py string parsing (item 5)

In `models.py` around line 318 (inside `_monitor()` or VRAM-parsing code):

Find pattern like:
```python
[p for p in line.split() if p.endswith("%")]
```
Replace with:
```python
next((p.rstrip("%") for p in line.split() if p.endswith("%")), None)
```

### Step B6: Improve pull monitor error logging (item 14)

In `models.py` inside `_monitor()` thread (lines 313-346), wrap each DB update site with specific error logging:

```python
try:
    with db._lock:
        conn = db._connect()
        conn.execute("UPDATE model_pulls SET progress=?, status=? WHERE model=?", ...)
        conn.commit()
except Exception as exc:
    _log.error("Pull monitor: failed to update progress for %s: %s", model_name, exc)
```

Ensure the outer catch-all still exists as a final safety net.

### Step B7: Run tests

```bash
pytest tests/test_health.py tests/test_models.py -v --timeout=120
```
Expected: all pass.

### Step B8: Run full suite

```bash
pytest --timeout=120 -x -q
```
Expected: 696 tests, all pass.

### Step B9: Commit

```bash
git add ollama_queue/health.py ollama_queue/models.py tests/test_health.py tests/test_models.py
git commit -m "perf: TTL cache nvidia-smi and ollama list subprocess calls"
```

---

## Task Group C — patcher.py + intercept.py
### Item 9: Subprocess error handling consistency

**Files:**
- Modify: `ollama_queue/patcher.py`
- Modify: `ollama_queue/intercept.py`
- Test: `tests/test_patcher.py`, `tests/test_intercept.py`

**Context:**
`scanner.py` correctly logs a WARNING and returns `[]` on non-zero subprocess exit. `patcher.py` and `intercept.py` have inconsistent patterns — some check returncode, some don't. Add consistent returncode checking.

### Step C1: Audit and fix patcher.py

Read `patcher.py` fully. For every `subprocess.run()` call:
- If `returncode != 0` and the result is used: log `_log.warning(...)` with returncode + stderr, then handle gracefully.
- For `_reload_systemd()` and `_restart_service()`: check returncode, log warning on failure, return `False`.

Pattern to apply:
```python
result = subprocess.run([...], capture_output=True, text=True, timeout=10)
if result.returncode != 0:
    _log.warning("%s exited %d: %s", "service-name", result.returncode, result.stderr.strip())
    return False  # or [] or appropriate falsy value
```

**Do NOT** add `# noqa` comments — ruff's `per-file-ignores` in `ruff.toml` already suppresses S603/S607 for patcher.py.

### Step C2: Audit and fix intercept.py

Read `intercept.py` fully. For every `subprocess.run()` / `subprocess.check_call()` call in `enable_intercept()` and `disable_intercept()`:
- If the call can fail, check `returncode`.
- Log warning with stderr on failure.
- Return structured error dict consistent with the existing `{"enabled": ..., "error": ...}` pattern.

### Step C3: Run tests

```bash
pytest tests/test_patcher.py tests/test_intercept.py -v --timeout=120
```
Expected: all pass.

### Step C4: Run full suite

```bash
pytest --timeout=120 -x -q
```
Expected: 696 tests, all pass.

### Step C5: Commit

```bash
git add ollama_queue/patcher.py ollama_queue/intercept.py
git commit -m "fix: consistent subprocess returncode checking in patcher and intercept"
```

---

## Task Group D — scheduler.py + db.py
### Items 3, 6, 15: Scheduler caching, sentinel filtering, batch recurring next_run

**Files:**
- Modify: `ollama_queue/scheduler.py`
- Modify: `ollama_queue/db.py`
- Test: `tests/test_scheduler.py`, `tests/test_db.py`

**Context:**
`scheduler.py` calls `list_recurring_jobs()` on every `load_map()` call (every dashboard refresh — no cache). `db.get_pending_jobs()` lacks an `exclude_sentinel=True` parameter for sentinel job filtering. `scheduler.py` calls separate lock acquisitions for `list_recurring_jobs()` + `_set_recurring_next_run()`.

### Step D1: Write failing test for scheduler cache

In `tests/test_scheduler.py`, add:

```python
def test_list_recurring_jobs_cached(scheduler_fixture, db_fixture):
    """list_recurring_jobs called at most once per cache window on repeated load_map()."""
    with patch.object(db_fixture, "list_recurring_jobs", wraps=db_fixture.list_recurring_jobs) as mock_list:
        scheduler_fixture.load_map()
        scheduler_fixture.load_map()
        scheduler_fixture.load_map()
    assert mock_list.call_count == 1, "list_recurring_jobs should be cached within TTL"
```

Run: `pytest tests/test_scheduler.py::test_list_recurring_jobs_cached -v`
Expected: FAIL.

### Step D2: Add recurring jobs cache to Scheduler

In `scheduler.py`, add to `__init__`:
```python
self._jobs_cache: tuple[float, list[dict]] | None = None
self._JOBS_CACHE_TTL = 10.0  # seconds

def _invalidate_jobs_cache(self) -> None:
    self._jobs_cache = None
```

Add cached accessor:
```python
def _get_recurring_jobs(self) -> list[dict]:
    now = time.monotonic()
    if self._jobs_cache is not None:
        ts, jobs = self._jobs_cache
        if now - ts < self._JOBS_CACHE_TTL:
            return jobs
    jobs = self.db.list_recurring_jobs()
    self._jobs_cache = (now, jobs)
    return jobs
```

In `load_map()`, replace `self.db.list_recurring_jobs()` with `self._get_recurring_jobs()`.

Call `self._invalidate_jobs_cache()` in any method that adds/removes/updates recurring jobs (check `promote_due_jobs`, `add_recurring_job`, `delete_recurring_job`).

### Step D3: Add batch_set_recurring_next_runs to db.py (item 3)

Add to `db.py`:
```python
def batch_set_recurring_next_runs(self, updates: dict[int, float]) -> None:
    """Update next_run for multiple recurring jobs in one lock acquisition.

    updates: {job_id: next_run_timestamp}
    """
    if not updates:
        return
    with self._lock:
        conn = self._connect()
        conn.executemany(
            "UPDATE recurring_jobs SET next_run=? WHERE id=?",
            [(next_run, job_id) for job_id, next_run in updates.items()],
        )
        conn.commit()
```

In `scheduler.py`'s `promote_due_jobs()`, collect `next_run` updates and call `self.db.batch_set_recurring_next_runs(updates)` once instead of calling `_set_recurring_next_run()` per job.

### Step D4: Add exclude_sentinel parameter to get_pending_jobs (item 6)

In `db.py`, find `get_pending_jobs()`. Add parameter:
```python
def get_pending_jobs(self, exclude_sentinel: bool = True) -> list[dict]:
```

If `exclude_sentinel=True` (default), add `AND command NOT LIKE 'proxy:%'` to the WHERE clause. This makes the default safe and explicit.

**Note:** Verify `get_pending_jobs()` call sites in daemon.py — the CLAUDE.md already notes sentinels are filtered. Set `exclude_sentinel=True` as default so existing callers automatically benefit.

### Step D5: Run tests

```bash
pytest tests/test_scheduler.py tests/test_db.py -v --timeout=120
```
Expected: all pass.

### Step D6: Run full suite

```bash
pytest --timeout=120 -x -q
```
Expected: 696 tests, all pass.

### Step D7: Commit

```bash
git add ollama_queue/scheduler.py ollama_queue/db.py tests/test_scheduler.py tests/test_db.py
git commit -m "perf: cache recurring jobs list, batch next_run updates, explicit sentinel filtering"
```

---

## Task Group E — eval_engine.py + scanner.py
### Items 7, 8: Minor eval improvements + scanner regex

**Files:**
- Modify: `ollama_queue/eval_engine.py`
- Modify: `ollama_queue/scanner.py`
- Test: `tests/test_eval_engine.py`, `tests/test_scanner.py`

**Context:**
`eval_engine.py` builds analysis markdown via 40+ `list.append()` calls — refactor to a single `"\n".join()`. Scanner regex is fine (compiled at module level), but verify no loops recompile.

### Step E1: Audit eval_engine.py markdown building (item 7)

Find `generate_eval_analysis()` in `eval_engine.py`. If it uses a `lines` list with many `.append()` calls, refactor to build a list literal and join at the end:

```python
# Instead of:
lines = []
lines.append("# Analysis")
lines.append("")
lines.append(f"**F1:** {f1}")
...
return "\n".join(lines)

# Preferred (same behavior, clearer intent):
sections = [
    "# Analysis",
    "",
    f"**F1:** {f1}",
    ...
]
return "\n".join(sections)
```

This is a readability improvement, not a performance one. Only refactor if the existing code uses the append pattern — do not change code that already uses join.

### Step E2: Check for duplicate JSON parsing (item 8)

In `eval_engine.py`, find the judge scoring loop. Check if `json.loads()` is called on the same response twice. If so, parse once and pass the dict:

```python
# Bad:
score = parse_score(json.loads(resp))
meta = parse_meta(json.loads(resp))  # second parse of same string

# Good:
data = json.loads(resp)
score = parse_score(data)
meta = parse_meta(data)
```

Only make this change if the double-parse actually exists.

### Step E3: Verify scanner.py regex patterns

Read `scanner.py:16-19`. Confirm `_STREAM_PATTERN` and `_OLLAMA_11434_PATTERN` are compiled at module level (not inside functions). If any regex is compiled inside a loop, extract it to module level.

No test needed for this unless a loop was found — it's a read-and-verify step.

### Step E4: Run tests

```bash
pytest tests/test_eval_engine.py tests/test_scanner.py -v --timeout=120
```
Expected: all pass.

### Step E5: Run full suite

```bash
pytest --timeout=120 -x -q
```
Expected: 696 tests, all pass.

### Step E6: Commit

```bash
git add ollama_queue/eval_engine.py ollama_queue/scanner.py
git commit -m "refactor: simplify eval analysis markdown building, verify scanner regex"
```

---

## Final Integration

After all task groups are committed (by the orchestrating agent):

```bash
cd ~/Documents/projects/ollama-queue/.worktrees/refactor
pytest --timeout=120 -q
make lint
```

Then create PR:
```bash
gh pr create --title "perf: efficiency refactor — settings cache, subprocess TTL, scheduler cache" \
  --body "Resolves 15 efficiency findings from codebase audit. See docs/plans/2026-03-09-refactor-efficiency.md."
```
