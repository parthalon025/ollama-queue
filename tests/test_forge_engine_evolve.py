"""Tests for forge engine_evolve — Phase 2 orchestration functions."""

from __future__ import annotations

import pytest

from ollama_queue.forge.engine_evolve import (
    allocate_oracle_budget_thompson,
    assign_pair_splits,
    maybe_evolve,
    populate_archive_cell,
    run_evolve_phase,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockDb:
    """Minimal mock DB for run_evolve_phase tests."""

    def get_forge_archive_grid(self) -> list:
        return []

    def upsert_forge_archive_cell(self, **kwargs) -> None:
        pass

    def load_forge_thompson_state(self) -> None:
        return None

    def save_forge_thompson_state(self, state) -> None:
        pass


# ---------------------------------------------------------------------------
# assign_pair_splits
# ---------------------------------------------------------------------------


def test_assign_pair_splits_adds_split_field():
    pairs = [{"item_a": "x", "item_b": "y"}]
    result = assign_pair_splits(pairs, seed=0)
    assert "split" in result[0]
    assert result[0]["split"] in ("train", "validation", "test")


def test_assign_pair_splits_deterministic():
    pairs = [{"item_a": "hello", "item_b": "world"}]
    r1 = assign_pair_splits(list(pairs), seed=42)
    r2 = assign_pair_splits(list(pairs), seed=42)
    assert r1[0]["split"] == r2[0]["split"]


def test_assign_pair_splits_empty():
    assert assign_pair_splits([]) == []


# ---------------------------------------------------------------------------
# populate_archive_cell
# ---------------------------------------------------------------------------


def test_populate_archive_cell_basic():
    results = [
        {"item_a": "a", "item_b": "b", "judge_score": 4, "quartile": "Q1", "calibrated_score": 0.8},
    ]
    metrics = {"f1": 0.75, "precision": 0.8, "recall": 0.7}
    cell = populate_archive_cell(results, metrics, variant_id="A")
    assert cell.variant_id == "A"
    assert cell.fitness == pytest.approx(0.75)
    assert cell.x_bin >= 0
    assert cell.y_bin >= 0


def test_populate_archive_cell_empty_results():
    cell = populate_archive_cell([], {}, variant_id="B")
    assert cell.variant_id == "B"
    assert cell.fitness == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# allocate_oracle_budget_thompson
# ---------------------------------------------------------------------------


def test_allocate_oracle_budget_thompson_empty():
    assert allocate_oracle_budget_thompson([], total_budget=10) == []


def test_allocate_oracle_budget_thompson_basic():
    results = [
        {"quartile": "Q1", "judge_score": 4},
        {"quartile": "Q2", "judge_score": 3},
        {"quartile": "Q3", "judge_score": 2},
    ]
    sample = allocate_oracle_budget_thompson(results, total_budget=3, seed=0)
    assert isinstance(sample, list)
    assert len(sample) <= 3


def test_allocate_oracle_budget_thompson_budget_respected():
    results = [{"quartile": "Q1", "judge_score": i} for i in range(20)]
    sample = allocate_oracle_budget_thompson(results, total_budget=5, seed=42)
    assert len(sample) <= 5


# ---------------------------------------------------------------------------
# maybe_evolve
# ---------------------------------------------------------------------------


def test_maybe_evolve_below_min_archive():
    """Returns empty list when archive has fewer than min_archive_size cells."""
    from ollama_queue.forge.archive import ArchiveCell

    grid = {
        (0, 0): ArchiveCell(x_bin=0, y_bin=0, x_value=0.1, y_value=0.1, variant_id="A", fitness=0.7),
        (1, 0): ArchiveCell(x_bin=1, y_bin=0, x_value=0.5, y_value=0.2, variant_id="B", fitness=0.6),
    }
    result = maybe_evolve(grid, min_archive_size=3, seed=0)
    assert result == []


def test_maybe_evolve_triggers_when_sufficient():
    """Returns a list (possibly empty) when archive meets min_archive_size."""
    from ollama_queue.forge.archive import ArchiveCell

    # Cells with prompt_text so crossover can produce non-empty offspring
    prompts = [
        "Evaluate the relevance of this lesson. Focus on applicability.",
        "Score how well the principle applies. Consider edge cases carefully.",
        "Does this lesson transfer? Assess both domains thoroughly.",
        "Rate the knowledge transfer. Think about context and specificity.",
        "Judge the applicability. Weight recent evidence more heavily.",
    ]
    grid = {
        (i, 0): ArchiveCell(
            x_bin=i,
            y_bin=0,
            x_value=float(i) / 10,
            y_value=0.5,
            variant_id=f"V{i}",
            fitness=0.5 + i * 0.1,
            prompt_text=prompts[i],
        )
        for i in range(5)
    }
    result = maybe_evolve(grid, n_offspring=2, min_archive_size=3, seed=7)
    assert isinstance(result, list)
    # With valid prompt_text, offspring should be non-empty strings when produced
    for item in result:
        assert isinstance(item, str)
        assert item.strip()


# ---------------------------------------------------------------------------
# run_evolve_phase — integration
# ---------------------------------------------------------------------------


def test_run_evolve_phase_succeeds():
    """run_evolve_phase completes without raising on a valid mock DB."""
    db = _MockDb()
    run = {"variant_id": "A", "seed": 42}
    results = [
        {
            "item_a": "x",
            "item_b": "y",
            "judge_score": 4,
            "quartile": "Q1",
            "calibrated_score": 0.8,
        }
    ]
    metrics = {"f1": 0.8, "precision": 0.8, "recall": 0.8}
    # Must not raise
    run_evolve_phase(db=db, run_id=1, run=run, results=results, metrics=metrics)


def test_run_evolve_phase_never_raises():
    """run_evolve_phase swallows exceptions from a broken DB."""

    class _BadDb:
        def get_forge_archive_grid(self):
            raise RuntimeError("db failed")

    run = {"variant_id": "A", "seed": 0}
    # Must not propagate RuntimeError
    run_evolve_phase(db=_BadDb(), run_id=99, run=run, results=[], metrics={})


def test_run_evolve_phase_records_archive_cell():
    """upsert_forge_archive_cell is called when try_insert succeeds."""
    upsert_calls = []

    class _TrackingDb(_MockDb):
        def upsert_forge_archive_cell(self, **kwargs):
            upsert_calls.append(kwargs)

    db = _TrackingDb()
    run = {"variant_id": "Z", "seed": 0}
    results = [{"item_a": "a", "item_b": "b", "judge_score": 5, "quartile": "Q1", "calibrated_score": 1.0}]
    metrics = {"f1": 0.9}
    run_evolve_phase(db=db, run_id=5, run=run, results=results, metrics=metrics)
    # Cell should have been inserted since the grid starts empty
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["variant_id"] == "Z"
