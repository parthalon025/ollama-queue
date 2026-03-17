"""Tests for expanded heartbeat fields (CPU/RAM/disk)."""

import time

import ollama_queue.api.backend_router as _router


def test_receive_heartbeat_cpu_ram():
    """receive_heartbeat stores cpu_pct and ram_pct in caches."""
    url = "http://testhost:11434"
    now = time.monotonic()

    _router.receive_heartbeat(
        url,
        {
            "healthy": True,
            "cpu_pct": 45.2,
            "ram_pct": 61.0,
            "ram_total_gb": 32.0,
            "disk_pct": 16.5,
            "disk_total_gb": 500.0,
            "disk_used_gb": 82.3,
            "ollama_storage_gb": 47.2,
            "agent_version": "0.1.0",
            "ollama_version": "0.5.13",
        },
        now,
    )

    # CPU cache
    assert url in _router._cpu_cache
    assert _router._cpu_cache[url][1] == 45.2

    # RAM cache
    assert url in _router._ram_cache
    assert _router._ram_cache[url][1] == 61.0

    # Cleanup
    for c in (_router._cpu_cache, _router._ram_cache, _router._health_cache, _router._hw_cache):
        c.pop(url, None)


def test_receive_heartbeat_partial_no_cpu():
    """receive_heartbeat without cpu_pct does not create cpu cache entry."""
    url = "http://testhost2:11434"
    now = time.monotonic()

    _router.receive_heartbeat(url, {"healthy": True}, now)

    assert url not in _router._cpu_cache
    assert url not in _router._ram_cache

    # Cleanup
    _router._health_cache.pop(url, None)


def test_agent_reachable_after_heartbeat():
    """agent_reachable returns True after a recent heartbeat."""
    url = "http://agent-test:11434"
    now = time.monotonic()

    _router.receive_heartbeat(url, {"healthy": True, "agent_version": "0.1.0"}, now)

    assert _router.agent_reachable(url) is True
    assert _router.agent_version(url) == "0.1.0"

    # Cleanup
    for c in (_router._health_cache, _router._heartbeat_ts_cache, _router._agent_version_cache):
        c.pop(url, None)


def test_agent_not_reachable_without_heartbeat():
    """agent_reachable returns False for unknown backends."""
    assert _router.agent_reachable("http://never-seen:11434") is False
    assert _router.agent_version("http://never-seen:11434") is None


def test_agent_stale_heartbeat():
    """agent_reachable returns False when heartbeat is older than TTL."""
    url = "http://stale-agent:11434"
    stale_ts = time.monotonic() - _router._AGENT_HEARTBEAT_TTL - 10

    _router._heartbeat_ts_cache[url] = stale_ts

    assert _router.agent_reachable(url) is False

    # Cleanup
    _router._heartbeat_ts_cache.pop(url, None)


def test_invalidate_clears_agent_caches():
    """invalidate_backend_caches removes heartbeat and agent version entries."""
    url = "http://clear-test:11434"
    now = time.monotonic()

    _router.receive_heartbeat(url, {"healthy": True, "agent_version": "0.1.0"}, now)
    assert url in _router._heartbeat_ts_cache
    assert url in _router._agent_version_cache

    _router.invalidate_backend_caches(url)

    assert url not in _router._heartbeat_ts_cache
    assert url not in _router._agent_version_cache
    assert url not in _router._health_cache
