"""Tests for required_models setting seeding."""

from ollama_queue.db import Database


def test_required_models_seeded(tmp_path):
    """required_models setting is seeded with model list on DB init."""
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    settings = db.get_all_settings()
    assert "required_models" in settings
    models = settings["required_models"]
    assert isinstance(models, list)
    assert len(models) > 0
    first = models[0]
    assert "name" in first
    assert "vram_mb" in first
    assert "tier" in first
    assert first["tier"] in ("core", "standard", "optional")


def test_required_models_contains_core_models(tmp_path):
    """required_models includes known core models."""
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    settings = db.get_all_settings()
    names = [m["name"] for m in settings["required_models"]]
    assert "nomic-embed-text" in names
    assert "qwen3.5:2b" in names
