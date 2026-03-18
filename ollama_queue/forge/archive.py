# ollama_queue/forge/archive.py
"""Forge MAP-Elites archive — quality-diversity prompt strategy storage.

A 2D grid indexed by behavior descriptors. Each cell holds the best-performing
variant for that behavioral niche. Coverage and QD-score track exploration.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArchiveCell:
    """One cell in the MAP-Elites grid."""

    x_bin: int
    y_bin: int
    x_value: float
    y_value: float
    variant_id: str
    fitness: float
    prompt_text: str | None = None
    metadata: dict | None = None


def try_insert(grid: dict[tuple[int, int], ArchiveCell], cell: ArchiveCell) -> bool:
    """Insert cell if empty or new fitness exceeds existing.

    Returns True if inserted/replaced, False if existing is better.
    """
    key = (cell.x_bin, cell.y_bin)
    existing = grid.get(key)
    if existing is None or cell.fitness > existing.fitness:
        grid[key] = cell
        return True
    return False


def compute_qd_score(grid: dict[tuple[int, int], ArchiveCell]) -> float:
    """Sum of fitness across all occupied cells."""
    return sum(cell.fitness for cell in grid.values())


def compute_coverage(grid: dict[tuple[int, int], ArchiveCell], grid_size: int) -> float:
    """Fraction of grid cells that are occupied. 0.0 to 1.0."""
    total = grid_size * grid_size
    if total == 0:
        return 0.0
    return len(grid) / total


def get_elites(grid: dict[tuple[int, int], ArchiveCell], *, top_n: int = 10) -> list[ArchiveCell]:
    """Return the top N cells by fitness, descending."""
    cells = sorted(grid.values(), key=lambda c: c.fitness, reverse=True)
    return cells[:top_n]


def grid_to_heatmap(grid: dict[tuple[int, int], ArchiveCell], grid_size: int) -> list[list[float | None]]:
    """Convert archive to a 2D fitness heatmap. None = empty cell."""
    heatmap: list[list[float | None]] = [[None] * grid_size for _ in range(grid_size)]
    for (x, y), cell in grid.items():
        if 0 <= x < grid_size and 0 <= y < grid_size:
            heatmap[y][x] = cell.fitness
    return heatmap
