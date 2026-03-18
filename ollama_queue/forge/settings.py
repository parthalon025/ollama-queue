"""Forge setting keys, defaults, and typed accessors."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from ollama_queue.db import Database

T = TypeVar("T", int, float, str, bool)

FORGE_DEFAULTS: dict[str, int | float | str | bool] = {
    # Oracle
    "forge.oracle_provider": "claude",
    "forge.oracle_model": "claude-sonnet-4-20250514",
    "forge.oracle_budget": 20,
    "forge.oracle_fraction": 0.2,
    "forge.oracle_min_kappa": 0.6,
    # Judge
    "forge.judge_model": "",
    "forge.judge_provider": "ollama",
    "forge.judge_temperature": 0.1,
    # Pair selection
    "forge.pairs_per_quartile": 20,
    "forge.positive_threshold": 3,
    "forge.embedding_model": "nomic-embed-text",
    # Autonomy
    "forge.autonomy_level": "observer",
    # Auto-promote gates
    "forge.f1_threshold": 0.7,
    "forge.auto_promote_min_improvement": 0.05,
    # Phase 2: Evolution
    "forge.grid_size": 10,
    "forge.evolution_enabled": False,
    "forge.evolution_offspring": 4,
    "forge.evolution_min_archive": 3,
    "forge.evolution_mutation_rate": 0.15,
    "forge.thompson_enabled": True,
    "forge.thompson_discount": 0.95,
    "forge.thompson_window": 100,
}


def get_forge_setting(db: Database, key: str, cast: type[T]) -> T:
    """Read a forge setting from DB, falling back to FORGE_DEFAULTS.

    Handles None and empty string gracefully for numeric types.
    """
    raw = db.get_setting(key)
    default = FORGE_DEFAULTS.get(key)

    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return cast(default) if default is not None else cast()

    try:
        if cast is bool:
            if isinstance(raw, str):
                return raw.strip().lower() not in ("false", "0", "")
            return bool(raw)
        return cast(raw)
    except (ValueError, TypeError):
        return cast(default) if default is not None else cast()
