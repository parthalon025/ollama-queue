"""Learned load patterns by hour-of-day and day-of-week.

Aggregates historical health log data into profiles that inform scheduling
decisions — like knowing that 02:00 is typically idle while 19:00 is busy.
"""

from __future__ import annotations

import time


class LoadPatterns:
    """Compute and cache load profiles from health log history."""

    def __init__(self) -> None:
        self._hourly: list[float] = [0.0] * 24
        self._daily: list[float] = [0.0] * 7
        self._computed = False

    def compute(self, health_log: list[dict]) -> dict:
        """Aggregate health_log into hourly/daily load profiles.

        Each entry should have 'recorded_at' (unix timestamp) and 'load' (float).
        Returns summary dict with computed profile sizes.
        """
        hourly_sums: list[float] = [0.0] * 24
        hourly_counts: list[int] = [0] * 24
        daily_sums: list[float] = [0.0] * 7
        daily_counts: list[int] = [0] * 7

        for entry in health_log:
            ts = entry.get("recorded_at")
            load = entry.get("load")
            if ts is None or load is None:
                continue

            lt = time.localtime(ts)
            hour = lt.tm_hour
            # tm_wday: Monday=0, Sunday=6
            day = lt.tm_wday

            hourly_sums[hour] += load
            hourly_counts[hour] += 1
            daily_sums[day] += load
            daily_counts[day] += 1

        self._hourly = [hourly_sums[h] / hourly_counts[h] if hourly_counts[h] > 0 else 0.0 for h in range(24)]
        self._daily = [daily_sums[d] / daily_counts[d] if daily_counts[d] > 0 else 0.0 for d in range(7)]
        self._computed = True

        return {
            "hourly_points": sum(hourly_counts),
            "daily_points": sum(daily_counts),
            "peak_hour": self._hourly.index(max(self._hourly)) if any(self._hourly) else None,
            "quietest_hour": self._hourly.index(min(self._hourly)) if any(self._hourly) else None,
        }

    def get_hourly_profile(self) -> list[float]:
        """24 floats: average load by hour (0=midnight, 23=11pm)."""
        return list(self._hourly)

    def get_daily_profile(self) -> list[float]:
        """7 floats: average load by day-of-week (0=Monday, 6=Sunday)."""
        return list(self._daily)

    @property
    def computed(self) -> bool:
        """Whether compute() has been called."""
        return self._computed
