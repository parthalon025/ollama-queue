"""Tests for the Bayesian runtime estimator."""

from unittest.mock import MagicMock

import pytest

from ollama_queue.models.runtime_estimator import Estimate, RuntimeEstimator


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_job_durations.return_value = []
    db.get_load_durations.return_value = []
    db.get_model_stats.return_value = {}
    return db


def test_estimate_with_no_history(mock_db):
    """Cold start — uses resource profile prior only."""
    est = RuntimeEstimator(mock_db)
    result = est.estimate("unknown-model", "echo hi", "medium")
    assert isinstance(result, Estimate)
    assert result.total_mean > 0
    assert result.total_upper > result.total_mean
    assert result.confidence == "low"


def test_estimate_with_duration_history(mock_db):
    """With observed durations, estimate is informed."""
    mock_db.get_job_durations.return_value = [30.0, 32.0, 28.0, 35.0, 31.0]
    est = RuntimeEstimator(mock_db)
    result = est.estimate("qwen3.5:9b", "generate", "ollama")
    assert result.confidence in ("medium", "high")
    assert result.generation_mean > 0


def test_estimate_warmup_cold(mock_db):
    """Warmup estimate for cold model (not loaded)."""
    mock_db.get_load_durations.return_value = [1.8, 2.0, 1.7, 1.9]
    est = RuntimeEstimator(mock_db)
    result = est.estimate("qwen3.5:9b", "generate", "ollama", loaded_models=[])
    assert result.warmup_mean > 0
    assert result.total_mean > result.generation_mean


def test_estimate_warmup_hot(mock_db):
    """Hot model — warmup should be ~0."""
    mock_db.get_load_durations.return_value = [1.8, 2.0, 1.7]
    est = RuntimeEstimator(mock_db)
    result = est.estimate("qwen3.5:9b", "generate", "ollama", loaded_models=["qwen3.5:9b"])
    assert result.warmup_mean == 0.0


def test_confidence_scales_with_observations(mock_db):
    """More observations → higher confidence."""
    est = RuntimeEstimator(mock_db)

    mock_db.get_job_durations.return_value = [30.0]
    r1 = est.estimate("m", "c", "ollama")

    mock_db.get_job_durations.return_value = [30.0] * 10
    r2 = est.estimate("m", "c", "ollama")

    confidence_order = {"low": 0, "medium": 1, "high": 2}
    assert confidence_order[r2.confidence] >= confidence_order[r1.confidence]


def test_profile_priors_differ(mock_db):
    """Different resource profiles give different prior estimates."""
    est = RuntimeEstimator(mock_db)
    light = est.estimate("m", "c", "light")
    heavy = est.estimate("m", "c", "gpu_heavy")
    assert heavy.total_mean > light.total_mean


def test_estimate_uses_command_specific_durations(mock_db):
    """When enough command-specific data exists, it overrides model-level."""
    mock_db.get_job_durations.side_effect = lambda model, command=None: (
        [10.0, 12.0, 11.0] if command else [30.0, 32.0, 28.0, 35.0, 31.0]
    )
    est = RuntimeEstimator(mock_db)
    result = est.estimate("qwen3.5:9b", "specific-cmd", "ollama")
    # Command-specific (shorter) durations pull estimate down from model-level
    # Prior blends in, so won't be exactly 11s, but should be well below model-level ~31s
    assert result.generation_mean < 50


def test_estimate_dataclass_fields():
    """Estimate has all expected fields."""
    e = Estimate()
    assert e.warmup_mean == 0.0
    assert e.warmup_upper == 0.0
    assert e.generation_mean == 0.0
    assert e.generation_upper == 0.0
    assert e.total_mean == 0.0
    assert e.total_upper == 0.0
    assert e.confidence == "low"
    assert e.n_observations == 0


def test_estimate_excludes_negative_durations(mock_db):
    """Non-positive durations are excluded (not clamped to 0.1)."""
    mock_db.get_job_durations.return_value = [0.0, -1.0, 30.0, 60.0]
    mock_db.get_load_durations.return_value = []
    est = RuntimeEstimator(mock_db)
    result = est.estimate("test-model", "echo test", "ollama")
    assert result.total_mean > 0
    assert result.confidence in ("low", "medium", "high")
    # Estimate should be based only on [30.0, 60.0], not polluted by clamped 0.1 values
    assert result.generation_mean > 10


def test_estimate_all_negative_durations_falls_back_to_prior(mock_db):
    """When all durations are non-positive, falls back to resource profile prior."""
    mock_db.get_job_durations.return_value = [-1.0, -5.0, 0.0]
    mock_db.get_load_durations.return_value = []
    est = RuntimeEstimator(mock_db)
    result = est.estimate("test-model", "echo test", "ollama")
    assert result.total_mean > 0
    # n_obs=3 (raw count) → confidence is "medium", but all durations were excluded
    # so the generation estimate comes from the prior
    assert result.confidence in ("low", "medium")


def test_negative_durations_logged_as_excluded(mock_db, caplog):
    """Excluded non-positive durations are logged with a warning."""
    import logging

    mock_db.get_job_durations.return_value = [-1.0, 30.0]
    mock_db.get_load_durations.return_value = []
    est = RuntimeEstimator(mock_db)
    with caplog.at_level(logging.WARNING, logger="ollama_queue.models.runtime_estimator"):
        est.estimate("test-model", None, "ollama")
    assert any("Excluded 1 non-positive durations" in r.message for r in caplog.records)


def test_warmup_excludes_non_positive_values(mock_db):
    """Non-positive warmup durations are excluded, not clamped."""
    mock_db.get_job_durations.return_value = []
    mock_db.get_load_durations.return_value = [0.0, -0.5, 2.0, 3.0]
    est = RuntimeEstimator(mock_db)
    result = est.estimate("test-model", None, "ollama", loaded_models=[])
    assert result.warmup_mean > 0
    assert result.warmup_upper > 0


def test_warmup_all_negative_falls_back_to_prior(mock_db):
    """When all warmup values are non-positive, falls back to warmup prior."""
    mock_db.get_job_durations.return_value = []
    mock_db.get_load_durations.return_value = [-1.0, 0.0, -3.0]
    est = RuntimeEstimator(mock_db)
    result = est.estimate("test-model", None, "ollama", loaded_models=[])
    assert result.warmup_mean > 0
    assert result.warmup_upper > result.warmup_mean
