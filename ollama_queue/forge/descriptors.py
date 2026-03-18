# ollama_queue/forge/descriptors.py
"""Forge behavior descriptors — quantify behavioral properties of judge outputs.

Default axes: output_length (normalized response verbosity) and
vocabulary_diversity (unique word ratio). Data sources can provide
custom axes via get_behavior_descriptors().
"""

from __future__ import annotations

DEFAULT_GRID_SIZE = 10

_DEFAULT_AXES = {
    "x": {"name": "output_length", "range": [0.0, 1.0], "description": "Normalized response length"},
    "y": {"name": "vocabulary_diversity", "range": [0.0, 1.0], "description": "Unique word ratio"},
}


def get_descriptor_axes(data_source=None) -> dict:
    """Get behavior descriptor axis definitions.

    Tries data_source.get_behavior_descriptors() first, falls back to defaults.
    """
    if data_source is not None:
        try:
            axes = data_source.get_behavior_descriptors()
            if axes and "x" in axes and "y" in axes:
                return axes
        except (AttributeError, NotImplementedError):
            pass
    return _DEFAULT_AXES


def compute_output_length(texts: list[str], *, max_length: int = 500) -> float:
    """Normalized average output length. Returns 0.0 to 1.0."""
    if not texts:
        return 0.0
    avg_len = sum(len(t) for t in texts) / len(texts)
    return min(1.0, avg_len / max_length)


def compute_vocabulary_diversity(texts: list[str]) -> float:
    """Unique word ratio across all texts. Returns 0.0 to 1.0."""
    if not texts:
        return 0.0
    words = []
    for t in texts:
        words.extend(t.lower().split())
    if not words:
        return 0.0
    return len(set(words)) / len(words)


def compute_default_descriptors(results: list[dict]) -> dict[str, float]:
    """Compute default behavior descriptors from judge results.

    Returns {"output_length": float, "vocabulary_diversity": float}.
    """
    texts = [r.get("judge_reasoning") or r.get("reasoning") or "" for r in results]
    texts = [t for t in texts if t]
    return {
        "output_length": compute_output_length(texts),
        "vocabulary_diversity": compute_vocabulary_diversity(texts),
    }


def normalize_to_bin(value: float, range_min: float, range_max: float, grid_size: int) -> int:
    """Map a continuous value to a discrete bin index (0 to grid_size-1)."""
    if range_max <= range_min:
        return 0
    normalized = (value - range_min) / (range_max - range_min)
    normalized = max(0.0, min(1.0, normalized))
    bin_idx = int(normalized * grid_size)
    return min(bin_idx, grid_size - 1)
