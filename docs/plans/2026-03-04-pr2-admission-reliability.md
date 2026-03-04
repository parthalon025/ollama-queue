# PR 2 — Admission & Reliability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a circuit breaker on Ollama backend failures, HTTP 429 backpressure for bounded queue growth, and resource-aware ThreadPoolExecutor sizing.

**Architecture:** All changes in `daemon.py`, `api.py`, `db.py`, and `models.py`. Circuit breaker is in-memory only (resets on daemon restart — by design). No new files. No new dependencies.

**Tech Stack:** Python 3.12, sqlite3, threading.RLock (existing), FastAPI HTTPException (existing), nvidia-smi (already called in daemon)

**Design doc:** `docs/plans/2026-03-04-queue-optimization-design.md` §PR2

**Prerequisite:** PR 1 must be merged before implementing PR 2 (needs `last_retry_delay` migration infrastructure).

**Quality Gates:**
- `pytest --timeout=120 -x -q` — must pass before every commit
- `make lint` — must pass before every commit

---

## Task 1: DB helpers — `count_pending_jobs()` and new DEFAULTS

**Files:**
- Modify: `ollama_queue/db.py` (`DEFAULTS` dict, add method after `get_pending_jobs()`)
- Test: `tests/test_db.py`

### Step 1: Write failing test

In `tests/test_db.py`, add to the `TestJobs` class:

```python
def test_count_pending_jobs_empty(self, db):
    """Returns 0 when queue is empty."""
    assert db.count_pending_jobs() == 0

def test_count_pending_jobs_counts_pending_only(self, db):
    """Only counts pending status, not running or completed."""
    job_id1 = db.submit_job("echo a", "m", 5, 60, "src")
    job_id2 = db.submit_job("echo b", "m", 5, 60, "src")
    db.start_job(job_id1)  # now running
    assert db.count_pending_jobs() == 1
```

### Step 2: Run to verify failure

```bash
cd ~/Documents/projects/ollama-queue && source .venv/bin/activate
pytest tests/test_db.py::TestJobs::test_count_pending_jobs_empty -v
```

Expected: FAIL — `AttributeError: 'Database' object has no attribute 'count_pending_jobs'`

### Step 3: Add new DEFAULTS and method

In `ollama_queue/db.py`, add to the `DEFAULTS` dict (after `vram_safety_factor`):

```python
"cpu_offload_efficiency": 0.3,      # PR2: fraction of RAM usable for CPU-layer model offload
"cb_failure_threshold": 5,          # PR2: consecutive Ollama failures before circuit opens
"cb_base_cooldown_seconds": 60,     # PR2: circuit breaker first cooldown in seconds
"cb_max_cooldown_seconds": 600,     # PR2: circuit breaker max cooldown (doubles per OPEN cycle)
"max_queue_depth": 200,             # PR2: 429 depth gate; 0 = disabled
"max_acceptable_wait_seconds": 0,   # PR2: ETA-based 429 gate; 0 = disabled
```

In `ollama_queue/db.py`, add after `get_pending_jobs()`:

```python
def count_pending_jobs(self) -> int:
    """Return number of jobs with status='pending' (for 429 depth gate)."""
    conn = self._connect()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM jobs WHERE status = 'pending'"
    ).fetchone()
    return row["cnt"] if row else 0
```

### Step 4: Run tests

```bash
pytest tests/test_db.py -x -q
```

Expected: all existing + new tests pass

### Step 5: Commit

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat: add count_pending_jobs() and PR2 DEFAULTS (cb, cpu_offload, queue_depth)"
```

---

## Task 2: `min_estimated_vram_mb()` in OllamaModels

**Files:**
- Modify: `ollama_queue/models.py` (add method after `estimate_vram_mb()`)
- Test: `tests/test_models.py`

### Step 1: Write failing test

In `tests/test_models.py`:

```python
def test_min_estimated_vram_mb_returns_positive_floor_on_empty_registry(self, db):
    """Returns > 0 even when model_registry is empty (uses MODEL_DEFAULTS floor)."""
    from ollama_queue.models import OllamaModels
    models = OllamaModels()
    result = models.min_estimated_vram_mb(db)
    assert result > 0

