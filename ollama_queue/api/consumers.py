"""Consumer management and intercept mode endpoints."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import ollama_queue.api as _api
from ollama_queue.config.intercept import disable_intercept, enable_intercept, get_intercept_status
from ollama_queue.config.patcher import check_health, patch_consumer, revert_consumer
from ollama_queue.config.scanner import run_scan

_log = logging.getLogger(__name__)

router = APIRouter()


class ConsumerIncludeRequest(BaseModel):
    restart_policy: str = "deferred"
    force_streaming_override: bool = False
    system_confirm: bool = False


@router.get("/api/consumers")
def list_consumers():
    db = _api.db
    return db.list_consumers()


@router.post("/api/consumers/scan")
def scan_consumers():
    db = _api.db
    return run_scan(db)


@router.post("/api/consumers/{consumer_id}/include")
def include_consumer(consumer_id: int, body: ConsumerIncludeRequest):
    db = _api.db
    import threading as _threading
    import time as _time

    consumer = db.get_consumer(consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="Consumer not found")

    if consumer.get("is_managed_job"):
        raise HTTPException(
            status_code=409,
            detail="Managed queue job — including would cause a deadlock (Lesson #1733)",
        )

    if consumer.get("platform") == "windows":
        raise HTTPException(
            status_code=422,
            detail="Auto-patch not supported on Windows. Use the generated snippet.",
        )

    if consumer.get("streaming_confirmed") and not body.force_streaming_override:
        raise HTTPException(
            status_code=422,
            detail="Streaming detected. Proxy forces stream=False. Send force_streaming_override=true to confirm.",
        )

    patch_path = consumer.get("patch_path", "")
    if patch_path.startswith("/etc/systemd/system") and not body.system_confirm:
        raise HTTPException(
            status_code=422,
            detail="System path requires explicit confirmation. Send system_confirm=true.",
        )

    try:
        result = patch_consumer({**consumer, "restart_policy": body.restart_policy})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Patch failed: {e}") from e
    db.update_consumer(
        consumer_id,
        status=result.get("status", "patched"),
        patch_applied=1 if result.get("patch_applied") else 0,
        patch_type=result.get("patch_type"),
        patch_snippet=result.get("patch_snippet"),
        onboarded_at=int(_time.time()),
    )

    if result.get("status") == "patched":
        _health_consumer = {**consumer, "id": consumer_id}

        def _run_health_check() -> None:
            try:
                check_health(_health_consumer, db)
            except Exception:
                _log.error("Health check failed for consumer %d", consumer_id, exc_info=True)

        _threading.Thread(target=_run_health_check, daemon=True).start()

    return db.get_consumer(consumer_id)


@router.post("/api/consumers/{consumer_id}/ignore")
def ignore_consumer(consumer_id: int):
    db = _api.db
    consumer = db.get_consumer(consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="Consumer not found")
    db.update_consumer(consumer_id, status="ignored")
    return db.get_consumer(consumer_id)


@router.post("/api/consumers/{consumer_id}/revert")
def revert_consumer_endpoint(consumer_id: int):
    db = _api.db
    consumer = db.get_consumer(consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="Consumer not found")
    try:
        revert_consumer(consumer)
    except Exception as e:
        _log.error("revert_consumer failed for consumer %d: %s", consumer_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"File revert failed: {e}") from e
    db.update_consumer(consumer_id, status="discovered", patch_applied=0, health_status="unknown")
    return db.get_consumer(consumer_id)


@router.get("/api/consumers/{consumer_id}/health")
def consumer_health(consumer_id: int):
    db = _api.db
    consumer = db.get_consumer(consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="Consumer not found")
    return check_health(consumer, db)


# --- Intercept mode endpoints ---


@router.post("/api/consumers/intercept/enable")
def intercept_enable():
    db = _api.db
    import platform as _plat

    if _plat.system() != "Linux":
        raise HTTPException(status_code=422, detail="iptables intercept is Linux-only")
    included = [c for c in db.list_consumers() if c.get("status") in ("patched", "included")]
    if not included:
        raise HTTPException(
            status_code=422,
            detail="Include at least one consumer before enabling intercept mode.",
        )
    uid = os.getuid()
    result = enable_intercept(uid=uid, queue_port=7683)
    if not result.get("enabled"):
        raise HTTPException(status_code=422, detail=result.get("error", "iptables failed"))
    db.set_setting("intercept_mode_enabled", "1")
    db.set_setting("intercept_mode_uid", str(uid))
    return result


@router.post("/api/consumers/intercept/disable")
def intercept_disable():
    db = _api.db
    uid = int(db.get_setting("intercept_mode_uid") or os.getuid())
    result = disable_intercept(uid=uid, queue_port=7683)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    db.set_setting("intercept_mode_enabled", "0")
    return result


@router.get("/api/consumers/intercept/status")
def intercept_status():
    db = _api.db
    uid = int(db.get_setting("intercept_mode_uid") or os.getuid())
    return get_intercept_status(uid=uid, queue_port=7683)
