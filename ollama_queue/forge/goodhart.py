# ollama_queue/forge/goodhart.py
"""Forge Goodhart monitoring — composite score for human observation only.

The monitoring composite is NEVER used as an optimization target.
The optimizer sees only calibrated F1 on the validation set.
"""

from __future__ import annotations

import statistics


def compute_monitoring_composite(
    *,
    kappa: float,
    calibrated_f1: float,
    archive_coverage: float = 0.0,
    score_variance: float = 0.0,
) -> float:
    """Weighted composite for human monitoring — NEVER for optimization.

    Weights: kappa=0.3, f1=0.3, coverage=0.2, variance=0.2.
    """
    return kappa * 0.3 + calibrated_f1 * 0.3 + archive_coverage * 0.2 + min(1.0, score_variance) * 0.2


def check_goodhart_divergence(
    train_f1s: list[float],
    validation_f1s: list[float],
    *,
    threshold: float = 0.15,
) -> dict:
    """Detect train/validation gap — sign of overfitting to train set."""
    if len(train_f1s) < 3 or len(validation_f1s) < 3:
        return {"diverging": False, "train_mean": None, "val_mean": None, "gap": None, "reason": "insufficient_data"}

    train_mean = statistics.mean(train_f1s[-5:])
    val_mean = statistics.mean(validation_f1s[-5:])
    gap = train_mean - val_mean

    return {
        "diverging": gap > threshold,
        "train_mean": round(train_mean, 4),
        "val_mean": round(val_mean, 4),
        "gap": round(gap, 4),
    }


def compute_metric_staleness(
    f1_history: list[float],
    *,
    window: int = 10,
    plateau_threshold: float = 0.02,
) -> dict:
    """Detect if optimization has stalled (F1 plateau)."""
    if len(f1_history) < window:
        return {"stale": False, "recent_stdev": None, "window_used": len(f1_history), "reason": "insufficient_data"}

    recent = f1_history[-window:]
    stdev = statistics.pstdev(recent)

    return {
        "stale": stdev < plateau_threshold,
        "recent_stdev": round(stdev, 4),
        "window_used": window,
    }