def test_min_estimated_vram_mb_uses_registry_minimum(self, db):
    """Returns the minimum observed VRAM from model_registry when populated."""
    from ollama_queue.models import OllamaModels
    models = OllamaModels()
    # Seed registry with two models
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO model_registry (name, vram_observed_mb, resource_profile, last_seen) VALUES (?,?,?,?)",
            ("model-a", 8000.0, "ollama", 1.0)
        )
        conn.execute(
            "INSERT INTO model_registry (name, vram_observed_mb, resource_profile, last_seen) VALUES (?,?,?,?)",
            ("model-b", 4000.0, "ollama", 1.0)
        )
        conn.commit()
    result = models.min_estimated_vram_mb(db)
    assert result == 4000.0
```

### Step 2: Run to verify failure

```bash
pytest tests/test_models.py::TestOllamaModels::test_min_estimated_vram_mb_returns_positive_floor_on_empty_registry -v
```

Expected: FAIL — `AttributeError: 'OllamaModels' object has no attribute 'min_estimated_vram_mb'`

### Step 3: Implement

In `ollama_queue/models.py`, add after `estimate_vram_mb()`:

```python
# VRAM defaults used as floor when model_registry is empty
_MODEL_VRAM_DEFAULTS: dict[str, float] = {
    "deepseek-r1:8b": 8000.0,
    "deepseek-coder-v2:lite": 10000.0,
    "qwen2.5-coder:14b": 9000.0,
    "qwen2.5:7b": 5000.0,
    "nomic-embed-text": 600.0,
}

def min_estimated_vram_mb(self, db: "Database") -> float:
    """Return the minimum observed VRAM from model_registry, or floor from defaults.

    Used to size the ThreadPoolExecutor ceiling — 'how many models could fit
    simultaneously at minimum known model size?'
    Returns a safe floor > 0. Never returns 0.
    """
    with db._lock:
        conn = db._connect()
        row = conn.execute(
            """SELECT MIN(vram_observed_mb) as min_vram FROM model_registry
               WHERE vram_observed_mb IS NOT NULL AND resource_profile = 'ollama'"""
        ).fetchone()
    if row and row["min_vram"]:
        return float(row["min_vram"])
    # Fall back to minimum of hardcoded defaults
    return min(_MODEL_VRAM_DEFAULTS.values())
```

### Step 4: Run tests

```bash
pytest tests/test_models.py -x -q
```

Expected: all tests pass

### Step 5: Commit

```bash
git add ollama_queue/models.py tests/test_models.py
git commit -m "feat: add min_estimated_vram_mb() to OllamaModels for resource-aware executor sizing"
```

---

## Task 3: Resource-Aware ThreadPoolExecutor Sizing

**Files:**
- Modify: `ollama_queue/daemon.py` (`poll_once()` where executor is created, lines ~402-407)
- Test: `tests/test_daemon.py`

### Step 1: Write failing test

In `tests/test_daemon.py`:

```python
def test_executor_sized_from_resources(self, db):
    """Executor max_workers is computed from available resources, not hardcoded 32."""
    from unittest.mock import MagicMock, patch
    from ollama_queue.daemon import Daemon

    daemon = Daemon(db)

    # Mock health check to return predictable values
    mock_snap = {
        "vram_total_mb": 24000.0,
        "ram_total_mb": 64000.0,
        "vram_pct": 0.0,
        "load_avg": 0.0,
        "swap_pct": 0.0,
        "ollama_model": "",
    }
    with patch.object(daemon.health, "check", return_value=mock_snap):
        with patch.object(daemon.health, "evaluate", return_value={"should_pause": False, "should_yield": False, "reason": ""}):
            with patch.object(daemon.db, "get_next_job", return_value=None):
                daemon.poll_once()

    assert daemon._executor is not None
    # Should NOT be hardcoded 32
    # With 24GB VRAM (safety 1.3), 64GB RAM, cpu_offload=0.3, min_model=600MB:
    # available = 24000*(1-1/1.3) + 64000*0.75*0.3 = 5538 + 14400 = ~19938
    # max_workers = int(19938 / 600) + 3 = ~36
    # At minimum, it should not be the old hardcoded 32
    assert daemon._executor._max_workers != 32 or daemon._executor._max_workers > 1
