"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter

import ollama_queue.api as _api
from ollama_queue.sensing import HealthMonitor

router = APIRouter()

# CPU count is a hardware constant — read once at startup via HealthMonitor
# (same path used by the daemon's pause/resume logic)
_CPU_COUNT = HealthMonitor().get_cpu_count()


@router.get("/api/health")
def get_health(hours: int = 24):
    db = _api.db
    daemon_state = db.get_daemon_state()
    burst_regime = daemon_state.get("burst_regime") or "unknown"
    return {
        "log": db.get_health_log(hours=hours),
        "burst_regime": burst_regime,
        "cpu_count": _CPU_COUNT,
    }
