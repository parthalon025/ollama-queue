"""Health endpoint."""

from __future__ import annotations

import time

from fastapi import APIRouter

import ollama_queue.api as _api
from ollama_queue.sensing import HealthMonitor

router = APIRouter()

# Hardware constants — read once at startup (GPU name, CPU count, VRAM total don't change)
_monitor = HealthMonitor()
_CPU_COUNT = _monitor.get_cpu_count()
_GPU_NAME = _monitor.get_gpu_name()  # None on non-GPU machines
_VRAM_TOTAL_GB = _monitor.get_vram_total_gb()  # 0.0 on non-GPU machines


@router.get("/api/health/status")
def get_health_status():
    """Lightweight probe for uptime checkers and container health checks.

    Returns only daemon liveness and uptime — no telemetry log.
    Response is always under 100 bytes.
    """
    db = _api.db
    daemon_state = db.get_daemon_state()
    state = daemon_state.get("state") or "idle"
    uptime_since = daemon_state.get("uptime_since")
    uptime_s = int(time.time() - uptime_since) if uptime_since else 0
    return {
        "ok": True,
        "daemon_state": state,
        "uptime_s": uptime_s,
    }


@router.get("/api/health")
def get_health(hours: int = 24):
    db = _api.db
    daemon_state = db.get_daemon_state()
    burst_regime = daemon_state.get("burst_regime") or "unknown"
    return {
        "log": db.get_health_log(hours=hours),
        "burst_regime": burst_regime,
        "cpu_count": _CPU_COUNT,
        "gpu_name": _GPU_NAME,
        "vram_total_gb": _VRAM_TOTAL_GB,
    }
