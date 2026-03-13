"""Job execution mixin for the Daemon class.

Contains _run_job, _check_stalled_jobs, _check_retryable_jobs,
_check_preemption, _preempt_job, _run_check_command, resource helpers,
and the _drain_pipes_with_tracking standalone function.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal as _signal
import subprocess
import subprocess as _subprocess  # real module — not replaced by test mocks targeting 'subprocess'
import time
from subprocess import TimeoutExpired as _TimeoutExpired

from ollama_queue.metrics_parser import parse_ollama_metrics
from ollama_queue.sensing.health import HealthMonitor

_log = logging.getLogger(__name__)


_MAX_STDOUT_BYTES = 128 * 1024  # 128KB — enough for metrics JSON + tail


def _drain_pipes_with_tracking(
    proc: subprocess.Popen,
    job_id: int,
    stall_detector,
) -> tuple[bytes, bytes]:
    """Drain stdout+stderr via select() loop, tracking stdout activity.

    Uses non-blocking fds + select() to avoid: (1) deadlock on large output,
    (2) blocking the worker thread from observing process exit.

    Stdout is capped at _MAX_STDOUT_BYTES to prevent OOM under MemoryMax=512M.
    Only the tail is kept — sufficient for metrics parsing and stdout_tail storage.
    """
    import fcntl
    import select as _select

    stdout_fd = proc.stdout.fileno()  # type: ignore[union-attr]
    stderr_fd = proc.stderr.fileno()  # type: ignore[union-attr]

    for fd in (stdout_fd, stderr_fd):
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    stdout_chunks: list[bytes] = []
    stdout_total: int = 0
    stderr_chunks: list[bytes] = []
    open_fds: set[int] = {stdout_fd, stderr_fd}

    def _append_stdout(data: bytes) -> None:
        nonlocal stdout_total
        stdout_chunks.append(data)
        stdout_total += len(data)
        stall_detector.update_stdout_activity(job_id, time.time())
        # Trim old chunks when over budget — keep tail
        while stdout_total > _MAX_STDOUT_BYTES and len(stdout_chunks) > 1:
            removed = stdout_chunks.pop(0)
            stdout_total -= len(removed)

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
                            if fd == stdout_fd:
                                _append_stdout(chunk)
                            else:
                                stderr_chunks.append(chunk)
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
                _append_stdout(chunk)
            else:
                stderr_chunks.append(chunk)

    return b"".join(stdout_chunks), b"".join(stderr_chunks)


class ExecutorMixin:
    """Job execution and resource-checking methods for Daemon."""

    # --- Concurrency helpers ---

    def _max_slots(self, settings: dict | None = None) -> int:
        if settings is not None:
            return max(1, int(settings.get("max_concurrent_jobs") or 1))
        return max(1, int(self.db.get_setting("max_concurrent_jobs") or 1))

    def _shadow_hours(self, settings: dict | None = None) -> float:
        if settings is not None:
            return float(settings.get("concurrent_shadow_hours") or 24)
        return float(self.db.get_setting("concurrent_shadow_hours") or 24)

    def _in_shadow_mode(self, settings: dict | None = None) -> bool:
        if self._max_slots(settings) <= 1:
            return False
        if self._concurrent_enabled_at is None:
            self._concurrent_enabled_at = time.time()
            return True
        elapsed_hours = (time.time() - self._concurrent_enabled_at) / 3600
        return elapsed_hours < self._shadow_hours(settings)

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
        except (OSError, _subprocess.TimeoutExpired, ValueError):
            _log.debug("nvidia-smi unavailable for VRAM check")
            return None
        except Exception:
            _log.warning("Unexpected error reading VRAM", exc_info=True)
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

    def _can_admit(self, job: dict, settings: dict | None = None) -> bool:
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

        # heavy: serialize — never concurrent with other heavy/ollama jobs.
        # Embed jobs consume negligible VRAM and must not block a heavy job.
        if profile == "heavy":
            with self._running_lock:
                non_embed = sum(
                    1
                    for jid in self._running
                    if self._ollama_models.classify(self._running_models.get(jid, ""))["resource_profile"] != "embed"
                )
                return non_embed == 0

        # Same model already running → serialize
        model = job.get("model") or ""
        with self._running_lock:
            if model and model in self._running_models.values():
                _log.debug("_can_admit: job #%d blocked — model %s already running", job["id"], model)
                return False
            running_count = len(self._running)
            # Compute committed VRAM inside the lock so the snapshot is consistent
            # with the running_count snapshot above. (#5)
            committed_vram = self._committed_vram_mb()

        # Pull in progress → block
        if self._model_pull_in_progress(model):
            _log.debug("_can_admit: job #%d blocked — model pull in progress for %s", job["id"], model)
            return False

        # Already at capacity → block
        if running_count >= self._max_slots(settings):
            _log.debug(
                "_can_admit: job #%d blocked — at capacity (%d/%d slots)",
                job["id"],
                running_count,
                self._max_slots(settings),
            )
            return False

        # Resource gate: estimate whether the new model fits alongside already-committed
        # models.  We derive "available" VRAM from committed model estimates rather than
        # a live nvidia-smi read, so two concurrent admission checks both see the same
        # deterministic value and cannot both pass on a tight GPU. (#5)
        #
        # max_vram_mb: optional DB setting; if absent, skip the absolute VRAM check
        # (health.evaluate's vram_pct gate still applies below as a safety net).
        model_vram = self._ollama_models.estimate_vram_mb(model, self.db) if model else 0.0
        max_vram_raw = settings.get("max_vram_mb") if settings is not None else self.db.get_setting("max_vram_mb")
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
        _settings = settings if settings is not None else self.db.get_all_settings()
        health_eval = self.health.evaluate(snap, _settings, currently_paused=False)

        if not resource_ok or health_eval["should_pause"]:
            _log.debug(
                "_can_admit: job #%d blocked — resource_ok=%s health_pause=%s reason=%s",
                job["id"],
                resource_ok,
                health_eval["should_pause"],
                health_eval.get("reason", ""),
            )
            # Proactive deferral: if resources won't fit and deferral is enabled,
            # defer the job so the scheduler can place it in a better time slot
            defer_enabled = settings.get("defer.enabled") if settings else self.db.get_setting("defer.enabled")
            if defer_enabled is not False and not resource_ok:
                try:
                    context = f"committed_vram={committed_vram:.0f}MB, model_vram={model_vram:.0f}MB"
                    self.db.defer_job(job["id"], reason="resource", context=context)
                    _log.info("Deferred job #%d: resource contention (%s)", job["id"], context)
                except Exception:
                    _log.exception("Failed to defer job #%d", job["id"])
            return False

        # Shadow mode — log but don't admit second job yet
        if self._in_shadow_mode(settings) and running_count > 0:
            _log.info(
                "SHADOW: would admit concurrent job #%d (%s) — shadow mode active",
                job["id"],
                job.get("source", ""),
            )
            return False

        return True

    def _run_job(self, job: dict) -> None:
        """Execute a job in a worker thread. Records result in DB."""
        start_time = time.time()
        _log.info(
            "Dispatching job #%d source=%s model=%s priority=%d",
            job["id"],
            job.get("source", ""),
            job.get("model") or "",
            job.get("priority", 5),
        )

        # Pre-flight check_command: gate job on external signal
        if job.get("recurring_job_id"):
            _rj = self.db.get_recurring_job(job["recurring_job_id"])
            if _rj and _rj.get("check_command"):
                _check_result = self._run_check_command(job, _rj)
                if _check_result in ("skip", "disable"):
                    return

        # Sample VRAM before job for observed-VRAM recording
        vram_before = self._free_vram_mb()

        out = b""
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

            # If the job was preempted, _preempt_job has already set status='pending'
            # and incremented preemption_count.  Skip complete_job to avoid overwriting
            # that state.  The slot will be released when the finally block runs.
            with self._running_lock:
                was_preempted = job["id"] in self._preempted

            if was_preempted:
                _log.info(
                    "Job #%d preempted and process exited (exit_code=%d) — skipping complete_job",
                    job["id"],
                    exit_code,
                )
                return

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

            if exit_code == 0:
                _log.info(
                    "Job #%d completed: exit_code=0 duration=%.1fs source=%s",
                    job["id"],
                    duration,
                    job.get("source", ""),
                )
            else:
                _log.warning(
                    "Job #%d failed: exit_code=%d duration=%.1fs source=%s",
                    job["id"],
                    exit_code,
                    duration,
                    job.get("source", ""),
                )

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

            # Always capture Ollama metrics — job may have produced output before failing
            full_stdout = out.decode("utf-8", errors="replace")
            metrics = parse_ollama_metrics(full_stdout)
            if metrics:
                metrics["model"] = job.get("model", "")
                metrics["command"] = job.get("command", "")
                metrics["resource_profile"] = job.get("resource_profile", "ollama")
                try:
                    self.db.store_job_metrics(job["id"], metrics)
                except Exception:
                    _log.exception("Failed to store metrics for job #%d", job["id"])

            # Record duration if successful — skip command-only jobs (model=None)
            # to avoid inserting corrupt null-model rows into duration_history (H6)
            if exit_code == 0 and job.get("model"):
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

            # Trigger DLQ auto-reschedule sweep (event-driven)
            try:
                self._dlq_scheduler.on_job_completed(job["id"])
            except Exception:
                _log.exception("DLQ auto-reschedule sweep failed after job #%d", job["id"])

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
            # Attempt to capture any partial metrics from output before crash
            if out:
                try:
                    full_stdout = out.decode("utf-8", errors="replace")
                    metrics = parse_ollama_metrics(full_stdout)
                    if metrics:
                        metrics["model"] = job.get("model", "")
                        metrics["command"] = job.get("command", "")
                        metrics["resource_profile"] = job.get("resource_profile", "ollama")
                        self.db.store_job_metrics(job["id"], metrics)
                except Exception:
                    _log.debug("Failed to capture partial metrics for job #%d", job["id"])
        finally:
            self.stall_detector.forget(job["id"])
            with self._running_lock:
                self._running.pop(job["id"], None)
                self._running_models.pop(job["id"], None)
                self._preempted.discard(job["id"])

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

        # Do NOT pop from _running here.  The worker thread is still alive waiting
        # for the process to exit.  Removing the entry early frees the concurrency
        # slot before the process has terminated, allowing the daemon to re-dequeue
        # this same job on the next poll cycle (double-execution).
        #
        # Instead, mark the job in _preempted so _run_job's finally block can skip
        # the complete_job call (the DB status is already 'pending') while still
        # holding the slot until the process actually exits.
        with self._running_lock:
            self._preempted.add(job_id)

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
                "check_command returned unknown exit code %d for recurring job id=%d — proceeding (fail-open)",
                code,
                rj_id,
            )
            return "proceed"
