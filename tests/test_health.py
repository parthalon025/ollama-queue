import pytest

from ollama_queue.health import HealthMonitor


@pytest.fixture
def monitor():
    return HealthMonitor()


def test_get_ram_pct(monitor):
    """RAM % should be a float between 0 and 100."""
    ram = monitor.get_ram_pct()
    assert isinstance(ram, float)
    assert 0 <= ram <= 100


def test_get_swap_pct(monitor):
    swap = monitor.get_swap_pct()
    assert isinstance(swap, float)
    assert 0 <= swap <= 100


def test_get_load_avg(monitor):
    load = monitor.get_load_avg()
    assert isinstance(load, float)
    assert load >= 0


def test_get_cpu_count(monitor):
    cpus = monitor.get_cpu_count()
    assert isinstance(cpus, int)
    assert cpus >= 1


def test_get_vram_pct_returns_float_or_none(monitor):
    """VRAM may be None if no NVIDIA GPU."""
    vram = monitor.get_vram_pct()
    assert vram is None or (isinstance(vram, float) and 0 <= vram <= 100)


def test_get_ollama_active_model(monitor):
    """Returns model name string or None."""
    model = monitor.get_ollama_active_model()
    assert model is None or isinstance(model, str)


def test_check_health_returns_snapshot(monitor):
    snap = monitor.check()
    assert "ram_pct" in snap
    assert "swap_pct" in snap
    assert "load_avg" in snap
    assert "cpu_count" in snap
    assert "vram_pct" in snap
    assert "ollama_model" in snap


def test_should_pause_ram():
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85,
        "ram_resume_pct": 75,
        "vram_pause_pct": 90,
        "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0,
        "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50,
        "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 90.0,
        "swap_pct": 10.0,
        "load_avg": 1.0,
        "cpu_count": 4,
        "vram_pct": 50.0,
        "ollama_model": None,
    }
    decision = m.evaluate(snap, settings, currently_paused=False)
    assert decision["should_pause"] is True
    assert "RAM" in decision["reason"]


def test_should_resume_ram():
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85,
        "ram_resume_pct": 75,
        "vram_pause_pct": 90,
        "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0,
        "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50,
        "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 70.0,
        "swap_pct": 10.0,
        "load_avg": 1.0,
        "cpu_count": 4,
        "vram_pct": 50.0,
        "ollama_model": None,
    }
    decision = m.evaluate(snap, settings, currently_paused=True)
    assert decision["should_pause"] is False


def test_hysteresis_no_resume_between_thresholds():
    """If paused at 85%, don't resume at 80% (between pause=85 and resume=75)."""
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85,
        "ram_resume_pct": 75,
        "vram_pause_pct": 90,
        "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0,
        "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50,
        "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 80.0,
        "swap_pct": 10.0,
        "load_avg": 1.0,
        "cpu_count": 4,
        "vram_pct": 50.0,
        "ollama_model": None,
    }
    decision = m.evaluate(snap, settings, currently_paused=True)
    assert decision["should_pause"] is True  # stay paused


def test_yield_to_interactive_ollama():
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85,
        "ram_resume_pct": 75,
        "vram_pause_pct": 90,
        "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0,
        "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50,
        "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 50.0,
        "swap_pct": 10.0,
        "load_avg": 1.0,
        "cpu_count": 4,
        "vram_pct": 50.0,
        "ollama_model": "qwen2.5:7b",
    }
    decision = m.evaluate(snap, settings, currently_paused=False, queued_model=None)
    assert decision["should_yield"] is True
    assert "ollama ps" in decision["reason"]


def test_no_yield_when_same_model_queued():
    """If the loaded model matches what the queue wants to run, don't yield."""
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85,
        "ram_resume_pct": 75,
        "vram_pause_pct": 90,
        "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0,
        "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50,
        "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 50.0,
        "swap_pct": 10.0,
        "load_avg": 1.0,
        "cpu_count": 4,
        "vram_pct": 50.0,
        "ollama_model": "qwen2.5:7b",
    }
    decision = m.evaluate(snap, settings, currently_paused=False, queued_model="qwen2.5:7b")
    assert decision["should_yield"] is False


