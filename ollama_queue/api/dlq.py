"""DLQ (dead letter queue) endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

import ollama_queue.api as _api

router = APIRouter()

# NOTE: /retry-all must come before /{dlq_id}/retry


@router.get("/api/dlq")
def list_dlq(include_resolved: bool = False):
    db = _api.db
    return db.list_dlq(include_resolved=include_resolved)


@router.post("/api/dlq/retry-all")
def retry_all_dlq():
    db = _api.db
    entries = db.list_dlq()
    new_ids = [db.retry_dlq_entry(e["id"]) for e in entries]
    return {"retried": len([x for x in new_ids if x])}


@router.post("/api/dlq/{dlq_id}/retry")
def retry_dlq(dlq_id: int):
    db = _api.db
    new_id = db.retry_dlq_entry(dlq_id)
    if new_id is None:
        raise HTTPException(404, "DLQ entry not found or already resolved")
    return {"new_job_id": new_id}


@router.post("/api/dlq/{dlq_id}/dismiss")
def dismiss_dlq(dlq_id: int):
    db = _api.db
    changed = db.dismiss_dlq_entry(dlq_id)
    if not changed:
        raise HTTPException(404, "DLQ entry not found or already resolved")
    return {"dismissed": dlq_id}


@router.delete("/api/dlq")
def clear_dlq():
    db = _api.db
    n = db.clear_dlq()
    return {"cleared": n}


@router.get("/api/dlq/schedule-preview")
def dlq_schedule_preview():
    """Preview what the next DLQ auto-reschedule sweep would do."""
    db = _api.db
    from ollama_queue.sensing.system_snapshot import classify_failure

    unscheduled = db.list_dlq(unscheduled_only=True)
    if not unscheduled:
        return {"entries": [], "count": 0}

    _ct = db.get_setting("dlq.chronic_failure_threshold")
    chronic_threshold = int(_ct) if _ct is not None else 3
    preview = []
    for entry in unscheduled:
        cat = classify_failure(entry.get("failure_reason", ""))
        reschedule_count = entry.get("auto_reschedule_count") or 0
        eligible = cat != "permanent" and reschedule_count < chronic_threshold
        preview.append(
            {
                "dlq_id": entry["id"],
                "model": entry.get("model"),
                "failure_category": cat,
                "reschedule_count": reschedule_count,
                "eligible": eligible,
                "skip_reason": (
                    "permanent failure"
                    if cat == "permanent"
                    else f"chronic (count={reschedule_count})"
                    if not eligible
                    else None
                ),
            }
        )
    return {"entries": preview, "count": sum(1 for p in preview if p["eligible"])}


@router.post("/api/dlq/{dlq_id}/reschedule")
def reschedule_dlq_entry(dlq_id: int):
    """Manually trigger reschedule for a single DLQ entry."""
    db = _api.db
    from ollama_queue.sensing.system_snapshot import classify_failure

    entry = None
    for e in db.list_dlq():
        if e["id"] == dlq_id:
            entry = e
            break
    if not entry:
        raise HTTPException(404, "DLQ entry not found")

    cat = classify_failure(entry.get("failure_reason", ""))
    if cat == "permanent":
        raise HTTPException(400, "Cannot reschedule permanent failure")

    new_job_id = db.submit_job(
        command=entry["command"],
        model=entry.get("model", ""),
        priority=entry.get("priority", 0),
        timeout=entry.get("timeout", 600),
        source=entry.get("source", "dlq-manual-reschedule"),
        tag=entry.get("tag"),
        resource_profile=entry.get("resource_profile", "ollama"),
    )
    db.update_dlq_reschedule(
        dlq_id,
        rescheduled_job_id=new_job_id,
        rescheduled_for=time.time(),
        reschedule_reasoning="manual reschedule",
    )
    return {"new_job_id": new_job_id, "failure_category": cat}
