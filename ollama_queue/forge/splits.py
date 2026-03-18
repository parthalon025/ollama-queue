# ollama_queue/forge/splits.py
"""Forge splits — deterministic train/validation/test assignment.

Items are assigned to splits using SHA-256 hash of (seed, item_id),
ensuring stable assignment: adding items doesn't reshuffle existing ones.
Default ratio: 60% train / 20% validation / 20% test.
"""

from __future__ import annotations

import hashlib

TRAIN = "train"
VALIDATION = "validation"
TEST = "test"


def assign_split(
    item_id: str,
    *,
    seed: int = 0,
    train_frac: float = 0.6,
    val_frac: float = 0.2,
) -> str:
    """Deterministic split assignment for one item.

    Uses SHA-256 hash mapped to [0, 1) for uniform distribution.
    """
    h = hashlib.sha256(f"{seed}:{item_id}".encode()).hexdigest()
    bucket = int(h[:8], 16) / 0xFFFFFFFF
    if bucket < train_frac:
        return TRAIN
    if bucket < train_frac + val_frac:
        return VALIDATION
    return TEST


def split_items(
    items: list[dict],
    *,
    seed: int = 0,
    train_frac: float = 0.6,
    val_frac: float = 0.2,
) -> dict[str, list[dict]]:
    """Partition items into train/validation/test splits."""
    result: dict[str, list[dict]] = {TRAIN: [], VALIDATION: [], TEST: []}
    for item in items:
        split = assign_split(item["id"], seed=seed, train_frac=train_frac, val_frac=val_frac)
        result[split].append(item)
    return result
