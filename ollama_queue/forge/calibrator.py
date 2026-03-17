"""Forge calibrator — isotonic regression maps judge scores to oracle scale.

Fits a monotonic function from judge->oracle scores so that calibrated
scores reflect the oracle's ground truth. Requires >=10 pairs.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

_MIN_PAIRS = 10


def fit_calibration(
    judge_scores: list[int],
    oracle_scores: list[int],
) -> dict | None:
    """Fit isotonic regression from judge scores to oracle scores.

    Returns a serializable dict {x_thresholds, y_values} or None if
    fewer than _MIN_PAIRS. The model is a piecewise-constant function:
    for input x, find the largest x_threshold <= x and return y_value.
    """
    if len(judge_scores) < _MIN_PAIRS:
        _log.info("calibrator: only %d pairs, need %d — skipping", len(judge_scores), _MIN_PAIRS)
        return None

    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        _log.warning("calibrator: scikit-learn not installed — skipping")
        return None

    ir = IsotonicRegression(y_min=1.0, y_max=5.0, out_of_bounds="clip")
    ir.fit(judge_scores, oracle_scores)

    return {
        "x_thresholds": ir.X_thresholds_.tolist(),
        "y_values": ir.y_thresholds_.tolist(),
    }


def apply_calibration(cal: dict | None, judge_score: int | float) -> float:
    """Apply calibration model to a judge score. Returns calibrated float.

    If cal is None, returns the raw score as a float.
    """
    if cal is None:
        return float(judge_score)

    thresholds = cal["x_thresholds"]
    values = cal["y_values"]

    # Binary search for the right bucket
    x = float(judge_score)
    if x <= thresholds[0]:
        return values[0]
    if x >= thresholds[-1]:
        return values[-1]

    # Find largest threshold <= x
    lo, hi = 0, len(thresholds) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if thresholds[mid] <= x:
            lo = mid
        else:
            hi = mid - 1

    # Linear interpolation between adjacent thresholds
    if lo < len(thresholds) - 1:
        t0, t1 = thresholds[lo], thresholds[lo + 1]
        v0, v1 = values[lo], values[lo + 1]
        if t1 > t0:
            frac = (x - t0) / (t1 - t0)
            return v0 + frac * (v1 - v0)

    return values[lo]
