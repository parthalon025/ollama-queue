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

import time as _time

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Simple TTL cache for catalog search results
_catalog_cache: dict[str, tuple[list, float]] = {}  # query -> (results, expires_at)
_CATALOG_CACHE_TTL = 300.0  # 5 minutes

from ollama_queue.db import DEFAULTS, Database
from ollama_queue.estimator import DurationEstimator
from ollama_queue.models import OllamaModels

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
PROXY_WAIT_TIMEOUT = 300
PROXY_POLL_INTERVAL = 0.5


_CURATED_MODELS = [
    {
        "name": "nomic-embed-text",
        "type_tag": "embed",
        "resource_profile": "embed",
        "description": "Best embedding model — fast, 274MB",
        "recommended": True,
    },
    {
        "name": "qwen2.5:7b",
        "type_tag": "general",
        "resource_profile": "ollama",
        "description": "Fast general-purpose model — 4.7GB",
        "recommended": True,
    },
    {
        "name": "qwen2.5-coder:14b",
        "type_tag": "coding",
        "resource_profile": "ollama",
        "description": "Best local coding model — 8.9GB",
        "recommended": True,
    },
    {
        "name": "deepseek-r1:8b",
        "type_tag": "reasoning",
        "resource_profile": "ollama",
        "description": "Reasoning model with CoT — 4.9GB",
        "recommended": True,
    },
    {
        "name": "llama3.2:3b",
        "type_tag": "general",
        "resource_profile": "ollama",
        "description": "Lightweight — 2GB",
        "recommended": False,
    },
    {
        "name": "deepseek-r1:70b",
        "type_tag": "reasoning",
        "resource_profile": "heavy",
        "description": "Max reasoning power — 39GB",
        "recommended": False,
    },
]


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
    check_command: str | None = None
    max_runs: int | None = None


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
    max_retries: int | None = None
    pinned: bool | None = None
    check_command: str | None = None
    max_runs: int | None = None


