"""Bayesian multi-signal stall detector for LLM jobs.

Plain English: The "is this job frozen?" detector. LLM jobs can't use a simple
timeout because some prompts legitimately take 30+ minutes. Instead, this module
watches four signals — process state (D=disk-wait, Z=zombie), CPU activity,
whether stdout has gone quiet, and whether Ollama still has the model loaded —
and combines them into a single probability score P(stuck) using Bayes' theorem.

Decision it drives: Is this running job actually making progress, or is it
frozen and should be killed?
"""

from __future__ import annotations

import json as _json
import logging
import math
import os
import threading
import urllib.request

_log = logging.getLogger(__name__)

PRIOR_LOG_ODDS: float = math.log(0.05 / 0.95)  # P(stuck)=0.05 → -2.944
_TICK_HZ: int = os.sysconf("SC_CLK_TCK")  # compile-time constant, always 100 on Linux


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
        # Protects _last_stdout: written by worker threads, read by poll thread
        self._stdout_lock = threading.Lock()

    # ── stdout activity tracking ──────────────────────────────────────────────

    def update_stdout_activity(self, job_id: int, now: float) -> None:
        with self._stdout_lock:
            self._last_stdout[job_id] = now

    def get_stdout_silence(self, job_id: int, now: float) -> float | None:
        """Seconds since last stdout output. None if never produced output."""
        with self._stdout_lock:
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
        """Return utime+stime ticks from /proc/{pid}/stat, or None on error.

        Parses past field 2 (comm) which is parenthesised and may contain spaces,
        then indexes utime/stime relative to the post-comm fields.
        """
        try:
            with open(f"/proc/{pid}/stat") as fh:
                raw = fh.read()
            # Comm field may contain spaces; find the last ')' to get a stable anchor.
            end = raw.rindex(")")
            fields = raw[end + 2 :].split()  # state, ppid, ... utime=fields[11], stime=fields[12]
            return int(fields[11]) + int(fields[12])
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
        return ((ticks - prev_ticks) / _TICK_HZ / elapsed) * 100.0

    # ── Ollama /api/ps ────────────────────────────────────────────────────────

    def get_ollama_ps_models(self) -> set[str]:
        """Return set of base model names currently loaded in Ollama.
        Returns empty set if Ollama is unreachable (treated as unknown)."""
        try:
            with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=2) as resp:
                data = _json.loads(resp.read())
            return {m.get("name", "").split(":")[0] for m in data.get("models", [])}
        except Exception as exc:
            _log.debug("get_ollama_ps_models failed: %s", exc)
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
        return 0.0  # 1-5%: neutral

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
        """Compute P(stuck) and return (posterior, signal_breakdown_dict).

        Plain English: Combines four independent clues into one probability.
        Each clue adds or subtracts log-odds weight: process in D-state adds
        weight (bad sign), active CPU subtracts weight (good sign), 5+ minutes
        of stdout silence adds weight, model absent from Ollama /api/ps adds
        weight. The result is a 0-1 probability: >= 0.8 means "probably stuck."
        The breakdown dict shows exactly which signals drove that conclusion.
        """
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
        with self._stdout_lock:
            self._last_stdout.pop(job_id, None)
        self._cpu_prev.pop(job_id, None)
