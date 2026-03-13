"""Multi-backend Ollama router for the proxy layer.

Selects the best backend for each request using a three-tier strategy:
  1. Health check — skip unreachable backends (2s timeout, 30s cache)
  2. Model availability — prefer backends that have the requested model (60s cache)
  3. Warm model — prefer backends with the model already loaded in VRAM (5s cache)
  4. Random choice among remaining tied candidates

Configure via env vars:
  OLLAMA_BACKENDS=http://host1:11434,http://host2:11434  (multi-backend)
  OLLAMA_URL=http://127.0.0.1:11434                      (single-backend fallback)

Single-backend setups (or no OLLAMA_BACKENDS set) skip all routing logic.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time

import httpx

_log = logging.getLogger(__name__)

# Parse backends once at import time. Tests may patch BACKENDS directly.
_raw = os.environ.get("OLLAMA_BACKENDS", "")
BACKENDS: list[str] = (
    [b.strip().rstrip("/") for b in _raw.split(",") if b.strip()]
    if _raw
    else [os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")]
)

# Cache TTLs (seconds)
_HEALTH_TTL = 30.0
_MODELS_TTL = 60.0
_LOADED_TTL = 5.0

# Module-level caches: url -> (timestamp, data)
_health_cache: dict[str, tuple[float, bool]] = {}
_models_cache: dict[str, tuple[float, frozenset[str]]] = {}
_loaded_cache: dict[str, tuple[float, frozenset[str]]] = {}


async def _backend_healthy(url: str) -> bool:
    """Return True if the backend responds to /api/tags within 2s. Result cached 30s."""
    now = time.monotonic()
    cached = _health_cache.get(url)
    if cached and now - cached[0] < _HEALTH_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{url}/api/tags")
            ok = r.status_code == 200
    except Exception as e:
        _log.debug("health check %s failed: %s", url, e)
        ok = False
    _health_cache[url] = (now, ok)
    return ok


async def _available_models(url: str) -> frozenset[str]:
    """Return model names available on this backend (/api/tags). Cached 60s."""
    now = time.monotonic()
    cached = _models_cache.get(url)
    if cached and now - cached[0] < _MODELS_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{url}/api/tags")
            models: frozenset[str] = frozenset(m["name"] for m in r.json().get("models", []))
    except Exception as e:
        _log.debug("model list %s failed: %s", url, e)
        models = frozenset()
    _models_cache[url] = (now, models)
    return models


async def _loaded_models(url: str) -> frozenset[str]:
    """Return model names currently in VRAM on this backend (/api/ps). Cached 5s."""
    now = time.monotonic()
    cached = _loaded_cache.get(url)
    if cached and now - cached[0] < _LOADED_TTL:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{url}/api/ps")
            loaded: frozenset[str] = frozenset(m["name"] for m in r.json().get("models", []))
    except Exception as e:
        _log.debug("loaded models %s failed: %s", url, e)
        loaded = frozenset()
    _loaded_cache[url] = (now, loaded)
    return loaded


async def select_backend(model: str = "") -> str:
    """Return the best Ollama backend URL for this request.

    Fast path: returns immediately for single-backend setups.
    Multi-backend: runs health + model checks in parallel via asyncio.gather.
    """
    if len(BACKENDS) == 1:
        return BACKENDS[0]

    # 1. Filter to healthy backends (parallel health checks)
    health = await asyncio.gather(*(_backend_healthy(b) for b in BACKENDS))
    healthy = [b for b, ok in zip(BACKENDS, health, strict=False) if ok]

    if not healthy:
        _log.warning("all Ollama backends unreachable — falling back to %s", BACKENDS[0])
        return BACKENDS[0]

    if len(healthy) == 1:
        return healthy[0]

    # 2. Prefer backends that have the requested model (parallel model list checks)
    if model:
        avail = await asyncio.gather(*(_available_models(b) for b in healthy))
        with_model = [b for b, ms in zip(healthy, avail, strict=False) if model in ms]
        if with_model:
            healthy = with_model

    if len(healthy) == 1:
        return healthy[0]

    # 3. Prefer warm — model already loaded in VRAM (parallel /api/ps checks)
    if model:
        loaded = await asyncio.gather(*(_loaded_models(b) for b in healthy))
        warm = [b for b, ls in zip(healthy, loaded, strict=False) if model in ls]
        if warm:
            return warm[0]

    # 4. Random among remaining candidates (distributes load over time; not crypto use)
    return random.choice(healthy)  # noqa: S311
