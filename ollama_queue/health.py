"""Health monitoring for system resources and Ollama state."""

from __future__ import annotations

import os
import subprocess


class HealthMonitor:
    """Reads system metrics and evaluates pause/resume/yield decisions."""

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
        """Return number of CPUs."""
        return os.cpu_count() or 1

    def get_vram_pct(self) -> float | None:
        """Query nvidia-smi for VRAM usage percentage.

        Returns None if nvidia-smi is not available or fails.
        """
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
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
                capture_output=True, text=True, timeout=5,
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

    def check(self) -> dict:
        """Return a full health snapshot."""
        return {
            "ram_pct": self.get_ram_pct(),
            "swap_pct": self.get_swap_pct(),
            "load_avg": self.get_load_avg(),
            "cpu_count": self.get_cpu_count(),
            "vram_pct": self.get_vram_pct(),
            "ollama_model": self.get_ollama_active_model(),
        }

    def evaluate(
        self,
        snap: dict,
        settings: dict,
        currently_paused: bool,
        queued_model: str | None = None,
        recent_job_models: set[str] | None = None,
    ) -> dict:
        """Evaluate a health snapshot against threshold settings.

        Returns:
            {
                "should_pause": bool,
                "should_yield": bool,
                "reason": str,
            }

        Hysteresis logic:
        - If NOT paused: pause when any metric exceeds its pause threshold.
        - If paused: only resume when ALL metrics are below their resume thresholds.

        Yield logic:
        - If an ollama model is loaded, yield_to_interactive is on,
          and the loaded model differs from queued_model and is not a
          recently-completed queue job model, yield.
        """
        reasons: list[str] = []

        # --- Pause / resume with hysteresis ---
        if not currently_paused:
            # Check if any metric exceeds its pause threshold
            if snap["ram_pct"] >= settings["ram_pause_pct"]:
                reasons.append(f"RAM {snap['ram_pct']}% >= {settings['ram_pause_pct']}%")
            if snap["swap_pct"] >= settings["swap_pause_pct"]:
                reasons.append(f"Swap {snap['swap_pct']}% >= {settings['swap_pause_pct']}%")
            load_pause = settings["load_pause_multiplier"] * snap["cpu_count"]
            if snap["load_avg"] >= load_pause:
                reasons.append(
                    f"Load {snap['load_avg']} >= {load_pause} "
                    f"({settings['load_pause_multiplier']}x {snap['cpu_count']} CPUs)"
                )
            if snap["vram_pct"] is not None and snap["vram_pct"] >= settings["vram_pause_pct"]:
                reasons.append(f"VRAM {snap['vram_pct']}% >= {settings['vram_pause_pct']}%")

            should_pause = len(reasons) > 0
        else:
            # Currently paused: only resume if ALL metrics below resume thresholds
            still_high: list[str] = []
            if snap["ram_pct"] > settings["ram_resume_pct"]:
                still_high.append(f"RAM {snap['ram_pct']}% > {settings['ram_resume_pct']}%")
            if snap["swap_pct"] > settings["swap_resume_pct"]:
                still_high.append(f"Swap {snap['swap_pct']}% > {settings['swap_resume_pct']}%")
            load_resume = settings["load_resume_multiplier"] * snap["cpu_count"]
            if snap["load_avg"] > load_resume:
                still_high.append(
                    f"Load {snap['load_avg']} > {load_resume} "
                    f"({settings['load_resume_multiplier']}x {snap['cpu_count']} CPUs)"
                )
            if snap["vram_pct"] is not None and snap["vram_pct"] > settings["vram_resume_pct"]:
                still_high.append(f"VRAM {snap['vram_pct']}% > {settings['vram_resume_pct']}%")

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
            reasons.append(
                f"ollama ps shows {snap['ollama_model']} loaded (interactive user); yielding"
            )

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
            pass
        return info