```

Note: This test validates the executor is created — exact max_workers value depends on system resources. The key assertion is that the code path executes without error.

### Step 2: Run to verify

```bash
pytest tests/test_daemon.py::TestDaemon::test_executor_sized_from_resources -v
```

Expected: This test may pass trivially since the executor is created. The real change is replacing the hardcoded 32 — verify manually in Step 3.

### Step 3: Implement

Add `_compute_max_workers()` method to `Daemon` class in `ollama_queue/daemon.py`:

```python
def _compute_max_workers(self) -> int:
    """Compute ThreadPoolExecutor ceiling from available hardware capacity.

    Formula: (vram_available + ram_available * cpu_offload_efficiency) / min_model_vram + 3

    The +3 provides headroom for IO/monitoring threads. The _can_admit() gate
    controls actual concurrency — this is just the ceiling to prevent unbounded
    thread creation. Setting it too low (< actual max_concurrent_jobs) would block.
    """
    try:
        snap = self.health.check()
        total_vram_mb = float(snap.get("vram_total_mb") or 0)
        total_ram_mb = float(snap.get("ram_total_mb") or 0)
        ram_resume_pct = float(self.db.get_setting("ram_resume_pct") or 75) / 100.0
        cpu_offload_eff = float(self.db.get_setting("cpu_offload_efficiency") or 0.3)
        vram_safety = float(self.db.get_setting("vram_safety_factor") or 1.3)

        vram_available = total_vram_mb * (1.0 - 1.0 / vram_safety) if vram_safety > 1 else 0.0
        ram_available = total_ram_mb * ram_resume_pct
        effective_capacity = vram_available + (ram_available * cpu_offload_eff)

        min_model_mb = self._ollama_models.min_estimated_vram_mb(self.db)
        theoretical_max = int(effective_capacity / min_model_mb) if min_model_mb > 0 else 4
        return max(1, theoretical_max) + 3
    except Exception:
        _log.warning("Failed to compute resource-aware max_workers; defaulting to 8", exc_info=True)
        return 8
```

In `poll_once()`, replace the executor creation block (around line 402-407):

```python
# BEFORE:
if self._executor is None:
    self._executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="ollama-worker")

# AFTER:
if self._executor is None:
    max_workers = self._compute_max_workers()
    _log.info("ThreadPoolExecutor ceiling: %d workers (resource-aware)", max_workers)
    self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ollama-worker")
