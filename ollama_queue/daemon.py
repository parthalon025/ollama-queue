"""Daemon polling loop and job runner for ollama-queue.

Plain English: The heartbeat. Every 5 seconds it wakes up, asks "is the system
healthy, is anyone else using Ollama, and do we have capacity?" — then either
grabs the next job and runs it as a subprocess, or goes back to sleep with a
reason logged (paused_health, paused_interactive, idle).

Decision it drives: Should a job start right now, or should we wait — and why?
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal as _signal
import statistics
import subprocess
import subprocess as _subprocess  # real module — not replaced by test mocks targeting 'subprocess'
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from math import log, log2
from subprocess import TimeoutExpired as _TimeoutExpired

from ollama_queue.burst import _default_detector as _burst_singleton
from ollama_queue.db import Database
from ollama_queue.dlq import DLQManager
from ollama_queue.estimator import DurationEstimator
from ollama_queue.health import HealthMonitor
from ollama_queue.models import OllamaModels
from ollama_queue.scheduler import Scheduler
from ollama_queue.stall import StallDetector

_log = logging.getLogger(__name__)


def _drain_pipes_with_tracking(
    proc: subprocess.Popen,
    job_id: int,
    stall_detector: StallDetector,
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


class Daemon:
    """Main daemon that polls for jobs and executes them."""

    # Models loaded by queue jobs stay resident in Ollama after completion.
    # Track them so the interactive-yield check doesn't self-block.
    RECENT_MODEL_WINDOW = 600  # 10 minutes

    def __init__(self, db: Database, health_monitor: HealthMonitor | None = None):
        self.db = db
        self.health = health_monitor or HealthMonitor()
        self.estimator = DurationEstimator(db)
        self.scheduler = Scheduler(db)
        self.dlq = DLQManager(db)
        self._last_prune: float = 0.0
        self._recent_job_models: dict[str, float] = {}  # model -> last_completed_at
        self._recent_models_lock = threading.Lock()
        # Concurrency tracking
        self._running: dict[int, Future] = {}  # job_id → Future
        self._running_models: dict[int, str] = {}  # job_id → model
        self._running_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._concurrent_enabled_at: float | None = None  # for shadow mode
        self._ollama_models = OllamaModels()
        self.stall_detector = StallDetector()
        # Circuit breaker state
        self._cb_failures: int = 0  # consecutive Ollama failures
        self._cb_state: str = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self._cb_opened_at: float | None = None  # timestamp when circuit opened
        self._cb_open_attempts: int = 0  # how many times circuit has opened (for exponential cooldown)
        self._cb_probe_in_flight: bool = False  # True while a HALF_OPEN probe is dispatched
        self._cb_lock: threading.Lock = threading.Lock()  # protects all _cb_* state
        # Adaptive entropy tracking (in-memory rolling baseline)
        self._entropy_history: deque[float] = deque(maxlen=30)
        self._entropy_suspend_until: float = 0.0
        # Burst detection — use the module-level singleton so the API's record_submission()
        # calls (on /api/queue/submit) feed into the same detector the daemon reads.
        self._burst_detector = _burst_singleton
        self._burst_regime: str = "unknown"  # cached for /api/health

    # --- Concurrency helpers ---

    def _max_slots(self) -> int:
        return max(1, int(self.db.get_setting("max_concurrent_jobs") or 1))

    def _shadow_hours(self) -> float:
        return float(self.db.get_setting("concurrent_shadow_hours") or 24)

    def _in_shadow_mode(self) -> bool:
        if self._max_slots() <= 1:
            return False
        if self._concurrent_enabled_at is None:
            self._concurrent_enabled_at = time.time()
            return True
        elapsed_hours = (time.time() - self._concurrent_enabled_at) / 3600
        return elapsed_hours < self._shadow_hours()

    def _compute_max_workers(self) -> int:
        """Compute max ThreadPoolExecutor workers based on available hardware resources.

        Formula: floor((vram_available + ram_available * cpu_offload_efficiency) / min_model_vram) + 3
        The +3 reserves slots for CPU-bound/embedding jobs that don't compete for GPU.
        Falls back to 4 if resource metrics are unavailable.

        Uses _free_vram_mb() and _free_ram_mb() (same sources as _can_admit) so the
        executor ceiling is grounded in actual hardware state at startup time.
        """
        vram_available = self._free_vram_mb() or 0.0
        ram_available = self._free_ram_mb()

        cpu_efficiency = float(self.db.get_setting("cpu_offload_efficiency") or 0.3)
        fallback_mb = int(self.db.get_setting("min_model_vram_mb") or 2000)
        min_model_vram = self._ollama_models.min_estimated_vram_mb(self.db, fallback_mb=fallback_mb)

        if min_model_vram <= 0:
            return 4

        effective_vram = vram_available + ram_available * cpu_efficiency
        workers = max(1, int(effective_vram / min_model_vram) + 3)
        return workers

    def _committed_vram_mb(self) -> float:
        """Estimate VRAM already committed to running jobs.

        Computed from the model estimates of jobs currently in _running_models
        rather than a live GPU reading, so two simultaneous admission checks
        both see the same deterministic committed total.  Must be called with
        _running_lock held so _running_models is stable. (#5)
        """
        model_counts: dict[str, int] = {}
        for model_name in self._running_models.values():
            if model_name:
                model_counts[model_name] = model_counts.get(model_name, 0) + 1
        total = 0.0
        for model_name, count in model_counts.items():
            per_model = self._ollama_models.estimate_vram_mb(model_name, self.db) or 0.0
            total += per_model * count
        return total

    def _free_vram_mb(self) -> float | None:
        """Free VRAM in MB from nvidia-smi, or None if unavailable."""
        try:
            result = _subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return float(result.stdout.strip().split("\n")[0])
        except Exception:
            _log.debug("nvidia-smi unavailable for VRAM check")
        return None

    def _free_ram_mb(self) -> float:
        """Free RAM in MB from /proc/meminfo."""
        info = HealthMonitor._parse_meminfo()
        return info.get("MemAvailable", 0) / 1024.0  # kB → MB

    def _model_pull_in_progress(self, model_name: str) -> bool:
        if not model_name:
            return False
        with self.db._lock:
            row = (
                self.db._connect()
                .execute(
                    "SELECT id FROM model_pulls WHERE model = ? AND status = 'pulling'",
                    (model_name,),
                )
                .fetchone()
            )
        return row is not None

    def _model_exists(self, model_name: str) -> bool:
        if not model_name:
            return True  # no model required, command-only job
        models = self._ollama_models.list_local()
        return any(m["name"] == model_name for m in models)

    def _can_admit(self, job: dict) -> bool:
        """Three-factor admission gate. Returns True if job can start now.

        Plain English: The bouncer. Before a job gets a worker thread, it must
        pass three checks in order:
          1. Concurrency type — embed jobs get up to 4 slots; heavy models
             (70B+) must run alone; standard models serialize per-model.
          2. Resource budget — does adding this job's estimated VRAM push us
             over the configured ceiling? (Uses committed-model math, not a
             live GPU read, so two simultaneous checks can't both pass.)
          3. System health — is health.evaluate() currently telling us to pause?

        INVARIANT: must only be called from the poll_once main thread.
        The running_count snapshot taken inside the lock is used outside it —
        this is safe only because poll_once is single-threaded and never calls
        _can_admit from worker threads.
        """
        profile = self._ollama_models.classify(job.get("model") or "")["resource_profile"]

        # embed: up to 4 concurrent embed jobs, no VRAM gate
        if profile == "embed":
            with self._running_lock:
                embed_count = sum(
                    1
                    for jid in self._running
                    if self._ollama_models.classify(self._running_models.get(jid, ""))["resource_profile"] == "embed"
                )
                return embed_count < 4

        # heavy: serialize — never concurrent
        if profile == "heavy":
            with self._running_lock:
                return len(self._running) == 0

        # Same model already running → serialize
        model = job.get("model") or ""
        with self._running_lock:
            if model and model in self._running_models.values():
                return False
            running_count = len(self._running)
            # Compute committed VRAM inside the lock so the snapshot is consistent
            # with the running_count snapshot above. (#5)
            committed_vram = self._committed_vram_mb()

        # Pull in progress → block
        if self._model_pull_in_progress(model):
            return False

        # Already at capacity → block
        if running_count >= self._max_slots():
            return False

        # Resource gate: estimate whether the new model fits alongside already-committed
        # models.  We derive "available" VRAM from committed model estimates rather than
        # a live nvidia-smi read, so two concurrent admission checks both see the same
        # deterministic value and cannot both pass on a tight GPU. (#5)
        #
        # max_vram_mb: optional DB setting; if absent, skip the absolute VRAM check
        # (health.evaluate's vram_pct gate still applies below as a safety net).
        model_vram = self._ollama_models.estimate_vram_mb(model, self.db) if model else 0.0
        max_vram_raw = self.db.get_setting("max_vram_mb")
        if max_vram_raw is not None:
            max_vram = float(max_vram_raw)
            resource_ok = (committed_vram + model_vram) <= max_vram * 0.8
        else:
            # TODO: read total GPU VRAM once from nvidia-smi at startup and cache it,
            # then use (total - committed) as the available headroom.  For now skip the
            # absolute check and rely on health.evaluate's vram_pct threshold.
            resource_ok = True

        # Health gate — reuse existing hysteresis logic
        snap = self.health.check()
        settings = self.db.get_all_settings()
        health_eval = self.health.evaluate(snap, settings, currently_paused=False)

        if not resource_ok or health_eval["should_pause"]:
            return False

        # Shadow mode — log but don't admit second job yet
        if self._in_shadow_mode() and running_count > 0:
            _log.info(
                "SHADOW: would admit concurrent job #%d (%s) — shadow mode active",
                job["id"],
                job.get("source", ""),
            )
            return False

        return True

    def _compute_queue_entropy(self, pending_jobs: list[dict], now: float) -> float:
        """Compute age-weighted Shannon entropy of the pending queue's priority distribution.

        Age-weighted: older jobs count more (log(1 + wait_seconds)).
        Higher entropy = diverse priority mix = healthy queue.
        Lower entropy = priority collapse = backlog or flood.
        Returns 0.0 for empty queue.
        """
        if not pending_jobs:
            return 0.0

        weights = {j["id"]: log(1.0 + max(0.0, now - (j.get("submitted_at") or now))) for j in pending_jobs}
        total_w = sum(weights.values()) or 1.0

        priority_weights: dict[int, float] = defaultdict(float)
        for j in pending_jobs:
            priority_weights[j["priority"]] += weights[j["id"]] / total_w

        return -sum(w * log2(w) for w in priority_weights.values() if w > 0)

    def _check_entropy(self, pending_jobs: list[dict], now: float) -> None:
        """Compute entropy, update rolling baseline, log anomalies, set suspension."""
        entropy = self._compute_queue_entropy(pending_jobs, now)
        self._entropy_history.append(entropy)

        # Need at least 10 samples for a meaningful baseline
        if len(self._entropy_history) < 10:
            return

        sigma = float(self.db.get_setting("entropy_alert_sigma") or 2.0)
        mean_entropy = statistics.mean(self._entropy_history)
        std_entropy = statistics.stdev(self._entropy_history) if len(self._entropy_history) > 1 else 0.1
        if std_entropy == 0:
            std_entropy = 0.1

        if entropy < mean_entropy - sigma * std_entropy:
            # Determine anomaly type
            high_priority_count = sum(1 for j in pending_jobs if j["priority"] <= 4)
            if high_priority_count / max(len(pending_jobs), 1) > 0.7:
                alert_type = "critical_backlog"
            else:
                alert_type = "background_flood"

            self.db.log_schedule_event(
                "entropy_alert",
                details={"entropy": entropy, "mean_entropy": mean_entropy, "sigma": sigma, "type": alert_type},
            )
            _log.warning(
                "Queue entropy anomaly detected: entropy=%.2f mean=%.2f type=%s",
                entropy,
                mean_entropy,
                alert_type,
            )

            suspend_enabled = self.db.get_setting("entropy_suspend_low_priority")
            if alert_type == "critical_backlog" and suspend_enabled:
                self._entropy_suspend_until = now + 60.0  # suspend p8-10 for 60s
                _log.info("Suspended low-priority (p8-10) promotion for 60s due to critical_backlog")

    def _recover_orphans(self) -> None:
        """Reset jobs stuck in 'running' on daemon startup (no live subprocess)."""
        orphans = self.db.get_running_jobs()
        for job in orphans:
            if job.get("pid") and job["pid"] > 0:
                try:
                    os.kill(job["pid"], _signal.SIGTERM)
                    _log.info("Sent SIGTERM to orphaned pid=%d (job #%d)", job["pid"], job["id"])
                except ProcessLookupError:
                    pass  # process already gone
            self.db.reset_job_to_pending(job["id"])
            _log.warning("Reset orphaned job #%d to pending", job["id"])

    # --- Circuit breaker ---

    def _compute_cb_cooldown(self, attempt: int) -> float:
        """Exponential cooldown: base * 2^attempt, capped at max."""
        base = float(self.db.get_setting("cb_base_cooldown") or 30)
        cap = float(self.db.get_setting("cb_max_cooldown") or 600)
        return min(cap, base * (2**attempt))

    def _record_ollama_failure(self) -> None:
        """Record a consecutive Ollama failure; open circuit if threshold reached."""
        # Read DB setting BEFORE acquiring _cb_lock to avoid lock-order inversion.
        # Call sites hold db._lock; acquiring _cb_lock inside would give db._lock -> _cb_lock,
        # while _is_circuit_open() would give _cb_lock -> db._lock — deadlock.
        threshold = int(self.db.get_setting("cb_failure_threshold") or 3)
        with self._cb_lock:
            self._cb_probe_in_flight = False
            self._cb_failures += 1
            if self._cb_failures >= threshold and self._cb_state in ("CLOSED", "HALF_OPEN"):
                self._cb_state = "OPEN"
                self._cb_opened_at = time.time()
                self._cb_open_attempts += 1
                _log.warning(
                    "Circuit breaker OPENED after %d consecutive Ollama failures",
                    self._cb_failures,
                )

    def _record_ollama_success(self) -> None:
        """Record a successful Ollama job; reset failure count and close circuit."""
        with self._cb_lock:
            self._cb_probe_in_flight = False
            self._cb_failures = 0
            self._cb_state = "CLOSED"
            if self._cb_open_attempts > 0:
                _log.info("Circuit breaker CLOSED after successful probe")

    def _is_circuit_open(self) -> bool:
        """Return True if the circuit breaker should block new jobs.

        CLOSED -> False (normal operation)
        OPEN + cooldown not elapsed -> True (blocking)
        OPEN + cooldown elapsed -> transition to HALF_OPEN, return False (allow one probe)
        HALF_OPEN + no probe in flight -> False (allow this probe through, mark in-flight)
        HALF_OPEN + probe in flight -> True (block subsequent polls until probe resolves)

        Lock ordering: _cb_lock must never be held while calling db methods (db._lock).
        Split into three phases: read state (lock), compute cooldown (no lock, reads DB),
        transition state (lock again with re-check for TOCTOU).
        """
        # Phase 1: read current state under lock
        with self._cb_lock:
            if self._cb_state == "CLOSED":
                return False
            if self._cb_state == "HALF_OPEN":
                if self._cb_probe_in_flight:
                    return True  # probe already dispatched, block further jobs
                self._cb_probe_in_flight = True  # mark probe as in-flight
                return False  # allow this one probe through
            # OPEN state — capture values needed for cooldown check
            attempt = self._cb_open_attempts
            opened_at = self._cb_opened_at or 0.0

        # Phase 2: compute cooldown OUTSIDE lock (calls db.get_setting -> db._lock).
        # Holding _cb_lock here while db._lock is acquired elsewhere causes deadlock.
        cooldown = self._compute_cb_cooldown(attempt)
        elapsed = time.time() - opened_at

        if elapsed < cooldown:
            return True  # still in cooldown period

        # Phase 3: transition to HALF_OPEN under lock with re-check.
        # Another thread may have already transitioned during Phase 2 — double-checked
        # locking prevents two polls both seeing an expired cooldown and both transitioning.
        with self._cb_lock:
            if self._cb_state != "OPEN":
                # Already transitioned (CLOSED or HALF_OPEN) — reflect current state
                return self._cb_state == "OPEN"  # always False here
            self._cb_state = "HALF_OPEN"
            self._cb_probe_in_flight = True  # first probe dispatched immediately on transition
            _log.info("Circuit breaker entering HALF_OPEN state for probe job")
            return False  # allow probe through

    def poll_once(self) -> None:
        """Single poll cycle.

        1. Check if manually paused -> skip
        2. Clean up completed futures; skip if at max slots
        3. Get health snapshot + log it
        4. Get next pending job
        5. If no job -> set idle, return
        6. Evaluate health -> if pause needed, update state, return
        7. Evaluate yield -> if interactive user, update state, return
        8. Admit via _can_admit; dispatch to executor thread
        """
        now = time.time()

        # 0. Promote due recurring jobs (runs even while a job is running)
        try:
            suspend_low_priority = now < self._entropy_suspend_until
            self.scheduler.promote_due_jobs(now, suspend_low_priority=suspend_low_priority)
        except Exception:
            _log.exception("Scheduler promotion failed; continuing")

        # 0b. Detect stalled running jobs
        try:
            self._check_stalled_jobs(now)
        except Exception:
            _log.exception("Stall detection failed; continuing")

        # 0c. Re-queue retryable jobs whose backoff has elapsed
        try:
            self._check_retryable_jobs(now)
        except Exception:
            _log.exception("Retry check failed; continuing")

        state = self.db.get_daemon_state()

        # 1. Check if manually paused
        if state["state"] == "paused_manual":
            self.db.update_daemon_state(last_poll_at=now)
            return

        # 2. Clean up completed futures; call .result() to surface worker exceptions
        with self._running_lock:
            done_ids = [jid for jid, fut in self._running.items() if fut.done()]
            for jid in done_ids:
                fut = self._running.pop(jid)
                self._running_models.pop(jid, None)
                try:
                    fut.result()
                except Exception:
                    _log.exception("Worker thread for job #%d raised unhandled exception", jid)

        # 3. Get health snapshot + log it
        snap = self.health.check()
        pending_jobs = self.db.get_pending_jobs()
        queue_depth = len(pending_jobs)
        current_state = state["state"]
        self.db.log_health(
            ram_pct=snap["ram_pct"],
            vram_pct=snap.get("vram_pct") or 0.0,
            load_avg=snap["load_avg"],
            swap_pct=snap["swap_pct"],
            ollama_model=snap.get("ollama_model") or "",
            queue_depth=queue_depth,
            daemon_state=current_state,
        )

        # 3b. Circuit breaker check — skip dequeue if Ollama is unreachable
        if self._is_circuit_open():
            _log.debug("Circuit breaker OPEN — skipping dequeue")
            return

        # 3c. Entropy anomaly detection
        try:
            self._check_entropy(pending_jobs, now)
        except Exception:
            _log.exception("Entropy check failed; continuing")

        # 3d. Update burst regime every poll (BurstDetector is cheap to query)
        try:
            self._burst_regime = self._burst_detector.regime(now)
            if self._burst_regime in ("warning", "critical"):
                _log.info("Burst regime: %s", self._burst_regime)
            self.db.update_daemon_state(burst_regime=self._burst_regime)
        except Exception:
            _log.exception("Burst regime check failed; continuing")

        # 4. Get next pending job — SJF + aging sort
        estimates = self.db.estimate_duration_bulk([j["source"] for j in pending_jobs])
        job = self._dequeue_next_job(pending_jobs, estimates, now)

        # 5. If no job -> set idle, return
        if job is None:
            self.db.update_daemon_state(state="idle", last_poll_at=now, current_job_id=None)
            return

        # 6. Evaluate health -> if pause needed, update state, return
        settings = self.db.get_all_settings()
        currently_paused = current_state.startswith("paused")
        # Prune expired entries from recent job models (lock: written by worker threads)
        with self._recent_models_lock:
            self._recent_job_models = {
                m: t for m, t in self._recent_job_models.items() if now - t < self.RECENT_MODEL_WINDOW
            }
            recent_models_snapshot = set(self._recent_job_models.keys())
        evaluation = self.health.evaluate(
            snap,
            settings,
            currently_paused=currently_paused,
            queued_model=job["model"],
            recent_job_models=recent_models_snapshot,
        )

        if evaluation["should_pause"]:
            self.db.update_daemon_state(
                state="paused_health",
                paused_reason=evaluation["reason"],
                paused_since=now if not currently_paused else state.get("paused_since"),
                last_poll_at=now,
                current_job_id=None,
            )
            return

        # 7. Evaluate yield -> if interactive user, update state, return
        if evaluation["should_yield"]:
            self.db.update_daemon_state(
                state="paused_interactive",
                paused_reason=evaluation["reason"],
                paused_since=now if not currently_paused else state.get("paused_since"),
                last_poll_at=now,
                current_job_id=None,
            )
            return

        # 8. Admit and dispatch
        if not self._can_admit(job):
            self.db.update_daemon_state(state="idle", last_poll_at=now, current_job_id=None)
            return

        # Preemption: if new job is priority 1-2, check if we should preempt a running job
        preempt_id = self._check_preemption(job, now)
        if preempt_id is not None:
            self._preempt_job(preempt_id)
            # Continue — now there's a free slot for the new job

        if self._executor is None:
            # max_workers is a ceiling; actual concurrency is governed entirely by
            # _can_admit, which reads the live max_concurrent_jobs setting.  The ceiling
            # is computed from real hardware resources at first-job startup so the thread
            # pool isn't arbitrarily oversized on memory-constrained hosts.
            self._executor = ThreadPoolExecutor(
                max_workers=self._compute_max_workers(),
                thread_name_prefix="ollama-worker",
            )

        self.db.start_job(job["id"])
        with self.db._lock:
            conn = self.db._connect()
            conn.execute("UPDATE jobs SET pid = -1 WHERE id = ?", (job["id"],))
            conn.commit()

        fut = self._executor.submit(self._run_job, job)
        with self._running_lock:
            self._running[job["id"]] = fut
            self._running_models[job["id"]] = job.get("model") or ""

        with self._running_lock:
            running_count = len(self._running)
        state_label = "running" if running_count == 1 else f"running({running_count})"
        self.db.update_daemon_state(
            state=state_label,
            current_job_id=job["id"],
            paused_reason=None,
            paused_since=None,
            last_poll_at=now,
        )

    def _run_job(self, job: dict) -> None:
        """Execute a job in a worker thread. Records result in DB."""
        start_time = time.time()

        # Pre-flight check_command: gate job on external signal
        if job.get("recurring_job_id"):
            _rj = self.db.get_recurring_job(job["recurring_job_id"])
            if _rj and _rj.get("check_command"):
                _check_result = self._run_check_command(job, _rj)
                if _check_result in ("skip", "disable"):
                    return

        # Sample VRAM before job for observed-VRAM recording
        vram_before = self._free_vram_mb()

        try:
            proc = subprocess.Popen(
                job["command"],
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Record real PID immediately for orphan recovery
            with self.db._lock:
                conn = self.db._connect()
                conn.execute("UPDATE jobs SET pid = ? WHERE id = ?", (proc.pid, job["id"]))
                conn.commit()

            # LLM jobs: select()-based drain (no hard timeout - stall detector handles it)
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
                    # Atomic: no reader sees 'killed' before DLQ routing decides retry/dead.
                    # db._lock is RLock — kill_job and handle_failure re-acquire safely. (#3)
                    with self.db._lock:
                        self.db.kill_job(
                            job["id"],
                            reason=f"timeout after {job['timeout']}s",
                            stdout_tail=out[-500:].decode("utf-8", errors="replace"),
                            stderr_tail=err[-500:].decode("utf-8", errors="replace"),
                        )
                        self._record_ollama_failure()
                        try:
                            self.dlq.handle_failure(job["id"], f"timeout after {job['timeout']}s")
                        except Exception:
                            _log.exception("DLQ routing failed for timed-out job #%d", job["id"])
                    return

            # Record result
            duration = time.time() - start_time
            exit_code = proc.returncode
            stdout_tail = out[-500:].decode("utf-8", errors="replace")
            stderr_tail = err[-500:].decode("utf-8", errors="replace")

            outcome_reason = None
            if exit_code != 0:
                outcome_reason = f"exit code {exit_code}"

            # Atomic: no reader sees 'failed' before DLQ routing decides retry/dead.
            # db._lock is RLock — complete_job and handle_failure re-acquire safely. (#3)
            with self.db._lock:
                self.db.complete_job(
                    job["id"],
                    exit_code=exit_code,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    outcome_reason=outcome_reason,
                )
                if exit_code != 0:
                    self._record_ollama_failure()
                    try:
                        self.dlq.handle_failure(job["id"], f"exit code {exit_code}")
                    except Exception:
                        _log.exception("DLQ routing failed for job #%d", job["id"])
                else:
                    self._record_ollama_success()

            # Increment daily counters atomically
            counter_field = "jobs_completed_today" if exit_code == 0 else "jobs_failed_today"
            with self.db._lock:
                conn = self.db._connect()
                conn.execute(f"UPDATE daemon_state SET {counter_field} = COALESCE({counter_field}, 0) + 1 WHERE id = 1")
                conn.commit()

            # Track model so interactive-yield check doesn't self-block
            if job.get("model"):
                with self._recent_models_lock:
                    self._recent_job_models[job["model"]] = time.time()

            # Record duration if successful
            if exit_code == 0:
                self.db.record_duration(
                    source=job["source"],
                    model=job["model"],
                    duration=duration,
                    exit_code=exit_code,
                )
                # Record observed VRAM delta
                vram_after = self._free_vram_mb()
                if vram_before is not None and vram_after is not None and job.get("model"):
                    delta = vram_before - vram_after
                    if delta > 0:
                        self._ollama_models.record_observed_vram(job["model"], delta, self.db)

                # max_runs countdown: decrement on success, auto-disable at 0
                if job.get("recurring_job_id"):
                    _rj_for_maxruns = self.db.get_recurring_job(job["recurring_job_id"])
                    if _rj_for_maxruns and _rj_for_maxruns.get("max_runs") is not None:
                        remaining = _rj_for_maxruns["max_runs"] - 1
                        if remaining <= 0:
                            self.db.disable_recurring_job(job["recurring_job_id"], "max_runs exhausted")
                            _log.info(
                                "Recurring job id=%d auto-disabled: max_runs exhausted",
                                job["recurring_job_id"],
                            )
                        else:
                            self.db.update_recurring_job(job["recurring_job_id"], max_runs=remaining)

            # Update recurring schedule
            if job.get("recurring_job_id"):
                try:
                    self.scheduler.update_next_run(
                        job["recurring_job_id"],
                        completed_at=time.time(),
                        job_id=job["id"],
                    )
                except Exception:
                    _log.exception("Scheduler next_run update failed for job #%d", job["id"])

        except Exception as exc:
            _log.exception("Unhandled exception in worker thread for job #%d; marking failed", job["id"])
            # Atomic: complete_job + handle_failure under one lock so no reader
            # sees a transient 'failed' state before DLQ decides retry/dead. (#3)
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
                try:
                    self.dlq.handle_failure(
                        job["id"],
                        f"internal error: {type(exc).__name__}",
                    )
                except Exception:
                    _log.exception("Failed to route job #%d to DLQ after internal error", job["id"])
        finally:
            self.stall_detector.forget(job["id"])
            with self._running_lock:
                self._running.pop(job["id"], None)
                self._running_models.pop(job["id"], None)

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
                        job_id,
                        posterior,
                        signals,
                    )
                elif action == "kill":
                    stall_age = now - stall_detected_at
                    if stall_age >= grace:
                        _log.warning(
                            "Killing stalled job #%d (stall_age=%.0fs posterior=%.2f)",
                            job_id,
                            stall_age,
                            posterior,
                        )
                        with contextlib.suppress(ProcessLookupError, PermissionError):
                            os.kill(pid, _signal.SIGTERM)

    def _check_retryable_jobs(self, now: float) -> None:
        """Clear retry_after for pending jobs whose backoff window has elapsed."""
        with self.db._lock:
            conn = self.db._connect()
            conn.execute(
                """UPDATE jobs SET retry_after = NULL
                   WHERE status = 'pending' AND retry_after IS NOT NULL AND retry_after <= ?""",
                (now,),
            )
            conn.commit()

    def _dequeue_next_job(
        self,
        pending: list[dict],
        estimates: dict[str, float],
        now: float,
    ) -> dict | None:
        """Return the highest-priority pending job using SJF + aging sort.

        Sort key: (priority, effective_duration) where:
          effective_duration = risk_adjusted / (1 + wait/aging_factor)
          risk_adjusted = mean + 0.5 * std_dev  (penalizes high-variance estimates)
          aging_factor from settings (default 3600s = 1 hour)

        Only returns jobs whose retry_after has elapsed (or is NULL).
        """
        if not pending:
            return None

        aging_factor = float(self.db.get_setting("sjf_aging_factor") or 3600)

        def sort_key(j: dict) -> tuple:
            duration, cv_sq = self.estimator.estimate_with_variance(
                j["source"],
                model=j.get("model"),
                cached=estimates,
            )
            std_dev = duration * (cv_sq**0.5)
            risk_adjusted = duration + 0.5 * std_dev

            submitted = j.get("submitted_at")
            wait = now - submitted if submitted is not None else 0
            effective = risk_adjusted / (1.0 + wait / aging_factor) if aging_factor > 0 and wait > 0 else risk_adjusted

            return (j["priority"], effective)

        # Filter out jobs still in backoff
        eligible = [j for j in pending if j.get("retry_after") is None or j["retry_after"] <= now]
        if not eligible:
            return None

        eligible.sort(key=sort_key)
        return eligible[0]

    def _check_preemption(self, new_job: dict, now: float) -> int | None:
        """Find a running job to preempt for new_job. Returns job_id or None.

        Preemption only occurs when:
        1. preemption_enabled=True (opt-in)
        2. new_job priority is 1 or 2
        3. A running job has run < preemption_window_seconds
        4. That running job has < max_preemptions_per_job preemptions
        5. Running job has been silent > 30s (likely not near completion)
        6. Running job's VRAM >= new_job's VRAM (would free enough headroom)
        7. Running job has more estimated time remaining than new_job's total duration
        """
        if int(new_job.get("priority") or 10) > 2:
            return None
        if not self.db.get_setting("preemption_enabled"):
            return None

        preempt_window = float(self.db.get_setting("preemption_window_seconds") or 120)
        max_preemptions = int(self.db.get_setting("max_preemptions_per_job") or 2)
        new_duration = self.estimator.estimate(new_job.get("source") or "", new_job.get("model"))
        new_vram = self._ollama_models.estimate_vram_mb(new_job.get("model") or "", self.db)

        with self._running_lock:
            candidates = list(self._running.keys())

        for jid in candidates:
            job = self.db.get_job(jid)
            if job is None:
                continue
            if (job.get("preemption_count") or 0) >= max_preemptions:
                continue  # immune

            _sa = job.get("started_at")
            started_at = _sa if _sa is not None else now
            elapsed = now - started_at
            if elapsed >= preempt_window:
                continue  # too far into execution

            # Skip recently active jobs (stdout in last 30s = likely near completion)
            silence = self.stall_detector.get_stdout_silence(jid, now)
            if silence is not None and silence < 30.0:
                continue

            running_vram = self._ollama_models.estimate_vram_mb(self._running_models.get(jid) or "", self.db)
            if running_vram < new_vram:
                continue  # wouldn't free enough VRAM

            estimated_duration = job.get("estimated_duration") or self.estimator.estimate(
                job.get("source") or "", job.get("model")
            )
            remaining = estimated_duration - elapsed
            if remaining <= new_duration:
                continue  # running job nearly done; not worth preempting

            return jid  # found a candidate

        return None

    def _preempt_job(self, job_id: int) -> None:
        """SIGTERM the running job and requeue as pending.

        NEVER sends to DLQ. Preempted jobs are healthy work interrupted deliberately.
        DLQ is for permanent failures requiring human review.
        """
        job = self.db.get_job(job_id)
        pid = job.get("pid") if job else None
        if pid and pid > 0:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, _signal.SIGTERM)
                _log.info("Sent SIGTERM to job #%d pid=%d for preemption", job_id, pid)

        self.db.requeue_preempted_job(job_id)
        self.db.log_schedule_event(
            "preempted",
            job_id=job_id,
            details={"job_id": job_id, "reason": "priority_preemption"},
        )
        _log.warning("Preempted job #%d — requeued as pending", job_id)

        with self._running_lock:
            self._running.pop(job_id, None)
            self._running_models.pop(job_id, None)

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
        except _TimeoutExpired:
            _log.warning(
                "check_command timed out for recurring job id=%d — proceeding (fail-open)",
                rj_id,
            )
            return "proceed"
        except Exception:
            _log.warning(
                "check_command failed with exception for recurring job id=%d — proceeding (fail-open)",
                rj_id,
                exc_info=True,
            )
            return "proceed"

        if code == 0:
            return "proceed"
        elif code == 1:
            _log.info(
                "check_command exit 1 for recurring job id=%d (%s) — no work, skipping",
                rj_id,
                recurring_job.get("name", ""),
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
                rj_id,
                recurring_job.get("name", ""),
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
                "check_command returned unknown exit code %d for recurring job id=%d — " "proceeding (fail-open)",
                code,
                rj_id,
            )
            return "proceed"

    def shutdown(self) -> None:
        """Shut down the thread pool executor, releasing worker threads."""
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None

    def run(self, poll_interval: int | None = None) -> None:
        """Main loop: poll_once() every N seconds. Prunes old data daily."""
        if poll_interval is None:
            poll_interval = self.db.get_setting("poll_interval_seconds") or 5

        self._recover_orphans()
        self.db.update_daemon_state(state="idle", uptime_since=time.time())

        try:
            while True:
                try:
                    self.poll_once()
                except Exception:
                    _log.exception("Unexpected error in poll_once(); attempting state recovery")
                    try:
                        self.db.update_daemon_state(state="idle", current_job_id=None, last_poll_at=time.time())
                    except Exception:
                        _log.exception("State recovery also failed; daemon loop continues")

                # Prune once per day + reset daily counters
                now = time.time()
                if now - self._last_prune > 86400:
                    try:
                        self.db.prune_old_data()
                        self.db.update_daemon_state(jobs_completed_today=0, jobs_failed_today=0)
                        self._last_prune = now
                    except Exception:
                        _log.exception("Daily prune failed; will retry next cycle")

                time.sleep(poll_interval)
        finally:
            self.shutdown()
