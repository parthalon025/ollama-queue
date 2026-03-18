"""Forge settings API endpoints — read/write forge.* settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

import ollama_queue.api as _api
from ollama_queue.forge.settings import FORGE_DEFAULTS

router = APIRouter(tags=["forge"])


@router.get("/api/forge/settings")
def get_forge_settings():
    result = {}
    for key, default in FORGE_DEFAULTS.items():
        val = _api.db.get_setting(key)
        result[key] = val if val is not None else default
    return result


@router.put("/api/forge/settings")
def put_forge_settings(body: dict):
    for key in body:
        if key not in FORGE_DEFAULTS:
            raise HTTPException(400, detail=f"Unknown forge setting: {key}")
    for key, val in body.items():
        _api.db.set_setting(key, str(val))
    return {"ok": True}
