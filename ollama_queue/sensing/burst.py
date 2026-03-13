"""EWMA-based burst detection for ollama-queue.

Dependency-free implementation. Detects self-exciting submission bursts by
comparing current EWMA inter-arrival time against a stable 75th-percentile
baseline. Self-excitation manifests as rapidly shrinking inter-arrival times.

No external packages required (no 'tick', no scipy).
"""

from __future__ import annotations

import logging
import threading
from collections import deque

_log = logging.getLogger(__name__)

# Regime: (name, ratio_low_bound, ratio_high_bound)
# ratio = ewma_interval / p75_baseline (dimensionless)
# Burst → ratio shrinks → falls into lower brackets
_REGIMES: list[tuple[str, float, float]] = [
    ("critical", 0.00, 0.15),  # ewma < 15% of baseline → severe burst
    ("warning", 0.15, 0.30),  # ewma 15-30% of baseline → approaching saturation
    ("moderate", 0.30, 0.50),  # ewma 30-50% of baseline → elevated load
    ("subcritical", 0.50, float("inf")),  # ewma > 50% of baseline → normal
]


class BurstDetector:
    """Dependency-free burst detection via EWMA of inter-arrival times.

    Regime is determined by comparing the EWMA inter-arrival time against
    a 75th-percentile baseline computed from historical intervals.

    The 75th-percentile baseline is robust: during a burst, new short
    intervals are added to the sample, but they don't displace the p75
    until the burst dominates >25% of history. This gives a stable baseline
    that reflects normal traffic rather than being contaminated by bursts.

    Usage:
        detector = BurstDetector()
        # On each job submission:
        detector.record_submission(time.time())
        # In daemon poll:
        regime = detector.regime(time.time())
    """

    def __init__(self, alpha: float = 0.3, baseline_window: int = 100):
        """
        Args:
            alpha: EWMA smoothing factor (0-1). Higher = faster response to changes.
                   0.3 = good balance between responsiveness and noise rejection.
            baseline_window: Number of inter-arrival samples to keep for p75 baseline.
        """
        self._alpha = alpha
        self._ewma: float | None = None
        self._baseline_samples: deque[float] = deque(maxlen=baseline_window)
        self._last_ts: float | None = None
        self._lock = threading.Lock()

    def record_submission(self, ts: float) -> None:
        """Record a job submission timestamp. Call on every /api/submit."""
        with self._lock:
            if self._last_ts is not None:
                interval = ts - self._last_ts
                if interval > 0:
                    self._baseline_samples.append(interval)
                    if self._ewma is None:
                        self._ewma = interval
                    else:
                        self._ewma = self._alpha * interval + (1 - self._alpha) * self._ewma
                else:
                    _log.debug(
                        "BurstDetector: discarding non-positive interval %.6f (clock skew or duplicate ts)", interval
                    )
            self._last_ts = ts

    def regime(self, now: float | None = None) -> str:
        """Return current burst regime classification.

        Returns:
            "unknown"     — insufficient data (< 10 samples)
            "subcritical" — normal traffic
            "moderate"    — elevated submission rate
            "warning"     — approaching saturation
            "critical"    — burst in progress; consider engaging 429 gate

        Requires at least 10 inter-arrival samples for a reliable baseline.
        """
        with self._lock:
            if len(self._baseline_samples) < 10 or self._ewma is None:
                return "unknown"
            # Copy under lock — avoids RuntimeError: deque mutated during iteration
            # when record_submission() appends concurrently from FastAPI worker threads.
            samples_copy = list(self._baseline_samples)
            ewma = self._ewma
        # Sort and all computation outside the lock — minimises lock hold time.
        # 75th percentile baseline: robust against burst contamination.
        # Nearest-rank p75 (0-indexed): ceil(0.75 * N) - 1, computed via
        # ceiling-division trick to avoid importing math.
        sorted_samples = sorted(samples_copy)
        n = len(sorted_samples)
        p75_idx = min(-(-n * 3 // 4) - 1, n - 1)
        baseline = sorted_samples[p75_idx]

        if baseline <= 0:
            return "unknown"

        ratio = ewma / baseline
        for name, low, high in _REGIMES:
            if low <= ratio < high:
                return name
        return "subcritical"


# Module-level singleton shared between daemon and API.
# Daemon writes via record_submission(); API reads via regime().
# Both modules import this instance directly.
_default_detector = BurstDetector()
