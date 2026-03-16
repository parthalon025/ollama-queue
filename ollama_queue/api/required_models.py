"""Required models endpoint — returns hardware-filtered model list for backend agents.

Plain English: Backend agents call this to find out which models they should have
installed. The queue filters the canonical model list based on each backend's VRAM
capacity (from heartbeat data), so small-GPU hosts don't waste time pulling models
they can't run efficiently.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Query

import ollama_queue.api as _api
import ollama_queue.api.backend_router as _router

_log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/required-models")
def get_required_models(backend_url: str | None = Query(None)):
    """Return the canonical model list, optionally filtered for a specific backend.

    Without backend_url: returns all models.
    With backend_url: filters by the backend's VRAM capacity from last heartbeat.

    Filtering: core=always, standard=if fits VRAM, optional=assigned to best-fit host.
    No VRAM data = core only (safe default).
    """
    db = _api.db
    settings = db.get_all_settings() if db else {}
    all_models = settings.get("required_models", [])

    if not backend_url:
        return {"models": all_models}

    url = backend_url.rstrip("/")

    # Look up VRAM from heartbeat cache
    cached = _router._vram_total_cache.get(url)
    if not cached:
        for key in list(_router._vram_total_cache.keys()):
            if key.rstrip("/") == url:
                cached = _router._vram_total_cache[key]
                break

    if not cached or (time.monotonic() - cached[0]) > 3600:
        _log.info("required-models: no VRAM data for %s, returning core only", url)
        return {"models": [m for m in all_models if m.get("tier") == "core"]}

    vram_total_gb = cached[1]
    vram_threshold_mb = vram_total_gb * 1024 * 0.95

    filtered = []
    for m in all_models:
        tier = m.get("tier", "standard")
        vram_mb = m.get("vram_mb", 0)
        if tier == "core" or (tier == "standard" and vram_mb <= vram_threshold_mb):
            filtered.append(m)
        elif tier == "optional":
            best_url = _find_best_backend_for_optional(vram_mb)
            if best_url and best_url.rstrip("/") == url:
                filtered.append(m)

    return {"models": filtered}


def _find_best_backend_for_optional(vram_mb: int) -> str | None:
    """Find the backend with the most VRAM that can fit this model."""
    best_url = None
    best_vram = 0.0
    now = time.monotonic()
    for url, (ts, vram_gb) in _router._vram_total_cache.items():
        if (now - ts) > 3600:
            continue
        if vram_gb * 1024 * 0.95 >= vram_mb and vram_gb > best_vram:
            best_vram = vram_gb
            best_url = url
    return best_url
