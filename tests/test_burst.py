"""Tests for EWMA-based burst detection."""

import time

from ollama_queue.sensing.burst import BurstDetector


class TestBurstDetector:
    def test_starts_unknown_before_10_samples(self):
        """Returns 'unknown' until 10 inter-arrival samples are collected."""
        detector = BurstDetector()
        now = time.time()
        for i in range(9):
            detector.record_submission(now + i * 10)
        assert detector.regime(now + 100) == "unknown"

    def test_subcritical_on_slow_steady_arrivals(self):
        """Slow, regular arrivals (100s apart) produce subcritical regime."""
        detector = BurstDetector()
        now = time.time()
        for i in range(20):
            detector.record_submission(now + i * 100.0)
        regime = detector.regime(now + 2000)
        assert regime in ("subcritical", "moderate"), f"Expected subcritical, got {regime}"

    def test_critical_on_rapid_burst(self):
        """Rapid burst (0.1s apart) after steady baseline produces critical regime."""
        detector = BurstDetector()
        now = time.time()
        for i in range(20):
            detector.record_submission(now + i * 60.0)
        burst_start = now + 1200.0
        for i in range(30):
            detector.record_submission(burst_start + i * 0.1)
        regime = detector.regime(burst_start + 3)
        assert regime in ("warning", "critical"), f"Expected warning/critical, got {regime}"

    def test_regime_transitions_on_resumed_normal(self):
        """Regime returns to subcritical after burst subsides (EWMA decays)."""
        detector = BurstDetector(alpha=0.5)  # faster decay for test
        now = time.time()
        for i in range(20):
            detector.record_submission(now + i * 60.0)
        burst = now + 1200
        for i in range(5):
            detector.record_submission(burst + i * 0.1)
        recovery = burst + 100
        for i in range(40):
            detector.record_submission(recovery + i * 60.0)
        regime = detector.regime(recovery + 2400)
        assert regime in ("subcritical", "moderate"), f"Expected recovery, got {regime}"

    def test_single_submission_does_not_crash(self):
        """Handles single submission without error."""
        detector = BurstDetector()
        detector.record_submission(time.time())
        assert detector.regime(time.time()) == "unknown"

    def test_sparse_arrivals_stay_subcritical(self):
        """Hours-apart arrivals do not false-alarm as bursts."""
        detector = BurstDetector()
        now = time.time()
        for i in range(15):
            detector.record_submission(now + i * 3600)
        regime = detector.regime(now + 15 * 3600)
        assert regime == "subcritical", f"Expected subcritical for sparse arrivals, got {regime}"

    def test_regime_returns_valid_value(self):
        """regime() always returns a valid string."""
        detector = BurstDetector()
        now = time.time()
        valid = {"unknown", "subcritical", "moderate", "warning", "critical"}
        for i in range(20):
            detector.record_submission(now + i * 10)
        assert detector.regime(now + 200) in valid

    def test_non_positive_interval_discarded(self):
        """Duplicate or reversed timestamps discard the interval (line 73)."""
        detector = BurstDetector()
        now = time.time()
        detector.record_submission(now)
        detector.record_submission(now)  # interval == 0 → discarded
        detector.record_submission(now - 1)  # interval < 0 → discarded
        # No valid intervals recorded, so EWMA stays None
        assert detector._ewma is None
        assert len(detector._baseline_samples) == 0

    def test_baseline_zero_returns_unknown(self):
        """When all baseline samples are 0, regime returns 'unknown' (line 103)."""
        detector = BurstDetector()
        # Manually inject 10 zero-value samples (can't happen via record_submission,
        # but tests the defensive guard)
        detector._baseline_samples.extend([0.0] * 10)
        detector._ewma = 1.0
        assert detector.regime(time.time()) == "unknown"

    def test_fallback_subcritical(self):
        """When ratio doesn't match any bracket, returns 'subcritical' (line 109).

        This is a defensive fallback that shouldn't happen with the current _REGIMES
        definition (subcritical covers 0.5 to inf), but we test the guard by making
        ratio negative (ewma < 0 while baseline > 0).
        """
        detector = BurstDetector()
        detector._baseline_samples.extend([1.0] * 10)
        detector._ewma = -0.1  # negative ratio, no bracket matches
        result = detector.regime(time.time())
        assert result == "subcritical"
