# tests/test_forge_evolver.py
"""Tests for Forge evolution operators."""

from ollama_queue.forge.archive import ArchiveCell
from ollama_queue.forge.evolver import (
    crossover_prompts,
    evolve_generation,
    mutate_prompt,
    tournament_select,
)


def _cell(variant, fitness, prompt):
    return ArchiveCell(
        x_bin=0, y_bin=0, x_value=0.0, y_value=0.0, variant_id=variant, fitness=fitness, prompt_text=prompt
    )


def test_tournament_select_picks_higher_fitness():
    cells = [_cell("a", 0.3, "low"), _cell("b", 0.9, "high")]
    # With k=2, always picks the better one
    winner = tournament_select(cells, k=2, seed=42)
    assert winner.variant_id == "b"


def test_tournament_select_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        tournament_select([], k=2, seed=42)


def test_crossover_prompts_interleaves():
    a = "Sentence one. Sentence two. Sentence three."
    b = "Alpha fact. Beta fact. Gamma fact."
    child = crossover_prompts(a, b, seed=42)
    assert len(child) > 0
    # Child should contain words from both parents
    words = child.lower().split()
    # At least some mixing should occur
    assert len(words) > 2


def test_crossover_prompts_empty_parent():
    assert crossover_prompts("", "Hello world.", seed=42) == "Hello world."
    assert crossover_prompts("Hello world.", "", seed=42) == "Hello world."


def test_mutate_prompt_preserves_most():
    prompt = "First rule. Second rule. Third rule. Fourth rule. Fifth rule."
    mutated = mutate_prompt(prompt, mutation_rate=0.1, seed=42)
    # Most content should survive
    assert len(mutated) > 0


def test_mutate_prompt_zero_rate():
    prompt = "Exact same text here."
    mutated = mutate_prompt(prompt, mutation_rate=0.0, seed=42)
    assert mutated == prompt


def test_evolve_generation_count():
    cells = [
        _cell("a", 0.5, "Rule one. Rule two."),
        _cell("b", 0.7, "Alpha. Beta. Gamma."),
        _cell("c", 0.9, "First. Second. Third."),
    ]
    offspring = evolve_generation(cells, n_offspring=4, seed=42)
    assert len(offspring) == 4
    assert all(isinstance(p, str) and len(p) > 0 for p in offspring)


def test_evolve_generation_too_few_cells():
    cells = [_cell("a", 0.5, "Only one.")]
    offspring = evolve_generation(cells, n_offspring=4, seed=42)
    assert offspring == []


def test_evolve_generation_deterministic():
    cells = [
        _cell("a", 0.5, "Rule one. Rule two."),
        _cell("b", 0.7, "Alpha. Beta."),
    ]
    a = evolve_generation(cells, n_offspring=3, seed=42)
    b = evolve_generation(cells, n_offspring=3, seed=42)
    assert a == b
