# Bayesian Stall Detection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the hard 600s timeout on LLM jobs with a Bayesian multi-signal stall detector (process state + CPU% + stdout silence + Ollama /api/ps) that flags and optionally kills genuinely stuck jobs without touching slow-but-healthy ones.

**Architecture:** New `StallDetector` class in `stall.py` owns all signal reading and Bayesian math. Daemon's `_run_job()` uses a `select()`-based pipe-drain loop (replacing `communicate()`) for LLM jobs to track stdout activity. `_check_stalled_jobs()` is rewritten to call `StallDetector.compute_posterior()` per running LLM job, flag stalls via `db.set_stall_detected()`, and optionally SIGTERM via `os.kill()`.

**Tech Stack:** Python stdlib only (`math`, `select`, `fcntl`, `os`, `urllib.request`). No new dependencies.

---

### Task 1: DB — `stall_signals` column + `set_stall_detected()` + new settings

**Files:**
- Modify: `ollama_queue/db.py`
- Test: `tests/test_db.py`

**Step 1: Write the failing tests**

Add at the end of `tests/test_db.py`:

```python
def test_stall_signals_column_exists(db):
    """initialize() creates stall_signals column on jobs table."""
    conn = db._connect()
    row = conn.execute("PRAGMA table_info(jobs)").fetchall()
    col_names = [r["name"] for r in row]
    assert "stall_signals" in col_names


def test_set_stall_detected(db):
    """set_stall_detected() writes stall_detected_at and stall_signals JSON."""
    import json, time
    job_id = db.submit_job("echo hi", "qwen2.5:7b", 5, 600, "test")
    now = time.time()
    signals = {"process": 3.56, "cpu": 2.08, "silence": 1.79, "ps": 0.0, "posterior": 0.92}
    db.set_stall_detected(job_id, now, signals)
    conn = db._connect()
    row = conn.execute("SELECT stall_detected_at, stall_signals FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["stall_detected_at"] == pytest.approx(now, abs=0.01)
    parsed = json.loads(row["stall_signals"])
    assert parsed["posterior"] == pytest.approx(0.92, abs=0.001)


def test_new_stall_settings_have_defaults(db):
    """Three new stall settings exist with correct defaults."""
    settings = db.get_all_settings()
    assert settings["stall_posterior_threshold"] == pytest.approx(0.8)
    assert settings["stall_action"] == "log"
    assert settings["stall_kill_grace_seconds"] == pytest.approx(60)
```

**Step 2: Run tests to confirm they fail**

```bash
cd ~/Documents/projects/ollama-queue && source .venv/bin/activate
pytest tests/test_db.py::test_stall_signals_column_exists tests/test_db.py::test_set_stall_detected tests/test_db.py::test_new_stall_settings_have_defaults -v
```

Expected: 3 FAILs (column missing, method missing, settings missing).

**Step 3: Implement — three changes to `db.py`**

**3a. Add 3 new entries to `DEFAULTS` dict** (around line 25, after `"stall_multiplier"`):

```python
    "stall_posterior_threshold": 0.8,
    "stall_action": "log",
    "stall_kill_grace_seconds": 60,
```

**3b. Add migration guard in `initialize()`.** Find the block where other `ALTER TABLE jobs ADD COLUMN` guards live (search for `"duplicate column"`). Add after the last one:

```python
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN stall_signals TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
```

**3c. Add `set_stall_detected()` method** to the `Database` class (add after `get_all_settings`):

```python
    def set_stall_detected(self, job_id: int, now: float, signals: dict) -> None:
        """Record stall detection timestamp and signal breakdown for a job."""
        import json as _json
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE jobs SET stall_detected_at = ?, stall_signals = ? WHERE id = ?",
                (now, _json.dumps(signals), job_id),
            )
            conn.commit()
```

**Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_db.py::test_stall_signals_column_exists tests/test_db.py::test_set_stall_detected tests/test_db.py::test_new_stall_settings_have_defaults -v
```

Expected: 3 PASSes.

**Step 5: Run full suite to confirm no regressions**

```bash
pytest --timeout=120 -x -q
```

Expected: all pass (test count may increase by 3).

**Step 6: Commit**

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat(db): add stall_signals column, set_stall_detected(), and 3 new stall settings"
```

