"""Bayesian multi-signal stall detector for LLM jobs."""

from __future__ import annotations

import json as _json
import logging
import math
import os
import urllib.request

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
            with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=2) as resp:
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