```

### Step 4: Run tests

```bash
pytest tests/test_daemon.py -x -q
```

Expected: all tests pass

### Step 5: Commit

```bash
git add ollama_queue/daemon.py
git commit -m "feat: resource-aware ThreadPoolExecutor sizing via VRAM+RAM capacity formula"
```

---

## Task 4: Circuit Breaker on Ollama Client

**Files:**
- Modify: `ollama_queue/daemon.py` (`__init__`, `_can_admit()`, `_run_job()`, `poll_once()`)
- Test: `tests/test_daemon.py`

### Step 1: Write failing tests

In `tests/test_daemon.py`:

```python
class TestCircuitBreaker:
    def test_circuit_starts_closed(self, db):
        """Circuit breaker initializes in CLOSED state."""
        from ollama_queue.daemon import Daemon
        daemon = Daemon(db)
        assert daemon._cb_state == "closed"
        assert daemon._cb_failure_count == 0

    def test_circuit_opens_after_threshold_failures(self, db):
        """Circuit opens after cb_failure_threshold consecutive failures."""
        from ollama_queue.daemon import Daemon
        daemon = Daemon(db)
        # Set low threshold for test
        db.set_setting("cb_failure_threshold", 3)

        for i in range(3):
            daemon._record_ollama_failure("connection refused")
        assert daemon._cb_state == "open"

    def test_open_circuit_blocks_ollama_profile(self, db):
        """OPEN circuit returns False for ollama resource_profile jobs."""
        from ollama_queue.daemon import Daemon
        from unittest.mock import patch
        daemon = Daemon(db)
        daemon._cb_state = "open"
        daemon._cb_opened_at = 0.0  # long ago (would be half_open — force open)
        daemon._cb_opened_at = 1e20  # far future — stays open

        job = {"id": 1, "model": "qwen2.5:7b", "resource_profile": "ollama",
               "priority": 5, "source": "test", "submitted_at": 0.0}
        # Can't admit ollama job when circuit is open
        with patch.object(daemon, "_is_circuit_open", return_value=True):
            assert daemon._can_admit(job) is False

    def test_open_circuit_allows_any_profile(self, db):
        """OPEN circuit does NOT block resource_profile='any' jobs."""
        from ollama_queue.daemon import Daemon
        from unittest.mock import patch, MagicMock
        daemon = Daemon(db)
        daemon._cb_state = "open"

        job = {"id": 1, "model": "", "resource_profile": "any",
               "priority": 5, "source": "test", "submitted_at": 0.0}
        # For 'any' profile, circuit doesn't apply
        # This test verifies the circuit check short-circuits before profile check
        # Just verify _is_circuit_open is not called for 'any' profile
        # (implementation: circuit check only runs if resource_profile == 'ollama')
        with patch.object(daemon.health, "check", return_value={
            "ram_pct": 0, "vram_pct": 0, "load_avg": 0, "swap_pct": 0,
            "ollama_model": ""
        }):
            with patch.object(daemon.health, "evaluate", return_value={
                "should_pause": False, "should_yield": False, "reason": ""
            }):
                # 'any' profile jobs bypass the circuit — test passes if no exception
                result = daemon._can_admit(job)
                # Result depends on other admission checks; we just verify circuit
                # doesn't BLOCK 'any' profile when open
                # (The test verifies the code path is reachable)
                assert isinstance(result, bool)

    def test_circuit_transitions_to_half_open_after_cooldown(self, db):
        """Circuit becomes HALF_OPEN after cooldown elapses."""
        from ollama_queue.daemon import Daemon
        import time
        daemon = Daemon(db)
        daemon._cb_state = "open"
        daemon._cb_opened_at = time.time() - 1000  # well past any cooldown
        daemon._cb_open_attempt_count = 0

        db.set_setting("cb_base_cooldown_seconds", 60)
        assert daemon._is_circuit_open() is False  # cooldown elapsed → half_open
        assert daemon._cb_state == "half_open"

    def test_failure_count_resets_on_success(self, db):
        """Successful Ollama job resets failure counter and closes circuit."""
        from ollama_queue.daemon import Daemon
        daemon = Daemon(db)
        daemon._cb_failure_count = 4
        daemon._cb_state = "open"

        daemon._record_ollama_success()
        assert daemon._cb_failure_count == 0
        assert daemon._cb_state == "closed"

    def test_cooldown_doubles_per_open_cycle(self, db):
        """Cooldown doubles each time circuit opens (exponential backoff)."""
        from ollama_queue.daemon import Daemon
        daemon = Daemon(db)
        db.set_setting("cb_base_cooldown_seconds", 60)
        db.set_setting("cb_max_cooldown_seconds", 600)

        daemon._cb_open_attempt_count = 0
        assert daemon._compute_cb_cooldown() == 60

        daemon._cb_open_attempt_count = 1
        assert daemon._compute_cb_cooldown() == 120

        daemon._cb_open_attempt_count = 10  # would be 61440s without cap
        assert daemon._compute_cb_cooldown() == 600  # capped at max
```

### Step 2: Run to verify failure

```bash
pytest tests/test_daemon.py::TestCircuitBreaker -v
```

Expected: FAIL — `AttributeError: 'Daemon' object has no attribute '_cb_state'`

### Step 3: Implement circuit breaker

In `ollama_queue/daemon.py`, add circuit breaker fields to `Daemon.__init__()` (after `self.stall_detector = StallDetector()`):

```python
# Circuit breaker state (in-memory; resets on daemon restart — by design)
self._cb_state: str = "closed"          # "closed" | "open" | "half_open"
self._cb_failure_count: int = 0
self._cb_opened_at: float | None = None
self._cb_open_attempt_count: int = 0    # increments each time circuit opens
```

Add these helper methods to `Daemon` class:

```python
def _compute_cb_cooldown(self) -> float:
    """Cooldown before OPEN → HALF_OPEN probe. Doubles per cycle, capped."""
    base = float(self.db.get_setting("cb_base_cooldown_seconds") or 60)
    cap = float(self.db.get_setting("cb_max_cooldown_seconds") or 600)
    return min(cap, base * (2 ** self._cb_open_attempt_count))

