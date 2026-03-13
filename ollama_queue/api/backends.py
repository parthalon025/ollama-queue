"""Backend status endpoint — per-backend health, models, and hardware metrics.

Plain English: Tells the dashboard which Ollama backends are configured, which ones
are reachable, how many models each one has, what's loaded in VRAM right now, and
how much GPU memory each one is using.

Decision it drives: The backend health panel on the Now tab shows this so the user
knows which machine is handling inference and whether either backend is under pressure.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

import ollama_queue.api.backend_router as _router

router = APIRouter()


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
