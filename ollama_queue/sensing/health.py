"""Health monitoring for system resources and Ollama state.

Plain English: The queue's doctor. Before starting any job, the daemon asks this
module: "Is the computer too stressed right now?" It reads RAM, swap, CPU load,
and GPU memory, then compares them to configurable thresholds.

Decision it drives: Should the queue pause (system overloaded), resume (system
recovered), or yield (a human is actively using Ollama right now)?
"""

from __future__ import annotations

import logging
import subprocess
import time

_log = logging.getLogger(__name__)


class HealthMonitor:
    """Reads system metrics and evaluates pause/resume/yield decisions."""

    def __init__(self) -> None:
        self._vram_cache: tuple[float, float | None] | None = None  # (timestamp, value)
        self._VRAM_TTL = 5.0  # seconds

    def get_ram_pct(self) -> float:
        """Parse /proc/meminfo for RAM usage percentage."""
        info = self._parse_meminfo()
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        if total == 0:
            return 0.0
        used = total - available
        return round(used / total * 100, 1)

    def get_swap_pct(self) -> float:
        """Parse /proc/meminfo for swap usage percentage."""
        info = self._parse_meminfo()
        total = info.get("SwapTotal", 0)
        free = info.get("SwapFree", 0)
        if total == 0:
            return 0.0
        used = total - free
        return round(used / total * 100, 1)

    def get_load_avg(self) -> float:
        """Read /proc/loadavg, return 1-minute average."""
        try:
            with open("/proc/loadavg") as f:
                return float(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            return 0.0

    def get_cpu_count(self) -> int:
        """Count logical CPUs from /proc/cpuinfo (one 'processor' entry per logical CPU)."""
        try:
            with open("/proc/cpuinfo") as f:
                count = sum(1 for line in f if line.startswith("processor"))
            return count or 1
        except OSError:
            return 1

    def get_vram_pct(self) -> float | None:
        """Query nvidia-smi for VRAM usage percentage, with TTL cache.

        Returns cached value if within TTL window. Returns None if
        nvidia-smi is not available or fails.
        """
        now = time.monotonic()
        if self._vram_cache is not None:
            ts, val = self._vram_cache
            if now - ts < self._VRAM_TTL:
                return val
        result = self._fetch_vram_pct()
        self._vram_cache = (now, result)
        return result

    _VRAM_TOTAL_FALLBACK_GB = 24.0

    def get_gpu_name(self) -> str | None:
        """Return the GPU model name from nvidia-smi, or None if unavailable."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            name = result.stdout.strip().split("\n")[0].strip()
            return name if name else None
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, UnicodeDecodeError):
            return None

    def get_vram_total_gb(self) -> float:
        """Return total GPU VRAM in GB from nvidia-smi, or 24.0 as fallback.

        Used by DeferralScheduler and DLQScheduler for slot VRAM headroom calculations.
        Falls back to 24.0 GB (original hardcoded value) when nvidia-smi is unavailable.
        """
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return self._VRAM_TOTAL_FALLBACK_GB
            total_mib = float(result.stdout.strip().split("\n")[0].strip())
            return round(total_mib / 1024, 1)
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, UnicodeDecodeError):
            return self._VRAM_TOTAL_FALLBACK_GB

    def _fetch_vram_pct(self) -> float | None:
        """Query nvidia-smi for VRAM usage percentage (uncached)."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            # First GPU line: "used, total"
            line = result.stdout.strip().split("\n")[0]
            parts = line.split(",")
            used = float(parts[0].strip())
            total = float(parts[1].strip())
            if total == 0:
                return 0.0
            return round(used / total * 100, 1)
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, UnicodeDecodeError):
            return None

    def get_ollama_active_model(self) -> str | None:
        """Run 'ollama ps' and parse for the loaded model name.

        Returns None if no model is loaded or ollama is not running.
        """
        try:
            result = subprocess.run(
                ["ollama", "ps"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            lines = result.stdout.strip().split("\n")
            # First line is header (NAME SIZE ...), data starts at line 1
            if len(lines) < 2:
                return None
            # Model name is the first whitespace-delimited field
            model = lines[1].split()[0]
            return model if model else None
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, UnicodeDecodeError):
            return None

    def get_loaded_models(self) -> list[dict]:
        """Return all currently loaded Ollama models. Multi-model aware."""
        from ollama_queue.models.client import OllamaModels

        return OllamaModels().get_loaded()

    def check(self) -> dict:
        """Return a full health snapshot."""
        loaded = self.get_loaded_models()
        return {
            "ram_pct": self.get_ram_pct(),
            "swap_pct": self.get_swap_pct(),
            "load_avg": self.get_load_avg(),
            "cpu_count": self.get_cpu_count(),
            "vram_pct": self.get_vram_pct(),
            "ollama_model": loaded[0]["name"] if loaded else self.get_ollama_active_model(),
            "ollama_loaded_models": loaded,
        }

    def evaluate(
        self,
        snap: dict,
        settings: dict,
        currently_paused: bool,
        queued_model: str | None = None,
        recent_job_models: set[str] | None = None,
        paused_since: float | None = None,
    ) -> dict:
        """Evaluate a health snapshot against threshold settings.

        Plain English: The go/no-go call. Given current RAM/CPU/VRAM readings,
        should the daemon start the next job, keep waiting, or step aside?
        Uses hysteresis (different pause vs resume thresholds) to prevent the
        queue from rapidly cycling on/off when a metric hovers near the limit.
        Also checks whether a human is interactively using Ollama right now —
        if so, yields politely rather than competing for the GPU.

        Returns:
            {
                "should_pause": bool,
                "should_yield": bool,
                "reason": str,
            }

        Hysteresis logic:
        - If NOT paused: pause when any metric exceeds its pause threshold.
        - If paused: only resume when ALL metrics are below their resume thresholds.

        Max-pause escape hatch:
        - If paused longer than max_pause_duration_seconds, force resume
          regardless of current metrics. Prevents indefinite stalls when
          metrics hover in the hysteresis band.

        Yield logic:
        - If an ollama model is loaded, yield_to_interactive is on,
          and the loaded model differs from queued_model and is not a
          recently-completed queue job model, yield.
        """
        # --- Max-pause escape hatch ---
        max_pause = settings.get("max_pause_duration_seconds")
        if currently_paused and max_pause and paused_since:
            pause_duration = time.time() - paused_since
            if pause_duration >= float(max_pause):
                return {
                    "should_pause": False,
                    "should_yield": False,
                    "reason": f"Force resume: paused {pause_duration:.0f}s >= max {max_pause}s",
                }

        reasons: list[str] = []

        # Guard against None values in settings (e.g. fresh install before defaults are seeded,
        # or a DB migration that added a new key without backfilling existing rows).
        # Use explicit None checks — NOT `x or default`, which treats 0 as falsy.
        ram_pause = settings["ram_pause_pct"] if settings["ram_pause_pct"] is not None else 85
        ram_resume = settings["ram_resume_pct"] if settings["ram_resume_pct"] is not None else 75
        swap_pause = settings["swap_pause_pct"] if settings["swap_pause_pct"] is not None else 50
        swap_resume = settings["swap_resume_pct"] if settings["swap_resume_pct"] is not None else 40
        load_pause_mult = settings["load_pause_multiplier"] if settings["load_pause_multiplier"] is not None else 2.0
        load_resume_mult = settings["load_resume_multiplier"] if settings["load_resume_multiplier"] is not None else 1.5

        # --- Pause / resume with hysteresis ---
        if not currently_paused:
            # Check if any metric exceeds its pause threshold
            if snap["ram_pct"] >= ram_pause:
                reasons.append(f"RAM {snap['ram_pct']}% >= {ram_pause}%")
            if snap["swap_pct"] >= swap_pause:
                reasons.append(f"Swap {snap['swap_pct']}% >= {swap_pause}%")
            load_pause = load_pause_mult * snap["cpu_count"]
            if snap["load_avg"] >= load_pause:
                reasons.append(f"Load {snap['load_avg']} >= {load_pause} ({load_pause_mult}x {snap['cpu_count']} CPUs)")
            should_pause = len(reasons) > 0
        else:
            # Currently paused: only resume if ALL metrics below resume thresholds
            still_high: list[str] = []
            if snap["ram_pct"] > ram_resume:
                still_high.append(f"RAM {snap['ram_pct']}% > {ram_resume}%")
            if snap["swap_pct"] > swap_resume:
                still_high.append(f"Swap {snap['swap_pct']}% > {swap_resume}%")
            load_resume = load_resume_mult * snap["cpu_count"]
            if snap["load_avg"] > load_resume:
                still_high.append(
                    f"Load {snap['load_avg']} > {load_resume} ({load_resume_mult}x {snap['cpu_count']} CPUs)"
                )
            should_pause = len(still_high) > 0
            reasons = still_high if still_high else ["all metrics below resume thresholds"]

        # --- Yield to interactive ollama user ---
        should_yield = False
        _recent = recent_job_models or set()
        if (
            settings.get("yield_to_interactive")
            and snap.get("ollama_model")
            and snap["ollama_model"] != queued_model
            and snap["ollama_model"] not in _recent
        ):
            should_yield = True
            reasons.append(f"ollama ps shows {snap['ollama_model']} loaded (interactive user); yielding")

        return {
            "should_pause": should_pause,
            "should_yield": should_yield,
            "reason": "; ".join(reasons),
        }

    # -- internal helpers --

    @staticmethod
    def _parse_meminfo() -> dict[str, int]:
        """Parse /proc/meminfo into a dict of field -> kB values."""
        info: dict[str, int] = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(":")
                        try:
                            info[key] = int(parts[1])
                        except ValueError:
                            pass
        except OSError:
            _log.warning("Failed to read /proc/meminfo; health metrics will report 0")
        return info