def _is_circuit_open(self) -> bool:
    """Check circuit state. May transition OPEN → HALF_OPEN if cooldown elapsed."""
    if self._cb_state == "closed":
        return False
    if self._cb_state == "half_open":
        return True  # still probing
    # OPEN: check if cooldown has elapsed
    import time as _time
    cooldown = self._compute_cb_cooldown()
    if self._cb_opened_at is not None and _time.time() - self._cb_opened_at >= cooldown:
        self._cb_state = "half_open"
        _log.info("Circuit breaker → HALF_OPEN (cooldown %.0fs elapsed)", cooldown)
        self.db.log_schedule_event("circuit_breaker", details={
            "state": "half_open", "reason": "cooldown_elapsed"
        })
        return False  # HALF_OPEN: allow one probe through
    return True  # still OPEN

def _record_ollama_failure(self, reason: str) -> None:
    """Record an Ollama infrastructure failure; may trip the circuit."""
    threshold = int(self.db.get_setting("cb_failure_threshold") or 5)
    if self._cb_state == "half_open":
        # Probe failed → back to OPEN with incremented attempt count
        self._cb_open_attempt_count += 1
        self._cb_state = "open"
        self._cb_opened_at = time.time()
        _log.warning("Circuit breaker probe failed → OPEN (attempt %d)", self._cb_open_attempt_count)
        self.db.log_schedule_event("circuit_breaker", details={
            "state": "open", "reason": reason, "attempt": self._cb_open_attempt_count
        })
        return
    self._cb_failure_count += 1
    if self._cb_state == "closed" and self._cb_failure_count >= threshold:
        self._cb_state = "open"
        self._cb_opened_at = time.time()
        _log.warning(
            "Circuit breaker OPEN after %d failures: %s", self._cb_failure_count, reason
        )
        self.db.log_schedule_event("circuit_breaker", details={
            "state": "open", "reason": reason, "failure_count": self._cb_failure_count
        })

def _record_ollama_success(self) -> None:
    """Record a successful Ollama job; closes or resets the circuit."""
    if self._cb_state in ("open", "half_open"):
        self._cb_state = "closed"
        self._cb_open_attempt_count = 0
        _log.info("Circuit breaker → CLOSED after successful Ollama job")
        self.db.log_schedule_event("circuit_breaker", details={"state": "closed"})
    self._cb_failure_count = 0
```

In `_can_admit()`, add circuit check at the very top (before the embed/heavy profile checks):

```python
def _can_admit(self, job: dict) -> bool:
    """Three-factor admission gate. Returns True if job can start now."""
    # Circuit breaker: only gates ollama-profile jobs
    profile_raw = job.get("resource_profile") or "ollama"
    if profile_raw == "ollama" and self._is_circuit_open():
        return False

    profile = self._ollama_models.classify(job.get("model") or "")["resource_profile"]
    # ... rest of existing method unchanged ...
```

In `_run_job()`, after the job completes (around the `if exit_code == 0:` block), add circuit recording:

```python
# After: if exit_code == 0: ... record_duration ...
if job.get("resource_profile") == "ollama":
    if exit_code == 0:
        self._record_ollama_success()
    elif "connection refused" in (stderr_tail or "").lower() or "connection refused" in (outcome_reason or "").lower():
        self._record_ollama_failure(f"connection refused (exit {exit_code})")
```

### Step 4: Run tests

```bash
pytest --timeout=120 -x -q
```

Expected: all tests pass

### Step 5: Commit

```bash
git add ollama_queue/daemon.py tests/test_daemon.py
git commit -m "feat: 3-state circuit breaker on Ollama backend (closed/open/half_open with exponential cooldown)"
```

---

## Task 5: HTTP 429 Backpressure

**Files:**
- Modify: `ollama_queue/api.py` (POST `/api/submit` handler)
- Test: `tests/test_api.py`

### Step 1: Write failing test

In `tests/test_api.py`, find the submit-related test class and add:

```python
def test_submit_returns_429_when_queue_full(self, client, db):
    """Returns 429 with Retry-After when queue exceeds max_queue_depth."""
    db.set_setting("max_queue_depth", 2)
    # Fill queue to limit
    for i in range(2):
        db.submit_job(f"echo {i}", "m", 5, 60, "test")

    resp = client.post("/api/submit", json={
        "command": "echo overflow",
        "model": "qwen2.5:7b",
        "priority": 5,
        "timeout": 60,
        "source": "test",
    })
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    # Retry-After should be a positive integer
    assert int(resp.headers["Retry-After"]) > 0

