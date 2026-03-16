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
import ipaddress
import logging
import time
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

import ollama_queue.api as _api
import ollama_queue.api.backend_router as _router

_WEIGHT_MIN = 0.1
_WEIGHT_MAX = 10.0


def _is_env_backend(url: str) -> bool:
    """Return True if url matches a backend in the env-var BACKENDS list.

    Plain English: Checks whether a URL is registered via the OLLAMA_BACKENDS
    env var (read-only — cannot be removed via the API). Used to distinguish
    "env-var backend, DB row not yet created" from "unknown backend, reject 404".
    Normalises trailing slashes before comparing.
    """
    normalized = url.rstrip("/")
    return any(b.rstrip("/") == normalized for b in _router.BACKENDS)


def _auto_register_if_env_backend(url: str, db, reason: str) -> None:
    """Insert an env-var backend into the DB if it isn't there yet.

    Plain English: Env-var backends are always included in routing but start
    with no DB row (nothing to persist until the user sets weight or mode).
    Calling this before a write ensures the row exists so UPDATE succeeds.
    """
    if not db.get_backend(url) and _is_env_backend(url):
        _log.info("auto-registering env-var backend %s for %s", url, reason)
        db.add_backend(url)


_log = logging.getLogger(__name__)
router = APIRouter()


def _is_env_backend(url: str) -> bool:
    """Return True if url matches a backend in the env-var BACKENDS list.

    Plain English: Checks whether a URL is registered via the OLLAMA_BACKENDS
    env var (read-only — cannot be removed via the API). Used to distinguish
    "env-var backend, DB row not yet created" from "unknown backend, reject 404".
    Normalises trailing slashes before comparing.
    """
    normalized = url.rstrip("/")
    return any(b.rstrip("/") == normalized for b in _router.BACKENDS)


# ── SSRF protection ───────────────────────────────────────────────────────────

# Deny cloud metadata endpoints to prevent SSRF via POST /api/backends.
# Private/RFC-1918 ranges are intentionally allowed: Ollama backends are
# typically LAN or Tailscale nodes (10.x, 172.16-31.x, 100.x, 192.168.x).
_SSRF_DENYLIST_NETS = [
    ipaddress.ip_network("169.254.0.0/16"),  # link-local (AWS/Azure metadata)
    ipaddress.ip_network("100.100.100.0/24"),  # Alibaba Cloud ECS metadata
]
_SSRF_DENYLIST_HOSTS = frozenset(["metadata.google.internal"])


def _is_safe_backend_url(url: str) -> bool:
    """Return True if the URL does not target a cloud metadata endpoint."""
    try:
        hostname = (urlparse(url).hostname or "").lower()
        if hostname in _SSRF_DENYLIST_HOSTS:
            return False
        addr = ipaddress.ip_address(hostname)
        return not any(addr in net for net in _SSRF_DENYLIST_NETS)
    except ValueError:
        return True  # hostname (not an IP literal) — passes SSRF check


# ── Request models ────────────────────────────────────────────────────────────


class AddBackendRequest(BaseModel):
    url: str
    weight: float = 1.0


# ── GET /api/backends ─────────────────────────────────────────────────────────


