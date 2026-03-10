"""10-factor time-slot scoring for VRAM-aware job scheduling."""

from __future__ import annotations

import time

_BASE_SCORE = 5.0
_HOT_MODEL_BONUS = 3.0
_RECURRING_CONFLICT_PENALTY = -5.0
_HISTORICAL_QUIET_BONUS = 2.0
_QUEUE_DEPTH_FACTOR = -0.5
_RESOURCE_FAILURE_HEADROOM = 0.30  # 30% extra VRAM margin


def score_slot(
    slot_load: float,
    slot_vram_committed_gb: float,
    job_vram_needed_gb: float,
    total_vram_gb: float,
    is_pinned: bool,
    model_is_hot: bool,
    recurring_conflict: bool,
    historical_quiet: bool,
    failure_category: str | None,
    queue_depth: int,
) -> float:
    """Score a time slot for scheduling a job. Higher is better.

    Returns -1 if slot is infeasible (VRAM hard gate or pinned).
    """
    # Factor 9: Pinned slot — never schedule into pinned slots
    if is_pinned:
        return -1.0

    # Factor 1: VRAM hard gate
    if job_vram_needed_gb + slot_vram_committed_gb > total_vram_gb:
        return -1.0

    # Factor 6: Resource failure — require 30% extra VRAM headroom
    if failure_category == "resource":
        headroom = total_vram_gb - (job_vram_needed_gb + slot_vram_committed_gb)
        required_headroom = job_vram_needed_gb * _RESOURCE_FAILURE_HEADROOM
        if headroom < required_headroom:
            return -1.0

    score = _BASE_SCORE

    # Factor 2: Load headroom — empty slots score higher
    score += max(0.0, 10.0 - slot_load)

    # Factor 3: Hot model bonus
    if model_is_hot:
        score += _HOT_MODEL_BONUS

    # Factor 4: Recurring conflict penalty
    if recurring_conflict:
        score += _RECURRING_CONFLICT_PENALTY

    # Factor 5: Historical quiet bonus
    if historical_quiet:
        score += _HISTORICAL_QUIET_BONUS

    # Factor 7: Timeout failure — prefer low-load (open-ended) slots
    if failure_category == "timeout":
        score += max(0.0, 5.0 - slot_load)

    # Factor 8: Queue depth penalty
    score += _QUEUE_DEPTH_FACTOR * queue_depth

    return score


def find_fitting_slot(
    load_map: list[dict],
    job_vram_needed_gb: float,
    total_vram_gb: float,
    estimated_slots: int,
    failure_category: str | None = None,
    loaded_models: list[str] | None = None,
    job_model: str | None = None,
) -> dict | None:
    """Find the best time slot for a job from the load map.

    Returns dict with slot_index, score, scheduled_time or None if no fit.
    Scans for contiguous runs of ``estimated_slots`` slots where all pass
    the VRAM gate.  Returns the window with the highest average score.
    """
    if not load_map or estimated_slots <= 0:
        return None

    loaded_models = loaded_models or []
    model_is_hot = job_model is not None and job_model in loaded_models

    # Score every slot individually
    scores: list[float] = []
    for entry in load_map:
        s = score_slot(
            slot_load=entry.get("load", 0.0),
            slot_vram_committed_gb=entry.get("vram_committed_gb", 0.0),
            job_vram_needed_gb=job_vram_needed_gb,
            total_vram_gb=total_vram_gb,
            is_pinned=entry.get("is_pinned", False),
            model_is_hot=model_is_hot,
            recurring_conflict=bool(entry.get("recurring_ids")),
            historical_quiet=entry.get("historical_quiet", False),
            failure_category=failure_category,
            queue_depth=entry.get("queue_depth", 0),
        )
        scores.append(s)

    # Scan for contiguous windows where every slot is feasible
    best_avg: float | None = None
    best_start: int | None = None

    for start in range(len(scores) - estimated_slots + 1):
        window = scores[start : start + estimated_slots]
        if any(s < 0 for s in window):
            continue
        avg = sum(window) / len(window)
        if best_avg is None or avg > best_avg:
            best_avg = avg
            best_start = start

    if best_start is None:
        return None

    return {
        "slot_index": best_start,
        "score": best_avg,
        "scheduled_time": load_map[best_start].get("timestamp") or (time.time() + best_start * 1800),
    }
