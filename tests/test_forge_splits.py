# tests/test_forge_splits.py
"""Tests for Forge train/validation/test splitting."""

from ollama_queue.forge.splits import TEST, TRAIN, VALIDATION, assign_split, split_items


def test_assign_split_deterministic():
    """Same item + seed always gets same split."""
    a = assign_split("item-42", seed=0)
    b = assign_split("item-42", seed=0)
    assert a == b


def test_assign_split_different_seeds():
    """Different seeds may produce different assignments."""
    results = {assign_split("item-1", seed=s) for s in range(20)}
    # With 20 different seeds, very likely to see at least 2 different splits
    assert len(results) >= 2


def test_assign_split_returns_valid_split():
    for i in range(100):
        split = assign_split(f"item-{i}", seed=42)
        assert split in (TRAIN, VALIDATION, TEST)


def test_split_items_approximate_ratios():
    items = [{"id": str(i)} for i in range(1000)]
    splits = split_items(items, seed=42)
    # Allow ±5% tolerance on 60/20/20
    assert 550 <= len(splits[TRAIN]) <= 650
    assert 150 <= len(splits[VALIDATION]) <= 250
    assert 150 <= len(splits[TEST]) <= 250


def test_split_items_no_overlap():
    items = [{"id": str(i)} for i in range(100)]
    splits = split_items(items, seed=42)
    all_ids = set()
    for split_items_list in splits.values():
        ids = {item["id"] for item in split_items_list}
        assert ids.isdisjoint(all_ids), "Items appear in multiple splits"
        all_ids.update(ids)


def test_split_items_stable_on_addition():
    """Adding items doesn't reshuffle existing assignments."""
    items_small = [{"id": str(i)} for i in range(50)]
    items_large = [{"id": str(i)} for i in range(100)]

    splits_small = split_items(items_small, seed=42)
    splits_large = split_items(items_large, seed=42)

    # Every item from small set should be in the same split in large set
    for split_name in (TRAIN, VALIDATION, TEST):
        small_ids = {item["id"] for item in splits_small[split_name]}
        large_ids = {item["id"] for item in splits_large[split_name]}
        assert small_ids.issubset(large_ids)


def test_split_items_custom_ratios():
    items = [{"id": str(i)} for i in range(1000)]
    splits = split_items(items, seed=42, train_frac=0.8, val_frac=0.1)
    assert len(splits[TRAIN]) > len(splits[VALIDATION])
    assert len(splits[TRAIN]) > len(splits[TEST])
