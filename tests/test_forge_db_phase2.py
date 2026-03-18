# tests/test_forge_db_phase2.py
"""Tests for Forge Phase 2 DB operations — archive + thompson state."""


def test_upsert_archive_cell_insert(db):
    db.upsert_forge_archive_cell(
        x_bin=0,
        y_bin=0,
        x_value=0.1,
        y_value=0.2,
        variant_id="A",
        fitness=0.75,
        prompt_text="test prompt",
        run_id=None,
    )
    cell = db.get_forge_archive_cell(0, 0)
    assert cell is not None
    assert cell["variant_id"] == "A"
    assert cell["fitness"] == 0.75


def test_upsert_archive_cell_replaces_lower_fitness(db):
    db.upsert_forge_archive_cell(x_bin=1, y_bin=1, x_value=0.5, y_value=0.5, variant_id="A", fitness=0.5)
    db.upsert_forge_archive_cell(x_bin=1, y_bin=1, x_value=0.5, y_value=0.5, variant_id="B", fitness=0.8)
    cell = db.get_forge_archive_cell(1, 1)
    assert cell["variant_id"] == "B"


def test_upsert_archive_cell_keeps_higher_fitness(db):
    db.upsert_forge_archive_cell(x_bin=2, y_bin=2, x_value=0.5, y_value=0.5, variant_id="A", fitness=0.9)
    db.upsert_forge_archive_cell(x_bin=2, y_bin=2, x_value=0.5, y_value=0.5, variant_id="B", fitness=0.3)
    cell = db.get_forge_archive_cell(2, 2)
    assert cell["variant_id"] == "A"


def test_get_forge_archive_grid(db):
    for i in range(3):
        db.upsert_forge_archive_cell(
            x_bin=i, y_bin=0, x_value=float(i), y_value=0.0, variant_id=f"v{i}", fitness=i * 0.1
        )
    grid = db.get_forge_archive_grid()
    assert len(grid) == 3


def test_clear_forge_archive(db):
    db.upsert_forge_archive_cell(x_bin=0, y_bin=0, x_value=0.0, y_value=0.0, variant_id="A", fitness=0.5)
    db.clear_forge_archive()
    assert db.get_forge_archive_grid() == []


def test_save_and_load_thompson_state(db):
    state = {"q1_likely": {"alpha": 3.0, "beta": 1.5, "observations": 10}}
    db.save_forge_thompson_state(state)
    loaded = db.load_forge_thompson_state()
    assert loaded == state


def test_load_thompson_state_empty(db):
    assert db.load_forge_thompson_state() is None
