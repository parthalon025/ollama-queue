"""Tests for DurationEstimator."""

import pytest

from ollama_queue.models.estimator import DurationEstimator


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


class TestEstimateWithVariance:
    """Tests for DurationEstimator.estimate_with_variance()."""

    def test_estimate_with_variance_returns_tuple(self, db):
        """Returns (mean, cv_squared) tuple."""
        from ollama_queue.models.estimator import DurationEstimator

        estimator = DurationEstimator(db)
        mean, cv_sq = estimator.estimate_with_variance("unknown-src")
        assert isinstance(mean, float) and mean > 0
        assert isinstance(cv_sq, float) and cv_sq >= 0

    def test_estimate_with_variance_uses_db_stats(self, db):
        """Uses actual duration history when available."""
        import time

        from ollama_queue.models.estimator import DurationEstimator

        now = time.time()
        # Insert predictable history: all same duration → variance = 0
        for _ in range(5):
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("uniform-src", "m", 600.0, 0, now),
            )
        db._connect().commit()
        estimator = DurationEstimator(db)
        mean, cv_sq = estimator.estimate_with_variance("uniform-src")
        assert abs(mean - 600.0) < 1.0
        assert cv_sq < 0.01  # near-zero variance → near-zero cv_sq

    def test_estimate_with_variance_cv_sq_high_for_mixed_durations(self, db):
        """High variance history produces cv_squared > 1.0."""
        import time

        from ollama_queue.models.estimator import DurationEstimator

        now = time.time()
        # Very mixed durations: 100, 1000, 100, 1000 (high variance)
        for d in [100.0, 1000.0, 100.0, 1000.0]:
            db._connect().execute(
                "INSERT INTO duration_history (source, model, duration, exit_code, recorded_at) VALUES (?,?,?,?,?)",
                ("mixed-src", "m", d, 0, now),
            )
        db._connect().commit()
        estimator = DurationEstimator(db)
        _, cv_sq = estimator.estimate_with_variance("mixed-src")
        assert cv_sq > 0.5  # high variance relative to mean

    def test_estimate_with_variance_uses_cached_mean(self, db):
        """Uses cached bulk dict for mean when no db stats available."""
        from ollama_queue.models.estimator import DurationEstimator

        estimator = DurationEstimator(db)
        cached = {"cached-src": 300.0}
        mean, cv_sq = estimator.estimate_with_variance("cached-src", cached=cached)
        assert mean == 300.0
        assert cv_sq == 1.5  # unknown variance default

    def test_estimate_with_variance_default_cv_sq_is_conservative(self, db):
        """Returns cv_squared=1.5 when no history exists (conservative default)."""
        from ollama_queue.models.estimator import DurationEstimator

        estimator = DurationEstimator(db)
        _, cv_sq = estimator.estimate_with_variance("no-history-src")
        assert cv_sq == 1.5

    def test_estimate_with_variance_uses_model_default(self, db):
        """Model-name default tier used when no db history and no cached dict."""
        from ollama_queue.models.estimator import DurationEstimator

        estimator = DurationEstimator(db)
        mean, cv_sq = estimator.estimate_with_variance("no-history-src", model="deepseek-r1:8b")
        assert mean == 1800.0  # MODEL_DEFAULTS entry for deepseek
        assert cv_sq == 1.5  # unknown variance → conservative default


def test_queue_etas_accepts_om_parameter(db):
    """queue_etas() reuses a passed OllamaModels instance instead of creating a new one."""
    from unittest.mock import patch

    from ollama_queue.models.client import OllamaModels
    from ollama_queue.models.estimator import DurationEstimator

    estimator = DurationEstimator(db)
    jobs = [{"source": "test", "model": "qwen2.5:7b", "resource_profile": "ollama"}]

    shared_om = OllamaModels()

    with patch("ollama_queue.models.estimator.OllamaModels") as mock_cls:
        # When om is passed, OllamaModels() constructor should NOT be called
        estimator.queue_etas(jobs, om=shared_om)
        mock_cls.assert_not_called()


def test_queue_etas_creates_om_when_none(db):
    """queue_etas() creates OllamaModels when om=None (default)."""
    from unittest.mock import patch

    from ollama_queue.models.estimator import DurationEstimator

    estimator = DurationEstimator(db)
    jobs = [{"source": "test", "model": "qwen2.5:7b", "resource_profile": "ollama"}]

    with patch("ollama_queue.models.estimator.OllamaModels") as mock_cls:
        mock_cls.return_value.classify.return_value = {"resource_profile": "ollama"}
        estimator.queue_etas(jobs)  # om=None by default
        mock_cls.assert_called_once()
