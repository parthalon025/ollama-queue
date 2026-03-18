"""Forge type definitions — Protocol, enums, result containers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class AutonomyLevel(Enum):
    """How much Forge can act on its own."""

    OBSERVER = "observer"  # Reports only
    ADVISOR = "advisor"  # Auto-promote when gates pass
    OPERATOR = "operator"  # Auto-promote + feedback to data source


class ForgeRunStatus(Enum):
    """Lifecycle states for a Forge run."""

    QUEUED = "queued"
    EMBEDDING = "embedding"
    JUDGING = "judging"
    ORACLE = "oracle"
    CALIBRATING = "calibrating"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        return self in (self.COMPLETE, self.FAILED, self.CANCELLED)


class PairQuartile(Enum):
    """Embedding distance quartile for stratified sampling."""

    LIKELY = "q1_likely"  # sim 0.75-1.0
    MAYBE = "q2_maybe"  # sim 0.50-0.75
    UNLIKELY = "q3_unlikely"  # sim 0.25-0.50
    NONE = "q4_none"  # sim 0.00-0.25


@dataclass(frozen=True, slots=True)
class ForgeResult:
    """One scored pair from a Forge run."""

    source_item_id: str
    target_item_id: str
    embedding_similarity: float
    quartile: PairQuartile
    judge_score: int | None = None
    oracle_score: int | None = None
    judge_reasoning: str | None = None
    oracle_reasoning: str | None = None
    calibrated_score: float | None = None


@runtime_checkable
class ForgeDataSource(Protocol):
    """Minimum contract for a Forge data source.

    Only get_items() is required. All other methods are optional —
    Forge computes embeddings itself and uses the oracle as ground truth.
    """

    def get_items(self, *, limit: int = 100) -> list[dict]:
        """Return items with id, title, one_liner, description, tags."""
        ...
