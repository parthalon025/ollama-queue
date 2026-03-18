# tests/test_forge_engine_evolve.py
"""Tests for Forge engine Phase 2 — evolve phase orchestration.

All Phase 2 engine integration tests live here. Each test targets one
function in engine_evolve.py. Functions are pure (no DB/HTTP) — they
take data in and return data out.
"""

from ollama_queue.forge.archive import ArchiveCell
from ollama_queue.forge.engine_evolve import (
    allocate_oracle_budget_thompson,
    assign_pair_splits,
    maybe_evolve,
    populate_archive_cell,
)
from ollama_queue.forge.splits import TEST, TRAIN, VALIDATION

# --- assign_pair_splits ---


def test_assign_pair_splits_deterministic():
    pairs = [
        {"item_a": "1", "item_b": "2", "similarity": 0.8, "quartile": "q1_likely"},
        {"item_a": "3", "item_b": "4", "similarity": 0.3, "quartile": "q3_unlikely"},
    ]
    result = assign_pair_splits(pairs, seed=42)
    assert all("split" in p for p in result)
    assert all(p["split"] in (TRAIN, VALIDATION, TEST) for p in result)

    # Deterministic
    result2 = assign_pair_splits(pairs, seed=42)
    assert [p["split"] for p in result] == [p["split"] for p in result2]


def test_assign_pair_splits_uses_item_a():
    """Split is determined by source item, not pair."""
    pairs = [
        {"item_a": "same", "item_b": "x", "similarity": 0.5, "quartile": "q2_maybe"},
        {"item_a": "same", "item_b": "y", "similarity": 0.3, "quartile": "q3_unlikely"},
    ]
    result = assign_pair_splits(pairs, seed=42)
    assert result[0]["split"] == result[1]["split"]


# --- populate_archive_cell ---


def test_populate_archive_creates_cell():
    results = [
        {"judge_reasoning": "Good match because error handling applies.", "judge_score": 4},
        {"judge_reasoning": "Partial overlap with testing concern.", "judge_score": 3},
    ]
    metrics = {"f1": 0.75}
    cell = populate_archive_cell(results, metrics, variant_id="A", grid_size=10)
    assert isinstance(cell, ArchiveCell)
    assert cell.variant_id == "A"
    assert cell.fitness == 0.75
    assert 0 <= cell.x_bin < 10
    assert 0 <= cell.y_bin < 10


def test_populate_archive_no_results():
    cell = populate_archive_cell([], {"f1": 0.0}, variant_id="A", grid_size=10)
    assert cell.x_bin == 0
    assert cell.y_bin == 0


# --- allocate_oracle_budget_thompson ---


def test_allocate_budget_with_quartiles():
    results = [
        {"quartile": "q1_likely", "id": 1, "judge_score": 3},
        {"quartile": "q1_likely", "id": 2, "judge_score": 4},
        {"quartile": "q2_maybe", "id": 3, "judge_score": 3},
        {"quartile": "q3_unlikely", "id": 4, "judge_score": 2},
        {"quartile": "q4_none", "id": 5, "judge_score": 1},
    ]
    sample = allocate_oracle_budget_thompson(
        results,
        total_budget=4,
        thompson_state=None,
        seed=42,
    )
    assert len(sample) <= 4
    assert all(r in results for r in sample)


def test_allocate_budget_returns_empty_for_no_results():
    sample = allocate_oracle_budget_thompson([], total_budget=10, thompson_state=None, seed=42)
    assert sample == []


# --- maybe_evolve ---


def _cell(x, y, variant, fitness, prompt="Test prompt."):
    return ArchiveCell(
        x_bin=x, y_bin=y, x_value=float(x), y_value=float(y), variant_id=variant, fitness=fitness, prompt_text=prompt
    )


def test_maybe_evolve_enough_cells():
    grid = {(i, 0): _cell(i, 0, f"v{i}", 0.5 + i * 0.1, f"Rule {i}.") for i in range(5)}
    offspring = maybe_evolve(grid, n_offspring=3, min_archive_size=3, seed=42)
    assert len(offspring) == 3


def test_maybe_evolve_too_few_cells():
    grid = {(0, 0): _cell(0, 0, "v0", 0.5, "One rule.")}
    offspring = maybe_evolve(grid, n_offspring=3, min_archive_size=3, seed=42)
    assert offspring == []
