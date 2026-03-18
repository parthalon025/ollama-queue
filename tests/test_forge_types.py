"""Tests for Forge type definitions and Protocol compliance."""

from ollama_queue.forge.types import (
    AutonomyLevel,
    ForgeDataSource,
    ForgeResult,
    ForgeRunStatus,
    PairQuartile,
)


def test_autonomy_levels():
    assert AutonomyLevel.OBSERVER.value == "observer"
    assert AutonomyLevel.ADVISOR.value == "advisor"
    assert AutonomyLevel.OPERATOR.value == "operator"


def test_pair_quartiles():
    assert PairQuartile.LIKELY.value == "q1_likely"
    assert PairQuartile.MAYBE.value == "q2_maybe"
    assert PairQuartile.UNLIKELY.value == "q3_unlikely"
    assert PairQuartile.NONE.value == "q4_none"


def test_run_status_terminal():
    assert ForgeRunStatus.COMPLETE.is_terminal()
    assert ForgeRunStatus.FAILED.is_terminal()
    assert ForgeRunStatus.CANCELLED.is_terminal()
    assert not ForgeRunStatus.JUDGING.is_terminal()
    assert not ForgeRunStatus.QUEUED.is_terminal()


def test_forge_result_fields():
    r = ForgeResult(
        source_item_id="101",
        target_item_id="102",
        embedding_similarity=0.85,
        quartile=PairQuartile.LIKELY,
        judge_score=4,
        oracle_score=None,
    )
    assert r.source_item_id == "101"
    assert r.oracle_score is None


class _MockSource:
    def get_items(self, *, limit=100):
        return [{"id": "1", "title": "t", "one_liner": "o", "description": "d"}]


def test_protocol_compliance():
    """A class implementing get_items satisfies ForgeDataSource."""
    src = _MockSource()
    assert isinstance(src, ForgeDataSource)
