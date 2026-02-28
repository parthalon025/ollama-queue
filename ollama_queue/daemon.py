"""Daemon polling loop and job runner for ollama-queue."""

from __future__ import annotations

import contextlib
import logging
import os
import signal as _signal
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from subprocess import TimeoutExpired as _TimeoutExpired

from ollama_queue.db import Database
from ollama_queue.dlq import DLQManager
from ollama_queue.estimator import DurationEstimator
from ollama_queue.health import HealthMonitor
from ollama_queue.models import OllamaModels
from ollama_queue.scheduler import Scheduler

_log = logging.getLogger(__name__)


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

    def _free_vram_mb(self) -> float | None:
        """Free VRAM in MB from nvidia-smi, or None if unavailable."""
        try:
            result = subprocess.run(
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

        # Pull in progress → block
        if self._model_pull_in_progress(model):
            return False

        # Already at capacity → block
        if running_count >= self._max_slots():
            return False

        # VRAM + RAM check
        free_vram = self._free_vram_mb()
        free_ram = self._free_ram_mb()
        model_vram = self._ollama_models.estimate_vram_mb(model, self.db) if model else 0.0

        vram_ok = free_vram is None or model_vram <= free_vram * 0.8
        ram_ok = model_vram * 0.5 <= free_ram * 0.8

        # Health gate — reuse existing hysteresis logic
        snap = self.health.check()
        settings = self.db.get_all_settings()
        health_eval = self.health.evaluate(snap, settings, currently_paused=False)

        if not vram_ok or not ram_ok or health_eval["should_pause"]:
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
            self.scheduler.promote_due_jobs(now)
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

        # 4. Get next pending job
        job = self.db.get_next_job()

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

        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._max_slots(), thread_name_prefix="oq-worker")

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

            # communicate() drains pipes while waiting — avoids deadlock on large output
            try:
                out, err = proc.communicate(timeout=job["timeout"])
            except _TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                try:
                    out, err = proc.communicate(timeout=5)
                except _TimeoutExpired:
                    out, err = b"", b""
                self.db.kill_job(
                    job["id"],
                    reason=f"timeout after {job['timeout']}s",
                    stdout_tail=out[-500:].decode("utf-8", errors="replace"),
                    stderr_tail=err[-500:].decode("utf-8", errors="replace"),
                )
                # Route timed-out jobs through DLQ so retry logic applies
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

            self.db.complete_job(
                job["id"],
                exit_code=exit_code,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
                outcome_reason=outcome_reason,
            )

            # Increment daily counters atomically
            counter_field = "jobs_completed_today" if exit_code == 0 else "jobs_failed_today"
            with self.db._lock:
                conn = self.db._connect()
                conn.execute(f"UPDATE daemon_state SET {counter_field} = COALESCE({counter_field}, 0) + 1 WHERE id = 1")
                conn.commit()

            # Route failed jobs through DLQ (retry or dead-letter)
            if exit_code != 0:
                try:
                    self.dlq.handle_failure(job["id"], f"exit code {exit_code}")
                except Exception:
                    _log.exception("DLQ routing failed for job #%d", job["id"])

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

        except Exception:
            _log.exception("Unhandled exception in worker thread for job #%d; marking failed", job["id"])
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
        finally:
            with self._running_lock:
                self._running.pop(job["id"], None)
                self._running_models.pop(job["id"], None)

    def _check_stalled_jobs(self, now: float) -> None:
        """Flag all running jobs exceeding stall threshold (multi-job aware)."""
        settings = self.db.get_all_settings()
        multiplier = settings.get("stall_multiplier", 2.0)
        # Iterate tracked running jobs (not just current_job_id)
        with self._running_lock:
            running_ids = list(self._running.keys())
        for job_id in running_ids:
            with self.db._lock:
                conn = self.db._connect()
                row = conn.execute(
                    "SELECT * FROM jobs WHERE id = ? AND stall_detected_at IS NULL",
                    (job_id,),
                ).fetchone()
            if not row:
                continue
            job = dict(row)
            estimated = job.get("estimated_duration")
            started = job.get("started_at")
            if estimated and started:
                elapsed = now - started
                stall_window = max(60, estimated * multiplier)
                if elapsed > stall_window:
                    with self.db._lock:
                        conn = self.db._connect()
                        conn.execute(
                            "UPDATE jobs SET stall_detected_at = ? WHERE id = ?",
                            (now, job_id),
                        )
                        conn.commit()
                    _log.warning(
                        "Job #%d stalled: elapsed=%.0fs, estimated=%.0fs",
                        job_id,
                        elapsed,
                        estimated,
                    )

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

    def run(self, poll_interval: int | None = None) -> None:
        """Main loop: poll_once() every N seconds. Prunes old data daily."""
        if poll_interval is None:
            poll_interval = self.db.get_setting("poll_interval_seconds") or 5

        self._recover_orphans()
        self.db.update_daemon_state(state="idle", uptime_since=time.time())

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
