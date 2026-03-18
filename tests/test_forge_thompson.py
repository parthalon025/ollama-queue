# tests/test_forge_thompson.py
"""Tests for Forge Thompson Sampling oracle budget allocation."""

from ollama_queue.forge.thompson import ThompsonBudget


def test_allocate_budget_uniform_initial():
    """With no observations, budget should be roughly uniform."""
    tb = ThompsonBudget()
    alloc = tb.allocate_budget(20, ["q1", "q2", "q3", "q4"], seed=42)
    assert sum(alloc.values()) == 20
    assert all(v >= 1 for v in alloc.values())


def test_allocate_budget_favors_uncertain():
    """Category with worse agreement should get more budget."""
    tb = ThompsonBudget()
    # q1 has high agreement
    for _ in range(20):
        tb.record_agreement("q1", agreed=True)
    # q2 has low agreement
    for _ in range(20):
        tb.record_agreement("q2", agreed=False)
    # q3 and q4 have no observations (maximally uncertain)

    alloc = tb.allocate_budget(40, ["q1", "q2", "q3", "q4"], seed=42)
    assert sum(alloc.values()) == 40
    # q1 (high agreement, low uncertainty) should get less than q2 (low agreement)
    assert alloc["q1"] <= alloc["q2"]


def test_allocate_budget_minimum_one_each():
    tb = ThompsonBudget()
    alloc = tb.allocate_budget(4, ["a", "b", "c", "d"], seed=42)
    assert all(v >= 1 for v in alloc.values())


def test_allocate_budget_insufficient():
    """When budget < categories, some get 0."""
    tb = ThompsonBudget()
    alloc = tb.allocate_budget(2, ["a", "b", "c", "d"], seed=42)
    assert sum(alloc.values()) == 2


def test_record_agreement_updates_state():
    tb = ThompsonBudget()
    tb.record_agreement("q1", agreed=True)
    tb.record_agreement("q1", agreed=False)
    stats = tb.get_stats()
    assert stats["q1"]["observations"] == 2


def test_discount_factor_decays():
    tb = ThompsonBudget(discount=0.9)
    for _ in range(10):
        tb.record_agreement("q1", agreed=True)
    stats = tb.get_stats()
    # Alpha should be less than 1.0 + 10 due to discount
    assert stats["q1"]["alpha"] < 11.0


def test_window_cap():
    tb = ThompsonBudget(window=5)
    for _ in range(20):
        tb.record_agreement("q1", agreed=True)
    stats = tb.get_stats()
    assert stats["q1"]["observations"] <= 5


def test_get_state_roundtrip():
    tb = ThompsonBudget()
    tb.record_agreement("q1", agreed=True)
    tb.record_agreement("q2", agreed=False)
    state = tb.get_state()

    tb2 = ThompsonBudget()
    tb2.load_state(state)
    assert tb2.get_stats() == tb.get_stats()


def test_allocate_budget_deterministic():
    tb = ThompsonBudget()
    a = tb.allocate_budget(20, ["q1", "q2", "q3", "q4"], seed=42)
    b = tb.allocate_budget(20, ["q1", "q2", "q3", "q4"], seed=42)
    assert a == b


def test_allocate_budget_empty_categories():
    tb = ThompsonBudget()
    assert tb.allocate_budget(10, []) == {}
