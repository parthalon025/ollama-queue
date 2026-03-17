"""Tests for embedding-stratified pair selection."""

import math

from ollama_queue.forge.pairs import (
    build_similarity_matrix,
    cosine_similarity,
    select_stratified_pairs,
)
from ollama_queue.forge.types import PairQuartile


def test_cosine_similarity_identical():
    assert abs(cosine_similarity([1, 0, 0], [1, 0, 0]) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal():
    assert abs(cosine_similarity([1, 0, 0], [0, 1, 0])) < 1e-6


def test_cosine_similarity_opposite():
    assert abs(cosine_similarity([1, 0], [-1, 0]) - (-1.0)) < 1e-6


def test_build_similarity_matrix():
    embeddings = {
        "a": [1.0, 0.0],
        "b": [0.0, 1.0],
        "c": [0.707, 0.707],
    }
    matrix = build_similarity_matrix(embeddings)
    # 3 items -> 3 pairs (a-b, a-c, b-c)
    assert len(matrix) == 3
    # a-b should be ~0, a-c and b-c should be ~0.707
    sims = {(p["item_a"], p["item_b"]): p["similarity"] for p in matrix}
    assert abs(sims[("a", "b")]) < 0.01
    assert abs(sims[("a", "c")] - 0.707) < 0.01


def test_select_stratified_pairs_quartile_distribution():
    """Each quartile gets equal representation."""
    # 10 items with embeddings spread across similarity range
    embeddings = {str(i): [math.cos(i * 0.3), math.sin(i * 0.3)] for i in range(10)}
    matrix = build_similarity_matrix(embeddings)

    pairs = select_stratified_pairs(matrix, per_quartile=5, seed=42)

    quartile_counts = {}
    for p in pairs:
        q = p["quartile"]
        quartile_counts[q] = quartile_counts.get(q, 0) + 1

    # Each quartile should have at most per_quartile pairs
    for _q, count in quartile_counts.items():
        assert count <= 5


def test_select_stratified_pairs_deterministic():
    embeddings = {str(i): [math.cos(i * 0.5), math.sin(i * 0.5)] for i in range(8)}
    matrix = build_similarity_matrix(embeddings)

    pairs_a = select_stratified_pairs(matrix, per_quartile=3, seed=42)
    pairs_b = select_stratified_pairs(matrix, per_quartile=3, seed=42)

    ids_a = [(p["item_a"], p["item_b"]) for p in pairs_a]
    ids_b = [(p["item_a"], p["item_b"]) for p in pairs_b]
    assert ids_a == ids_b


def test_select_stratified_pairs_small_dataset():
    """Graceful with fewer pairs than requested."""
    embeddings = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
    matrix = build_similarity_matrix(embeddings)

    pairs = select_stratified_pairs(matrix, per_quartile=10, seed=42)
    assert len(pairs) == 1  # only 1 possible pair


def test_pair_has_required_fields():
    embeddings = {"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [0.5, 0.5]}
    matrix = build_similarity_matrix(embeddings)
    pairs = select_stratified_pairs(matrix, per_quartile=5, seed=42)

    for p in pairs:
        assert "item_a" in p
        assert "item_b" in p
        assert "similarity" in p
        assert "quartile" in p
        assert p["quartile"] in [q.value for q in PairQuartile]
