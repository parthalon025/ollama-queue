"""Tests for DurationEstimator."""

import pytest

from ollama_queue.estimator import DurationEstimator


@pytest.fixture
def estimator(db):
    return DurationEstimator(db)


def test_estimate_with_history(estimator, db):
    for d in [100, 110, 120, 130, 140]:
        db.record_duration("aria-full", "deepseek-r1:8b", d, 0)
    est = estimator.estimate("aria-full")
    assert est == 120.0


def test_estimate_unknown_source_default_by_model(estimator):
    """Unknown source with known model returns model-based default."""
    est = estimator.estimate("unknown", model="deepseek-r1:8b")
    assert est == 1800  # 30 min default for 8b+ models


def test_estimate_unknown_source_default_small_model(estimator):
    est = estimator.estimate("unknown", model="qwen2.5:7b")
    assert est == 600  # 10 min default for 7b models


def test_estimate_unknown_everything(estimator):
    est = estimator.estimate("unknown")
    assert est == 600  # generic default


def test_embed_jobs_dont_block_serial_slots(db):
    """Embed jobs run concurrently — serial queue offset stays 0 for next job."""
    jobs = [
        {"source": "aria-embed", "model": "nomic-embed-text:latest", "resource_profile": "embed"},
        {"source": "aria-full", "model": "qwen2.5-coder:14b", "resource_profile": "ollama"},
    ]
    etas = DurationEstimator(db).queue_etas(jobs)
    assert etas[0]["concurrent"] is True
    assert etas[1]["estimated_start_offset"] == 0.0


def test_queue_eta(estimator, db):
    for d in [60, 60, 60, 60, 60]:
        db.record_duration("a", "m", d, 0)
    for d in [120, 120, 120, 120, 120]:
        db.record_duration("b", "m", d, 0)

    queue = [
        {"source": "a", "model": "m"},
        {"source": "b", "model": "m"},
    ]
    etas = estimator.queue_etas(queue)
    assert len(etas) == 2
    assert etas[0]["estimated_start_offset"] == 0  # first job starts now
    assert etas[1]["estimated_start_offset"] == 60  # after first job