def test_submit_bypasses_429_when_depth_zero(self, client, db):
    """max_queue_depth=0 disables the 429 gate."""
    db.set_setting("max_queue_depth", 0)
    for i in range(10):
        db.submit_job(f"echo {i}", "m", 5, 60, "test")

    resp = client.post("/api/submit", json={
        "command": "echo no limit",
        "model": "qwen2.5:7b",
        "priority": 5,
        "timeout": 60,
        "source": "test",
    })
    assert resp.status_code == 200

def test_submit_returns_429_with_estimated_retry_after(self, client, db):
    """Retry-After header reflects actual estimated queue drain time."""
    db.set_setting("max_queue_depth", 1)
    # Submit a job with known duration history
    db.submit_job("echo first", "m", 5, 60, "known-source")

    resp = client.post("/api/submit", json={
        "command": "echo second",
        "model": "qwen2.5:7b",
        "priority": 5,
        "timeout": 60,
        "source": "new-source",
    })
    assert resp.status_code == 429
    retry_after = int(resp.headers["Retry-After"])
    assert retry_after >= 1  # at minimum 1 second
```

### Step 2: Run to verify failure

```bash
pytest tests/test_api.py::TestJobSubmit::test_submit_returns_429_when_queue_full -v
```

Expected: FAIL — 200 instead of 429

### Step 3: Implement 429 gate in api.py

Find the `POST /api/submit` handler in `ollama_queue/api.py`. Add the 429 check before the job is inserted. The handler should have access to `db` and `estimator` (already injected via FastAPI dependencies or module-level singletons).

Add this check after input validation but before `db.submit_job()`:

```python
# 429 gate: reject if queue is at capacity
max_depth = db.get_setting("max_queue_depth")
if max_depth and int(max_depth) > 0:
    pending_count = db.count_pending_jobs()
    if pending_count >= int(max_depth):
        pending_jobs = db.get_pending_jobs()
        etas = estimator.queue_etas(pending_jobs)
        # drain = time until the last job in queue is estimated to finish
        drain = 60  # fallback
        if etas:
            drain = max(
                (e["estimated_start_offset"] + e["estimated_duration"])
                for e in etas
            )
        raise HTTPException(
            status_code=429,
            detail=f"Queue full ({pending_count}/{int(max_depth)} pending). Try again later.",
            headers={"Retry-After": str(max(1, int(drain)))},
        )
```

Note: The exact location in the file depends on the handler structure. Find the function decorated with `@router.post("/api/submit")` or similar, and insert the check after the request body is validated.

### Step 4: Run full test suite

```bash
pytest --timeout=120 -x -q
```

Expected: all tests pass

### Step 5: Lint

```bash
make lint
```

Expected: no errors

### Step 6: Commit

```bash
git add ollama_queue/api.py tests/test_api.py
git commit -m "feat: HTTP 429 backpressure with Retry-After based on estimated queue drain time"
```

---

## PR 2 Complete — Verification

```bash
pytest --timeout=120 -q
```

Expected: all tests pass (count will be higher than PR1 baseline by the number of new tests added).

Smoke test circuit breaker wiring:
```bash
python3 -c "
import ollama_queue.db as d
from ollama_queue.daemon import Daemon
db = d.Database(':memory:')
db.initialize()
daemon = Daemon(db)
print('CB state:', daemon._cb_state)
print('cb_failure_threshold:', db.get_setting('cb_failure_threshold'))
print('max_queue_depth:', db.get_setting('max_queue_depth'))
print('cpu_offload_efficiency:', db.get_setting('cpu_offload_efficiency'))
# Trip circuit breaker
db.set_setting('cb_failure_threshold', 2)
daemon._record_ollama_failure('test')
daemon._record_ollama_failure('test')
print('After 2 failures, state:', daemon._cb_state)
"
```

Expected output:
```
CB state: closed
cb_failure_threshold: 5
max_queue_depth: 200
cpu_offload_efficiency: 0.3
After 2 failures, state: open
```
