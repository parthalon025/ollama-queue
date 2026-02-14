"""Daemon polling loop and job runner for ollama-queue."""

from __future__ import annotations

import subprocess
import time

from ollama_queue.db import Database
from ollama_queue.estimator import DurationEstimator
from ollama_queue.health import HealthMonitor


class Daemon:
    """Main daemon that polls for jobs and executes them."""

    def __init__(self, db: Database, health_monitor: HealthMonitor | None = None):
        self.db = db
        self.health = health_monitor or HealthMonitor()
        self.estimator = DurationEstimator(db)
        self._last_prune: float = 0.0

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
        evaluation = self.health.evaluate(
            snap, settings, currently_paused=currently_paused, queued_model=job["model"]
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
            # 10a. Timeout -> kill
            proc.kill()
            stdout_tail = proc.stdout.read()[-500:].decode("utf-8", errors="replace")
            stderr_tail = proc.stderr.read()[-500:].decode("utf-8", errors="replace")
            self.db.kill_job(job["id"], reason=f"timeout after {job['timeout']}s")
            self.db.update_daemon_state(
                state="idle", current_job_id=None, last_poll_at=time.time()
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

        # 11. Record duration if successful
        if exit_code == 0:
            self.db.record_duration(
                source=job["source"],
                model=job["model"],
                duration=duration,
                exit_code=exit_code,
            )

        # 12. Update daemon state back to idle
        self.db.update_daemon_state(
            state="idle", current_job_id=None, last_poll_at=time.time()
        )

    def run(self, poll_interval: int | None = None) -> None:
        """Main loop: poll_once() every N seconds. Prunes old data daily."""
        if poll_interval is None:
            poll_interval = self.db.get_setting("poll_interval_seconds") or 5

        self.db.update_daemon_state(state="idle", uptime_since=time.time())

        while True:
            self.poll_once()

            # Prune once per day
            now = time.time()
            if now - self._last_prune > 86400:
                self.db.prune_old_data()
                self._last_prune = now

            time.sleep(poll_interval)
