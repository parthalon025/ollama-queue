"""Tests for Forge calibrator — isotonic regression judge->oracle mapping."""

from ollama_queue.forge.calibrator import apply_calibration, fit_calibration


def test_fit_calibration_identity():
    """When judge and oracle agree, calibration is identity-ish."""
    judge = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    oracle = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    cal = fit_calibration(judge, oracle)
    assert cal is not None
    # Calibrated 3 should be close to 3
    assert abs(apply_calibration(cal, 3) - 3.0) < 0.5


def test_fit_calibration_bias_correction():
    """Judge consistently scores 1 higher than oracle."""
    judge = [2, 2, 3, 3, 4, 4, 5, 5, 5, 5]
    oracle = [1, 1, 2, 2, 3, 3, 4, 4, 4, 4]
    cal = fit_calibration(judge, oracle)
    # Calibrated judge=4 should be closer to oracle=3
    calibrated = apply_calibration(cal, 4)
    assert calibrated < 4.0


def test_fit_calibration_too_few_pairs():
    """Returns None when fewer than 10 pairs."""
    judge = [1, 2, 3]
    oracle = [1, 2, 3]
    cal = fit_calibration(judge, oracle)
    assert cal is None


def test_apply_calibration_none_returns_raw():
    """When no calibration model, return raw score."""
    assert apply_calibration(None, 3) == 3.0


def test_fit_calibration_monotonic():
    """Calibrated scores should be monotonically non-decreasing."""
    judge = [1, 1, 2, 3, 3, 4, 4, 5, 5, 5, 2, 3]
    oracle = [1, 2, 2, 2, 3, 4, 3, 5, 4, 5, 1, 4]
    cal = fit_calibration(judge, oracle)
    calibrated = [apply_calibration(cal, s) for s in range(1, 6)]
    for i in range(len(calibrated) - 1):
        assert calibrated[i] <= calibrated[i + 1] + 1e-6


def test_calibration_serialization():
    """Calibration model can be serialized to JSON."""
    judge = [1, 2, 2, 3, 3, 4, 4, 5, 5, 5]
    oracle = [1, 1, 2, 3, 3, 4, 4, 5, 4, 5]
    cal = fit_calibration(judge, oracle)
    import json

    serialized = json.dumps(cal)
    deserialized = json.loads(serialized)
    assert abs(apply_calibration(deserialized, 3) - apply_calibration(cal, 3)) < 1e-6