def test_no_yield_when_model_is_recent_job():
    """Don't yield if the loaded model was recently used by a queue job."""
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85,
        "ram_resume_pct": 75,
        "vram_pause_pct": 90,
        "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0,
        "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50,
        "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 50.0,
        "swap_pct": 10.0,
        "load_avg": 1.0,
        "cpu_count": 4,
        "vram_pct": 50.0,
        "ollama_model": "nomic-embed-text",
    }
    # nomic-embed-text is in recent_job_models, so it shouldn't trigger yield
    decision = m.evaluate(
        snap,
        settings,
        currently_paused=False,
        queued_model="deepseek-r1:8b",
        recent_job_models={"nomic-embed-text"},
    )
    assert decision["should_yield"] is False


def test_yield_when_model_is_truly_interactive():
    """Yield if the loaded model is NOT in recent job models."""
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85,
        "ram_resume_pct": 75,
        "vram_pause_pct": 90,
        "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0,
        "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50,
        "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 50.0,
        "swap_pct": 10.0,
        "load_avg": 1.0,
        "cpu_count": 4,
        "vram_pct": 50.0,
        "ollama_model": "llama3:70b",
    }
    # llama3:70b is NOT a recent job model — should yield
    decision = m.evaluate(
        snap,
        settings,
        currently_paused=False,
        queued_model="qwen2.5:7b",
        recent_job_models={"nomic-embed-text"},
    )
    assert decision["should_yield"] is True


# --- T4: get_loaded_models() multi-model support ---


def test_get_loaded_models_empty_when_none(monkeypatch):
    import subprocess
    from unittest.mock import MagicMock

    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "NAME    ID    SIZE    PROCESSOR    UNTIL\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock)
    result = HealthMonitor().get_loaded_models()
    assert result == []


def test_get_loaded_models_single():
    from unittest.mock import MagicMock, patch

    output = "NAME          ID            SIZE    PROCESSOR    UNTIL\nqwen2.5:7b    abc           4.7 GB  100% GPU     4 minutes from now\n"
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = output
    with patch("subprocess.run", return_value=mock):
        result = HealthMonitor().get_loaded_models()
    assert len(result) == 1
    assert result[0]["name"] == "qwen2.5:7b"


def test_get_loaded_models_multi():
    from unittest.mock import MagicMock, patch

    output = (
        "NAME                ID    SIZE      PROCESSOR    UNTIL\n"
        "qwen2.5:7b          a     4.7 GB    100% GPU     3 min\n"
        "nomic-embed-text    b     274 MB    0% GPU       5 min\n"
    )
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = output
    with patch("subprocess.run", return_value=mock):
        result = HealthMonitor().get_loaded_models()
    assert len(result) == 2
    names = [r["name"] for r in result]
    assert "qwen2.5:7b" in names
    assert "nomic-embed-text" in names


def test_check_includes_loaded_models_list():
    from unittest.mock import MagicMock, patch

    output = "NAME    ID    SIZE    PROCESSOR    UNTIL\nqwen2.5:7b    abc    4.7 GB    100% GPU    3 min\n"
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = output
    with patch("subprocess.run", return_value=mock):
        snap = HealthMonitor().check()
    assert "ollama_loaded_models" in snap
    assert isinstance(snap["ollama_loaded_models"], list)
    # backward compat: ollama_model is still present
    assert "ollama_model" in snap


def test_get_vram_pct_cached(monkeypatch):
    """nvidia-smi subprocess called at most once per TTL window."""

    from ollama_queue.health import HealthMonitor

    call_count = 0

    def fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return type("R", (), {"returncode": 0, "stdout": "1024\n"})()

    monkeypatch.setattr("ollama_queue.health.subprocess.run", fake_run)
    h = HealthMonitor()
    h.get_vram_pct()
    h.get_vram_pct()
    h.get_vram_pct()
    assert call_count == 1, "nvidia-smi should be called once within TTL window"
