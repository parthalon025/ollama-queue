# ollama_queue/forge/evolver.py
"""Forge evolver — variant creation through tournament selection + crossover + mutation.

Creates new prompt variants by combining and mutating successful strategies
from the MAP-Elites archive. Each offspring enters the next Forge cycle.
"""

from __future__ import annotations

import random
import re

from ollama_queue.forge.archive import ArchiveCell

_SENTENCE_RE = re.compile(r"(?<=[.!?\n])\s+")


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = _SENTENCE_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def tournament_select(
    cells: list[ArchiveCell],
    *,
    k: int = 2,
    seed: int | None = None,
) -> ArchiveCell:
    """Select a parent via k-tournament. Higher fitness wins."""
    if not cells:
        raise ValueError("Cannot select from empty archive")
    rng = random.Random(seed)
    contestants = rng.sample(cells, min(k, len(cells)))
    return max(contestants, key=lambda c: c.fitness)


def crossover_prompts(
    parent_a: str,
    parent_b: str,
    *,
    seed: int | None = None,
) -> str:
    """Sentence-level crossover between two prompt strings."""
    rng = random.Random(seed)
    sents_a = _split_sentences(parent_a)
    sents_b = _split_sentences(parent_b)

    if not sents_a:
        return parent_b
    if not sents_b:
        return parent_a

    result = []
    max_len = max(len(sents_a), len(sents_b))
    for i in range(max_len):
        if rng.random() < 0.5 and i < len(sents_a):
            result.append(sents_a[i])
        elif i < len(sents_b):
            result.append(sents_b[i])
        elif i < len(sents_a):
            result.append(sents_a[i])

    return " ".join(result)


def mutate_prompt(
    prompt: str,
    *,
    mutation_rate: float = 0.15,
    seed: int | None = None,
) -> str:
    """Apply small random mutations to a prompt string."""
    rng = random.Random(seed)
    sentences = _split_sentences(prompt)
    if not sentences:
        return prompt

    mutated = list(sentences)
    for i in range(len(mutated)):
        if rng.random() < mutation_rate:
            op = rng.choice(["emphasis", "remove", "swap"])
            if op == "emphasis" and mutated[i]:
                mutated[i] = mutated[i].rstrip(".") + " — this is critical."
            elif op == "remove" and len(mutated) > 2:
                mutated[i] = ""
            elif op == "swap" and len(mutated) > 1:
                j = rng.randint(0, len(mutated) - 1)
                mutated[i], mutated[j] = mutated[j], mutated[i]

    return " ".join(s for s in mutated if s).strip()


def evolve_generation(
    cells: list[ArchiveCell],
    *,
    n_offspring: int = 4,
    mutation_rate: float = 0.15,
    seed: int | None = None,
) -> list[str]:
    """Create n_offspring new prompt variants from the archive.

    Returns list of new prompt strings (not yet registered as variants).
    """
    if len(cells) < 2:
        return []
    rng = random.Random(seed)
    offspring = []

    for _ in range(n_offspring):
        parent_a = tournament_select(cells, k=2, seed=rng.randint(0, 2**31))
        parent_b = tournament_select(cells, k=2, seed=rng.randint(0, 2**31))
        attempts = 0
        while parent_b.variant_id == parent_a.variant_id and attempts < 5:
            parent_b = tournament_select(cells, k=2, seed=rng.randint(0, 2**31))
            attempts += 1

        child = crossover_prompts(parent_a.prompt_text or "", parent_b.prompt_text or "", seed=rng.randint(0, 2**31))
        child = mutate_prompt(child, mutation_rate=mutation_rate, seed=rng.randint(0, 2**31))

        if child.strip():
            offspring.append(child)

    return offspring
