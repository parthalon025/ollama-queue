"""Daemon polling loop and job runner for ollama-queue."""

from __future__ import annotations

import logging
import subprocess
import time

from ollama_queue.db import Database
from ollama_queue.estimator import DurationEstimator
from ollama_queue.health import HealthMonitor

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
        self._last_prune: float = 0.0
        self._recent_job_models: dict[str, float] = {}  # model -> last_completed_at

    def poll_once(self) -> None:
        """Single poll cycle.

        1. Check if manually paused -> skip
        2. Check if already running a job -> skip
        3. Get health snapshot + log it
        4. Get next pending job
        5. If no job -> set idle, return
        6. Evaluate health -> if pause needed, update state, return
        7. Evaluate yield -> if interactive user, update state, return
        8. Start job (subprocess.Popen with shell=True)
        9. Wait with timeout
        10. Record result (complete/fail/kill)
        11. Record duration if successful
        12. Update daemon state back to idle
        """
        now = time.time()
        state = self.db.get_daemon_state()

        # 1. Check if manually paused
        if state["state"] == "paused_manual":
            self.db.update_daemon_state(last_poll_at=now)
            return

        # 2. Check if already running a job
        if state["state"] == "running" and state["current_job_id"] is not None:
            self.db.update_daemon_state(last_poll_at=now)
            return

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
            ollama_model=snap.get("ollama_model"),
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
        # Prune expired entries from recent job models
        self._recent_job_models = {
            m: t for m, t in self._recent_job_models.items()
            if now - t < self.RECENT_MODEL_WINDOW
        }
        evaluation = self.health.evaluate(
            snap, settings, currently_paused=currently_paused, queued_model=job["model"],
            recent_job_models=set(self._recent_job_models.keys()),
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

        # 8. Start job
        self.db.start_job(job["id"])
        self.db.update_daemon_state(
            state="running",
            current_job_id=job["id"],
            paused_reason=None,
            paused_since=None,
            last_poll_at=now,
        )

        start_time = time.time()
        proc = subprocess.Popen(
            job["command"],
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # 9. Wait with timeout
        try:
            proc.wait(timeout=job["timeout"])
        except subprocess.TimeoutExpired:
            # 10a. Timeout -> kill, then drain pipes with a safety timeout
            proc.kill()
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out, err = b"", b""
            stdout_tail = out[-500:].decode("utf-8", errors="replace")
            stderr_tail = err[-500:].decode("utf-8", errors="replace")
            self.db.kill_job(
                job["id"],
                reason=f"timeout after {job['timeout']}s",
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
            failed_count = (state.get("jobs_failed_today") or 0) + 1
            self.db.update_daemon_state(
                state="idle", current_job_id=None, last_poll_at=time.time(),
                jobs_failed_today=failed_count,
            )
            return

        # 10b. Record result
        duration = time.time() - start_time
        exit_code = proc.returncode
        stdout_tail = proc.stdout.read()[-500:].decode("utf-8", errors="replace")
        stderr_tail = proc.stderr.read()[-500:].decode("utf-8", errors="replace")

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

        # Track model so interactive-yield check doesn't self-block
        if job.get("model"):
            self._recent_job_models[job["model"]] = time.time()

        # 11. Record duration if successful
        if exit_code == 0:
            self.db.record_duration(
                source=job["source"],
                model=job["model"],
                duration=duration,
                exit_code=exit_code,
            )

        # 12. Update daemon state back to idle + increment daily counters
        counter_field = "jobs_completed_today" if exit_code == 0 else "jobs_failed_today"
        current_count = (state.get(counter_field) or 0) + 1
        self.db.update_daemon_state(
            state="idle", current_job_id=None, last_poll_at=time.time(),
            **{counter_field: current_count},
        )

    def run(self, poll_interval: int | None = None) -> None:
        """Main loop: poll_once() every N seconds. Prunes old data daily."""
        if poll_interval is None:
            poll_interval = self.db.get_setting("poll_interval_seconds") or 5

        self.db.update_daemon_state(state="idle", uptime_since=time.time())

        while True:
            try:
                self.poll_once()
            except Exception:
                _log.exception("Unexpected error in poll_once(); attempting state recovery")
                try:
                    self.db.update_daemon_state(
                        state="idle", current_job_id=None, last_poll_at=time.time()
                    )
                except Exception:
                    _log.exception("State recovery also failed; daemon loop continues")

            # Prune once per day + reset daily counters
            now = time.time()
            if now - self._last_prune > 86400:
                try:
                    self.db.prune_old_data()
                    self.db.update_daemon_state(jobs_completed_today=0, jobs_failed_today=0)
                except Exception:
                    _log.exception("Daily prune failed; will retry next cycle")
                self._last_prune = now

            time.sleep(poll_interval)