def create_app(db: Database) -> FastAPI:
    """Application factory. Takes a Database instance for test injection."""
    from starlette.middleware.base import BaseHTTPMiddleware

    app = FastAPI(title="Ollama Queue")

    class _NoCacheSPA(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/ui"):
                response.headers["cache-control"] = "no-store"
            return response

    app.add_middleware(_NoCacheSPA)

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
            model=req.model or None,
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
        """Forward a generate request to Ollama, serializing through the queue.

        Queue-specific fields (extracted from body, not forwarded to Ollama):
          _priority: int (default 0) — job priority (lower = higher priority)
          _source: str (default "proxy") — caller identifier for history/debugging
          _timeout: int (default 120) — request timeout in seconds
        """
        state = db.get_daemon_state()
        if state and state.get("state") == "paused_manual":
            raise HTTPException(status_code=503, detail="Queue is manually paused")

        # Extract queue-specific fields before forwarding to Ollama
        priority = body.pop("_priority", 0)
        source = body.pop("_source", "proxy")
        req_timeout = body.pop("_timeout", 120)

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
            priority=priority,
            timeout=req_timeout,
            source=source,
        )
        db.start_job(job_id)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(float(req_timeout))) as client:
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

    @app.post("/api/embed")
    async def proxy_embed(body: dict = Body(...)):
        """Forward an embed request to Ollama, serializing through the queue.

        Queue-specific fields (extracted from body, not forwarded to Ollama):
          _priority: int (default 0) — job priority (lower = higher priority)
          _source: str (default "proxy") — caller identifier for history/debugging
          _timeout: int (default 120) — request timeout in seconds

        Supports both single-string and array input:
          {"model": "nomic-embed-text", "input": "text"}
          {"model": "nomic-embed-text", "input": ["text1", "text2"]}
        """
        state = db.get_daemon_state()
        if state and state.get("state") == "paused_manual":
            raise HTTPException(status_code=503, detail="Queue is manually paused")

        # Extract queue-specific fields before forwarding to Ollama
        priority = body.pop("_priority", 0)
        source = body.pop("_source", "proxy")
        req_timeout = body.pop("_timeout", 120)

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
            command="proxy:/api/embed",
            model=model,
            priority=priority,
            timeout=req_timeout,
            source=source,
        )
        db.start_job(job_id)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(float(req_timeout))) as client:
                resp = await client.post(f"{OLLAMA_URL}/api/embed", json=body)
                result = resp.json()

            # Mark job completed
            embeddings = result.get("embeddings", [])
            stdout_summary = f"embeddings: {len(embeddings)} vectors"
            db.complete_job(
                job_id=job_id,
                exit_code=0,
                stdout_tail=stdout_summary[:500],
                stderr_tail="",
                outcome_reason=None,
            )
            return result
        except Exception as e:
            _log.error("proxy_embed failed for job %d: %s", job_id, e, exc_info=True)
            # Mark job failed
            db.complete_job(
                job_id=job_id,
                exit_code=1,
                stdout_tail="",
                stderr_tail=str(e)[:500],
                outcome_reason=f"proxy error: {e}",
            )
            raise HTTPException(status_code=502, detail=f"Ollama embed request failed: {e}") from e
        finally:
            try:
                db.release_proxy_claim()
            except Exception:
                _log.exception("release_proxy_claim failed — daemon may be stuck at sentinel job_id")

    # --- Schedule (recurring jobs) ---
    # NOTE: fixed routes (/rebalance, /events) must come before parameterized /{rj_id}

    @app.get("/api/schedule")
    def list_schedule():
        jobs = db.list_recurring_jobs()
        est = DurationEstimator(db)
        om = OllamaModels()
        for rj in jobs:
            rj["estimated_duration"] = est.estimate(
                rj.get("name") or rj.get("source") or "",
                model=rj.get("model"),
            )
            if rj.get("model"):
                classification = om.classify(rj["model"])
                rj["model_profile"] = classification["resource_profile"]
                rj["model_type"] = classification["type_tag"]
                rj["model_vram_mb"] = round(om.estimate_vram_mb(rj["model"], db), 1)
            else:
                rj["model_profile"] = "ollama"
                rj["model_type"] = "general"
                rj["model_vram_mb"] = None
        return jobs

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

    @app.post("/api/schedule/batch-toggle")
    def batch_toggle_schedule(body: dict = Body(...)):
        tag = body.get("tag")
        enabled = body.get("enabled")
        if not tag or enabled is None:
            raise HTTPException(status_code=400, detail="tag and enabled are required")
        jobs = db.list_recurring_jobs()
        matched = [rj for rj in jobs if rj.get("tag") == tag]
        for rj in matched:
            db.update_recurring_job(rj["id"], enabled=bool(enabled))
        return {"updated": len(matched)}

    @app.post("/api/schedule/batch-run")
    def batch_run_schedule(body: dict = Body(...)):
        tag = body.get("tag")
        if not tag:
            raise HTTPException(status_code=400, detail="tag is required")
        jobs = db.list_recurring_jobs()
        matched = [rj for rj in jobs if rj.get("tag") == tag and rj.get("enabled")]
        job_ids = []
        for rj in matched:
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
            job_ids.append(job_id)
        return {"submitted": len(job_ids), "job_ids": job_ids}

    @app.post("/api/schedule")
    def add_schedule(body: RecurringJobCreate):
        from ollama_queue.scheduler import Scheduler

        rj_id = db.add_recurring_job(**body.model_dump())
        Scheduler(db).rebalance()
        return db.get_recurring_job(rj_id)

    @app.put("/api/schedule/{rj_id}")
    def update_schedule(rj_id: int, body: RecurringJobUpdate):
        updated = db.update_recurring_job(rj_id, **body.model_dump(exclude_unset=True))
        if not updated:
            raise HTTPException(status_code=404, detail="Recurring job not found")
        # Rebalance next_run after edit
        try:
            from ollama_queue.scheduler import Scheduler

            Scheduler(db).rebalance()
        except Exception:
            _log.exception("rebalance after update_schedule failed")
        return {"ok": True}

    @app.post("/api/schedule/jobs/{name}/enable")
    def enable_schedule_by_name(name: str):
        """Re-enable a recurring job that was auto-disabled, clearing outcome_reason."""
        if not db.set_recurring_job_enabled(name, True):
            raise HTTPException(status_code=404, detail="Recurring job not found")
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

    @app.get("/api/schedule/{rj_id}/runs")
    def get_schedule_runs(rj_id: int, limit: int = 5):
        conn = db._connect()
        rows = conn.execute(
            """SELECT id, status, started_at, completed_at,
                      CASE WHEN started_at IS NOT NULL AND completed_at IS NOT NULL
                           THEN completed_at - started_at ELSE NULL END as duration,
                      exit_code
               FROM jobs WHERE recurring_job_id = ?
               ORDER BY id DESC LIMIT ?""",
            (rj_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

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

    # --- Models ---

    @app.get("/api/models")
    def get_models():
        om = OllamaModels()
        local = om.list_local()
        loaded_names = {m["name"] for m in om.get_loaded()}
        result = []
        for m in local:
            classification = om.classify(m["name"])
            vram_mb = om.estimate_vram_mb(m["name"], db)
            est = DurationEstimator(db).estimate(m["name"], model=m["name"])
            result.append(
                {
                    "name": m["name"],
                    "size_bytes": m["size_bytes"],
                    "vram_mb": round(vram_mb, 1),
                    "resource_profile": classification["resource_profile"],
                    "type_tag": classification["type_tag"],
                    "loaded": m["name"] in loaded_names,
                    "avg_duration_seconds": est,
                }
            )
        return result

    @app.get("/api/models/catalog")
    def get_catalog(q: str | None = None):
        curated = [c.copy() for c in _CURATED_MODELS]
        search_results = []
        if q:
            import json as _json
            import urllib.parse
            import urllib.request

            now = _time.time()
            cached = _catalog_cache.get(q)
            if cached and cached[1] > now:
                search_results = cached[0]
            else:
                try:
                    url = f"https://ollama.com/search?q={urllib.parse.quote(q)}&format=json"
                    with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310
                        search_results = _json.loads(r.read())[:10]
                    _catalog_cache[q] = (search_results, now + _CATALOG_CACHE_TTL)
                except Exception as exc:
                    _log.warning("Ollama catalog search failed: %s", exc)
        return {"curated": curated, "search_results": search_results}

    @app.post("/api/models/pull")
    def start_pull(body: dict = Body(...)):
        model = body.get("model", "").strip()
        if not model:
            raise HTTPException(status_code=400, detail="model is required")
        pull_id = OllamaModels().pull(model, db)
        return {"pull_id": pull_id}

    @app.get("/api/models/pull/{pull_id}")
    def get_pull_status_endpoint(pull_id: int):
        status = OllamaModels().get_pull_status(pull_id, db)
        if "error" in status:
            raise HTTPException(status_code=404, detail=status["error"])
        return status

    @app.delete("/api/models/pull/{pull_id}")
    def cancel_pull_endpoint(pull_id: int):
        ok = OllamaModels().cancel_pull(pull_id, db)
        return {"cancelled": ok}

    # --- Queue ETAs ---

    @app.get("/api/queue/etas")
    def get_queue_etas():
        jobs = db.get_pending_jobs()
        return DurationEstimator(db).queue_etas(jobs)

    # --- Static files for SPA ---
    spa_dir = Path(__file__).parent / "dashboard" / "spa" / "dist"
    if spa_dir.exists():
        _no_store = {"Cache-Control": "no-store"}

        @app.get("/ui/{path:path}")
        async def spa_static(path: str):
            if path and "\x00" in path:
                return HTMLResponse("Not found", status_code=404)
            real = (spa_dir / path).resolve() if path else None
            if real and real.is_file() and real.is_relative_to(spa_dir.resolve()):
                return FileResponse(real, headers=_no_store)
            index = spa_dir / "index.html"
            return (
                FileResponse(index, headers=_no_store)
                if index.is_file()
                else HTMLResponse("Not found", status_code=404)
            )

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
        "jobs_7d_ok": ok,
        "jobs_7d_bad": bad,
    }
