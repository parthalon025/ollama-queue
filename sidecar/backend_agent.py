"""backend_agent.py — Ollama backend agent.

Runs alongside Ollama on each remote host. Provides:
- Reconciliation loop: fetches required models from queue, pulls missing ones
- Heartbeat: pushes health + system metrics to queue every 30s
- Command endpoints: sync-models, update-ollama, restart-ollama
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
import urllib.parse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI

VERSION = os.environ.get("AGENT_VERSION", "0.1.0")
QUEUE_URL = os.environ.get("QUEUE_URL", "http://127.0.0.1:7683").rstrip("/")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:11434").rstrip("/")
DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR", "/data"))
RECONCILE_INTERVAL = int(os.environ.get("RECONCILE_INTERVAL", "300"))
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))
PORT = int(os.environ.get("AGENT_PORT", "11435"))
OLLAMA_VOLUME = Path(os.environ.get("OLLAMA_VOLUME", "/ollama"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger("backend-agent")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start background loops on app startup, cancel on shutdown."""
    reconcile_task = asyncio.create_task(_reconcile_loop())
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    _log.info("Agent started: queue=%s ollama=%s backend=%s", QUEUE_URL, OLLAMA_URL, BACKEND_URL)
    yield
    reconcile_task.cancel()
    heartbeat_task.cancel()
    for task in (reconcile_task, heartbeat_task):
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="ollama-backend-agent", docs_url=None, redoc_url=None, lifespan=_lifespan)

# State
_last_reconcile: float | None = None
_last_reconcile_result: dict = {}
_cached_models_path = DATA_DIR / "required-models.json"


# -- System metrics ------------------------------------------------------------


def _read_cpu_pct() -> float:
    """Read CPU usage from /proc/stat (two samples, 100ms apart)."""
    try:

        def _read_idle():
            with open("/proc/stat") as f:
                parts = f.readline().split()
            total = sum(int(x) for x in parts[1:])
            idle = int(parts[4])
            return total, idle

        t1, i1 = _read_idle()
        time.sleep(0.1)
        t2, i2 = _read_idle()
        dt = t2 - t1
        if dt == 0:
            return 0.0
        return round((1.0 - (i2 - i1) / dt) * 100, 1)
    except Exception:
        _log.warning("Failed to read CPU from /proc/stat")
        return 0.0


def _read_ram() -> tuple[float, float]:
    """Read RAM from /proc/meminfo. Returns (pct, total_gb)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total_kb = info.get("MemTotal", 0)
        avail_kb = info.get("MemAvailable", 0)
        if total_kb == 0:
            return 0.0, 0.0
        return round((1.0 - avail_kb / total_kb) * 100, 1), round(total_kb / 1024 / 1024, 1)
    except Exception:
        _log.warning("Failed to read RAM from /proc/meminfo")
        return 0.0, 0.0


def _read_disk() -> tuple[float, float, float]:
    """Read disk usage. Returns (used_pct, total_gb, used_gb)."""
    try:
        path = str(OLLAMA_VOLUME) if OLLAMA_VOLUME.exists() else "/"
        usage = shutil.disk_usage(path)
        total_gb = round(usage.total / 1024**3, 1)
        used_gb = round(usage.used / 1024**3, 1)
        pct = round(usage.used / usage.total * 100, 1) if usage.total else 0.0
        return pct, total_gb, used_gb
    except Exception:
        _log.warning("Failed to read disk usage")
        return 0.0, 0.0, 0.0


def _read_ollama_storage_gb() -> float:
    """Total size of Ollama models directory."""
    try:
        models_dir = OLLAMA_VOLUME / "models"
        if not models_dir.exists():
            return 0.0
        return round(sum(f.stat().st_size for f in models_dir.rglob("*") if f.is_file()) / 1024**3, 1)
    except Exception:
        _log.warning("Failed to read Ollama storage size")
        return 0.0


# -- Ollama helpers ------------------------------------------------------------


async def _ollama_tags() -> list[str]:
    """Fetch installed model names from Ollama /api/tags."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            resp = await c.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                return [m["name"] for m in resp.json().get("models", [])]
    except Exception as e:
        _log.warning("Failed to get Ollama tags: %s", e)
    return []


