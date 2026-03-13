"""Orchestration loop mixin for the Daemon class.

Contains poll_once, run, shutdown, orphan recovery, dequeue,
entropy/circuit-breaker, and scheduling helpers.
"""

from __future__ import annotations

import logging
import os
import signal as _signal
import statistics
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from math import log, log2

_log = logging.getLogger(__name__)


class LoopMixin:
    """Polling loop, scheduling, and circuit-breaker methods for Daemon."""

    # Models loaded by queue jobs stay resident in Ollama after completion.
    # Track them so the interactive-yield check doesn't self-block.
    RECENT_MODEL_WINDOW = 600  # 10 minutes

    def _get_load_map(self) -> list[dict]:
        """Load map accessor for DLQ/deferral schedulers."""
        if hasattr(self.scheduler, "load_map_extended"):
            return self.scheduler.load_map_extended()
        return []

    # --- Entropy ---

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

    def _check_entropy(self, pending_jobs: list[dict], now: float, settings: dict | None = None) -> None:
        """Compute entropy, update rolling baseline, log anomalies, set suspension."""
        entropy = self._compute_queue_entropy(pending_jobs, now)
        self._entropy_history.append(entropy)

        # Need at least 10 samples for a meaningful baseline
        if len(self._entropy_history) < 10:
            return

        sigma = float(
            (
                settings.get("entropy_alert_sigma")
                if settings is not None
                else self.db.get_setting("entropy_alert_sigma")
            )
            or 2.0
        )
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

            suspend_enabled = (
                settings.get("entropy_suspend_low_priority")
                if settings is not None
                else self.db.get_setting("entropy_suspend_low_priority")
            )
            if alert_type == "critical_backlog" and suspend_enabled:
                self._entropy_suspend_until = now + 60.0  # suspend p8-10 for 60s
                _log.info("Suspended low-priority (p8-10) promotion for 60s due to critical_backlog")

    # --- Orphan recovery ---

    def _recover_orphans(self) -> None:
        """Reset jobs stuck in 'running' on daemon startup (no live subprocess).

        Also marks eval_runs stuck in 'generating' or 'judging' as failed — their
        background threads died with the previous process.
        """
        from datetime import UTC
        from datetime import datetime as _dt

        with self.db._lock:
            conn = self.db._connect()
            stuck = conn.execute(
                "SELECT id FROM eval_runs WHERE status IN ('generating', 'judging', 'pending')"
            ).fetchall()
            now = _dt.now(UTC).isoformat()
            for row in stuck:
                result_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM eval_results WHERE run_id = ?", (row["id"],)
                ).fetchone()["cnt"]
                error_msg = "daemon restart: session abandoned"
                if result_count > 0:
                    error_msg += f" ({result_count} partial results remain)"
                conn.execute(
                    "UPDATE eval_runs SET status='failed', error=?," " completed_at=? WHERE id=?",
                    (error_msg, now, row["id"]),
                )
                _log.warning("Abandoned eval run #%d on daemon restart", row["id"])
            if stuck:
                conn.commit()

        orphans = self.db.get_running_jobs()
        for job in orphans:
            if job.get("command", "").startswith("proxy:"):
                # Proxy jobs are tracked in the jobs table as sentinels but must never be
                # re-queued — they have no subprocess and can't be shell-executed.
                # The HTTP request that owned this slot already timed out; mark it failed.
                self.db.complete_job(
                    job_id=job["id"],
                    exit_code=-1,
                    stdout_tail="",
                    stderr_tail="daemon restart: proxy request abandoned",
                    outcome_reason="daemon restart",
                )
                _log.warning("Abandoned proxy sentinel job #%d on daemon restart", job["id"])
                continue
            if job.get("pid") and job["pid"] > 0:
                try:
                    os.kill(job["pid"], _signal.SIGTERM)
                    _log.info("Sent SIGTERM to orphaned pid=%d (job #%d)", job["pid"], job["id"])
                except ProcessLookupError:
                    pass  # process already gone
            else:
                _log.warning(
                    "Orphan job #%d has no PID — process may still be running. "
                    "Resetting to pending; check for duplicate execution.",
                    job["id"],
                )
            self.db.reset_job_to_pending(job["id"])
            _log.warning("Reset orphaned job #%d to pending", job["id"])

        # Clear orphaned proxy sentinel. If daemon crashed while a proxy held
        # the sentinel (-1), it persists and blocks all future proxy requests.
        with self.db._lock:
            conn = self.db._connect()
            conn.execute("UPDATE daemon_state SET current_job_id = NULL " "WHERE id = 1 AND current_job_id = -1")
            conn.commit()

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

    # --- Dequeue ---

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

    # --- Main loop ---

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

        # Fetch all settings once per poll cycle — reused by _check_entropy, health.evaluate,
        # and _can_admit to avoid redundant per-method DB round-trips.
        settings = self.db.get_all_settings()

        # 3c. Entropy anomaly detection
        try:
            self._check_entropy(pending_jobs, now, settings)
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

        # 3e. Periodic DLQ/deferral sweep (fallback for event-driven sweep)
        sweep_interval = float(settings.get("dlq.sweep_fallback_minutes") or 30) * 60
        if now - self._last_dlq_sweep >= sweep_interval:
            try:
                self._dlq_scheduler.periodic_sweep()
                self._deferral_scheduler.sweep()
            except Exception:
                _log.exception("Periodic DLQ/deferral sweep failed; continuing")
            self._last_dlq_sweep = now

        # 4. Get next pending job — SJF + aging sort
        estimates = self.db.estimate_duration_bulk([j["source"] for j in pending_jobs])
        job = self._dequeue_next_job(pending_jobs, estimates, now)

        # 5. If no job -> set idle, return
        # Guard: don't clobber an in-flight proxy claim (current_job_id=-1 sentinel).
        # The proxy's release_proxy_claim() owns that transition back to NULL.
        if job is None:
            if state.get("current_job_id") == -1:
                self.db.update_daemon_state(state="idle", last_poll_at=now)
            else:
                self.db.update_daemon_state(state="idle", last_poll_at=now, current_job_id=None)
            return

        # 6. Evaluate health -> if pause needed, update state, return
        # (settings already fetched above at 3b — reusing the same dict)
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
            paused_since=state.get("paused_since"),
        )

        if evaluation["should_pause"]:
            if not currently_paused:
                _log.warning("Queue pausing (health): %s", evaluation["reason"])
            state_update = dict(
                state="paused_health",
                paused_reason=evaluation["reason"],
                paused_since=now if not currently_paused else state.get("paused_since"),
                last_poll_at=now,
            )
            if state.get("current_job_id") != -1:
                state_update["current_job_id"] = None
            self.db.update_daemon_state(**state_update)
            return

        # 7. Evaluate yield -> if interactive user, update state, return
        if evaluation["should_yield"]:
            if not currently_paused:
                _log.info("Queue yielding to interactive Ollama user: %s", evaluation["reason"])
            state_update = dict(
                state="paused_interactive",
                paused_reason=evaluation["reason"],
                paused_since=now if not currently_paused else state.get("paused_since"),
                last_poll_at=now,
            )
            if state.get("current_job_id") != -1:
                state_update["current_job_id"] = None
            self.db.update_daemon_state(**state_update)
            return

        # Log recovery from health/interactive pause
        if currently_paused:
            _log.info("Queue resuming from %s", current_state)

        # 8. Admit and dispatch
        # Guard: don't clobber proxy sentinel (same reason as step 5).
        # Re-read current_job_id from DB — the snapshot from step 0 may be stale
        # if a proxy was claimed between then and now (#82).
        if not self._can_admit(job, settings):
            fresh_state = self.db.get_daemon_state()
            if fresh_state.get("current_job_id") == -1:
                self.db.update_daemon_state(state="idle", last_poll_at=now)
            else:
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

    def shutdown(self) -> None:
        """Shut down the thread pool executor, releasing worker threads.

        wait=True ensures all in-flight worker threads finish before the daemon
        exits.  wait=False would return immediately, leaving background threads
        that may write to a closed DB connection or update state after shutdown.
        """
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def run(self, poll_interval: int | None = None) -> None:
        """Main loop: poll_once() every N seconds. Prunes old data daily."""
        if poll_interval is None:
            _pi = self.db.get_setting("poll_interval_seconds")
            poll_interval = int(_pi) if _pi is not None else 5

        self._recover_orphans()
        self.db.update_daemon_state(state="idle", uptime_since=time.time())

        try:
            while True:
                try:
                    self.poll_once()
                except Exception:
                    _log.exception("Unexpected error in poll_once(); attempting state recovery")
                    try:
                        # Don't clear current_job_id here — a proxy may be holding the sentinel.
                        self.db.update_daemon_state(state="idle", last_poll_at=time.time())
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
