"""Settings and daemon control endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

import ollama_queue.api as _api

router = APIRouter()


@router.get("/api/settings")
def get_settings():
    db = _api.db
    return db.get_all_settings()


@router.put("/api/settings")
def put_settings(body: dict):
    db = _api.db
    current = db.get_all_settings()
    unknown = [k for k in body if k not in current]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown setting keys: {unknown}")
    errors = []
    for key, value in body.items():
        existing = current[key]
        # Reject non-numeric strings for settings that must be numeric.
        # SIM102: combined into one condition per ruff's requirement.
        if (
            isinstance(existing, int | float)
            and not isinstance(existing, bool)
            and (not isinstance(value, int | float) or isinstance(value, bool))
        ):
            errors.append(f"'{key}' must be a number (got {type(value).__name__})")
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))
    for key, value in body.items():
        db.set_setting(key, value)
    return {"ok": True}


# --- Daemon control ---


@router.post("/api/daemon/pause")
def daemon_pause():
    db = _api.db
    db.update_daemon_state(state="paused_manual", paused_since=time.time())
    return {"ok": True}


@router.post("/api/daemon/resume")
def daemon_resume():
    db = _api.db
    db.update_daemon_state(state="idle", paused_reason=None, paused_since=None)
    return {"ok": True}


@router.post("/api/daemon/restart")
def daemon_restart():
    """Signal the daemon to restart: transition to 'restarting', then back to 'idle'.

    The daemon's polling loop detects the 'restarting' state and re-initialises
    itself on the next cycle, after which it returns to 'idle'/'running'.
    """
    db = _api.db
    db.update_daemon_state(state="restarting")
    return {"ok": True}
