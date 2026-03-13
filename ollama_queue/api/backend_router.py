"""Multi-backend Ollama router for the proxy layer.

Selects the best backend for each request using a four-tier strategy:
  1. Health check — skip unreachable backends (2s timeout, 30s cache)
  2. Model availability — prefer backends that have the requested model (60s cache)
  3. Warm model — prefer backends with the model already loaded in VRAM (5s cache)
  4. Hardware load — prefer backends with lower VRAM pressure (10s cache)
  5. Weighted random among remaining tied candidates (OLLAMA_BACKEND_WEIGHTS)

Configure via env vars:
  OLLAMA_BACKENDS=http://host1:11434,http://host2:11434      (multi-backend)
  OLLAMA_URL=http://127.0.0.1:11434                          (single-backend fallback)
  OLLAMA_QUEUE_PORT=7683                                      (queue health port for HW checks)
  OLLAMA_BACKEND_WEIGHTS=http://host1:11434:2,http://host2:11434:1
                                                              (tie-break preference; higher = more traffic)

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

# Port where ollama-queue health endpoint is reachable on each backend host.
# Used to derive queue URL from Ollama backend URL for VRAM pressure checks.
_QUEUE_PORT = int(os.environ.get("OLLAMA_QUEUE_PORT", "7683"))

# Parse per-backend weights for tie-break routing: OLLAMA_BACKEND_WEIGHTS=url:weight,...
# Unspecified backends default to weight 1. Used by weighted random in select_backend.
_weights_raw = os.environ.get("OLLAMA_BACKEND_WEIGHTS", "")
_BACKEND_WEIGHTS: dict[str, float] = {}
if _weights_raw:
    import contextlib

    for _entry in _weights_raw.split(","):
        _entry = _entry.strip()
        if _entry:
            _parts = _entry.rsplit(":", 1)
            if len(_parts) == 2:
                with contextlib.suppress(ValueError):
                    _BACKEND_WEIGHTS[_parts[0].rstrip("/")] = float(_parts[1])


def _get_weights(backends: list[str]) -> list[float]:
    """Return per-backend weights in the same order as backends list. Default weight: 1.0."""
    return [_BACKEND_WEIGHTS.get(b, 1.0) for b in backends]


# Cache TTLs (seconds)
_HEALTH_TTL = 30.0
_MODELS_TTL = 60.0
_LOADED_TTL = 5.0
_HW_TTL = 10.0
_GPU_NAME_TTL = 600.0  # GPU names don't change — refresh every 10 minutes

# Module-level caches: url -> (timestamp, data)
_health_cache: dict[str, tuple[float, bool]] = {}
_models_cache: dict[str, tuple[float, frozenset[str]]] = {}
_loaded_cache: dict[str, tuple[float, frozenset[str]]] = {}
_hw_cache: dict[str, tuple[float, float]] = {}
_gpu_name_cache: dict[str, tuple[float, str | None]] = {}


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


async def _backend_vram_pct(url: str) -> float:
    """Return VRAM utilisation % for this backend's host machine.

    Queries the ollama-queue health endpoint on the same host (port OLLAMA_QUEUE_PORT).
    Returns 0.0 (no penalty) on any error — prefer known-good over unknown.
    Result cached 10s.
    """
    now = time.monotonic()
    cached = _hw_cache.get(url)
    if cached and now - cached[0] < _HW_TTL:
        return cached[1]
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        queue_url = urlunparse(parsed._replace(netloc=f"{parsed.hostname}:{_QUEUE_PORT}"))
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{queue_url}/api/health")
            if r.status_code == 200:
                log = r.json().get("log", [])
                vram = float(log[0]["vram_pct"]) if log else 0.0
            else:
                _log.debug("vram check %s: HTTP %d", url, r.status_code)
                vram = 0.0
    except Exception as e:
        _log.debug("vram check %s failed: %s", url, e)
        vram = 0.0
    _hw_cache[url] = (now, vram)
    return vram


async def _backend_gpu_name(url: str) -> str | None:
    """Return the GPU model name for this backend's host machine.

    Queries the ollama-queue health endpoint on the same host (port OLLAMA_QUEUE_PORT).
    Returns None on any error or when the backend has no GPU.
    Result cached 600s — GPU names are static hardware identifiers.
    """
    now = time.monotonic()
    cached = _gpu_name_cache.get(url)
    if cached and now - cached[0] < _GPU_NAME_TTL:
        return cached[1]
    name: str | None = None
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        queue_url = urlunparse(parsed._replace(netloc=f"{parsed.hostname}:{_QUEUE_PORT}"))
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{queue_url}/api/health")
            if r.status_code == 200:
                name = r.json().get("gpu_name")
    except Exception as e:
        _log.debug("gpu name %s failed: %s", url, e)
    _gpu_name_cache[url] = (now, name)
    return name


async def fetch_all_backend_models() -> list[dict]:
    """Fetch /api/tags from all backends and return a merged, deduplicated model list.

    Each entry: {name, size_bytes, backends: [url, ...]}
    Models available on multiple backends list all backend URLs in 'backends'.
    """

    async def _fetch_one(url: str) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{url}/api/tags")
                if r.status_code != 200:
                    _log.debug("fetch models %s: HTTP %d", url, r.status_code)
                    return []
                return [
                    {"name": m["name"], "size_bytes": m.get("size", 0), "backend_url": url}
                    for m in r.json().get("models", [])
                ]
        except Exception as e:
            _log.debug("fetch models %s failed: %s", url, e)
            return []

    results = await asyncio.gather(*(_fetch_one(b) for b in BACKENDS))

    seen: dict[str, dict] = {}
    for backend_models in results:
        for m in backend_models:
            name = m["name"]
            if name not in seen:
                seen[name] = {"name": name, "size_bytes": m["size_bytes"], "backends": [m["backend_url"]]}
            elif m["backend_url"] not in seen[name]["backends"]:
                seen[name]["backends"].append(m["backend_url"])

    return list(seen.values())


async def _route_by_model(healthy: list[str], model: str) -> list[str]:
    """Apply model-aware tiers (availability → warm → HW pressure) to a healthy list.

    Returns a narrowed list of candidates. Caller picks from this list (random or first).
    Extracted to keep select_backend under the PLR0911 return-statement limit.
    """
    # 2. Prefer backends that have the requested model (parallel model list checks)
    avail = await asyncio.gather(*(_available_models(b) for b in healthy))
    with_model = [b for b, ms in zip(healthy, avail, strict=False) if model in ms]
    if with_model:
        healthy = with_model

    if len(healthy) == 1:
        return healthy

    # 3. Prefer warm — model already loaded in VRAM (parallel /api/ps checks)
    loaded = await asyncio.gather(*(_loaded_models(b) for b in healthy))
    warm = [b for b, ls in zip(healthy, loaded, strict=False) if model in ls]
    if warm:
        healthy = warm  # narrow to warm candidates; HW check breaks ties below

    if len(healthy) == 1:
        return healthy

    # 4. Prefer backend with lower VRAM pressure (parallel queue health checks, 10s cache)
    hw = await asyncio.gather(*(_backend_vram_pct(b) for b in healthy))
    min_vram = min(hw)
    # 5% tolerance — avoids penalising a backend for trivial measurement jitter
    low_vram = [b for b, v in zip(healthy, hw, strict=False) if v <= min_vram + 5.0]
    return low_vram if low_vram else healthy


async def select_backend(model: str = "") -> str:
    """Return the best Ollama backend URL for this request.

    Fast path: returns immediately for single-backend setups.
    Multi-backend: runs health + model + hardware checks in parallel via asyncio.gather.
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

    if model:
        healthy = await _route_by_model(healthy, model)
        if len(healthy) == 1:
            return healthy[0]

    # 5. Weighted random among remaining candidates — higher weight = more traffic share
    weights = _get_weights(healthy)
    return random.choices(healthy, weights=weights, k=1)[0]  # noqa: S311
