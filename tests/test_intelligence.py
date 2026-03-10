"""Tests for ollama_queue.intelligence — LoadPatterns."""

import time

from ollama_queue.intelligence import LoadPatterns


class TestLoadPatterns:
    def test_empty_log(self):
        lp = LoadPatterns()
        result = lp.compute([])
        assert result["hourly_points"] == 0
        assert lp.get_hourly_profile() == [0.0] * 24
        assert lp.get_daily_profile() == [0.0] * 7

    def test_single_entry(self):
        lp = LoadPatterns()
        # Monday at 14:00 with load 5.0
        monday_2pm = time.mktime(time.strptime("2026-03-09 14:00:00", "%Y-%m-%d %H:%M:%S"))
        lp.compute([{"recorded_at": monday_2pm, "load": 5.0}])

        hourly = lp.get_hourly_profile()
        assert hourly[14] == 5.0
        assert hourly[0] == 0.0  # other hours are 0

    def test_multiple_entries_average(self):
        lp = LoadPatterns()
        monday_2pm = time.mktime(time.strptime("2026-03-09 14:00:00", "%Y-%m-%d %H:%M:%S"))
        entries = [
            {"recorded_at": monday_2pm, "load": 4.0},
            {"recorded_at": monday_2pm + 60, "load": 6.0},  # same hour
        ]
        lp.compute(entries)

        hourly = lp.get_hourly_profile()
        assert hourly[14] == 5.0  # average of 4 and 6

    def test_daily_profile(self):
        lp = LoadPatterns()
        # Monday and Tuesday entries
        monday = time.mktime(time.strptime("2026-03-09 10:00:00", "%Y-%m-%d %H:%M:%S"))
        tuesday = time.mktime(time.strptime("2026-03-10 10:00:00", "%Y-%m-%d %H:%M:%S"))
        lp.compute(
            [
                {"recorded_at": monday, "load": 3.0},
                {"recorded_at": tuesday, "load": 7.0},
            ]
        )

        daily = lp.get_daily_profile()
        assert daily[0] == 3.0  # Monday
        assert daily[1] == 7.0  # Tuesday

    def test_skips_entries_without_fields(self):
        lp = LoadPatterns()
        result = lp.compute(
            [
                {"recorded_at": None, "load": 5.0},
                {"recorded_at": time.time(), "load": None},
                {"load": 5.0},
                {"recorded_at": time.time()},
            ]
        )
        assert result["hourly_points"] == 0

    def test_computed_flag(self):
        lp = LoadPatterns()
        assert not lp.computed
        lp.compute([])
        assert lp.computed

    def test_peak_and_quietest_hour(self):
        lp = LoadPatterns()
        base = time.mktime(time.strptime("2026-03-09 00:00:00", "%Y-%m-%d %H:%M:%S"))
        # Fill all 24 hours so min() is meaningful
        entries = [{"recorded_at": base + h * 3600, "load": 5.0} for h in range(24)]
        entries[3] = {"recorded_at": base + 3 * 3600, "load": 1.0}  # 03:00 - quiet
        entries[19] = {"recorded_at": base + 19 * 3600, "load": 9.0}  # 19:00 - peak
        result = lp.compute(entries)
        assert result["peak_hour"] == 19
        assert result["quietest_hour"] == 3

    def test_profiles_are_copies(self):
        lp = LoadPatterns()
        lp.compute([])
        h1 = lp.get_hourly_profile()
        h2 = lp.get_hourly_profile()
        assert h1 is not h2  # independent copies