async def _ollama_version() -> str | None:
    """Fetch Ollama version string."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            resp = await c.get(f"{OLLAMA_URL}/api/version")
            if resp.status_code == 200:
                return resp.json().get("version")
    except Exception:
        _log.warning("Failed to get Ollama version")
    return None


async def _ollama_healthy() -> bool:
    """Check if Ollama is reachable and responding."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            resp = await c.get(f"{OLLAMA_URL}/api/tags")
            return resp.status_code == 200
    except Exception:
        _log.debug("Ollama health check failed")
        return False


async def _ollama_pull(model: str) -> bool:
    """Pull a model from the Ollama registry."""
    _log.info("Pulling model: %s", model)
    try:
        async with httpx.AsyncClient(timeout=3600.0) as c:
            resp = await c.post(f"{OLLAMA_URL}/api/pull", json={"model": model, "stream": False})
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    _log.info("Pull complete: %s", model)
                    return True
            _log.warning("Pull failed for %s: HTTP %d", model, resp.status_code)
    except Exception as e:
        _log.error("Pull error for %s: %s", model, e)
    return False


# -- Required models -----------------------------------------------------------


async def _fetch_required_models() -> list[dict]:
    """Fetch the required model list from the queue, with disk cache fallback."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.get(f"{QUEUE_URL}/api/required-models", params={"backend_url": BACKEND_URL})
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                _cached_models_path.write_text(json.dumps(models))
                return models
    except Exception as e:
        _log.warning("Failed to fetch required models: %s", e)
    # Disk cache fallback
    if _cached_models_path.exists():
        try:
            return json.loads(_cached_models_path.read_text())
        except Exception:
            _log.warning("Failed to read cached required models")
    return []


# -- Reconciliation -----------------------------------------------------------


async def _reconcile() -> dict:
    """Fetch required models, diff against installed, pull missing."""
    global _last_reconcile, _last_reconcile_result
    required = await _fetch_required_models()
    required_names = [m["name"] for m in required]
    installed = await _ollama_tags()
    missing = [n for n in required_names if n not in installed]
    extra = [n for n in installed if n not in required_names]
    pulled, failed = [], []
    for model in missing:
        if await _ollama_pull(model):
            pulled.append(model)
        else:
            failed.append(model)
    _last_reconcile = time.time()
    _last_reconcile_result = {
        "required": len(required_names),
        "installed": len(installed),
        "missing_before": len(missing),
        "pulled": pulled,
        "failed": failed,
        "extra": extra,
    }
    _log.info(
        "Reconcile: %d required, %d installed, %d pulled, %d failed",
        len(required_names),
        len(installed),
        len(pulled),
        len(failed),
    )
    return _last_reconcile_result


# -- Heartbeat ----------------------------------------------------------------


async def _send_heartbeat():
    """Push health + system metrics to the queue server."""
    installed = await _ollama_tags()
    ollama_ver = await _ollama_version()
    healthy = await _ollama_healthy()
    ram_pct, ram_total_gb = _read_ram()
    disk_pct, disk_total_gb, disk_used_gb = _read_disk()
    payload = {
        "healthy": healthy,
        "cpu_pct": _read_cpu_pct(),
        "ram_pct": ram_pct,
        "ram_total_gb": ram_total_gb,
        "disk_total_gb": disk_total_gb,
        "disk_used_gb": disk_used_gb,
        "disk_pct": disk_pct,
        "ollama_storage_gb": _read_ollama_storage_gb(),
        "available_models": installed,
        "agent_version": VERSION,
        "ollama_version": ollama_ver,
        "last_reconcile": _last_reconcile,
    }
    try:
        encoded_url = urllib.parse.quote(BACKEND_URL, safe="")
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.put(f"{QUEUE_URL}/api/backends/{encoded_url}/heartbeat", json=payload)
            if resp.status_code != 200:
                _log.warning("Heartbeat rejected: HTTP %d", resp.status_code)
    except Exception as e:
        _log.warning("Heartbeat failed: %s", e)


# -- Background loops ---------------------------------------------------------


async def _reconcile_loop():
    """Periodically reconcile installed models with required list."""
    await asyncio.sleep(10)
    while True:
        try:
            await _reconcile()
        except Exception as e:
            _log.error("Reconcile error: %s", e)
        await asyncio.sleep(RECONCILE_INTERVAL)


async def _heartbeat_loop():
    """Periodically push heartbeat to the queue server."""
    await asyncio.sleep(5)
    while True:
        try:
            await _send_heartbeat()
        except Exception as e:
            _log.error("Heartbeat error: %s", e)
        await asyncio.sleep(HEARTBEAT_INTERVAL)


# -- Endpoints ----------------------------------------------------------------


@app.get("/health")
async def health():
    return {
        "ok": True,
        "ollama_healthy": await _ollama_healthy(),
        "ollama_url": OLLAMA_URL,
        "queue_url": QUEUE_URL,
        "backend_url": BACKEND_URL,
        "version": VERSION,
        "last_reconcile": _last_reconcile,
    }


@app.get("/version")
def version():
    return {"version": VERSION}


@app.get("/status")
async def status():
    installed = await _ollama_tags()
    required = await _fetch_required_models()
    required_names = [m["name"] for m in required]
    missing = [n for n in required_names if n not in installed]
    # Wrap blocking I/O in to_thread to avoid stalling the event loop
    cpu_pct = await asyncio.to_thread(_read_cpu_pct)
    ram_pct, ram_total_gb = await asyncio.to_thread(_read_ram)
    disk_pct, disk_total_gb, disk_used_gb = await asyncio.to_thread(_read_disk)
    storage_gb = await asyncio.to_thread(_read_ollama_storage_gb)
    return {
        "backend_url": BACKEND_URL,
        "version": VERSION,
        "ollama_healthy": await _ollama_healthy(),
        "ollama_version": await _ollama_version(),
        "models": {
            "installed": sorted(installed),
            "required": sorted(required_names),
            "missing": sorted(missing),
        },
        "system": {
            "cpu_pct": cpu_pct,
            "ram_pct": ram_pct,
            "ram_total_gb": ram_total_gb,
            "disk_pct": disk_pct,
            "disk_total_gb": disk_total_gb,
            "disk_used_gb": disk_used_gb,
            "ollama_storage_gb": storage_gb,
        },
        "last_reconcile": _last_reconcile,
        "last_reconcile_result": _last_reconcile_result,
    }


@app.post("/sync-models")
async def sync_models():
    result = await _reconcile()
    return {"ok": True, **result}


@app.post("/update-ollama")
async def update_ollama():
    """Pull latest Ollama image and recreate container."""
    try:
        import docker as docker_sdk

        client = docker_sdk.from_env()
    except Exception as e:
        return {"ok": False, "error": f"Docker unavailable: {e}"}
    log = []
    container_name = os.environ.get("OLLAMA_CONTAINER", "ollama")
    log.append("Pulling ollama/ollama:latest...")
    try:
        client.images.pull("ollama/ollama", tag="latest")
        log.append("Pull complete.")
    except Exception as e:
        return {"ok": False, "error": f"Pull failed: {e}", "log": log}
    try:
        old = client.containers.get(container_name)
    except Exception:
        _log.info("Container '%s' not found, using defaults", container_name)
        log.append(f"'{container_name}' not found, using defaults.")
        run_config = {
            "image": "ollama/ollama:latest",
            "name": container_name,
            "detach": True,
            "restart_policy": {"Name": "always"},
            "ports": {"11434/tcp": "11434"},
            "volumes": ["ollama:/root/.ollama"],
        }
    else:
        hc = old.attrs["HostConfig"]
        cfg = old.attrs["Config"]
        port_bindings = hc.get("PortBindings") or {}
        binds = [b for b in (hc.get("Binds") or []) if "docker.sock" not in b]
        run_config = {
            "image": "ollama/ollama:latest",
            "name": container_name,
            "detach": True,
            "restart_policy": {
                "Name": (hc.get("RestartPolicy") or {}).get("Name", "always"),
            },
            "ports": {k: v[0]["HostPort"] for k, v in port_bindings.items() if v},
            "volumes": binds,
            "environment": cfg.get("Env") or [],
            "runtime": hc.get("Runtime"),
            "device_requests": hc.get("DeviceRequests"),
        }
        run_config = {k: v for k, v in run_config.items() if v is not None}
        log.append("Stopping old container...")
        old.stop(timeout=10)
        old.remove()
        log.append("Removed.")
    try:
        container = client.containers.run(**run_config)
        time.sleep(2)
        container.reload()
        log.append(f"Started (status={container.status}).")
        return {"ok": True, "log": log, "status": container.status}
    except Exception as e:
        return {"ok": False, "error": f"Recreate failed: {e}", "log": log}


@app.post("/restart-ollama")
async def restart_ollama():
    """Restart the Ollama Docker container."""
    try:
        import docker as docker_sdk

        client = docker_sdk.from_env()
        container = client.containers.get(os.environ.get("OLLAMA_CONTAINER", "ollama"))
        container.restart(timeout=10)
        return {"ok": True, "status": "restarted"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")  # noqa: S104
