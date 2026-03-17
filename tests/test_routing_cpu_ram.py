"""Tests for CPU/RAM-aware routing in multi-backend selection."""

from __future__ import annotations

import asyncio
import time

import pytest

import ollama_queue.api.backend_router as _router


def run(coro):
    """Run an async coroutine synchronously — matches this project's no-pytest-asyncio pattern."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_caches():
    """Clean router caches before and after each test."""
    saved_backends = _router.BACKENDS[:]
    yield
    _router.BACKENDS[:] = saved_backends
    for c in (
        _router._health_cache,
        _router._models_cache,
        _router._loaded_cache,
        _router._hw_cache,
        _router._gpu_name_cache,
        _router._vram_total_cache,
        _router._cpu_cache,
        _router._ram_cache,
    ):
        c.clear()


def test_skip_high_cpu_backend():
    """Backend with cpu_pct > 90 is deprioritized."""
    _router.BACKENDS[:] = ["http://a:11434", "http://b:11434"]
    now = time.monotonic()

    # Both healthy, both have the model, neither warm
    _router._health_cache["http://a:11434"] = (now, True)
    _router._health_cache["http://b:11434"] = (now, True)
    _router._models_cache["http://a:11434"] = (now, frozenset(["test:7b"]))
    _router._models_cache["http://b:11434"] = (now, frozenset(["test:7b"]))
    _router._hw_cache["http://a:11434"] = (now, 50.0)
    _router._hw_cache["http://b:11434"] = (now, 50.0)

    # A has high CPU, B is fine
    _router._cpu_cache["http://a:11434"] = (now, 95.0)
    _router._cpu_cache["http://b:11434"] = (now, 20.0)

    result = run(_router.select_backend("test:7b"))
    assert result == "http://b:11434"


def test_skip_high_ram_backend():
    """Backend with ram_pct > 90 is deprioritized."""
    _router.BACKENDS[:] = ["http://a:11434", "http://b:11434"]
    now = time.monotonic()

    _router._health_cache["http://a:11434"] = (now, True)
    _router._health_cache["http://b:11434"] = (now, True)
    _router._models_cache["http://a:11434"] = (now, frozenset(["test:7b"]))
    _router._models_cache["http://b:11434"] = (now, frozenset(["test:7b"]))
    _router._hw_cache["http://a:11434"] = (now, 50.0)
    _router._hw_cache["http://b:11434"] = (now, 50.0)

    # A has high RAM, B is fine
    _router._ram_cache["http://a:11434"] = (now, 95.0)
    _router._ram_cache["http://b:11434"] = (now, 30.0)

    result = run(_router.select_backend("test:7b"))
    assert result == "http://b:11434"


def test_fail_open_all_overloaded():
    """When ALL backends are overloaded, none are filtered (fail-open)."""
    _router.BACKENDS[:] = ["http://a:11434", "http://b:11434"]
    now = time.monotonic()

    _router._health_cache["http://a:11434"] = (now, True)
    _router._health_cache["http://b:11434"] = (now, True)
    _router._models_cache["http://a:11434"] = (now, frozenset(["test:7b"]))
    _router._models_cache["http://b:11434"] = (now, frozenset(["test:7b"]))
    _router._hw_cache["http://a:11434"] = (now, 50.0)
    _router._hw_cache["http://b:11434"] = (now, 50.0)

    # Both overloaded on CPU
    _router._cpu_cache["http://a:11434"] = (now, 95.0)
    _router._cpu_cache["http://b:11434"] = (now, 95.0)

    # Should still return one of the two (fail-open)
    result = run(_router.select_backend("test:7b"))
    assert result in ("http://a:11434", "http://b:11434")


def test_stale_cpu_cache_ignored():
    """CPU cache entries older than TTL are ignored (treated as OK)."""
    _router.BACKENDS[:] = ["http://a:11434", "http://b:11434"]
    now = time.monotonic()

    _router._health_cache["http://a:11434"] = (now, True)
    _router._health_cache["http://b:11434"] = (now, True)
    _router._models_cache["http://a:11434"] = (now, frozenset(["test:7b"]))
    _router._models_cache["http://b:11434"] = (now, frozenset(["test:7b"]))
    _router._hw_cache["http://a:11434"] = (now, 50.0)
    _router._hw_cache["http://b:11434"] = (now, 50.0)

    # A has high CPU but the entry is stale (60s old, TTL is 30s)
    _router._cpu_cache["http://a:11434"] = (now - 60.0, 95.0)
    _router._cpu_cache["http://b:11434"] = (now, 20.0)

    # A should NOT be filtered since its cache entry is stale
    result = run(_router.select_backend("test:7b"))
    assert result in ("http://a:11434", "http://b:11434")


def test_no_cache_entries_no_filtering():
    """Backends without CPU/RAM cache entries are not filtered."""
    _router.BACKENDS[:] = ["http://a:11434", "http://b:11434"]
    now = time.monotonic()

    _router._health_cache["http://a:11434"] = (now, True)
    _router._health_cache["http://b:11434"] = (now, True)
    _router._models_cache["http://a:11434"] = (now, frozenset(["test:7b"]))
    _router._models_cache["http://b:11434"] = (now, frozenset(["test:7b"]))
    _router._hw_cache["http://a:11434"] = (now, 50.0)
    _router._hw_cache["http://b:11434"] = (now, 50.0)

    # No CPU/RAM cache entries — both should remain candidates
    result = run(_router.select_backend("test:7b"))
    assert result in ("http://a:11434", "http://b:11434")
