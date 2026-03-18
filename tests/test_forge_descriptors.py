# tests/test_forge_descriptors.py
"""Tests for Forge behavior descriptors."""

from ollama_queue.forge.descriptors import (
    compute_default_descriptors,
    compute_output_length,
    compute_vocabulary_diversity,
    get_descriptor_axes,
    normalize_to_bin,
)


def test_compute_output_length_empty():
    assert compute_output_length([]) == 0.0


def test_compute_output_length_normalized():
    texts = ["hello world", "this is a test sentence with more words"]
    val = compute_output_length(texts, max_length=500)
    assert 0.0 < val < 1.0


def test_compute_output_length_capped_at_one():
    texts = ["x" * 1000]
    assert compute_output_length(texts, max_length=100) == 1.0


def test_vocabulary_diversity_empty():
    assert compute_vocabulary_diversity([]) == 0.0


def test_vocabulary_diversity_all_unique():
    val = compute_vocabulary_diversity(["alpha bravo charlie delta echo"])
    assert val == 1.0


def test_vocabulary_diversity_all_same():
    val = compute_vocabulary_diversity(["the the the the the"])
    assert val == 0.2  # 1 unique / 5 total


def test_compute_default_descriptors():
    results = [
        {"judge_reasoning": "This principle clearly applies because the error handling pattern matches."},
        {"judge_reasoning": "Partial match — the lesson addresses a different concern but shares the approach."},
    ]
    desc = compute_default_descriptors(results)
    assert "output_length" in desc
    assert "vocabulary_diversity" in desc
    assert 0.0 <= desc["output_length"] <= 1.0
    assert 0.0 <= desc["vocabulary_diversity"] <= 1.0


def test_normalize_to_bin_edges():
    assert normalize_to_bin(0.0, 0.0, 1.0, 10) == 0
    assert normalize_to_bin(1.0, 0.0, 1.0, 10) == 9  # clamped
    assert normalize_to_bin(0.5, 0.0, 1.0, 10) == 5


def test_normalize_to_bin_out_of_range():
    assert normalize_to_bin(-0.5, 0.0, 1.0, 10) == 0
    assert normalize_to_bin(1.5, 0.0, 1.0, 10) == 9


def test_get_descriptor_axes_default():
    axes = get_descriptor_axes(data_source=None)
    assert "x" in axes and "y" in axes
    assert axes["x"]["name"] == "output_length"
    assert axes["y"]["name"] == "vocabulary_diversity"


class _CustomSource:
    def get_behavior_descriptors(self):
        return {
            "x": {"name": "specificity", "range": [0, 10]},
            "y": {"name": "domain_coverage", "range": [0, 10]},
        }


def test_get_descriptor_axes_custom():
    axes = get_descriptor_axes(data_source=_CustomSource())
    assert axes["x"]["name"] == "specificity"
