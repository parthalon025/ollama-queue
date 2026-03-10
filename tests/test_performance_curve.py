"""Tests for cross-model performance curve regression."""

from ollama_queue.performance_curve import PerformanceCurve


def test_no_data():
    """No data — predict returns None."""
    curve = PerformanceCurve()
    assert curve.predict_tok_per_min(5.0) is None


def test_single_point():
    """Single data point — linear extrapolation from that point."""
    curve = PerformanceCurve()
    curve.fit([{"model_size_gb": 5.0, "avg_tok_per_min": 80.0}])
    result = curve.predict_tok_per_min(5.0)
    assert result is not None
    assert abs(result - 80.0) < 1.0


def test_two_points():
    """Two points — linear interpolation."""
    curve = PerformanceCurve()
    curve.fit(
        [
            {"model_size_gb": 5.0, "avg_tok_per_min": 80.0},
            {"model_size_gb": 10.0, "avg_tok_per_min": 45.0},
        ]
    )
    result = curve.predict_tok_per_min(7.5)
    assert result is not None
    assert 45 < result < 80


def test_regression():
    """3+ points — log-linear regression."""
    curve = PerformanceCurve()
    curve.fit(
        [
            {"model_size_gb": 2.0, "avg_tok_per_min": 120.0},
            {"model_size_gb": 5.0, "avg_tok_per_min": 80.0},
            {"model_size_gb": 10.0, "avg_tok_per_min": 45.0},
            {"model_size_gb": 40.0, "avg_tok_per_min": 8.0},
        ]
    )
    result = curve.predict_tok_per_min(20.0)
    assert result is not None
    assert 5 < result < 45


def test_warmup():
    """Warmup curve — linear fit."""
    curve = PerformanceCurve()
    curve.fit(
        [
            {"model_size_gb": 2.0, "avg_warmup_s": 0.8},
            {"model_size_gb": 5.0, "avg_warmup_s": 1.8},
            {"model_size_gb": 10.0, "avg_warmup_s": 3.2},
        ]
    )
    result = curve.predict_warmup(7.5)
    assert result is not None
    assert 1.8 < result < 3.2


def test_confidence_interval():
    """Curve provides confidence interval."""
    curve = PerformanceCurve()
    curve.fit(
        [
            {"model_size_gb": 2.0, "avg_tok_per_min": 120.0},
            {"model_size_gb": 5.0, "avg_tok_per_min": 80.0},
            {"model_size_gb": 10.0, "avg_tok_per_min": 45.0},
        ]
    )
    result = curve.predict_tok_per_min_ci(7.5)
    assert result is not None
    mean, lower, upper = result
    assert lower < mean < upper


def test_get_curve_data():
    """get_curve_data returns serializable dict."""
    curve = PerformanceCurve()
    curve.fit(
        [
            {"model_size_gb": 5.0, "avg_tok_per_min": 80.0},
            {"model_size_gb": 10.0, "avg_tok_per_min": 45.0},
        ]
    )
    data = curve.get_curve_data()
    assert data["fitted"] is True
    assert data["n_points"] == 2
    assert data["tok_slope"] is not None


def test_not_fitted():
    """get_curve_data reflects unfitted state."""
    curve = PerformanceCurve()
    data = curve.get_curve_data()
    assert data["fitted"] is False
    assert data["tok_slope"] is None


def test_fit_ignores_negative_stats():
    """Negative model_size_gb or avg_tok_per_min must not crash math.log."""
    curve = PerformanceCurve()
    curve.fit(
        [
            {"model_size_gb": -1.0, "avg_tok_per_min": 80.0},
            {"model_size_gb": 5.0, "avg_tok_per_min": -5.0},
            {"model_size_gb": 10.0, "avg_tok_per_min": 45.0},
        ]
    )
    # Should fit from valid point only (single-point path)
    assert curve.fitted


def test_fit_degenerate_same_size():
    """All models same size — should not crash, slope=0."""
    curve = PerformanceCurve()
    curve.fit(
        [
            {"model_size_gb": 7.0, "avg_tok_per_min": 80.0},
            {"model_size_gb": 7.0, "avg_tok_per_min": 75.0},
        ]
    )
    assert curve.fitted
    result = curve.predict_tok_per_min(7.0)
    assert result is not None


def test_zero_model_size_returns_none():
    """model_size_gb <= 0 returns None instead of crashing."""
    curve = PerformanceCurve()
    curve.fit(
        [
            {"model_size_gb": 5.0, "avg_tok_per_min": 80.0},
            {"model_size_gb": 10.0, "avg_tok_per_min": 45.0},
        ]
    )
    assert curve.predict_tok_per_min(0) is None
    assert curve.predict_tok_per_min(-1) is None
    assert curve.predict_tok_per_min_ci(0) is None
