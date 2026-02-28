"""Tests for the Bayesian multi-signal stall detector."""

import time
from unittest.mock import MagicMock, patch

import pytest

from ollama_queue.stall import PRIOR_LOG_ODDS, StallDetector, _sigmoid


@pytest.fixture
def det():
    return StallDetector()


# ── math primitives ───────────────────────────────────────────────────────────


def test_sigmoid_midpoint():
    assert _sigmoid(0.0) == pytest.approx(0.5, abs=0.001)


def test_sigmoid_large_positive():
    assert _sigmoid(10.0) > 0.99


def test_prior_log_odds():
    """Prior P(stuck)=0.05 gives log_odds ≈ -2.944."""
    assert pytest.approx(-2.944, abs=0.01) == PRIOR_LOG_ODDS


# ── posterior combinations ────────────────────────────────────────────────────


def test_posterior_all_healthy(det):
    """R-state + high CPU + recent stdout + model loaded → posterior < 0.10."""
    ps = {"qwen2.5"}
    det.update_stdout_activity(1, time.time() - 5)
    with (
        patch.object(det, "get_process_state", return_value="R"),
        patch.object(det, "get_cpu_pct", return_value=50.0),
    ):
        p, _ = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), ps)
    assert p < 0.10


def test_posterior_d_state_only(det):
    """D-state alone → 0.5 < posterior < 0.80 (suspicious, not conclusive)."""
    with (
        patch.object(det, "get_process_state", return_value="D"),
        patch.object(det, "get_cpu_pct", return_value=None),
        patch.object(det, "get_stdout_silence", return_value=None),
    ):
        p, signals = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), set())
    assert 0.50 < p < 0.80
    assert signals["process"] == pytest.approx(3.56, abs=0.01)


def test_posterior_d_state_plus_cpu(det):
    """D-state + CPU < 1% → posterior > 0.90."""
    with (
        patch.object(det, "get_process_state", return_value="D"),
        patch.object(det, "get_cpu_pct", return_value=0.5),
        patch.object(det, "get_stdout_silence", return_value=None),
    ):
        p, _ = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), set())
    assert p > 0.90


def test_posterior_all_signals_high(det):
    """All four groups fire → posterior > 0.98."""
    with (
        patch.object(det, "get_process_state", return_value="D"),
        patch.object(det, "get_cpu_pct", return_value=0.5),
        patch.object(det, "get_stdout_silence", return_value=400.0),
    ):
        p, _ = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), set())
    assert p > 0.98


def test_posterior_silence_300s_plus_cpu(det):
    """Stdout silent 300s + CPU < 1% → > 0.88 (design spec)."""
    with (
        patch.object(det, "get_process_state", return_value="S"),
        patch.object(det, "get_cpu_pct", return_value=0.5),
        patch.object(det, "get_stdout_silence", return_value=400.0),
    ):
        p, _ = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), set())
    assert p > 0.88


def test_posterior_signals_dict_keys(det):
    """compute_posterior returns dict with expected keys."""
    with (
        patch.object(det, "get_process_state", return_value="S"),
        patch.object(det, "get_cpu_pct", return_value=None),
    ):
        _, signals = det.compute_posterior(1, 9999, "qwen2.5:7b", time.time(), set())
    for key in ("process", "cpu", "silence", "ps", "posterior"):
        assert key in signals


# ── stdout silence ────────────────────────────────────────────────────────────


def test_stdout_silence_tracking(det):
    """update_stdout_activity → get_stdout_silence returns elapsed seconds."""
    job_id = 42
    now = time.time()
    det.update_stdout_activity(job_id, now - 150.0)
    silence = det.get_stdout_silence(job_id, now)
    assert silence == pytest.approx(150.0, abs=1.0)


def test_stdout_silence_none_before_first_update(det):
    """No activity recorded → silence returns None (batch jobs: neutral)."""
    assert det.get_stdout_silence(99, time.time()) is None


def test_stdout_silence_lr_recent(det):
    """Silence < 30s -> strong healthy signal (-2.30)."""
    det.update_stdout_activity(1, time.time() - 5)
    lr = det._silence_group_lr(det.get_stdout_silence(1, time.time()))
    assert lr == pytest.approx(-2.30, abs=0.01)


def test_stdout_silence_lr_300s(det):
    """Silence > 300s → strong stall signal (+3.81)."""
    lr = det._silence_group_lr(400.0)
    assert lr == pytest.approx(3.81, abs=0.01)


# ── CPU delta ─────────────────────────────────────────────────────────────────


def test_cpu_delta_first_call_returns_none(det):
    """First get_cpu_pct call returns None (no delta yet)."""
    with patch.object(det, "_read_cpu_ticks", return_value=1000):
        result = det.get_cpu_pct(9999, 1, 0.0)
    assert result is None


def test_cpu_delta_second_call_returns_float(det):
    """Second get_cpu_pct call returns a non-negative float."""
    with patch.object(det, "_read_cpu_ticks", return_value=100):
        det.get_cpu_pct(9999, 1, 0.0)
    with patch.object(det, "_read_cpu_ticks", return_value=200):
        result = det.get_cpu_pct(9999, 1, 2.0)
    assert result is not None
    assert result >= 0.0


def test_cpu_pct_none_on_read_error(det):
    """If /proc/pid/stat is unreadable, returns None."""
    with patch.object(det, "_read_cpu_ticks", return_value=None):
        result = det.get_cpu_pct(9999, 1, 0.0)
    assert result is None


# ── Ollama /api/ps ────────────────────────────────────────────────────────────


def test_get_ollama_ps_models_parses_response(det):
    """get_ollama_ps_models() returns set of model base names."""
    mock_body = b'{"models":[{"name":"qwen2.5:7b"},{"name":"nomic-embed-text:latest"}]}'
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value.read.return_value = mock_body
    with patch("urllib.request.urlopen", return_value=mock_cm):
        result = det.get_ollama_ps_models()
    assert "qwen2.5" in result


def test_get_ollama_ps_models_on_error_returns_empty(det):
    """Network error → empty set (treat ps as unknown, not stuck)."""
    with patch("urllib.request.urlopen", side_effect=OSError("refused")):
        result = det.get_ollama_ps_models()
    assert result == set()


def test_ps_lr_model_loaded(det):
    """Model in ps -> healthy signal (-1.50)."""
    lr = det._ps_group_lr("qwen2.5:7b", {"qwen2.5"})
    assert lr == pytest.approx(-1.50, abs=0.01)


def test_ps_lr_model_not_loaded(det):
    """Model not in ps → stall signal (+1.61)."""
    lr = det._ps_group_lr("qwen2.5:7b", {"llama3.2"})
    assert lr == pytest.approx(1.61, abs=0.01)


def test_ps_lr_empty_ps_models(det):
    """Empty ps set (Ollama unreachable) → neutral (0.0)."""
    lr = det._ps_group_lr("qwen2.5:7b", set())
    assert lr == 0.0


# ── cleanup ───────────────────────────────────────────────────────────────────


def test_forget_clears_stdout_state(det):
    job_id = 5
    det.update_stdout_activity(job_id, time.time())
    det.forget(job_id)
    assert det.get_stdout_silence(job_id, time.time()) is None


def test_forget_clears_cpu_state(det):
    job_id = 5
    det._cpu_prev[job_id] = (time.time(), 500)
    det.forget(job_id)
    assert job_id not in det._cpu_prev


def test_forget_unknown_job_is_safe(det):
    """forget() on a job that was never tracked should not raise."""
    det.forget(999999)  # should not raise
