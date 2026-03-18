"""Tests for Forge oracle — scoring, kappa, per-group breakdown."""

from ollama_queue.forge.oracle import (
    compute_kappa,
    compute_per_group_kappa,
    select_oracle_sample,
)


def test_compute_kappa_perfect_agreement():
    judge = [1, 2, 3, 4, 5]
    oracle = [1, 2, 3, 4, 5]
    k = compute_kappa(judge, oracle, tolerance=0)
    assert k == 1.0


def test_compute_kappa_within_tolerance():
    judge = [1, 2, 3, 4, 5]
    oracle = [2, 3, 4, 5, 5]  # all within 1
    k = compute_kappa(judge, oracle, tolerance=1)
    assert k == 1.0


def test_compute_kappa_no_agreement():
    judge = [1, 1, 1, 1, 1]
    oracle = [5, 5, 5, 5, 5]
    k = compute_kappa(judge, oracle, tolerance=0)
    assert k < 0  # worse than chance


def test_compute_kappa_empty():
    assert compute_kappa([], [], tolerance=1) == 0.0


def test_select_oracle_sample_respects_fraction():
    results = [{"id": i, "judge_score": 3} for i in range(100)]
    sample = select_oracle_sample(results, fraction=0.2, budget=50, seed=42)
    assert len(sample) == 20  # 0.2 * 100 = 20, under budget


def test_select_oracle_sample_respects_budget():
    results = [{"id": i, "judge_score": 3} for i in range(100)]
    sample = select_oracle_sample(results, fraction=0.5, budget=10, seed=42)
    assert len(sample) == 10  # 0.5 * 100 = 50, capped to budget=10


def test_select_oracle_sample_deterministic():
    results = [{"id": i, "judge_score": 3} for i in range(50)]
    a = select_oracle_sample(results, fraction=0.2, budget=20, seed=42)
    b = select_oracle_sample(results, fraction=0.2, budget=20, seed=42)
    assert [r["id"] for r in a] == [r["id"] for r in b]


def test_compute_per_group_kappa():
    results = [
        {"group": "async", "judge_score": 4, "oracle_score": 4},
        {"group": "async", "judge_score": 3, "oracle_score": 3},
        {"group": "async", "judge_score": 2, "oracle_score": 2},
        {"group": "error", "judge_score": 4, "oracle_score": 1},
        {"group": "error", "judge_score": 3, "oracle_score": 1},
        {"group": "error", "judge_score": 2, "oracle_score": 1},
    ]
    breakdown = compute_per_group_kappa(results, tolerance=1)
    assert "async" in breakdown
    assert "error" in breakdown
    assert breakdown["async"]["kappa"] == 1.0  # perfect
    assert breakdown["error"]["kappa"] < 0.5  # poor


def test_compute_per_group_kappa_no_groups():
    results = [
        {"judge_score": 4, "oracle_score": 4},
        {"judge_score": 3, "oracle_score": 3},
    ]
    breakdown = compute_per_group_kappa(results, tolerance=1)
    assert breakdown == {}  # no group field = no breakdown