---

### Task 2: New module `ollama_queue/stall.py` — StallDetector (TDD)

**Files:**
- Create: `ollama_queue/stall.py`
- Create: `tests/test_stall.py`

**Step 1: Write all failing tests in `tests/test_stall.py`**

```python
"""Tests for the Bayesian multi-signal stall detector."""
import time
from unittest.mock import MagicMock, patch

import pytest

from ollama_queue.stall import PRIOR_LOG_ODDS, StallDetector, _sigmoid


@pytest.fixture
def det():
    return StallDetector()


# ── math primitives ───────────────────────────────────────────────────────────

def test_sigmoid_midpoint():
    assert _sigmoid(0.0) == pytest.approx(0.5, abs=0.001)


def test_sigmoid_large_positive():
    assert _sigmoid(10.0) > 0.99


def test_prior_log_odds():
    """Prior P(stuck)=0.05 gives log_odds ≈ -2.944."""
    assert PRIOR_LOG_ODDS == pytest.approx(-2.944, abs=0.01)


# ── posterior combinations ────────────────────────────────────────────────────

def test_posterior_all_healthy(det):
    """R-state + high CPU + recent stdout + model loaded → posterior < 0.10."""
    ps = {"qwen2.5"}
    det.update_stdout_activity(1, time.time() - 5)
    with (
        patch.object(det, "get_process_state", return_value="R"),
        patch.object(det, "get_cpu_pct", return_value=50.0),
    ):
        p, _ = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), ps)
    assert p < 0.10


def test_posterior_d_state_only(det):
    """D-state alone → 0.5 < posterior < 0.80 (suspicious, not conclusive)."""
    with (
        patch.object(det, "get_process_state", return_value="D"),
        patch.object(det, "get_cpu_pct", return_value=None),
        patch.object(det, "get_stdout_silence", return_value=None),
    ):
        p, signals = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), set())
    assert 0.50 < p < 0.80
    assert signals["process"] == pytest.approx(3.56, abs=0.01)


def test_posterior_d_state_plus_cpu(det):
    """D-state + CPU < 1% → posterior > 0.90."""
    with (
        patch.object(det, "get_process_state", return_value="D"),
        patch.object(det, "get_cpu_pct", return_value=0.5),
        patch.object(det, "get_stdout_silence", return_value=None),
    ):
        p, _ = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), set())
    assert p > 0.90


def test_posterior_all_signals_high(det):
    """All four groups fire → posterior > 0.98."""
    with (
        patch.object(det, "get_process_state", return_value="D"),
        patch.object(det, "get_cpu_pct", return_value=0.5),
        patch.object(det, "get_stdout_silence", return_value=400.0),
    ):
        p, _ = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), set())
    assert p > 0.98


def test_posterior_silence_300s_plus_cpu(det):
    """Stdout silent 300s + CPU < 1% → > 0.88 (design spec)."""
    with (
        patch.object(det, "get_process_state", return_value="S"),
        patch.object(det, "get_cpu_pct", return_value=0.5),
        patch.object(det, "get_stdout_silence", return_value=400.0),
    ):
        p, _ = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), set())
    assert p > 0.88


def test_posterior_signals_dict_keys(det):
    """compute_posterior returns dict with expected keys."""
    with (
        patch.object(det, "get_process_state", return_value="S"),
        patch.object(det, "get_cpu_pct", return_value=None),
    ):
        _, signals = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), set())
    for key in ("process", "cpu", "silence", "ps", "posterior"):
        assert key in signals


# ── stdout silence ────────────────────────────────────────────────────────────

def test_stdout_silence_tracking(det):
    """update_stdout_activity → get_stdout_silence returns elapsed seconds."""
    job_id = 42
    now = time.time()
    det.update_stdout_activity(job_id, now - 150.0)
    silence = det.get_stdout_silence(job_id, now)
    assert silence == pytest.approx(150.0, abs=1.0)


def test_stdout_silence_none_before_first_update(det):
    """No activity recorded → silence returns None (batch jobs: neutral)."""
    assert det.get_stdout_silence(99, time.time()) is None


def test_stdout_silence_lr_recent(det):
    """Silence < 30s → strong healthy signal (−2.30)."""
    det.update_stdout_activity(1, time.time() - 5)
    lr = det._silence_group_lr(det.get_stdout_silence(1, time.time()))
    assert lr == pytest.approx(-2.30, abs=0.01)


def test_stdout_silence_lr_300s(det):
    """Silence > 300s → strong stall signal (+3.81)."""
    lr = det._silence_group_lr(400.0)
    assert lr == pytest.approx(3.81, abs=0.01)


# ── CPU delta ─────────────────────────────────────────────────────────────────

def test_cpu_delta_first_call_returns_none(det):
    """First get_cpu_pct call returns None (no delta yet)."""
    with patch.object(det, "_read_cpu_ticks", return_value=1000):
        result = det.get_cpu_pct(9999, 1, 0.0)
    assert result is None


def test_cpu_delta_second_call_returns_float(det):
    """Second get_cpu_pct call returns a non-negative float."""
    with patch.object(det, "_read_cpu_ticks", return_value=100):
        det.get_cpu_pct(9999, 1, 0.0)
    with patch.object(det, "_read_cpu_ticks", return_value=200):
        result = det.get_cpu_ticks_for_test = det.get_cpu_pct(9999, 1, 2.0)
    assert result is not None
    assert result >= 0.0


def test_cpu_pct_none_on_read_error(det):
    """If /proc/pid/stat is unreadable, returns None."""
    with patch.object(det, "_read_cpu_ticks", return_value=None):
        result = det.get_cpu_pct(9999, 1, 0.0)
    assert result is None


# ── Ollama /api/ps ────────────────────────────────────────────────────────────

def test_get_ollama_ps_models_parses_response(det):
    """get_ollama_ps_models() returns set of model base names."""
    mock_body = b'{"models":[{"name":"qwen2.5:7b"},{"name":"nomic-embed-text:latest"}]}'
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value.read.return_value = mock_body
    with patch("urllib.request.urlopen", return_value=mock_cm):
        result = det.get_ollama_ps_models()
    assert "qwen2.5" in result


def test_get_ollama_ps_models_on_error_returns_empty(det):
    """Network error → empty set (treat ps as unknown, not stuck)."""
    with patch("urllib.request.urlopen", side_effect=OSError("refused")):
        result = det.get_ollama_ps_models()
    assert result == set()


def test_ps_lr_model_loaded(det):
    """Model in ps → healthy signal (−1.50)."""
    lr = det._ps_group_lr("qwen2.5:7b", {"qwen2.5"})
    assert lr == pytest.approx(-1.50, abs=0.01)


def test_ps_lr_model_not_loaded(det):
    """Model not in ps → stall signal (+1.61)."""
    lr = det._ps_group_lr("qwen2.5:7b", {"llama3.2"})
    assert lr == pytest.approx(1.61, abs=0.01)


def test_ps_lr_empty_ps_models(det):
    """Empty ps set (Ollama unreachable) → neutral (0.0)."""
    lr = det._ps_group_lr("qwen2.5:7b", set())
    assert lr == 0.0


# ── cleanup ───────────────────────────────────────────────────────────────────

def test_forget_clears_stdout_state(det):
    job_id = 5
    det.update_stdout_activity(job_id, time.time())
    det.forget(job_id)
    assert det.get_stdout_silence(job_id, time.time()) is None


def test_forget_clears_cpu_state(det):
    job_id = 5
    det._cpu_prev[job_id] = (time.time(), 500)
    det.forget(job_id)
    assert job_id not in det._cpu_prev


def test_forget_unknown_job_is_safe(det):
    """forget() on a job that was never tracked should not raise."""
    det.forget(999999)  # should not raise
```

