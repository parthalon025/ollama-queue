"""Multi-backend Ollama router for the proxy layer.

Selects the best backend for each request using a five-tier strategy:
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

# Module-level DB reference — set by register_routes() at startup.
# Used to merge DB-registered backends with env-var backends.
_db = None

# Parse backends once at import time. Tests may patch BACKENDS directly.
_raw = os.environ.get("OLLAMA_BACKENDS", "")
_ENV_BACKENDS: list[str] = (
    [b.strip().rstrip("/") for b in _raw.split(",") if b.strip()]
    if _raw
    else [os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")]
)
BACKENDS: list[str] = list(_ENV_BACKENDS)

# OLLAMA_QUEUE_PORT is read at call time (not module load) so env changes are picked up
# without a restart. Default: 7683.

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
    """Return per-backend weights. DB weights take precedence over OLLAMA_BACKEND_WEIGHTS env var."""
    import contextlib

    import ollama_queue.api as _api  # deferred: avoids circular import at module load

    db_weights: dict[str, float] = {}
    if _api.db is not None:
        with contextlib.suppress(Exception):
            for row in _api.db.list_backends():
                db_weights[row["url"].rstrip("/")] = float(row["weight"])
    return [db_weights.get(b.rstrip("/"), _BACKEND_WEIGHTS.get(b.rstrip("/"), 1.0)) for b in backends]


# Cache TTLs (seconds)
_HEALTH_TTL = 30.0
_MODELS_TTL = 60.0
_LOADED_TTL = 5.0
_HW_TTL = 10.0
_GPU_NAME_TTL = 600.0  # GPU names don't change — refresh every 10 minutes
_VRAM_TOTAL_TTL = 600.0  # Total VRAM is hardware-constant — refresh every 10 minutes

# Module-level caches: url -> (timestamp, data)
_health_cache: dict[str, tuple[float, bool]] = {}
_models_cache: dict[str, tuple[float, frozenset[str]]] = {}
_loaded_cache: dict[str, tuple[float, frozenset[str]]] = {}
_hw_cache: dict[str, tuple[float, float]] = {}
_gpu_name_cache: dict[str, tuple[float, str | None]] = {}
_vram_total_cache: dict[str, tuple[float, float]] = {}


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
            if r.status_code == 200:
                models: frozenset[str] = frozenset(m["name"] for m in r.json().get("models", []))
            else:
                models = frozenset()
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
            if r.status_code == 200:
                loaded: frozenset[str] = frozenset(m["name"] for m in r.json().get("models", []))
            else:
                loaded = frozenset()
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
        queue_url = urlunparse(
            parsed._replace(netloc=f"{parsed.hostname}:{int(os.environ.get('OLLAMA_QUEUE_PORT', '7683'))}")
        )
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

    Cache TTL strategy:
    - HTTP 200 with name  → cache 600s (hardware doesn't change)
    - HTTP 200, name=null → cache 600s (WSL2/Docker quirk — legitimate null)
    - Network error       → cache 30s (backend may be restarting; retry sooner)

    Without this distinction, a container restart leaves BackendsPanel showing
    "unknown" for 10 minutes even after the backend comes back up.
    """
    now = time.monotonic()
    cached = _gpu_name_cache.get(url)
    if cached and now - cached[0] < _GPU_NAME_TTL:
        return cached[1]
    name: str | None = None
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        queue_url = urlunparse(
            parsed._replace(netloc=f"{parsed.hostname}:{int(os.environ.get('OLLAMA_QUEUE_PORT', '7683'))}")
        )
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{queue_url}/api/health")
            if r.status_code == 200:
                name = r.json().get("gpu_name")
        # Successful response (name may still be None for no-GPU machines) — cache full TTL
        _gpu_name_cache[url] = (now, name)
    except Exception as e:
        _log.debug("gpu name %s failed: %s", url, e)
        # Network failure — cache with short TTL so we retry after the backend recovers
        _gpu_name_cache[url] = (now - _GPU_NAME_TTL + 30.0, None)
    return name


async def _backend_vram_total_gb(url: str) -> float:
    """Return total VRAM (GB) for this backend's host machine.

    Plain English: Queries the remote ollama-queue /api/health endpoint for the
    vram_total_gb field (populated from nvidia-smi at startup). Used by the gpu_only
    filter to check whether a model's estimated VRAM will fit.

    Returns 0.0 when the backend has no GPU or is unreachable. Cached 10min.
    """
    now = time.monotonic()
    cached = _vram_total_cache.get(url)
    if cached and now - cached[0] < _VRAM_TOTAL_TTL:
        return cached[1]
    total = 0.0
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        queue_url = urlunparse(
            parsed._replace(netloc=f"{parsed.hostname}:{int(os.environ.get('OLLAMA_QUEUE_PORT', '7683'))}")
        )
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{queue_url}/api/health")
            if r.status_code == 200:
                total = float(r.json().get("vram_total_gb") or 0.0)
        _vram_total_cache[url] = (now, total)
    except Exception as e:
        _log.debug("vram_total_gb %s failed: %s", url, e)
        _vram_total_cache[url] = (now, 0.0)
    return total


def _get_inference_modes() -> dict[str, str]:
    """Return {url: inference_mode} for all DB-registered backends.

    Plain English: Reads per-backend inference mode settings from the DB so the
    router can enforce gpu_only restrictions. Returns cpu_shared for any URL not
    explicitly configured (env-var backends not in DB default to cpu_shared).
    """
    import contextlib

    import ollama_queue.api as _api  # deferred: same pattern as _get_weights

    modes: dict[str, str] = {}
    if _api.db is not None:
        with contextlib.suppress(Exception):
            for row in _api.db.list_backends():
                modes[row["url"].rstrip("/")] = row.get("inference_mode", "cpu_shared")
    return modes


