"""Job queue endpoints: status, queue, submit, cancel, priority, ETAs, history, heatmap, durations."""

from __future__ import annotations

import json
import logging
import time
from typing import cast

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import ollama_queue.api as _api
from ollama_queue.db import DEFAULTS, Database
from ollama_queue.models.estimator import DurationEstimator
from ollama_queue.sensing.burst import _default_detector as _burst_detector

_log = logging.getLogger(__name__)

router = APIRouter()


class SubmitJobRequest(BaseModel):
    command: str
    source: str
    model: str | None = None
    priority: int | None = None
    timeout: int | None = None


# --- Status ---


@router.get("/api/status")
def get_status():
    db = _api.db
    daemon = db.get_daemon_state()
    queue = db.get_pending_jobs()
    kpis = _compute_kpis(db)
    # Include current running job details for the dashboard
    current_job = None
    if daemon and daemon.get("current_job_id"):
        current_job = db.get_job(daemon["current_job_id"])
    # Include active eval run so the Now tab can show eval activity between proxy calls.
    # eval_runs.status stays 'generating'/'judging' for the whole session (unlike
    # daemon_state which flips idle→running→idle on each individual proxy call).
    active_eval = None
    with db._lock:
        conn = db._connect()
        row = conn.execute(
            "SELECT id, status, judge_model FROM eval_runs"
            " WHERE status IN ('generating', 'judging') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            active_eval = dict(row)
    return {"daemon": daemon, "queue": queue, "kpis": kpis, "current_job": current_job, "active_eval": active_eval}


# --- Queue ---


@router.get("/api/queue")
def get_queue():
    db = _api.db
    return db.get_pending_jobs()


@router.post("/api/queue/submit")
def submit_job(req: SubmitJobRequest):
    db = _api.db
    # Admission gate: reject with 429 when queue depth exceeds max_queue_depth
    _v = db.get_setting("max_queue_depth")
    max_depth = int(_v) if _v is not None else 50
    pending = db.count_pending_jobs()
    # count_pending_jobs() excludes retry_after-deferred jobs (not actionable yet).
    # get_pending_jobs() returns all pending — the inline filter below must be preserved
    # to keep ETA computation consistent with this count.
    if pending >= max_depth:
        # Estimate drain time from current queue ETAs.
        # Filter to actionable jobs only (matching count_pending_jobs semantics)
        # so deferred retry_after jobs don't inflate the Retry-After header.
        try:
            _now = time.time()
            jobs = [j for j in db.get_pending_jobs() if not j["retry_after"] or j["retry_after"] <= _now]
            etas = DurationEstimator(db).queue_etas(jobs)
            if etas:
                drain_seconds = max(
                    1,
                    int(max(e["estimated_start_offset"] + e["estimated_duration"] for e in etas)),
                )
            else:
                drain_seconds = max(1, pending * 60)
        except Exception:
            _log.warning("ETA calculation failed for 429 response; using fallback", exc_info=True)
            drain_seconds = max(1, pending * 60)  # fallback: 1 min per pending job

        return JSONResponse(
            status_code=429,
            content={"error": "queue_full", "pending": pending, "max_queue_depth": max_depth},
            headers={"Retry-After": str(drain_seconds)},
        )
    priority: int = req.priority if req.priority is not None else cast(int, DEFAULTS["default_priority"])
    timeout: int = req.timeout if req.timeout is not None else cast(int, DEFAULTS["default_timeout_seconds"])
    job_id = db.submit_job(
        command=req.command,
        model=req.model or None,
        priority=priority,
        timeout=timeout,
        source=req.source,
    )
    _burst_detector.record_submission(time.time())
    return {"job_id": job_id}


@router.post("/api/queue/cancel/{job_id}")
def cancel_job(job_id: int):
    db = _api.db
    result = db.cancel_job(job_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Job not found")
    if result == "already_terminal":
        raise HTTPException(status_code=409, detail="Job is already in a terminal state")
    return {"ok": True}


@router.put("/api/queue/{job_id}/priority")
def set_priority(job_id: int, body: dict = Body(...)):
    db = _api.db
    priority = body.get("priority")
    if not isinstance(priority, int):
        raise HTTPException(status_code=400, detail="priority must be an integer")
    updated = db.set_job_priority(job_id, priority)
    if not updated:
        raise HTTPException(status_code=404, detail="Job not found or not pending")
    return {"ok": True}


# --- Queue ETAs ---


@router.get("/api/queue/etas")
def get_queue_etas():
    db = _api.db
    jobs = db.get_pending_jobs()
    return DurationEstimator(db).queue_etas(jobs)


# --- History ---


@router.get("/api/history")
def get_history(limit: int = 20, offset: int = 0, source: str | None = None):
    db = _api.db
    return db.get_history(limit=limit, offset=offset, source=source)


# --- Heatmap ---


@router.get("/api/heatmap")
def get_heatmap(days: int = 7):
    db = _api.db
    cutoff = time.time() - (days * 86400)
    with db._lock:
        conn = db._connect()
        rows = conn.execute(
            """SELECT strftime('%w', datetime(started_at, 'unixepoch', 'localtime')) as dow,
                      strftime('%H', datetime(started_at, 'unixepoch', 'localtime')) as hour,
                      SUM(completed_at - started_at) / 60.0 as gpu_minutes
               FROM jobs
               WHERE status='completed' AND started_at > ?
               GROUP BY dow, hour""",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Durations ---


@router.get("/api/durations")
def get_durations(days: int = 7, source: str | None = None):
    db = _api.db
    cutoff = time.time() - (days * 86400)
    with db._lock:
        conn = db._connect()
        if source:
            rows = conn.execute(
                "SELECT * FROM duration_history WHERE recorded_at >= ? AND source = ? ORDER BY recorded_at DESC",
                (cutoff, source),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM duration_history WHERE recorded_at >= ? ORDER BY recorded_at DESC",
                (cutoff,),
            ).fetchall()
    return [dict(r) for r in rows]


# --- Deferrals ---


@router.get("/api/deferred")
def list_deferred_jobs():
    """List deferred jobs with scheduled resume times."""
    db = _api.db
    return db.list_deferred()


@router.get("/api/jobs/{job_id}/log")
def get_job_log(job_id: int, tail: int = 5):
    """Return the last N lines of a job's stdout output.

    Plain English: fetches the captured stdout tail for a specific job and
    returns the last `tail` lines (default 5, max 50). Used by the dashboard
    to show live-ish log output without streaming. Returns 404 if the job
    does not exist; returns empty lines list if the job has no output yet.
    """
    db = _api.db
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    output = job.get("stdout_tail") or ""
    tail = max(1, min(tail, 50))
    lines = [ln for ln in output.splitlines() if ln.strip()]
    return {"lines": lines[-tail:]}


@router.post("/api/jobs/{job_id}/defer")
def defer_job(job_id: int, body: dict = Body(default={})):
    """User-initiated deferral."""
    db = _api.db
    reason = body.get("reason", "manual")
    context = body.get("context", "")
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] not in ("pending", "queued"):
        raise HTTPException(400, f"Cannot defer job in status '{job['status']}'")
    deferral_id = db.defer_job(job_id, reason=reason, context=context)
    return {"deferral_id": deferral_id, "job_id": job_id}


@router.post("/api/deferred/{deferral_id}/resume")
def resume_deferred(deferral_id: int):
    """Manually resume a deferred job."""
    db = _api.db
    deferral = db.get_deferral(deferral_id)
    if not deferral:
        raise HTTPException(404, "Deferral not found")
    if deferral.get("resumed_at"):
        raise HTTPException(400, "Already resumed")
    db.resume_deferred_job(deferral_id)
    return {"resumed": deferral_id, "job_id": deferral["job_id"]}


# --- KPI helpers ---


def _compute_kpis(db: Database) -> dict:
    """Compute dashboard KPIs from the database."""
    with db._lock:
        return _compute_kpis_locked(db)


def _compute_kpis_locked(db: Database) -> dict:
    """Compute dashboard KPIs (must be called with db._lock held)."""
    conn = db._connect()
    now = time.time()

    # jobs_24h: completed jobs in last 24h
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM jobs WHERE status='completed' AND completed_at >= ?",
        (now - 86400,),
    ).fetchone()
    jobs_24h = row["cnt"] if row else 0

    # avg_wait_seconds: average (started_at - submitted_at) for jobs in last 24h
    row = conn.execute(
        """SELECT AVG(started_at - submitted_at) as avg_wait
           FROM jobs
           WHERE started_at IS NOT NULL AND completed_at >= ?""",
        (now - 86400,),
    ).fetchone()
    avg_wait_seconds = round(row["avg_wait"], 1) if row and row["avg_wait"] is not None else 0.0

    # pause_minutes_24h: total minutes in paused states in last 24h
    # Each health_log entry represents one poll interval where daemon was in that state.
    # We approximate by counting paused entries x poll_interval.
    # NOTE: Use raw conn query (not db.get_setting) to avoid thread-safety issues
    # when _compute_kpis is called from FastAPI worker threads.
    setting_row = conn.execute("SELECT value FROM settings WHERE key = ?", ("poll_interval_seconds",)).fetchone()
    _raw_pi = json.loads(setting_row["value"]) if setting_row else None
    poll_interval = int(float(_raw_pi)) if _raw_pi else 5
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM health_log
           WHERE daemon_state LIKE '%paused%' AND timestamp >= ?""",
        (now - 86400,),
    ).fetchone()
    pause_minutes_24h = round((row["cnt"] * poll_interval) / 60.0, 1) if row else 0.0

    # success_rate_7d: completed / (completed + failed + killed) over 7 days
    row = conn.execute(
        """SELECT
               SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as ok,
               SUM(CASE WHEN status IN ('failed', 'killed') THEN 1 ELSE 0 END) as bad
           FROM jobs
           WHERE status IN ('completed', 'failed', 'killed') AND completed_at >= ?""",
        (now - 7 * 86400,),
    ).fetchone()
    ok = row["ok"] or 0 if row else 0
    bad = row["bad"] or 0 if row else 0
    total = ok + bad
    success_rate_7d = round(ok / total, 2) if total > 0 else 1.0

    return {
        "jobs_24h": jobs_24h,
        "avg_wait_seconds": avg_wait_seconds,
        "pause_minutes_24h": pause_minutes_24h,
        "success_rate_7d": success_rate_7d,
        "jobs_7d_ok": ok,
        "jobs_7d_bad": bad,
    }
