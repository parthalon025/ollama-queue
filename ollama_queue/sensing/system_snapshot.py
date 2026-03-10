"""Point-in-time system state capture for scheduling decisions.

Plain English: Before the DLQ/deferral scheduler decides *when* to retry a failed
job, it needs to know what the system looks like right now. This module captures
RAM, VRAM, GPU temp, load, swap, loaded models, and queue depth into a frozen
snapshot. The classify_failure function tags *why* a job failed so the scheduler
can pick the right retry strategy (e.g. wait for VRAM to free up vs. never retry).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)


@dataclass
class SystemSnapshot:
    """Point-in-time system state for scheduling decisions."""

    timestamp: float = 0.0
    ram_used_pct: float = 0.0
    ram_available_gb: float = 0.0
    vram_used_pct: float = 0.0
    vram_available_gb: float = 0.0
    gpu_temp_c: float | None = None
    load_avg_1m: float = 0.0
    swap_used_pct: float = 0.0
    loaded_models: list[str] = field(default_factory=list)
    queue_depth: int = 0
    current_job_model: str | None = None

    @classmethod
    def capture(
        cls,
        health_monitor: object | None = None,
        db: object | None = None,
    ) -> SystemSnapshot:
        """Capture current system state from a HealthMonitor and Database.

        For now returns a snapshot with timestamp set and defaults for all
        other fields. Real integration with HealthMonitor/Database happens
        in Batch 4.
        """
        snap = cls(timestamp=time.time())

        if health_monitor is not None:
            try:
                snap.ram_used_pct = health_monitor.get_ram_pct()  # type: ignore[attr-defined]
            except Exception:
                _log.debug("Failed to read RAM pct from health monitor", exc_info=True)
            try:
                snap.swap_used_pct = health_monitor.get_swap_pct()  # type: ignore[attr-defined]
            except Exception:
                _log.debug("Failed to read swap pct from health monitor", exc_info=True)
            try:
                snap.load_avg_1m = health_monitor.get_load_avg()  # type: ignore[attr-defined]
            except Exception:
                _log.debug("Failed to read load avg from health monitor", exc_info=True)
            try:
                vram = health_monitor.get_vram_pct()  # type: ignore[attr-defined]
                if vram is not None:
                    snap.vram_used_pct = vram
            except Exception:
                _log.debug("Failed to read VRAM pct from health monitor", exc_info=True)

        return snap


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

# Patterns are checked in order; first match wins.
_RESOURCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"out of memory", re.IGNORECASE),
    re.compile(r"\bOOM\b"),
    re.compile(r"CUDA out of memory", re.IGNORECASE),
    re.compile(r"VRAM", re.IGNORECASE),
    re.compile(r"cannot allocate memory", re.IGNORECASE),
    re.compile(r"disk full", re.IGNORECASE),
    re.compile(r"no space left", re.IGNORECASE),
    re.compile(r"memory exhausted", re.IGNORECASE),
    re.compile(r"insufficient memory", re.IGNORECASE),
]

_TIMEOUT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"timeout", re.IGNORECASE),
    re.compile(r"timed out", re.IGNORECASE),
    re.compile(r"exceeded.*time.?limit", re.IGNORECASE),
    re.compile(r"deadline exceeded", re.IGNORECASE),
]

_MODEL_ERROR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"model.*(not found|missing)", re.IGNORECASE),
    re.compile(r"corrupt.*(weight|model|file)", re.IGNORECASE),
    re.compile(r"invalid model", re.IGNORECASE),
    re.compile(r"failed to load model", re.IGNORECASE),
    re.compile(r"no such model", re.IGNORECASE),
    re.compile(r"model.*does not exist", re.IGNORECASE),
]

_TRANSIENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"connection (refused|reset|timed out)", re.IGNORECASE),
    re.compile(r"network.*(unreachable|error)", re.IGNORECASE),
    re.compile(r"temporarily unavailable", re.IGNORECASE),
    re.compile(r"service unavailable", re.IGNORECASE),
    re.compile(r"502|503|504", re.IGNORECASE),
    re.compile(r"ECONNREFUSED", re.IGNORECASE),
    re.compile(r"ETIMEDOUT", re.IGNORECASE),
    re.compile(r"retry.?able", re.IGNORECASE),
]

_PERMANENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"syntax error", re.IGNORECASE),
    re.compile(r"command not found", re.IGNORECASE),
    re.compile(r"permission denied", re.IGNORECASE),
    re.compile(r"no such file", re.IGNORECASE),
    re.compile(r"missing script", re.IGNORECASE),
    re.compile(r"bad command", re.IGNORECASE),
    re.compile(r"invalid argument", re.IGNORECASE),
    re.compile(r"exit code 127"),
    re.compile(r"exit code 126"),
]

_CATEGORY_PATTERNS: list[tuple[str, list[re.Pattern[str]]]] = [
    ("resource", _RESOURCE_PATTERNS),
    ("timeout", _TIMEOUT_PATTERNS),
    ("model_error", _MODEL_ERROR_PATTERNS),
    ("transient", _TRANSIENT_PATTERNS),
    ("permanent", _PERMANENT_PATTERNS),
]


def classify_failure(failure_reason: str, exit_code: int | None = None) -> str:
    """Classify a job failure into a scheduling-relevant category.

    Categories:
    - 'resource': OOM, VRAM exhaustion, disk full
    - 'timeout': exceeded time limit
    - 'model_error': model not found, corrupt weights
    - 'transient': network errors, temporary unavailability
    - 'permanent': bad command, syntax error, missing script
    - 'unknown': none of the above matched

    Args:
        failure_reason: The error message / stderr from the failed job.
        exit_code: Optional process exit code for additional signal.

    Returns:
        One of the category strings listed above.
    """
    # Try keyword matching on the failure reason text
    if failure_reason:
        for category, patterns in _CATEGORY_PATTERNS:
            for pat in patterns:
                if pat.search(failure_reason):
                    return category

    # Fall back to exit code heuristics
    if exit_code == 137:
        return "resource"  # killed by OOM killer (SIGKILL)
    if exit_code in (126, 127):
        return "permanent"

    return "unknown"
