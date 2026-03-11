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
    known = set(db.get_all_settings().keys())
    unknown = [k for k in body if k not in known]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown setting keys: {unknown}")
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
