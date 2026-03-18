"""Tests for Forge setting keys and helpers."""

from ollama_queue.forge.settings import (
    FORGE_DEFAULTS,
    get_forge_setting,
)


def test_defaults_contain_all_keys():
    required = {
        "forge.oracle_provider",
        "forge.oracle_model",
        "forge.oracle_budget",
        "forge.oracle_fraction",
        "forge.oracle_min_kappa",
        "forge.judge_model",
        "forge.judge_provider",
        "forge.judge_temperature",
        "forge.pairs_per_quartile",
        "forge.positive_threshold",
        "forge.f1_threshold",
        "forge.auto_promote_min_improvement",
        "forge.autonomy_level",
        "forge.embedding_model",
    }
    assert required.issubset(FORGE_DEFAULTS.keys())


def test_get_forge_setting_with_db_value(db):
    db.set_setting("forge.oracle_budget", "30")
    assert get_forge_setting(db, "forge.oracle_budget", int) == 30


def test_get_forge_setting_falls_back_to_default(db):
    val = get_forge_setting(db, "forge.oracle_budget", int)
    assert val == FORGE_DEFAULTS["forge.oracle_budget"]


def test_get_forge_setting_handles_none_for_numeric(db):
    db.set_setting("forge.oracle_budget", None)
    val = get_forge_setting(db, "forge.oracle_budget", int)
    assert val == FORGE_DEFAULTS["forge.oracle_budget"]
