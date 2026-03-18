"""Forge oracle — cross-validation with a stronger LLM.

The oracle re-scores a sample of judge results. Cohen's kappa measures
agreement. Per-group breakdown available when group labels exist.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict

# Uniform 5-class expected agreement
_PE_UNIFORM = 0.2


def compute_kappa(
    judge_scores: list[int],
    oracle_scores: list[int],
    *,
    tolerance: int = 1,
) -> float:
    """Cohen's kappa with optional tolerance window.

    tolerance=0: exact agreement only.
    tolerance=1: scores within 1 point count as agreement (default).
    """
    n = len(judge_scores)
    if n == 0:
        return 0.0

    agree = sum(1 for j, o in zip(judge_scores, oracle_scores, strict=False) if abs(j - o) <= tolerance)
    po = agree / n
    pe = _PE_UNIFORM
    if pe >= 1.0:
        return 0.0
    return (po - pe) / (1 - pe)


def select_oracle_sample(
    results: list[dict],
    *,
    fraction: float = 0.2,
    budget: int = 20,
    seed: int | None = None,
) -> list[dict]:
    """Select a sample of judge results for oracle validation.

    Takes min(ceil(len * fraction), budget) results.
    Deterministic when seed is provided.
    """
    n = min(math.ceil(len(results) * fraction), budget)
    n = min(n, len(results))
    if n <= 0:
        return []
    rng = random.Random(seed)  # noqa: S311
    return rng.sample(results, n)


def compute_per_group_kappa(
    results: list[dict],
    *,
    tolerance: int = 1,
) -> dict[str, dict]:
    """Compute kappa per group label. Returns {} if no groups present.

    Each result must have judge_score and oracle_score.
    Group field is optional — results without it are skipped.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        group = r.get("group")
        if group and r.get("oracle_score") is not None:
            groups[group].append(r)

    breakdown = {}
    for group, items in groups.items():
        if len(items) < 2:
            continue
        judge = [r["judge_score"] for r in items]
        oracle = [r["oracle_score"] for r in items]
        kappa = compute_kappa(judge, oracle, tolerance=tolerance)
        breakdown[group] = {
            "kappa": round(kappa, 4),
            "pairs": len(items),
        }

    return breakdown
