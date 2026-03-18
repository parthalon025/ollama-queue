"""Embedding-stratified pair selection for Forge evaluation.

Selects diverse pairs across the full similarity spectrum so the judge
is tested on easy matches, hard matches, and everything in between.
No cluster labels required.
"""

from __future__ import annotations

import math
import random

from ollama_queue.forge.types import PairQuartile

# Quartile boundaries (inclusive lower, exclusive upper)
_QUARTILE_BOUNDS: list[tuple[float, float, PairQuartile]] = [
    (0.75, 1.01, PairQuartile.LIKELY),
    (0.50, 0.75, PairQuartile.MAYBE),
    (0.25, 0.50, PairQuartile.UNLIKELY),
    (-1.01, 0.25, PairQuartile.NONE),
]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns -1.0 to 1.0."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_similarity_matrix(
    embeddings: dict[str, list[float]],
) -> list[dict]:
    """Compute pairwise cosine similarity for all item pairs.

    Returns list of {item_a, item_b, similarity} dicts, sorted by
    similarity descending.
    """
    ids = sorted(embeddings.keys())
    pairs = []
    for i, id_a in enumerate(ids):
        for id_b in ids[i + 1 :]:
            sim = cosine_similarity(embeddings[id_a], embeddings[id_b])
            pairs.append({"item_a": id_a, "item_b": id_b, "similarity": sim})
    pairs.sort(key=lambda p: p["similarity"], reverse=True)
    return pairs


def select_stratified_pairs(
    similarity_matrix: list[dict],
    *,
    per_quartile: int = 20,
    seed: int | None = None,
) -> list[dict]:
    """Select pairs stratified across 4 similarity quartiles.

    Returns up to per_quartile pairs from each quartile, each annotated
    with its quartile label. Deterministic when seed is provided.
    """
    rng = random.Random(seed)  # noqa: S311 — not crypto, deterministic sampling only

    # Bucket pairs by quartile
    buckets: dict[str, list[dict]] = {q.value: [] for q in PairQuartile}
    for pair in similarity_matrix:
        sim = pair["similarity"]
        for low, high, quartile in _QUARTILE_BOUNDS:
            if low <= sim < high:
                buckets[quartile.value].append(pair)
                break

    # Sample from each bucket
    selected = []
    for quartile in PairQuartile:
        bucket = buckets[quartile.value]
        n = min(per_quartile, len(bucket))
        sampled = rng.sample(bucket, n) if n > 0 else []
        for pair in sampled:
            selected.append({**pair, "quartile": quartile.value})

    return selected
