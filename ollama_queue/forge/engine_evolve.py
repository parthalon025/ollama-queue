# ollama_queue/forge/engine_evolve.py
"""Forge engine — Phase 2 (Evolve) orchestration.

Coordinates splits, descriptors, archive, Thompson Sampling, and evolution.
Called from engine.py as a single run_evolve_phase() entry point.
Each public function does ONE thing — pure computation, no DB/HTTP.
"""

from __future__ import annotations

import random

from ollama_queue.forge.archive import ArchiveCell, try_insert
from ollama_queue.forge.descriptors import (
    DEFAULT_GRID_SIZE,
    compute_default_descriptors,
    get_descriptor_axes,
    normalize_to_bin,
)
from ollama_queue.forge.evolver import evolve_generation
from ollama_queue.forge.splits import assign_split
from ollama_queue.forge.thompson import ThompsonBudget


def assign_pair_splits(pairs: list[dict], *, seed: int = 0) -> list[dict]:
    """Annotate each pair with its train/validation/test split.

    Split is determined by the source item (item_a) for consistency.
    """
    for pair in pairs:
        pair["split"] = assign_split(pair["item_a"], seed=seed)
    return pairs


def populate_archive_cell(
    results: list[dict],
    metrics: dict,
    *,
    variant_id: str,
    grid_size: int = DEFAULT_GRID_SIZE,
) -> ArchiveCell:
    """Compute behavior descriptors and create an archive cell for this variant."""
    desc = compute_default_descriptors(results)
    axes = get_descriptor_axes()

    x_range = axes["x"].get("range", [0.0, 1.0])
    y_range = axes["y"].get("range", [0.0, 1.0])
    x_bin = normalize_to_bin(desc.get("output_length", 0.0), x_range[0], x_range[1], grid_size)
    y_bin = normalize_to_bin(desc.get("vocabulary_diversity", 0.0), y_range[0], y_range[1], grid_size)

    return ArchiveCell(
        x_bin=x_bin,
        y_bin=y_bin,
        x_value=desc.get("output_length", 0.0),
        y_value=desc.get("vocabulary_diversity", 0.0),
        variant_id=variant_id,
        fitness=metrics.get("f1", 0.0),
    )


def allocate_oracle_budget_thompson(
    results: list[dict],
    *,
    total_budget: int,
    thompson_state: dict | None = None,
    seed: int | None = None,
) -> list[dict]:
    """Allocate oracle budget across quartiles using Thompson Sampling."""
    if not results:
        return []

    tb = ThompsonBudget()
    if thompson_state:
        tb.load_state(thompson_state)

    by_quartile: dict[str, list[dict]] = {}
    for r in results:
        by_quartile.setdefault(r.get("quartile", "unknown"), []).append(r)

    categories = list(by_quartile.keys())
    if not categories:
        return []

    allocation = tb.allocate_budget(total_budget, categories, seed=seed)
    rng = random.Random(seed)  # noqa: S311
    sample = []
    for cat, n in allocation.items():
        pool = by_quartile.get(cat, [])
        n_pick = min(n, len(pool))
        if n_pick > 0:
            sample.extend(rng.sample(pool, n_pick))
    return sample


def maybe_evolve(
    grid: dict,
    *,
    n_offspring: int = 4,
    min_archive_size: int = 3,
    seed: int | None = None,
) -> list[str]:
    """Trigger evolution if archive is large enough. Returns new prompt strings."""
    cells = list(grid.values())
    if len(cells) < min_archive_size:
        return []
    return evolve_generation(cells, n_offspring=n_offspring, seed=seed)


__all__ = [
    "allocate_oracle_budget_thompson",
    "assign_pair_splits",
    "maybe_evolve",
    "populate_archive_cell",
    "try_insert",
]
