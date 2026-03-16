"""Models endpoints: list, catalog, pull, cancel-pull, defer, resume, metrics."""

from __future__ import annotations

import logging
import time as _time

from fastapi import APIRouter, Body, HTTPException

import ollama_queue.api as _api
from ollama_queue.models.client import OllamaModels
from ollama_queue.models.estimator import DurationEstimator

_log = logging.getLogger(__name__)

router = APIRouter()

# Simple TTL cache for catalog search results
_catalog_cache: dict[str, tuple[list, float]] = {}  # query -> (results, expires_at)
_CATALOG_CACHE_TTL = 300.0  # 5 minutes

_CURATED_MODELS = [
    {
        "name": "nomic-embed-text",
        "type_tag": "embed",
        "resource_profile": "embed",
        "description": "Best embedding model — fast, 274MB",
        "recommended": True,
    },
    {
        "name": "qwen2.5:7b",
        "type_tag": "general",
        "resource_profile": "ollama",
        "description": "Fast general-purpose model — 4.7GB",
        "recommended": True,
    },
    {
        "name": "qwen2.5-coder:14b",
        "type_tag": "coding",
        "resource_profile": "ollama",
        "description": "Best local coding model — 8.9GB",
        "recommended": True,
    },
    {
        "name": "deepseek-r1:8b",
        "type_tag": "reasoning",
        "resource_profile": "ollama",
        "description": "Reasoning model with CoT — 4.9GB",
        "recommended": True,
    },
    {
        "name": "llama3.2:3b",
        "type_tag": "general",
        "resource_profile": "ollama",
        "description": "Lightweight — 2GB",
        "recommended": False,
    },
    {
        "name": "deepseek-r1:70b",
        "type_tag": "reasoning",
        "resource_profile": "heavy",
        "description": "Max reasoning power — 39GB",
        "recommended": False,
    },
]


@router.get("/api/models")
async def get_models():
    import asyncio

    from ollama_queue.api.backend_router import BACKENDS, fetch_all_backend_models

    db = _api.db
    om = OllamaModels()
    # get_loaded() calls subprocess.run(timeout=5) — run in threadpool to avoid blocking event loop
    loaded_names = {m["name"] for m in await asyncio.to_thread(om.get_loaded)}

    if len(BACKENDS) > 1:
        # Multi-backend: merge /api/tags from all backends via HTTP
        raw = await fetch_all_backend_models()
    else:
        # list_local() calls subprocess.run(timeout=10) — run in threadpool
        local = await asyncio.to_thread(om.list_local)
        raw = [{"name": m["name"], "size_bytes": m["size_bytes"], "backends": [BACKENDS[0]]} for m in local]

    result = []
    for m in raw:
        classification = om.classify(m["name"])
        vram_mb = om.estimate_vram_mb(m["name"], db)
        est = DurationEstimator(db).estimate(m["name"], model=m["name"])
        result.append(
            {
                "name": m["name"],
                "size_bytes": m["size_bytes"],
                "vram_mb": round(vram_mb, 1),
                "resource_profile": classification["resource_profile"],
                "type_tag": classification["type_tag"],
                "loaded": m["name"] in loaded_names,
                "avg_duration_seconds": est,
                "backends": m.get("backends", [BACKENDS[0]]),
            }
        )
    return result


@router.get("/api/models/catalog")
def get_catalog(q: str | None = None):
    curated = [c.copy() for c in _CURATED_MODELS]
    search_results = []
    if q:
        import json as _json
        import urllib.parse
        import urllib.request

        now = _time.time()
        cached = _catalog_cache.get(q)
        if cached and cached[1] > now:
            search_results = cached[0]
        else:
            try:
                url = f"https://ollama.com/search?q={urllib.parse.quote(q)}&format=json"
                with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310
                    search_results = _json.loads(r.read())[:10]
                _catalog_cache[q] = (search_results, now + _CATALOG_CACHE_TTL)
            except Exception as exc:
                _log.warning("Ollama catalog search failed: %s", exc)
    return {"curated": curated, "search_results": search_results}


@router.post("/api/models/pull")
def start_pull(body: dict = Body(...)):
    db = _api.db
    model = body.get("model", "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    pull_id = OllamaModels().pull(model, db)
    return {"pull_id": pull_id}


@router.get("/api/models/pull/{pull_id}")
def get_pull_status_endpoint(pull_id: int):
    db = _api.db
    status = OllamaModels().get_pull_status(pull_id, db)
    if "error" in status:
        raise HTTPException(status_code=404, detail=status["error"])
    return status


@router.delete("/api/models/pull/{pull_id}")
def cancel_pull_endpoint(pull_id: int):
    db = _api.db
    ok = OllamaModels().cancel_pull(pull_id, db)
    return {"cancelled": ok}


# --- Metrics ---


@router.get("/api/metrics/models")
def get_model_metrics():
    """Per-model performance stats from stored job metrics."""
    db = _api.db
    return db.get_model_stats()


@router.get("/api/metrics/backends")
def get_backend_metrics():
    """Per-backend, per-model throughput stats from stored proxy metrics."""
    db = _api.db
    return db.get_backend_stats()


@router.get("/api/metrics/performance-curve")
def get_performance_curve():
    """Fitted cross-model performance curve."""
    db = _api.db
    from ollama_queue.models.performance_curve import PerformanceCurve

    stats = db.get_model_stats()
    curve = PerformanceCurve()
    points = [
        {
            "model_size_gb": s["model_size_gb"],
            "avg_tok_per_min": s["avg_tok_per_min"],
            "avg_warmup_s": s.get("avg_warmup_s"),
        }
        for s in stats.values()
        if s.get("model_size_gb") and s.get("avg_tok_per_min")
    ]
    if points:
        curve.fit(points)
    return curve.get_curve_data()
