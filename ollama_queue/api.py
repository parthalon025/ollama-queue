"""FastAPI REST API for ollama-queue."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import cast

_log = logging.getLogger(__name__)

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ollama_queue.db import DEFAULTS, Database

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
PROXY_WAIT_TIMEOUT = 300
PROXY_POLL_INTERVAL = 0.5


class SubmitJobRequest(BaseModel):
    command: str
    source: str
    model: str | None = None
    priority: int | None = None
    timeout: int | None = None


class RecurringJobCreate(BaseModel):
    name: str
    command: str
    interval_seconds: int | None = None
    cron_expression: str | None = None
    model: str | None = None
    priority: int = 5
    timeout: int = 600
    source: str | None = None
    tag: str | None = None
    max_retries: int = 0
    resource_profile: str = "ollama"
    pinned: bool = False


class RecurringJobUpdate(BaseModel):
    enabled: bool | None = None
    priority: int | None = None
    interval_seconds: int | None = None
    cron_expression: str | None = None
    tag: str | None = None
    command: str | None = None
    name: str | None = None
    model: str | None = None
    timeout: int | None = None
    pinned: bool | None = None


def create_app(db: Database) -> FastAPI:
    """Application factory. Takes a Database instance for test injection."""
    app = FastAPI(title="Ollama Queue")

    # --- Status ---

    @app.get("/api/status")
    def get_status():
        daemon = db.get_daemon_state()
        queue = db.get_pending_jobs()
        kpis = _compute_kpis(db)
        # Include current running job details for the dashboard
        current_job = None
        if daemon and daemon.get("current_job_id"):
            current_job = db.get_job(daemon["current_job_id"])
        return {"daemon": daemon, "queue": queue, "kpis": kpis, "current_job": current_job}

    # --- Queue ---

    @app.get("/api/queue")
    def get_queue():
        return db.get_pending_jobs()

    @app.post("/api/queue/submit")
    def submit_job(req: SubmitJobRequest):
        priority: int = req.priority if req.priority is not None else cast(int, DEFAULTS["default_priority"])
        timeout: int = req.timeout if req.timeout is not None else cast(int, DEFAULTS["default_timeout_seconds"])
        job_id = db.submit_job(
            command=req.command,
            model=req.model or "",
            priority=priority,
            timeout=timeout,
            source=req.source,
        )
        return {"job_id": job_id}

    @app.post("/api/queue/cancel/{job_id}")
    def cancel_job(job_id: int):
        db.cancel_job(job_id)
        return {"ok": True}

    @app.put("/api/queue/{job_id}/priority")
    def set_priority(job_id: int, body: dict = Body(...)):
        priority = body.get("priority")
        if not isinstance(priority, int):
            raise HTTPException(status_code=400, detail="priority must be an integer")
        updated = db.set_job_priority(job_id, priority)
        if not updated:
            raise HTTPException(status_code=404, detail="Job not found or not pending")
        return {"ok": True}

    # --- History ---

    @app.get("/api/history")
    def get_history(limit: int = 20, offset: int = 0, source: str | None = None):
        return db.get_history(limit=limit, offset=offset, source=source)

    # --- Health ---

    @app.get("/api/health")
    def get_health(hours: int = 24):
        return db.get_health_log(hours=hours)

    # --- Durations ---

    @app.get("/api/durations")
    def get_durations(days: int = 7, source: str | None = None):
        conn = db._connect()
        cutoff = time.time() - (days * 86400)
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

    # --- Heatmap ---

    @app.get("/api/heatmap")
    def get_heatmap(days: int = 7):
        conn = db._connect()
        cutoff = time.time() - (days * 86400)
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

    # --- Settings ---

    @app.get("/api/settings")
    def get_settings():
        return db.get_all_settings()

    @app.put("/api/settings")
    def put_settings(body: dict):
        known = set(db.get_all_settings().keys())
        unknown = [k for k in body if k not in known]
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown setting keys: {unknown}")
        for key, value in body.items():
            db.set_setting(key, value)
        return {"ok": True}

    # --- Daemon control ---

    @app.post("/api/daemon/pause")
    def daemon_pause():
        db.update_daemon_state(state="paused_manual", paused_since=time.time())
        return {"ok": True}

    @app.post("/api/daemon/resume")
    def daemon_resume():
        db.update_daemon_state(state="idle", paused_reason=None, paused_since=None)
        return {"ok": True}

    # --- Proxy ---

    @app.post("/api/generate")
    async def proxy_generate(body: dict = Body(...)):
        """Forward a generate request to Ollama, serializing through the queue."""
        state = db.get_daemon_state()
        if state and state.get("state") == "paused_manual":
            raise HTTPException(status_code=503, detail="Queue is manually paused")

        body["stream"] = False  # MVP: no streaming

        model = body.get("model", "")

        waited = 0.0
        claimed = False
        while waited < PROXY_WAIT_TIMEOUT:
            claimed = db.try_claim_for_proxy()
            if claimed:
                break
            await asyncio.sleep(PROXY_POLL_INTERVAL)
            waited += PROXY_POLL_INTERVAL

        if not claimed:
            raise HTTPException(status_code=504, detail="Timed out waiting for queue turn")

        # Log proxy request in the jobs table
        job_id = db.submit_job(
            command="proxy:/api/generate",
            model=model,
            priority=0,
            timeout=120,
            source="proxy",
        )
        db.start_job(job_id)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                resp = await client.post(f"{OLLAMA_URL}/api/generate", json=body)
                result = resp.json()

            # Mark job completed
            db.complete_job(
                job_id=job_id,
                exit_code=0,
                stdout_tail=str(result.get("response", ""))[:500],
                stderr_tail="",
                outcome_reason=None,
            )
            return result
        except Exception as e:
            _log.error("proxy_generate failed for job %d: %s", job_id, e, exc_info=True)
            # Mark job failed
            db.complete_job(
                job_id=job_id,
                exit_code=1,
                stdout_tail="",
                stderr_tail=str(e)[:500],
                outcome_reason=f"proxy error: {e}",
            )
            raise HTTPException(status_code=502, detail=f"Ollama request failed: {e}") from e
        finally:
            try:
                db.release_proxy_claim()
            except Exception:
                _log.exception("release_proxy_claim failed — daemon may be stuck at sentinel job_id")

    # --- Schedule (recurring jobs) ---
    # NOTE: fixed routes (/rebalance, /events) must come before parameterized /{rj_id}

    @app.get("/api/schedule")
    def list_schedule():
        return db.list_recurring_jobs()

    @app.post("/api/schedule/rebalance")
    def trigger_rebalance():
        from ollama_queue.scheduler import Scheduler

        changes = Scheduler(db).rebalance()
        return {"rebalanced": len(changes), "changes": changes}

    @app.get("/api/schedule/events")
    def get_schedule_events(limit: int = 100):
        return db.get_schedule_events(limit=limit)

    @app.get("/api/schedule/load-map")
    def get_load_map():
        from ollama_queue.scheduler import Scheduler

        slots = Scheduler(db).load_map()
        return {"slots": slots, "slot_minutes": 30, "count": len(slots)}

    @app.post("/api/schedule")
    def add_schedule(body: RecurringJobCreate):
        from ollama_queue.scheduler import Scheduler

        rj_id = db.add_recurring_job(**body.model_dump())
        Scheduler(db).rebalance()
        return db.get_recurring_job(rj_id)

    @app.put("/api/schedule/{rj_id}")
    def update_schedule(rj_id: int, body: RecurringJobUpdate):
        updated = db.update_recurring_job(rj_id, **body.model_dump(exclude_none=True))
        if not updated:
            raise HTTPException(status_code=404, detail="Recurring job not found")
        # Rebalance next_run after edit
        try:
            from ollama_queue.scheduler import Scheduler

            Scheduler(db).rebalance()
        except Exception:
            _log.exception("rebalance after update_schedule failed")
        return {"ok": True}

    @app.post("/api/schedule/{rj_id}/run-now")
    def run_schedule_now(rj_id: int):
        rj = db.get_recurring_job(rj_id)
        if not rj:
            raise HTTPException(status_code=404, detail="Recurring job not found")
        job_id = db.submit_job(
            command=rj["command"],
            model=rj.get("model") or "",
            priority=rj.get("priority", 5),
            timeout=rj.get("timeout", 600),
            source=rj["name"],
            tag=rj.get("tag"),
            recurring_job_id=rj["id"],
            max_retries=rj.get("max_retries", 0),
            resource_profile=rj.get("resource_profile", "ollama"),
        )
        return {"job_id": job_id}

    @app.delete("/api/schedule/{rj_id}")
    def delete_schedule(rj_id: int):
        deleted = db.delete_recurring_job_by_id(rj_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Recurring job not found")
        return {"ok": True}

    # --- DLQ ---
    # NOTE: /retry-all must come before /{dlq_id}/retry

    @app.get("/api/dlq")
    def list_dlq(include_resolved: bool = False):
        return db.list_dlq(include_resolved=include_resolved)

    @app.post("/api/dlq/retry-all")
    def retry_all_dlq():
        entries = db.list_dlq()
        new_ids = [db.retry_dlq_entry(e["id"]) for e in entries]
        return {"retried": len([x for x in new_ids if x])}

    @app.post("/api/dlq/{dlq_id}/retry")
    def retry_dlq(dlq_id: int):
        new_id = db.retry_dlq_entry(dlq_id)
        return {"new_job_id": new_id}

    @app.post("/api/dlq/{dlq_id}/dismiss")
    def dismiss_dlq(dlq_id: int):
        db.dismiss_dlq_entry(dlq_id)
        return {"dismissed": dlq_id}

    @app.delete("/api/dlq")
    def clear_dlq():
        n = db.clear_dlq()
        return {"cleared": n}

    # --- Static files for SPA ---
    spa_dir = Path(__file__).parent / "dashboard" / "spa" / "dist"
    if spa_dir.exists():
        app.mount("/ui", StaticFiles(directory=str(spa_dir), html=True), name="ui")

    return app


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
    poll_interval = json.loads(setting_row["value"]) if setting_row else 5
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
    }
