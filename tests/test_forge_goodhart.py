# tests/test_forge_goodhart.py
"""Tests for Forge Goodhart monitoring — composite + divergence."""

from ollama_queue.forge.goodhart import (
    check_goodhart_divergence,
    compute_metric_staleness,
    compute_monitoring_composite,
)


def test_composite_all_ones():
    val = compute_monitoring_composite(kappa=1.0, calibrated_f1=1.0, archive_coverage=1.0, score_variance=1.0)
    assert val == 1.0


def test_composite_all_zeros():
    assert compute_monitoring_composite(kappa=0.0, calibrated_f1=0.0, archive_coverage=0.0, score_variance=0.0) == 0.0


def test_composite_weights():
    # Only kappa=1, rest=0 -> 0.3
    val = compute_monitoring_composite(kappa=1.0, calibrated_f1=0.0, archive_coverage=0.0, score_variance=0.0)
    assert abs(val - 0.3) < 0.001


def test_divergence_detected():
    train = [0.8, 0.82, 0.85, 0.87, 0.90]
    val = [0.6, 0.58, 0.55, 0.52, 0.50]
    result = check_goodhart_divergence(train, val)
    assert result["diverging"] is True
    assert result["gap"] > 0.15


def test_divergence_not_detected():
    train = [0.8, 0.82, 0.81, 0.83, 0.82]
    val = [0.78, 0.80, 0.79, 0.81, 0.80]
    result = check_goodhart_divergence(train, val)
    assert result["diverging"] is False


def test_divergence_insufficient_data():
    result = check_goodhart_divergence([0.8], [0.7])
    assert result["diverging"] is False
    assert result["reason"] == "insufficient_data"


def test_staleness_plateau():
    history = [0.75, 0.75, 0.76, 0.75, 0.75, 0.76, 0.75, 0.75, 0.76, 0.75]
    result = compute_metric_staleness(history, window=10)
    assert result["stale"] is True


def test_staleness_improving():
    history = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
    result = compute_metric_staleness(history, window=10)
    assert result["stale"] is False


def test_staleness_insufficient():
    result = compute_metric_staleness([0.5, 0.6], window=10)
    assert result["reason"] == "insufficient_data"
