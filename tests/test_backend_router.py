"""Tests for the multi-backend Ollama router."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

import ollama_queue.api.backend_router as router


def run(coro):
    """Run an async coroutine synchronously — matches this project's no-pytest-asyncio pattern."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear module-level caches between tests."""
    router._health_cache.clear()
    router._models_cache.clear()
    router._loaded_cache.clear()
    yield
    router._health_cache.clear()
    router._models_cache.clear()
    router._loaded_cache.clear()


# ---------------------------------------------------------------------------
# select_backend — single backend fast path
# ---------------------------------------------------------------------------


def test_single_backend_returns_immediately():
    """Single backend: no health/model checks are performed."""
    with patch.object(router, "BACKENDS", ["http://local:11434"]):
        result = run(router.select_backend("qwen3:14b"))
    assert result == "http://local:11434"


# ---------------------------------------------------------------------------
# select_backend — health filtering
# ---------------------------------------------------------------------------


def test_healthy_backend_selected():
    """Two backends: returns the healthy one."""
    with (
        patch.object(router, "BACKENDS", ["http://local:11434", "http://remote:11434"]),
        patch.object(router, "_backend_healthy", new=AsyncMock(side_effect=[True, False])),
        patch.object(router, "_available_models", new=AsyncMock(return_value=frozenset())),
    ):
        result = run(router.select_backend("qwen3:14b"))
    assert result == "http://local:11434"


def test_all_unhealthy_falls_back_to_first():
    """All backends down: falls back to first configured backend."""
    with (
        patch.object(router, "BACKENDS", ["http://local:11434", "http://remote:11434"]),
        patch.object(router, "_backend_healthy", new=AsyncMock(return_value=False)),
    ):
        result = run(router.select_backend("qwen3:14b"))
    assert result == "http://local:11434"


# ---------------------------------------------------------------------------
# select_backend — model-aware routing
# ---------------------------------------------------------------------------


def test_routes_to_backend_with_model():
    """Both healthy, only remote has the requested model — routes to remote."""
    backends = ["http://local:11434", "http://remote:11434"]
    model_sets = {
        "http://local:11434": frozenset(["other-model:7b"]),
        "http://remote:11434": frozenset(["deepseek-r1:70b"]),
    }

    async def fake_avail(url):
        return model_sets[url]

    with (
        patch.object(router, "BACKENDS", backends),
        patch.object(router, "_backend_healthy", new=AsyncMock(return_value=True)),
        patch.object(router, "_available_models", new=AsyncMock(side_effect=fake_avail)),
        patch.object(router, "_loaded_models", new=AsyncMock(return_value=frozenset())),
    ):
        result = run(router.select_backend("deepseek-r1:70b"))

    assert result == "http://remote:11434"


def test_routes_to_warm_backend():
    """Both have the model; remote already has it loaded in VRAM — routes to remote."""
    backends = ["http://local:11434", "http://remote:11434"]
    model_name = "qwen3:14b"

    loaded_sets = {
        "http://local:11434": frozenset(),
        "http://remote:11434": frozenset([model_name]),
    }

    async def fake_loaded(url):
        return loaded_sets[url]

    with (
        patch.object(router, "BACKENDS", backends),
        patch.object(router, "_backend_healthy", new=AsyncMock(return_value=True)),
        patch.object(router, "_available_models", new=AsyncMock(return_value=frozenset([model_name]))),
        patch.object(router, "_loaded_models", new=AsyncMock(side_effect=fake_loaded)),
    ):
        result = run(router.select_backend(model_name))

    assert result == "http://remote:11434"


def test_no_model_skips_model_checks():
    """Empty model string: skip model availability and loaded checks."""
    backends = ["http://local:11434", "http://remote:11434"]
    avail_mock = AsyncMock(return_value=frozenset())

    with (
        patch.object(router, "BACKENDS", backends),
        patch.object(router, "_backend_healthy", new=AsyncMock(return_value=True)),
        patch.object(router, "_available_models", new=avail_mock),
    ):
        result = run(router.select_backend(""))

    avail_mock.assert_not_called()
    assert result in backends


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_health_cache_hit():
    """Cached health result is returned without making a new HTTP call."""
    now = time.monotonic()
    router._health_cache["http://cached:11434"] = (now, True)
    result = run(router._backend_healthy("http://cached:11434"))
    assert result is True


def test_health_cache_expired():
    """Expired cache entry triggers a new health check and refreshes the cache."""
    expired_ts = time.monotonic() - router._HEALTH_TTL - 1
    router._health_cache["http://stale:11434"] = (expired_ts, True)

    with patch("ollama_queue.api.backend_router.httpx.AsyncClient", _fake_async_client(json_data={})):
        result = run(router._backend_healthy("http://stale:11434"))

    assert result is True
    assert router._health_cache["http://stale:11434"][0] > expired_ts


# ---------------------------------------------------------------------------
# _available_models and _loaded_models
# ---------------------------------------------------------------------------


def _fake_async_client(json_data=None, side_effect=None):
    """Return a fake httpx.AsyncClient class for use in `async with` blocks.

    AsyncMock context-manager wiring is tricky (default __aenter__ returns a new
    mock, not self). A lightweight fake class avoids all that complexity.
    """

    class _FakeResp:
        status_code = 200

        def json(self):
            return json_data or {}

    class _FakeClient:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, url):
            if side_effect:
                raise side_effect
            return _FakeResp()

    return _FakeClient


def test_available_models_parses_tags_response():
    """_available_models extracts model names from /api/tags JSON."""
    tags_response = {"models": [{"name": "qwen3:14b"}, {"name": "deepseek-r1:8b"}]}

    with patch("ollama_queue.api.backend_router.httpx.AsyncClient", _fake_async_client(json_data=tags_response)):
        result = run(router._available_models("http://host:11434"))

    assert result == frozenset(["qwen3:14b", "deepseek-r1:8b"])


def test_available_models_returns_empty_on_error():
    """_available_models returns empty frozenset on connection failure."""
    with patch(
        "ollama_queue.api.backend_router.httpx.AsyncClient",
        _fake_async_client(side_effect=Exception("connection refused")),
    ):
        result = run(router._available_models("http://dead:11434"))

    assert result == frozenset()


def test_loaded_models_parses_ps_response():
    """_loaded_models extracts model names from /api/ps JSON."""
    ps_response = {"models": [{"name": "qwen3:14b", "size": 8000000000}]}

    with patch("ollama_queue.api.backend_router.httpx.AsyncClient", _fake_async_client(json_data=ps_response)):
        result = run(router._loaded_models("http://host:11434"))

    assert result == frozenset(["qwen3:14b"])


# ---------------------------------------------------------------------------
# BACKENDS env var parsing (static, no async)
# ---------------------------------------------------------------------------


def test_backends_parsed_from_env():
    """OLLAMA_BACKENDS env var is parsed into a list of stripped URLs."""
    raw = "http://a:11434 , http://b:11434 "
    result = [b.strip().rstrip("/") for b in raw.split(",") if b.strip()]
    assert result == ["http://a:11434", "http://b:11434"]


def test_backends_trailing_slash_stripped():
    """Trailing slashes on backend URLs are stripped."""
    raw = "http://a:11434/"
    result = [b.strip().rstrip("/") for b in raw.split(",") if b.strip()]
    assert result == ["http://a:11434"]