**Step 2: Run tests to confirm they ALL fail**

```bash
pytest tests/test_stall.py -v 2>&1 | head -30
```

Expected: ImportError or all FAILs (module doesn't exist yet).

**Step 3: Implement `ollama_queue/stall.py`**

Create the file:

```python
"""Bayesian multi-signal stall detector for LLM jobs."""
from __future__ import annotations

import json as _json
import logging
import math
import os
import urllib.request
from typing import TYPE_CHECKING

_log = logging.getLogger(__name__)

PRIOR_LOG_ODDS: float = math.log(0.05 / 0.95)  # P(stuck)=0.05 → -2.944


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class StallDetector:
    """Combines process state, CPU%, stdout silence, and Ollama /api/ps
    into a single posterior P(stuck) using naïve Bayes over four independent
    evidence groups."""

    def __init__(self) -> None:
        # job_id → timestamp of last stdout bytes received
        self._last_stdout: dict[int, float] = {}
        # job_id → (sample_timestamp, cpu_ticks) for delta computation
        self._cpu_prev: dict[int, tuple[float, int]] = {}

    # ── stdout activity tracking ──────────────────────────────────────────────

    def update_stdout_activity(self, job_id: int, now: float) -> None:
        self._last_stdout[job_id] = now

    def get_stdout_silence(self, job_id: int, now: float) -> float | None:
        """Seconds since last stdout output. None if never produced output."""
        last = self._last_stdout.get(job_id)
        return None if last is None else now - last

    # ── process state ─────────────────────────────────────────────────────────

    def get_process_state(self, pid: int) -> str:
        """Return single-char process state from /proc/{pid}/status.
        Returns '?' if unreadable (process may have exited)."""
        try:
            with open(f"/proc/{pid}/status") as fh:
                for line in fh:
                    if line.startswith("State:"):
                        return line.split()[1]  # e.g. 'R', 'S', 'D', 'Z', 'T'
        except OSError:
            pass
        return "?"

    # ── CPU delta ─────────────────────────────────────────────────────────────

    def _read_cpu_ticks(self, pid: int) -> int | None:
        """Return utime+stime ticks from /proc/{pid}/stat, or None on error."""
        try:
            with open(f"/proc/{pid}/stat") as fh:
                fields = fh.read().split()
            return int(fields[13]) + int(fields[14])  # utime + stime
        except (OSError, IndexError, ValueError):
            return None

    def get_cpu_pct(self, pid: int, job_id: int, now: float) -> float | None:
        """CPU% since the previous call for this job_id. None on first call."""
        ticks = self._read_cpu_ticks(pid)
        if ticks is None:
            return None
        prev = self._cpu_prev.get(job_id)
        self._cpu_prev[job_id] = (now, ticks)
        if prev is None:
            return None
        prev_time, prev_ticks = prev
        elapsed = now - prev_time
        if elapsed <= 0:
            return None
        tick_hz = os.sysconf("SC_CLK_TCK")  # 100 on Linux
        return ((ticks - prev_ticks) / tick_hz / elapsed) * 100.0

    # ── Ollama /api/ps ────────────────────────────────────────────────────────

    def get_ollama_ps_models(self) -> set[str]:
        """Return set of base model names currently loaded in Ollama.
        Returns empty set if Ollama is unreachable (treated as unknown)."""
        try:
            with urllib.request.urlopen(  # noqa: S310
                "http://localhost:11434/api/ps", timeout=2
            ) as resp:
                data = _json.loads(resp.read())
            return {m.get("name", "").split(":")[0] for m in data.get("models", [])}
        except Exception:
            return set()

    # ── log-likelihood ratios per group ──────────────────────────────────────

    def _process_group_lr(self, state: str) -> float:
        return {"D": 3.56, "Z": 6.86, "T": 3.40, "R": -2.48}.get(state, 0.0)

    def _cpu_group_lr(self, cpu_pct: float | None) -> float:
        if cpu_pct is None:
            return 0.0
        if cpu_pct < 1.0:
            return 2.08
        if cpu_pct >= 5.0:
            return -2.64
        return 0.0  # 1–5%: neutral

    def _silence_group_lr(self, silence: float | None) -> float:
        if silence is None:
            return 0.0  # Never produced output — neutral (batch jobs)
        if silence < 30:
            return -2.30
        if silence < 120:
            return 0.0
        if silence < 300:
            return 1.79
        return 3.81

    def _ps_group_lr(self, model: str, ps_models: set[str]) -> float:
        if not model or not ps_models:
            return 0.0  # Unknown — neutral
        base = model.split(":")[0]
        loaded = base in ps_models or model in ps_models
        return -1.50 if loaded else 1.61

    # ── Bayesian combination ──────────────────────────────────────────────────

    def compute_posterior(
        self,
        job_id: int,
        pid: int,
        model: str,
        now: float,
        ps_models: set[str],
    ) -> tuple[float, dict[str, object]]:
        """Compute P(stuck) and return (posterior, signal_breakdown_dict)."""
        state = self.get_process_state(pid)
        cpu_pct = self.get_cpu_pct(pid, job_id, now)
        silence = self.get_stdout_silence(job_id, now)

        lr_process = self._process_group_lr(state)
        lr_cpu = self._cpu_group_lr(cpu_pct)
        lr_silence = self._silence_group_lr(silence)
        lr_ps = self._ps_group_lr(model, ps_models)

        log_odds = PRIOR_LOG_ODDS + lr_process + lr_cpu + lr_silence + lr_ps
        posterior = _sigmoid(log_odds)

        return posterior, {
            "process": round(lr_process, 3),
            "cpu": round(lr_cpu, 3),
            "silence": round(lr_silence, 3),
            "ps": round(lr_ps, 3),
            "posterior": round(posterior, 3),
            "state": state,
            "cpu_pct": round(cpu_pct, 1) if cpu_pct is not None else None,
            "silence_s": round(silence, 1) if silence is not None else None,
        }

    # ── cleanup ───────────────────────────────────────────────────────────────

    def forget(self, job_id: int) -> None:
        """Remove all tracking state for a completed or cancelled job."""
        self._last_stdout.pop(job_id, None)
        self._cpu_prev.pop(job_id, None)
```

**Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_stall.py -v
```

Expected: all pass. Fix any failures before continuing.

**Step 5: Run full suite to confirm no regressions**

```bash
pytest --timeout=120 -x -q
```

**Step 6: Commit**

```bash
git add ollama_queue/stall.py tests/test_stall.py
git commit -m "feat(stall): add Bayesian StallDetector with 4-group multi-signal posterior"
```

---

### Task 3: Daemon — `_drain_pipes_with_tracking()` + `_run_job()` branch

**Files:**
- Modify: `ollama_queue/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write the failing test**

In `tests/test_daemon.py`, add a test that confirms LLM jobs do NOT call `proc.communicate()`. Add after the existing `test_poll_runs_job`:

```python
def test_llm_job_does_not_use_communicate(daemon):
    """LLM jobs (resource_profile='ollama') use pipe drain, not communicate()."""
    daemon.db.submit_job("echo hello", "qwen2.5:7b", 5, 60, "test")
    with (
        patch.object(
            daemon.health,
            "check",
            return_value={
                "ram_pct": 50.0, "swap_pct": 10.0, "load_avg": 1.0,
                "cpu_count": 4, "vram_pct": 50.0, "ollama_model": None,
            },
        ),
        patch("ollama_queue.daemon.subprocess") as mock_sub,
        patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"hello", b"")) as mock_drain,
    ):
        proc = MagicMock()
        proc.pid = 1234
        proc.returncode = 0
        mock_sub.Popen.return_value = proc
        daemon.poll_once()
        _drain(daemon)

    mock_drain.assert_called_once()
    proc.communicate.assert_not_called()
```

**Step 2: Run the failing test**

```bash
pytest tests/test_daemon.py::test_llm_job_does_not_use_communicate -v
```

Expected: FAIL (function doesn't exist yet, communicate IS called).

**Step 3: Add `_drain_pipes_with_tracking()` to `daemon.py`**

Add this as a **module-level function** (before the `Daemon` class), after the imports:

```python
def _drain_pipes_with_tracking(
    proc: "subprocess.Popen[bytes]",
    job_id: int,
    stall_detector: "StallDetector",
) -> tuple[bytes, bytes]:
    """Drain stdout+stderr via select() loop, tracking stdout activity.

    Uses non-blocking fds + select() to avoid: (1) deadlock on large output,
    (2) blocking the worker thread from observing process exit.
    """
    import fcntl
    import select as _select

    stdout_fd = proc.stdout.fileno()  # type: ignore[union-attr]
    stderr_fd = proc.stderr.fileno()  # type: ignore[union-attr]

    for fd in (stdout_fd, stderr_fd):
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    open_fds: set[int] = {stdout_fd, stderr_fd}

    while open_fds:
        try:
            ready, _, _ = _select.select(list(open_fds), [], [], 1.0)
        except (ValueError, OSError):
            break

        if not ready:
            if proc.poll() is not None:
                # Process exited — drain any buffered bytes then exit
                for fd in list(open_fds):
                    try:
                        while True:
                            chunk = os.read(fd, 4096)
                            if not chunk:
                                open_fds.discard(fd)
                                break
                            (stdout_chunks if fd == stdout_fd else stderr_chunks).append(chunk)
                            if fd == stdout_fd:
                                stall_detector.update_stdout_activity(job_id, time.time())
                    except (BlockingIOError, OSError):
                        open_fds.discard(fd)
                break
            continue

        for fd in ready:
            try:
                chunk = os.read(fd, 4096)
            except (BlockingIOError, OSError):
                open_fds.discard(fd)
                continue
            if not chunk:
                open_fds.discard(fd)
                continue
            if fd == stdout_fd:
                stdout_chunks.append(chunk)
                stall_detector.update_stdout_activity(job_id, time.time())
            else:
                stderr_chunks.append(chunk)

    return b"".join(stdout_chunks), b"".join(stderr_chunks)
```

Also add the import at the top of `daemon.py`:

```python
from ollama_queue.stall import StallDetector
```

And add `self.stall_detector = StallDetector()` to `Daemon.__init__()` (near other component instantiations like `self.health`, `self.dlq`).

**Step 4: Update `_run_job()` to branch on resource_profile**

Find the block starting at line 382 (`# communicate() drains pipes...`). Replace the entire `try: out, err = proc.communicate(...)` block **and its `except _TimeoutExpired` handler** with:

```python
            # LLM jobs: select()-based drain (no hard timeout — stall detector handles it)
            # Non-LLM jobs: communicate() with hard timeout
            if job.get("resource_profile") == "ollama":
                out, err = _drain_pipes_with_tracking(proc, job["id"], self.stall_detector)
                proc.wait()  # ensure returncode is set (drain loop exits on proc.poll())
            else:
                try:
                    out, err = proc.communicate(timeout=job["timeout"])
                except _TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    try:
                        out, err = proc.communicate(timeout=5)
                    except _TimeoutExpired:
                        out, err = b"", b""
                    with self.db._lock:
                        self.db.kill_job(
                            job["id"],
                            reason=f"timeout after {job['timeout']}s",
                            stdout_tail=out[-500:].decode("utf-8", errors="replace"),
                            stderr_tail=err[-500:].decode("utf-8", errors="replace"),
                        )
                        try:
                            self.dlq.handle_failure(job["id"], f"timeout after {job['timeout']}s")
                        except Exception:
                            _log.exception("DLQ routing failed for timed-out job #%d", job["id"])
                    return
```

**Step 5: Add `stall_detector.forget()` to the `finally` block**

In `_run_job()`'s `finally` block (currently just cleans `_running` and `_running_models`):

```python
        finally:
            self.stall_detector.forget(job["id"])   # ← add this line
            with self._running_lock:
                self._running.pop(job["id"], None)
                self._running_models.pop(job["id"], None)
```

**Step 6: Update existing daemon tests that mock `communicate` for LLM jobs**

All existing tests that set `proc.communicate.return_value = (b"...", b"")` for `resource_profile='ollama'` (the default) need to also patch `_drain_pipes_with_tracking`. The simplest fix is to add a patcher to `test_poll_runs_job` and any other test that exercises LLM jobs:

For `test_poll_runs_job`, `test_job_failure_routes_to_dlq`, `test_timeout_kills_job`, and any other tests where the submitted job uses the default resource_profile — wrap the existing `patch("ollama_queue.daemon.subprocess")` context with an additional patch:

```python
patch("ollama_queue.daemon._drain_pipes_with_tracking", return_value=(b"hello", b"")),
```

For `test_timeout_kills_job` specifically: this test exercises the non-LLM timeout path. Either change the submitted job to `resource_profile='any'`, OR keep the test as-is since the `resource_profile='ollama'` job now won't use `communicate()`. Review each test and apply the minimal fix.

**Step 7: Run the new test**

```bash
pytest tests/test_daemon.py::test_llm_job_does_not_use_communicate -v
```

Expected: PASS.

**Step 8: Run full daemon test suite**

```bash
pytest tests/test_daemon.py -v
```

Fix any failures from the communicate→drain migration.

**Step 9: Run full suite**

```bash
pytest --timeout=120 -x -q
```

**Step 10: Commit**

```bash
git add ollama_queue/daemon.py ollama_queue/stall.py tests/test_daemon.py
git commit -m "feat(daemon): replace communicate() with select()-based drain for LLM jobs"
```

---

### Task 4: Daemon — rewrite `_check_stalled_jobs()` with Bayesian logic + kill action

**Files:**
- Modify: `ollama_queue/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write the failing tests**

Add to `tests/test_daemon.py`:

```python
def test_stall_detection_flags_job(daemon):
    """_check_stalled_jobs sets stall_detected_at when posterior >= threshold."""
    import time
    job_id = daemon.db.submit_job("sleep 9999", "qwen2.5:7b", 5, 600, "test")
    # Manually move job to running state with a real-looking pid
    with daemon.db._lock:
        conn = daemon.db._connect()
        conn.execute(
            "UPDATE jobs SET status='running', started_at=?, pid=9999 WHERE id=?",
            (time.time() - 400, job_id),
        )
        conn.commit()

    with daemon._running_lock:
        daemon._running[job_id] = MagicMock()

    with (
        patch.object(daemon.stall_detector, "compute_posterior", return_value=(0.95, {"posterior": 0.95})),
        patch.object(daemon.stall_detector, "get_ollama_ps_models", return_value=set()),
    ):
        daemon._check_stalled_jobs(time.time())

    job = daemon.db.get_job(job_id)
    assert job["stall_detected_at"] is not None


def test_stall_kill_action(daemon):
    """_check_stalled_jobs calls os.kill when stall_action='kill' and grace elapsed."""
    import time
    daemon.db.update_setting("stall_action", "kill")
    daemon.db.update_setting("stall_kill_grace_seconds", 0)  # no grace period
    job_id = daemon.db.submit_job("sleep 9999", "qwen2.5:7b", 5, 600, "test")
    stall_time = time.time() - 120
    with daemon.db._lock:
        conn = daemon.db._connect()
        conn.execute(
            "UPDATE jobs SET status='running', started_at=?, pid=9999, stall_detected_at=? WHERE id=?",
            (time.time() - 400, stall_time, job_id),
        )
        conn.commit()

    with daemon._running_lock:
        daemon._running[job_id] = MagicMock()

    with (
        patch.object(daemon.stall_detector, "compute_posterior", return_value=(0.95, {"posterior": 0.95})),
        patch.object(daemon.stall_detector, "get_ollama_ps_models", return_value=set()),
        patch("ollama_queue.daemon.os.kill") as mock_kill,
    ):
        daemon._check_stalled_jobs(time.time())

    mock_kill.assert_called_once_with(9999, _signal.SIGTERM)


def test_stall_no_kill_within_grace(daemon):
    """_check_stalled_jobs does NOT kill if stall_kill_grace_seconds not elapsed."""
    import time
    daemon.db.update_setting("stall_action", "kill")
    daemon.db.update_setting("stall_kill_grace_seconds", 300)
    job_id = daemon.db.submit_job("sleep 9999", "qwen2.5:7b", 5, 600, "test")
    with daemon.db._lock:
        conn = daemon.db._connect()
        conn.execute(
            "UPDATE jobs SET status='running', started_at=?, pid=9999, stall_detected_at=? WHERE id=?",
            (time.time() - 400, time.time() - 10, job_id),  # stalled only 10s ago
        )
        conn.commit()

    with daemon._running_lock:
        daemon._running[job_id] = MagicMock()

    with (
        patch.object(daemon.stall_detector, "compute_posterior", return_value=(0.95, {"posterior": 0.95})),
        patch.object(daemon.stall_detector, "get_ollama_ps_models", return_value=set()),
        patch("ollama_queue.daemon.os.kill") as mock_kill,
    ):
        daemon._check_stalled_jobs(time.time())

    mock_kill.assert_not_called()
```

**Step 2: Run the failing tests**

```bash
pytest tests/test_daemon.py::test_stall_detection_flags_job tests/test_daemon.py::test_stall_kill_action tests/test_daemon.py::test_stall_no_kill_within_grace -v
```

Expected: 3 FAILs (old `_check_stalled_jobs` uses stall_multiplier, not Bayesian).

**Step 3: Rewrite `_check_stalled_jobs()` in `daemon.py`**

Replace the entire current method (lines 498–533) with:

```python
    def _check_stalled_jobs(self, now: float) -> None:
        """Bayesian multi-signal stall detection for running LLM jobs.

        One /api/ps HTTP call per poll cycle. Flags stall via DB when posterior
        exceeds threshold. Optionally sends SIGTERM after grace period elapses.
        """
        settings = self.db.get_all_settings()
        threshold = float(settings.get("stall_posterior_threshold", 0.8))
        action = settings.get("stall_action", "log")
        grace = float(settings.get("stall_kill_grace_seconds", 60))

        with self._running_lock:
            running_ids = list(self._running.keys())

        if not running_ids:
            return

        ps_models = self.stall_detector.get_ollama_ps_models()

        for job_id in running_ids:
            with self.db._lock:
                conn = self.db._connect()
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                continue
            job = dict(row)

            if job.get("resource_profile") != "ollama":
                continue  # non-LLM jobs use hard timeout, not stall detection

            pid = job.get("pid") or 0
            if pid <= 0:
                continue

            posterior, signals = self.stall_detector.compute_posterior(
                job_id, pid, job.get("model") or "", now, ps_models
            )

            stall_detected_at = job.get("stall_detected_at")

            if posterior >= threshold:
                if not stall_detected_at:
                    self.db.set_stall_detected(job_id, now, signals)
                    _log.warning(
                        "Job #%d stall detected: posterior=%.2f signals=%s",
                        job_id, posterior, signals,
                    )
                elif action == "kill":
                    stall_age = now - stall_detected_at
                    if stall_age >= grace:
                        _log.warning(
                            "Killing stalled job #%d (stall_age=%.0fs posterior=%.2f)",
                            job_id, stall_age, posterior,
                        )
                        with contextlib.suppress(ProcessLookupError, PermissionError):
                            os.kill(pid, _signal.SIGTERM)
```

**Step 4: Add `_signal` import check**

Verify `signal as _signal` is already imported in `daemon.py` (it is, from the recent audit fixes). If not, add:
```python
import signal as _signal
```

**Step 5: Run the new tests**

```bash
pytest tests/test_daemon.py::test_stall_detection_flags_job tests/test_daemon.py::test_stall_kill_action tests/test_daemon.py::test_stall_no_kill_within_grace -v
```

Expected: 3 PASSes.

**Step 6: Run full suite**

```bash
pytest --timeout=120 -x -q
```

Expected: all pass. Note and fix any failures.

**Step 7: Commit**

```bash
git add ollama_queue/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): Bayesian _check_stalled_jobs with posterior threshold and kill action"
```

---

### Task 5: Integration verification + push

**Step 1: Run complete test suite**

```bash
pytest --timeout=120 -q
```

Record pass count. Expected: all pass (previous count + ~25 new tests).

**Step 2: Build SPA to confirm no JS breakage**

```bash
cd ollama_queue/dashboard/spa && npm run build && cd ../../..
```

Expected: clean build.

**Step 3: Verify service restarts cleanly with schema migration**

```bash
systemctl --user restart ollama-queue.service
sleep 3
journalctl --user -u ollama-queue.service -n 20 --no-pager
```

Expected: no ERROR lines, "Starting ollama-queue on port 7683..." visible.

**Step 4: Verify new settings exist in running instance**

```bash
curl -s http://localhost:7683/api/settings | python3 -c "import sys,json; s=json.load(sys.stdin); print(s.get('stall_posterior_threshold'), s.get('stall_action'), s.get('stall_kill_grace_seconds'))"
```

Expected: `0.8 log 60`

**Step 5: Push**

```bash
git push
```

---

## Execution Handoff

Plan saved. Three execution options:

**1. Subagent-Driven (this session)** — Fresh subagent per task with two-stage review, fast iteration, watch progress.

**2. Parallel Session (separate)** — Open new session, use `executing-plans` skill, batch execution with human checkpoints.

**3. Headless (walk away)** —
```bash
scripts/run-plan.sh docs/plans/2026-02-28-bayesian-stall-detection-impl.md --quality-gate "scripts/quality-gate.sh --project-root ."
```

Which approach?
