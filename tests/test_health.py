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
        "ram_pause_pct": 85, "ram_resume_pct": 75,
        "vram_pause_pct": 90, "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0, "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50, "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 90.0, "swap_pct": 10.0, "load_avg": 1.0,
        "cpu_count": 4, "vram_pct": 50.0, "ollama_model": None,
    }
    decision = m.evaluate(snap, settings, currently_paused=False)
    assert decision["should_pause"] is True
    assert "RAM" in decision["reason"]


def test_should_resume_ram():
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85, "ram_resume_pct": 75,
        "vram_pause_pct": 90, "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0, "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50, "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 70.0, "swap_pct": 10.0, "load_avg": 1.0,
        "cpu_count": 4, "vram_pct": 50.0, "ollama_model": None,
    }
    decision = m.evaluate(snap, settings, currently_paused=True)
    assert decision["should_pause"] is False


def test_hysteresis_no_resume_between_thresholds():
    """If paused at 85%, don't resume at 80% (between pause=85 and resume=75)."""
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85, "ram_resume_pct": 75,
        "vram_pause_pct": 90, "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0, "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50, "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 80.0, "swap_pct": 10.0, "load_avg": 1.0,
        "cpu_count": 4, "vram_pct": 50.0, "ollama_model": None,
    }
    decision = m.evaluate(snap, settings, currently_paused=True)
    assert decision["should_pause"] is True  # stay paused


def test_yield_to_interactive_ollama():
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85, "ram_resume_pct": 75,
        "vram_pause_pct": 90, "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0, "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50, "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 50.0, "swap_pct": 10.0, "load_avg": 1.0,
        "cpu_count": 4, "vram_pct": 50.0, "ollama_model": "qwen2.5:7b",
    }
    decision = m.evaluate(snap, settings, currently_paused=False, queued_model=None)
    assert decision["should_yield"] is True
    assert "ollama ps" in decision["reason"]


def test_no_yield_when_same_model_queued():
    """If the loaded model matches what the queue wants to run, don't yield."""
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85, "ram_resume_pct": 75,
        "vram_pause_pct": 90, "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0, "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50, "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 50.0, "swap_pct": 10.0, "load_avg": 1.0,
        "cpu_count": 4, "vram_pct": 50.0, "ollama_model": "qwen2.5:7b",
    }
    decision = m.evaluate(snap, settings, currently_paused=False, queued_model="qwen2.5:7b")
    assert decision["should_yield"] is False


def test_no_yield_when_model_is_recent_job():
    """Don't yield if the loaded model was recently used by a queue job."""
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85, "ram_resume_pct": 75,
        "vram_pause_pct": 90, "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0, "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50, "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 50.0, "swap_pct": 10.0, "load_avg": 1.0,
        "cpu_count": 4, "vram_pct": 50.0, "ollama_model": "nomic-embed-text",
    }
    # nomic-embed-text is in recent_job_models, so it shouldn't trigger yield
    decision = m.evaluate(
        snap, settings, currently_paused=False, queued_model="deepseek-r1:8b",
        recent_job_models={"nomic-embed-text"},
    )
    assert decision["should_yield"] is False


def test_yield_when_model_is_truly_interactive():
    """Yield if the loaded model is NOT in recent job models."""
    m = HealthMonitor()
    settings = {
        "ram_pause_pct": 85, "ram_resume_pct": 75,
        "vram_pause_pct": 90, "vram_resume_pct": 80,
        "load_pause_multiplier": 2.0, "load_resume_multiplier": 1.5,
        "swap_pause_pct": 50, "swap_resume_pct": 40,
        "yield_to_interactive": True,
    }
    snap = {
        "ram_pct": 50.0, "swap_pct": 10.0, "load_avg": 1.0,
        "cpu_count": 4, "vram_pct": 50.0, "ollama_model": "llama3:70b",
    }
    # llama3:70b is NOT a recent job model — should yield
    decision = m.evaluate(
        snap, settings, currently_paused=False, queued_model="qwen2.5:7b",
        recent_job_models={"nomic-embed-text"},
    )
    assert decision["should_yield"] is True