async def _apply_gpu_only_filter(healthy: list[str], model: str) -> list[str]:
    """Remove gpu_only backends where the model's estimated VRAM won't fit.

    Plain English: For backends configured as gpu_only, checks whether the model
    would fit in available VRAM (total_gb x (1 - used%)). If not, removes the
    backend from routing candidates so Ollama won't fall back to CPU RAM there.
    Returns the original list unchanged if no gpu_only backends are in play or if
    VRAM data is unavailable (fail-open to preserve routing).
    """
    import contextlib

    import ollama_queue.api as _api

    modes = _get_inference_modes()
    gpu_only_set = {u for u, m in modes.items() if m == "gpu_only"}
    healthy_keys = {b.rstrip("/") for b in healthy}

    if not (gpu_only_set & healthy_keys):
        return healthy  # fast path: no gpu_only backends in healthy set

    vram_totals, vram_pcts = await asyncio.gather(
        asyncio.gather(*(_backend_vram_total_gb(b) for b in healthy)),
        asyncio.gather(*(_backend_vram_pct(b) for b in healthy)),
    )

    needed_mb = 0.0
    if _api.db is not None:
        with contextlib.suppress(Exception):
            from ollama_queue.models.client import OllamaModels

            needed_mb = OllamaModels().estimate_vram_mb(model, _api.db)

    filtered = []
    for b, total_gb, pct in zip(healthy, vram_totals, vram_pcts, strict=False):
        if b.rstrip("/") in gpu_only_set and total_gb > 0 and needed_mb > 0:
            avail_mb = total_gb * 1024.0 * max(0.0, 1.0 - pct / 100.0)
            if needed_mb > avail_mb:
                _log.debug("gpu_only filter: %s excluded (need %.0fMB avail %.0fMB)", b, needed_mb, avail_mb)
                continue
        filtered.append(b)

    # Never leave the candidate list empty — fail-open so requests aren't dropped
    return filtered if filtered else healthy


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
    # 0. gpu_only filter — exclude backends where the model won't fit in VRAM
    healthy = await _apply_gpu_only_filter(healthy, model)

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
    # Capture once — refresh_backends_from_db() may reassign the module-level name
    # at any asyncio await point, causing zip() length mismatches if we re-read BACKENDS.
    backends = BACKENDS
    if len(backends) == 1:
        return backends[0]

    # 1. Filter to healthy backends (parallel health checks)
    health = await asyncio.gather(*(_backend_healthy(b) for b in backends))
    healthy = [b for b, ok in zip(backends, health, strict=False) if ok]

    if not healthy:
        _log.warning("all Ollama backends unreachable — falling back to %s", backends[0])
        return backends[0]

    if len(healthy) == 1:
        return healthy[0]

    if model:
        healthy = await _route_by_model(healthy, model)
        if len(healthy) == 1:
            return healthy[0]

    # 5. Weighted random among remaining candidates — higher weight = more traffic share
    weights = _get_weights(healthy)
    return random.choices(healthy, weights=weights, k=1)[0]  # noqa: S311


def has_healthy_remote_backend() -> bool:
    """Return True if at least one non-local backend is cached as healthy.

    Plain English: Reads the health cache without making any network calls.
    Used by the admission gate (_can_admit) to decide whether to bypass the
    local CPU load check — if inference will happen on a remote machine, the
    local CPU load shouldn't block the job.

    Decision it drives: When True, _can_admit skips the CPU-load pause for jobs
    that will proxy to the remote backend.  RAM/VRAM/Swap gates still apply.

    A backend is considered 'remote' if its URL doesn't point to localhost or
    127.0.0.1 (i.e. it's not the same machine running the daemon).
    """
    if len(BACKENDS) <= 1:
        return False  # single-backend — no remote to route to
    now = time.monotonic()
    for url in BACKENDS:
        # Skip local backends — they share CPU with the daemon
        if "127.0.0.1" in url or "localhost" in url:
            continue
        cached = _health_cache.get(url)
        if cached and now - cached[0] < _HEALTH_TTL and cached[1]:
            return True
    return False


def invalidate_backend_caches(url: str) -> None:
    """Remove a specific URL from all backend caches after add/remove operations.

    Plain English: When a backend is added or removed via the API, stale cache
    entries for that URL must be evicted so the next routing decision reflects
    the current backend list rather than a cached state from before the change.
    """
    for cache in (_health_cache, _models_cache, _loaded_cache, _hw_cache, _gpu_name_cache, _vram_total_cache):
        cache.pop(url, None)  # type: ignore[union-attr]  # all caches are dicts; mypy infers object from list


def refresh_backends_from_db() -> None:
    """Rebuild BACKENDS list from env-var baseline + DB-registered additions.

    Plain English: Called after add/remove operations so the in-process BACKENDS
    list (used by select_backend and get_backends) stays in sync with the DB.
    Env-var backends are always included as the read-only baseline; DB entries
    are runtime additions. Order is preserved; duplicates are deduplicated.
    """
    global BACKENDS
    db_urls: list[str] = []
    if _db is not None:
        try:
            db_urls = [b["url"] for b in _db.list_backends()]
        except Exception as e:
            _log.warning("refresh_backends_from_db: DB read failed: %s", e)
    # Merge: env-var first, then DB additions. dict.fromkeys preserves insertion order + dedupes.
    BACKENDS = list(dict.fromkeys(_ENV_BACKENDS + db_urls))