@router.get("/api/backends")
async def get_backends():
    """Return health and resource status for all configured Ollama backends.

    Each entry includes:
      weight      — routing weight (DB value, then env-var, then 1.0)
      checked_at  — unix timestamp of last health-check or heartbeat (null if never checked)
    """
    db = _api.db
    # Build url→inference_mode and url→weight maps from DB
    db_modes: dict[str, str] = {}
    db_weights: dict[str, float] = {}
    if db:
        for row in db.list_backends():
            key = row["url"].rstrip("/")
            db_modes[key] = row.get("inference_mode", "cpu_shared")
            db_weights[key] = float(row.get("weight", 1.0))

    # Monotonic→wall-time conversion anchor (computed once per request)
    _mono_now = time.monotonic()
    _wall_now = time.time()

    results = []
    for url in _router.BACKENDS:
        healthy, models, loaded, vram_pct, gpu_name = await asyncio.gather(
            _router._backend_healthy(url),
            _router._available_models(url),
            _router._loaded_models(url),
            _router._backend_vram_pct(url),
            _router._backend_gpu_name(url),
        )
        # Derive checked_at from the health-cache entry (monotonic → wall time)
        cached_health = _router._health_cache.get(url)
        checked_at = round(_wall_now - (_mono_now - cached_health[0]), 1) if cached_health else None

        # Weight: DB > env-var > 1.0
        weight = db_weights.get(url.rstrip("/"), _router._BACKEND_WEIGHTS.get(url.rstrip("/"), 1.0))

        results.append(
            {
                "url": url,
                "healthy": healthy,
                "model_count": len(models),
                "loaded_models": sorted(loaded),
                "vram_pct": round(vram_pct, 1),
                "gpu_name": gpu_name,
                "inference_mode": db_modes.get(url.rstrip("/"), "cpu_shared"),
                "weight": weight,
                "checked_at": checked_at,
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
    if not (_WEIGHT_MIN <= req.weight <= _WEIGHT_MAX):
        raise HTTPException(status_code=400, detail=f"weight must be between {_WEIGHT_MIN} and {_WEIGHT_MAX}")
    if not _is_safe_backend_url(req.url):
        raise HTTPException(status_code=400, detail="url targets a disallowed host")

    db = _api.db
    if not db:
        _log.error("add_backend: database not available — cannot register %s", req.url)
        raise HTTPException(status_code=503, detail="database not available")

    existing = db.get_backend(req.url)
    if existing:
        raise HTTPException(status_code=409, detail=f"backend {req.url} already registered")

    # Connectivity test — must succeed before we persist
    try:
        async with httpx.AsyncClient(timeout=3.0, verify=False) as client:  # noqa: S501
            resp = await client.get(f"{req.url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            model_count = len(data.get("models", []))
    except httpx.InvalidURL as e:
        raise HTTPException(status_code=400, detail=f"invalid url: {e}") from e
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        _log.warning("connectivity test failed for %s: %s", req.url, e)
        raise HTTPException(status_code=502, detail=f"connectivity test failed: {e}") from e
    except httpx.HTTPStatusError as e:
        _log.warning("backend %s returned HTTP %d", req.url, e.response.status_code)
        raise HTTPException(status_code=502, detail=f"backend returned {e.response.status_code}") from e
    except Exception as e:
        _log.error("unexpected error during connectivity test for %s: %s", req.url, e)
        raise HTTPException(status_code=500, detail="internal error during connectivity test") from e

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
    if not db:
        _log.error("remove_backend: database not available for %s", url)
        raise HTTPException(status_code=503, detail="database not available")

    removed = db.remove_backend(url)
    if not removed:
        if _is_env_backend(url):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"backend {url} is configured via OLLAMA_BACKENDS and cannot be removed via "
                    "the API — update the env var and restart the service"
                ),
            )
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
    if not (_WEIGHT_MIN <= weight <= _WEIGHT_MAX):
        raise HTTPException(status_code=400, detail=f"weight must be between {_WEIGHT_MIN} and {_WEIGHT_MAX}")

    db = _api.db
    if not db:
        _log.error("update_backend_weight: database not available for %s", url)
        raise HTTPException(status_code=503, detail="database not available")

    _auto_register_if_env_backend(url, db, "weight")

    updated = db.update_backend_weight(url, weight)
    if not updated:
        raise HTTPException(status_code=404, detail=f"backend {url} not found")

    return {"url": url, "weight": weight}


# ── PUT /api/backends/{url}/inference-mode ───────────────────────────────────


@router.put("/api/backends/{url:path}/inference-mode")
async def update_backend_inference_mode(url: str = Path(...), mode: str = Query(...)):
    """Set whether a backend restricts inference to GPU VRAM only or allows CPU overflow.

    Plain English: 'gpu_only' means the queue will skip this backend when the model
    won't fit in VRAM (avoids CPU-RAM overflow). 'cpu_shared' (default) allows Ollama
    to fall back to CPU RAM for models too large for VRAM.

    Env-var backends not yet in the DB are auto-registered on first inference-mode set.
    """
    url = unquote(url)
    if mode not in ("gpu_only", "cpu_shared"):
        raise HTTPException(status_code=400, detail="mode must be 'gpu_only' or 'cpu_shared'")

    db = _api.db
    if not db:
        raise HTTPException(status_code=503, detail="database not available")

    _auto_register_if_env_backend(url, db, "inference_mode")

    updated = db.update_backend_inference_mode(url, mode)
    if not updated:
        raise HTTPException(status_code=404, detail=f"backend {url} not found or not configured")

    return {"url": url, "inference_mode": mode}


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
        _log.info("test_backend unreachable %s: %s", url, e)
        return {
            "url": url,
            "healthy": False,
            "model_count": 0,
            "latency_ms": round(latency_ms, 1),
            "error": str(e),
        }


# ── PUT /api/backends/{url}/heartbeat ─────────────────────────────────────────


class HeartbeatRequest(BaseModel):
    """Health state pushed by a remote ollama-queue instance.

    Plain English: A remote ollama-queue daemon calls this endpoint periodically
    (recommended every 30s) to push its own health metrics directly into the primary
    instance's routing caches. This replaces the primary having to poll the remote on
    every routing decision — the remote proves reachability by contacting us.

    Fields mirror what the primary's own /api/health returns plus Ollama model state.
    All fields are optional so partial pushes work (e.g. no GPU → omit vram fields).
    """

    healthy: bool = True
    gpu_name: str | None = None
    vram_pct: float = 0.0
    vram_total_gb: float = 0.0
    loaded_models: list[str] = []
    available_models: list[str] = []
    cpu_pct: float = 0.0
    ram_pct: float = 0.0
    ram_total_gb: float = 0.0
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_pct: float = 0.0
    ollama_storage_gb: float = 0.0
    agent_version: str | None = None
    ollama_version: str | None = None
    last_reconcile: float | None = None


@router.put("/api/backends/{url:path}/heartbeat")
async def backend_heartbeat(url: str = Path(...), req: HeartbeatRequest = None):
    """Receive a health push from a remote ollama-queue instance.

    Plain English: The remote host calls this instead of waiting to be polled.
    Writes directly into the primary's in-process routing caches (health, VRAM,
    GPU name, loaded models, available models, CPU, RAM) so the next routing decision
    uses fresh data without any outbound HTTP call to the remote.

    Auto-registers the backend if it is not yet in BACKENDS or the DB — the act
    of pushing a heartbeat proves the host is reachable (no separate test needed).

    Idempotent — safe to call repeatedly. Rate limiting is the caller's responsibility.
    """
    if req is None:
        req = HeartbeatRequest()

    url = unquote(url)

    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    if not _is_safe_backend_url(url):
        raise HTTPException(status_code=400, detail="url targets a disallowed host")

    now = time.monotonic()

    # Auto-register: add to BACKENDS list and DB so routing can use it immediately.
    # No connectivity test needed — the remote proved reachability by calling us.
    if not _is_env_backend(url):
        _log.info("heartbeat auto-registering new backend %s", url)
        db = _api.db
        if db and not db.get_backend(url):
            db.add_backend(url)
        _router.refresh_backends_from_db()

    # exclude_unset=True ensures only explicitly-provided fields update their caches;
    # default-valued fields (e.g. vram_pct=0.0) don't overwrite cached data on partial pushes
    _router.receive_heartbeat(url, req.model_dump(exclude_unset=True), now)

    return {"url": url, "ok": True}


# ── POST /api/backends/{url}/command ─────────────────────────────────────────

_ALLOWED_ACTIONS = frozenset({"sync-models", "update-ollama", "restart-ollama", "status"})
_AGENT_PORT = 11435


class CommandRequest(BaseModel):
    action: str


@router.post("/api/backends/{url:path}/command")
async def backend_command(url: str = Path(...), req: CommandRequest = None):
    """Dispatch a command to a remote backend agent.

    Plain English: The dashboard or CLI calls this endpoint, and the queue
    forwards the request to the backend agent running on port 11435 of the
    same host.

    Supported actions: sync-models, update-ollama, restart-ollama, status
    """
    url = unquote(url)
    if not _is_safe_backend_url(url):
        raise HTTPException(status_code=400, detail="url targets a disallowed host")
    if req is None or req.action not in _ALLOWED_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"action must be one of: {', '.join(sorted(_ALLOWED_ACTIONS))}",
        )

    parsed = urlparse(url)
    agent_base = f"{parsed.scheme}://{parsed.hostname}:{_AGENT_PORT}"
    agent_endpoint = f"{agent_base}/{req.action}"

    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:  # noqa: S501
            resp = await client.post(agent_endpoint)
            return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        _log.warning("command %s to agent %s failed: %s", req.action, agent_base, e)
        raise HTTPException(status_code=502, detail=f"agent unreachable: {e}") from e
    except Exception as e:
        _log.error("unexpected error dispatching %s to %s: %s", req.action, agent_base, e)
        raise HTTPException(status_code=500, detail="internal error") from e
