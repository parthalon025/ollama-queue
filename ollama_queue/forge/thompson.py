# ollama_queue/forge/thompson.py
"""Forge Thompson Sampling — adaptive oracle budget allocation.

Allocates oracle budget to categories (quartiles or groups) based on
uncertainty. Categories where judge-oracle agreement is uncertain get
more oracle checks. Adapted from ARIA shadow_engine.py ThompsonSampler.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

_DISCOUNT_FACTOR = 0.95
_WINDOW_SIZE = 100


@dataclass
class _Bucket:
    alpha: float = 1.0
    beta: float = 1.0
    observations: int = 0


class ThompsonBudget:
    """Allocates oracle budget across categories using Thompson Sampling.

    Categories with high uncertainty (alpha ~ beta) get more checks.
    """

    def __init__(self, discount: float = _DISCOUNT_FACTOR, window: int = _WINDOW_SIZE):
        self._buckets: dict[str, _Bucket] = {}
        self._discount = discount
        self._window = window

    def _get_bucket(self, category: str) -> _Bucket:
        if category not in self._buckets:
            self._buckets[category] = _Bucket()
        return self._buckets[category]

    def record_agreement(self, category: str, agreed: bool) -> None:
        """Update posterior after observing judge-oracle agreement."""
        b = self._get_bucket(category)
        b.alpha = max(1.0, b.alpha * self._discount)
        b.beta = max(1.0, b.beta * self._discount)
        if agreed:
            b.alpha += 1.0
        else:
            b.beta += 1.0
        b.observations += 1
        if b.observations > self._window:
            scale = self._window / b.observations
            b.alpha = 1.0 + (b.alpha - 1.0) * scale
            b.beta = 1.0 + (b.beta - 1.0) * scale
            b.observations = self._window

    def allocate_budget(
        self,
        total_budget: int,
        categories: list[str],
        *,
        seed: int | None = None,
    ) -> dict[str, int]:
        """Allocate oracle checks — uncertain categories get more budget."""
        if not categories:
            return {}
        rng = random.Random(seed)  # noqa: S311 — not used for crypto

        samples: dict[str, float] = {}
        for cat in categories:
            b = self._get_bucket(cat)
            sample = rng.betavariate(b.alpha, b.beta)
            samples[cat] = 1.0 - sample  # invert: low agreement = high weight

        if total_budget < len(categories):
            allocation: dict[str, int] = {}
            for i, cat in enumerate(categories):
                allocation[cat] = 1 if i < total_budget else 0
            return allocation

        remaining = total_budget - len(categories)
        allocation = {cat: 1 for cat in categories}
        total_weight = sum(samples.values())

        if total_weight > 0 and remaining > 0:
            for cat in categories:
                extra = round(remaining * samples[cat] / total_weight)
                allocation[cat] += extra
            diff = total_budget - sum(allocation.values())
            if diff != 0:
                top = max(categories, key=lambda c: samples[c])
                allocation[top] += diff

        return allocation

    def get_state(self) -> dict[str, dict]:
        return {
            cat: {"alpha": b.alpha, "beta": b.beta, "observations": b.observations} for cat, b in self._buckets.items()
        }

    def load_state(self, state: dict[str, dict]) -> None:
        self._buckets.clear()
        for cat, vals in state.items():
            self._buckets[cat] = _Bucket(
                alpha=vals.get("alpha", 1.0),
                beta=vals.get("beta", 1.0),
                observations=vals.get("observations", 0),
            )

    def get_stats(self) -> dict[str, dict]:
        result = {}
        for cat, b in self._buckets.items():
            mean = b.alpha / (b.alpha + b.beta)
            result[cat] = {
                "mean": round(mean, 4),
                "alpha": round(b.alpha, 2),
                "beta": round(b.beta, 2),
                "observations": b.observations,
            }
        return result
