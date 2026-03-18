# tests/test_forge_archive.py
"""Tests for Forge MAP-Elites archive."""

from ollama_queue.forge.archive import (
    ArchiveCell,
    compute_coverage,
    compute_qd_score,
    get_elites,
    grid_to_heatmap,
    try_insert,
)


def _cell(x, y, fitness, variant="v1"):
    return ArchiveCell(x_bin=x, y_bin=y, x_value=float(x), y_value=float(y), variant_id=variant, fitness=fitness)


def test_try_insert_empty_cell():
    grid = {}
    assert try_insert(grid, _cell(0, 0, 0.8)) is True
    assert (0, 0) in grid


def test_try_insert_higher_fitness_replaces():
    grid = {}
    try_insert(grid, _cell(0, 0, 0.5, "old"))
    assert try_insert(grid, _cell(0, 0, 0.8, "new")) is True
    assert grid[(0, 0)].variant_id == "new"


def test_try_insert_lower_fitness_rejected():
    grid = {}
    try_insert(grid, _cell(0, 0, 0.8, "old"))
    assert try_insert(grid, _cell(0, 0, 0.5, "new")) is False
    assert grid[(0, 0)].variant_id == "old"


def test_compute_qd_score():
    grid = {}
    try_insert(grid, _cell(0, 0, 0.5))
    try_insert(grid, _cell(1, 0, 0.7))
    try_insert(grid, _cell(0, 1, 0.3))
    assert compute_qd_score(grid) == 1.5


def test_compute_qd_score_empty():
    assert compute_qd_score({}) == 0.0


def test_compute_coverage():
    grid = {}
    try_insert(grid, _cell(0, 0, 0.5))
    try_insert(grid, _cell(1, 1, 0.5))
    assert compute_coverage(grid, grid_size=10) == 2 / 100


def test_compute_coverage_full():
    grid = {}
    for x in range(3):
        for y in range(3):
            try_insert(grid, _cell(x, y, 0.5))
    assert compute_coverage(grid, grid_size=3) == 1.0


def test_get_elites():
    grid = {}
    for i in range(5):
        try_insert(grid, _cell(i, 0, i * 0.1 + 0.1, f"v{i}"))
    top = get_elites(grid, top_n=3)
    assert len(top) == 3
    assert top[0].fitness > top[1].fitness > top[2].fitness


def test_grid_to_heatmap():
    grid = {}
    try_insert(grid, _cell(0, 0, 0.5))
    try_insert(grid, _cell(2, 1, 0.8))
    hm = grid_to_heatmap(grid, grid_size=3)
    assert hm[0][0] == 0.5
    assert hm[1][2] == 0.8
    assert hm[0][1] is None
