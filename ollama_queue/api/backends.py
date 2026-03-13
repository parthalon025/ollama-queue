"""Backend management endpoints — status, dynamic registration, weight updates.

Plain English: Tells the dashboard which Ollama backends are configured, which
are reachable, how many models each has, what's loaded in VRAM, and how much GPU
memory each is using. Also allows adding/removing backends at runtime and testing
connectivity to a specific URL.

Decision it drives:
  GET  /api/backends         — Backend health panel (Now tab + Backends tab)
  POST /api/backends         — Register a new GPU node without restarting the daemon
  DELETE /api/backends/{url} — Remove a node from routing
  PUT  /api/backends/{url}/weight — Tune routing weight for load distribution
  GET  /api/backends/{url}/test   — Verify connectivity before registering
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

import ollama_queue.api as _api
import ollama_queue.api.backend_router as _router

_log = logging.getLogger(__name__)
router = APIRouter()


# ── Request models ────────────────────────────────────────────────────────────


class AddBackendRequest(BaseModel):
    url: str
    weight: float = 1.0


# ── GET /api/backends ─────────────────────────────────────────────────────────


@router.get("/api/backends")
async def get_backends():
    """Return health and resource status for all configured Ollama backends."""
    results = []
    for url in _router.BACKENDS:
        healthy, models, loaded, vram_pct, gpu_name = await asyncio.gather(
            _router._backend_healthy(url),
            _router._available_models(url),
            _router._loaded_models(url),
            _router._backend_vram_pct(url),
            _router._backend_gpu_name(url),
        )
        results.append(
            {
                "url": url,
                "healthy": healthy,
                "model_count": len(models),
                "loaded_models": sorted(loaded),
                "vram_pct": round(vram_pct, 1),
                "gpu_name": gpu_name,
            }
        )
    return results


# ── POST /api/backends ────────────────────────────────────────────────────────


@router.post("/api/backends")
async def add_backend(req: AddBackendRequest):
    """Register a new Ollama backend. Tests connectivity before persisting.

    Plain English: Validates the URL, pings the backend to confirm it's reachable,
    then stores it in the DB so it survives daemon restarts. The in-process BACKENDS
    list is refreshed immediately so routing picks it up without a restart.
    """
    if not (req.url.startswith("http://") or req.url.startswith("https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    if not (0.1 <= req.weight <= 10.0):
        raise HTTPException(status_code=400, detail="weight must be between 0.1 and 10.0")

    db = _api.db
    existing = db.get_backend(req.url) if db else None
    if existing:
        raise HTTPException(status_code=409, detail=f"backend {req.url} already registered")

    # Connectivity test — must succeed before we persist
    try:
        async with httpx.AsyncClient(timeout=3.0, verify=False) as client:  # noqa: S501
            resp = await client.get(f"{req.url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            model_count = len(data.get("models", []))
    except Exception as e:
        _log.warning("connectivity test failed for %s: %s", req.url, e)
        raise HTTPException(status_code=502, detail=f"connectivity test failed: {e}") from e

    if db:
        db.add_backend(req.url, req.weight)

    _router.invalidate_backend_caches(req.url)
    _router.refresh_backends_from_db()

    return {"url": req.url, "weight": req.weight, "healthy": True, "model_count": model_count}


# ── DELETE /api/backends/{url} ────────────────────────────────────────────────


@router.delete("/api/backends/{url:path}")
async def remove_backend(url: str = Path(...)):
    """Remove a registered backend from the DB and evict its cache entries.

    Plain English: Stops routing traffic to this backend immediately. Env-var
    backends cannot be removed via this endpoint (they are always included).
    """
    url = unquote(url)
    db = _api.db
    removed = db.remove_backend(url) if db else False
    if not removed:
        raise HTTPException(status_code=404, detail=f"backend {url} not found")

    _router.invalidate_backend_caches(url)
    _router.refresh_backends_from_db()

    return {"removed": url}


# ── PUT /api/backends/{url}/weight ───────────────────────────────────────────


@router.put("/api/backends/{url:path}/weight")
async def update_backend_weight(url: str = Path(...), weight: float = Query(...)):
    """Update the routing weight for a registered backend.

    Plain English: Higher weight = more traffic share in weighted-random tie-breaks.
    Range 0.1-10.0. Takes effect on the next routing decision.
    """
    url = unquote(url)
    if not (0.1 <= weight <= 10.0):
        raise HTTPException(status_code=400, detail="weight must be between 0.1 and 10.0")

    db = _api.db
    updated = db.update_backend_weight(url, weight) if db else False
    if not updated:
        raise HTTPException(status_code=404, detail=f"backend {url} not found")

    return {"url": url, "weight": weight}


# ── GET /api/backends/{url}/test ─────────────────────────────────────────────


@router.get("/api/backends/{url:path}/test")
async def test_backend(url: str = Path(...)):
    """Test connectivity to a backend URL and return health + model count.

    Plain English: Lets the dashboard verify that a new node is reachable before
    submitting POST /api/backends. Never raises — always returns a JSON object
    with healthy=True/False so the UI can render the result inline.
    """
    url = unquote(url)
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:  # noqa: S501
            resp = await client.get(f"{url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            latency_ms = (time.time() - start) * 1000
            return {
                "url": url,
                "healthy": True,
                "model_count": len(data.get("models", [])),
                "latency_ms": round(latency_ms, 1),
            }
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        _log.debug("test_backend unreachable %s: %s", url, e)
        return {
            "url": url,
            "healthy": False,
            "model_count": 0,
            "latency_ms": round(latency_ms, 1),
            "error": str(e),
        }
