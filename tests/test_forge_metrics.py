"""Tests for Forge metrics — oracle-ground-truth F1, Spearman, variance."""

from ollama_queue.forge.metrics import (
    compute_forge_metrics,
    score_variance,
    spearman_rank_correlation,
)


def test_spearman_perfect_positive():
    assert abs(spearman_rank_correlation([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) - 1.0) < 1e-6


def test_spearman_perfect_negative():
    assert abs(spearman_rank_correlation([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) - (-1.0)) < 1e-6


def test_spearman_no_correlation():
    """Random-ish data should be near 0."""
    rho = spearman_rank_correlation([1, 2, 3, 4, 5], [3, 1, 5, 2, 4])
    assert abs(rho) < 0.5


def test_spearman_empty():
    assert spearman_rank_correlation([], []) == 0.0


def test_score_variance_all_same():
    assert score_variance([3, 3, 3, 3, 3]) == 0.0


def test_score_variance_spread():
    v = score_variance([1, 2, 3, 4, 5])
    assert v > 1.0  # should be 2.0


def test_compute_forge_metrics_basic():
    results = [
        {"judge_score": 4, "oracle_score": 4, "embedding_similarity": 0.9},
        {"judge_score": 4, "oracle_score": 5, "embedding_similarity": 0.8},
        {"judge_score": 2, "oracle_score": 1, "embedding_similarity": 0.2},
        {"judge_score": 1, "oracle_score": 1, "embedding_similarity": 0.1},
    ]
    m = compute_forge_metrics(results, positive_threshold=3)
    assert "f1" in m
    assert "precision" in m
    assert "recall" in m
    assert "kappa" in m
    assert "spearman" in m
    assert "score_variance" in m
    assert m["f1"] > 0  # should have some agreement


def test_compute_forge_metrics_perfect():
    results = [
        {"judge_score": 5, "oracle_score": 5, "embedding_similarity": 0.9},
        {"judge_score": 1, "oracle_score": 1, "embedding_similarity": 0.1},
    ]
    m = compute_forge_metrics(results, positive_threshold=3)
    assert m["f1"] == 1.0
    assert m["precision"] == 1.0
    assert m["recall"] == 1.0


def test_compute_forge_metrics_no_oracle():
    """Results without oracle scores return partial metrics."""
    results = [
        {"judge_score": 4, "embedding_similarity": 0.9},
        {"judge_score": 2, "embedding_similarity": 0.2},
    ]
    m = compute_forge_metrics(results, positive_threshold=3)
    assert m["f1"] is None  # can't compute without oracle
    assert m["spearman"] is not None  # can compute from embeddings
    assert m["score_variance"] is not None
