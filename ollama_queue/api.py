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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

# Simple TTL cache for catalog search results
_catalog_cache: dict[str, tuple[list, float]] = {}  # query -> (results, expires_at)
_CATALOG_CACHE_TTL = 300.0  # 5 minutes

# HTTP hop-by-hop headers that must not be forwarded to clients.
_hop_by_hop = frozenset(
    [
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
    ]
)

from ollama_queue.burst import _default_detector as _burst_detector
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
    description: str | None = None


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
    description: str | None = None


_JOB_DESCRIPTION_CONTEXT = (
    "You are describing scheduled jobs in a personal AI + home automation system owned by one person.\n\n"
    "System context:\n"
    "- 'aria' tag = ARIA, a Home Assistant intelligence system that runs ML predictions, detects behavioral patterns,\n"
    "  correlates entities, and learns from logbook history\n"
    "- 'embeddings' tag = nightly vector index generation for semantic search across project codebases and documents\n"
    "- 'lessons' tag = a personal engineering lessons database with spaced repetition (FSRS) that schedules review of past mistakes\n"  # noqa: E501
    "- 'telegram' tag = sends AI-generated daily briefings (morning/midday/evening) to the owner via Telegram\n"
    "  using summarized data from Notion and Home Assistant\n"
    "- 'notion' tag = syncs and re-indexes the owner's personal Notion workspace (7,800+ pages) for local semantic search\n"  # noqa: E501
    "- Commands like 'aria run --mode X' run a specific ARIA analysis mode.\n"
    "  Modes: learn=update behavioral patterns from HA logbook, predict=generate activity predictions,\n"
    "  embeddings=generate vector index, snapshot=save model state, meta-learn=meta-learning pass\n"
    "- Commands like 'telegram-brief --time X' send a scheduled Telegram message summarizing the day\n"
    "- 'lessons-db' = the personal engineering lessons database CLI\n"
    "- 'notion-sync' = syncs the local Notion replica from the cloud workspace"
)


def _call_generate_description(rj_id: int, name: str, tag: str | None, command: str, db_ref: Database) -> None:
    """Call local Ollama to generate a layman description for a recurring job, then persist it.

    Plain English: Asks the local AI model to write 2 sentences explaining what this scheduled
    job does and why it runs regularly, then saves the result to the database.
    Decision it drives: Shows job owners what each scheduled task is actually for in plain terms.
    """
    prompt = (
        f"{_JOB_DESCRIPTION_CONTEXT}\n\n"
        f"Job name: {name}\n"
        f"Tag: {tag or 'none'}\n"
        f"Command: {command}\n\n"
        "In 2 plain-English sentences, explain what this job does and why it runs regularly. "
        "Write for the technical owner who built this system — be specific about what data or "
        "action is involved, not generic. Do not start with 'This job'."
    )
    payload = {
        "model": "qwen3:8b",
        "prompt": prompt,
        "temperature": 0.2,
        "stream": False,
        "think": False,
        "_source": "description-gen",
        "_timeout": 120,
    }
    try:
        # Route through queue proxy (port 7683) to respect Ollama concurrency limits
        with httpx.Client(timeout=150.0) as client:
            resp = client.post("http://127.0.0.1:7683/api/generate", json=payload)
        resp.raise_for_status()
        description = (resp.json().get("response") or "").strip()
        if description:
            db_ref.update_recurring_job(rj_id, description=description)
        else:
            _log.warning("generate-description: empty response from model for job %d", rj_id)
    except Exception:
        _log.exception("generate-description failed for recurring job %s", rj_id)


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

    @app.get("/api/queue")
    def get_queue():
        return db.get_pending_jobs()

    @app.post("/api/queue/submit")
    def submit_job(req: SubmitJobRequest):
        # Admission gate: reject with 429 when queue depth exceeds max_queue_depth
        max_depth = int(db.get_setting("max_queue_depth") or 50)
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
        daemon_state = db.get_daemon_state()
        burst_regime = daemon_state.get("burst_regime") or "unknown"
        return {"log": db.get_health_log(hours=hours), "burst_regime": burst_regime}

    # --- Durations ---

    @app.get("/api/durations")
    def get_durations(days: int = 7, source: str | None = None):
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

    # --- Heatmap ---

    @app.get("/api/heatmap")
    def get_heatmap(days: int = 7):
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

    async def _iter_ndjson(rp_resp, release_fn=None):
        """Yield complete NDJSON lines from a streaming httpx response.

        Buffers aiter_raw() output — chunks are NOT guaranteed line-aligned.
        Calls release_fn() when done=true final chunk is seen (releases proxy claim).
        """
        buffer = b""
        try:
            async for raw in rp_resp.aiter_raw():
                buffer += raw
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    yield line + b"\n"
                    try:
                        if json.loads(line).get("done") and release_fn:
                            release_fn()
                            release_fn = None
                    except (ValueError, AttributeError):
                        pass
            if buffer.strip():
                yield buffer
        finally:
            if release_fn:
                release_fn()

    async def _proxy_ollama_request(
        endpoint: str,
        command: str,
        body: dict,
        resource_profile: str,
        extract_stdout_fn,
        error_prefix: str,
    ):
        """Shared proxy logic for Ollama requests serialized through the queue.

        Handles: pause check, _priority/_source/_timeout extraction, claim polling,
        job logging, HTTP forwarding, and job completion/failure recording. Both
        proxy_generate and proxy_embed delegate to this helper; the caller is
        responsible for any endpoint-specific body mutations (e.g. stream=False)
        before calling this function.

        Args:
            endpoint: Full Ollama URL path (e.g. "/api/generate").
            command: Label stored in the jobs table (e.g. "proxy:/api/generate").
            body: Request body dict (queue-specific fields are popped inside).
            resource_profile: "ollama" for generate, "embed" for embed models.
            extract_stdout_fn: Callable[[dict], str] — extracts a short summary
                from the Ollama JSON response for the stdout_tail column.
            error_prefix: Prefix for the 502 error detail message.
        """
        state = db.get_daemon_state()
        if state and state.get("state") == "paused_manual":
            raise HTTPException(status_code=503, detail="Queue is manually paused")

        priority = body.pop("_priority", 0)
        source = body.pop("_source", "proxy")
        req_timeout = body.pop("_timeout", 600)  # default matches default_timeout_seconds; callers may override

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

        job_id = db.submit_job(
            command=command,
            model=model,
            priority=priority,
            timeout=req_timeout,
            source=source,
            resource_profile=resource_profile,
        )
        db.start_job(job_id)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(float(req_timeout))) as client:
                resp = await client.post(f"{OLLAMA_URL}{endpoint}", json=body)
                result = resp.json()

            db.complete_job(
                job_id=job_id,
                exit_code=0,
                stdout_tail=extract_stdout_fn(result),
                stderr_tail="",
                outcome_reason=None,
            )
            result["_queue_job_id"] = job_id
            return result
        except httpx.ReadTimeout as e:
            # ReadTimeout is expected for slow models (e.g. deepseek-r1); log as WARNING not ERROR
            _log.warning("%s timed out for job %d after %ss (pass _timeout to override)", command, job_id, req_timeout)
            db.complete_job(
                job_id=job_id,
                exit_code=1,
                stdout_tail="",
                stderr_tail=str(e)[:500],
                outcome_reason=f"proxy timeout after {req_timeout}s",
            )
            raise HTTPException(status_code=504, detail=f"{error_prefix}: read timeout after {req_timeout}s") from e
        except Exception as e:
            _log.error("%s failed for job %d: %s", command, job_id, e, exc_info=True)
            db.complete_job(
                job_id=job_id,
                exit_code=1,
                stdout_tail="",
                stderr_tail=str(e)[:500],
                outcome_reason=f"proxy error: {e}",
            )
            raise HTTPException(status_code=502, detail=f"{error_prefix}: {e}") from e
        finally:
            try:
                db.release_proxy_claim()
            except Exception:
                _log.exception("release_proxy_claim failed — daemon may be stuck at sentinel job_id")

    @app.post("/api/generate")
    async def proxy_generate(body: dict = Body(...)):
        """Forward a generate request to Ollama, serializing through the queue.

        Queue-specific fields (extracted from body, not forwarded to Ollama):
          _priority: int (default 0) — job priority (lower = higher priority)
          _source: str (default "proxy") — caller identifier for history/debugging
          _timeout: int (default 600) — request timeout in seconds; increase for slow reasoning models

        Streaming: if the caller sets stream=True, the response is a StreamingResponse of
        NDJSON chunks exactly as Ollama emits them. If stream is absent or False, the
        existing single-JSON-blob path is used unchanged.
        """
        is_streaming = body.get("stream", False)

        if not is_streaming:
            # Non-streaming path: preserve existing behaviour exactly.
            body["stream"] = False
            return await _proxy_ollama_request(
                endpoint="/api/generate",
                command="proxy:/api/generate",
                body=body,
                resource_profile="ollama",
                extract_stdout_fn=lambda r: str(r.get("response", ""))[:500],
                error_prefix="Ollama request failed",
            )

        # --- Streaming path ---
        state = db.get_daemon_state()
        if state and state.get("state") == "paused_manual":
            raise HTTPException(status_code=503, detail="Queue is manually paused")

        priority = body.pop("_priority", 0)
        source = body.pop("_source", "proxy")
        req_timeout = body.pop("_timeout", 600)
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

        job_id = db.submit_job(
            command="proxy:/api/generate",
            model=model,
            priority=priority,
            timeout=req_timeout,
            source=source,
            resource_profile="ollama",
        )
        db.start_job(job_id)

        try:
            # Use build_request + send(stream=True) so httpx doesn't buffer the body.
            async_client = httpx.AsyncClient(timeout=httpx.Timeout(None))
            rp_req = async_client.build_request("POST", f"{OLLAMA_URL}/api/generate", json=body)
            rp_resp = await async_client.send(rp_req, stream=True)
        except Exception as e:
            _log.error("proxy:/api/generate streaming setup failed for job %d: %s", job_id, e, exc_info=True)
            db.complete_job(
                job_id, exit_code=1, stdout_tail="", stderr_tail=str(e)[:500], outcome_reason=f"proxy error: {e}"
            )
            db.release_proxy_claim()
            raise HTTPException(status_code=502, detail=f"Ollama request failed: {e}") from e

        def _release():
            try:
                db.complete_job(job_id, exit_code=0, stdout_tail="(streaming)", stderr_tail="", outcome_reason=None)
            except Exception:
                _log.exception("complete_job failed for streaming job %d", job_id)
            try:
                db.release_proxy_claim()
            except Exception:
                _log.exception("release_proxy_claim failed for streaming job %d", job_id)

        headers = {k: v for k, v in rp_resp.headers.items() if k.lower() not in _hop_by_hop}

        return StreamingResponse(
            _iter_ndjson(rp_resp, release_fn=_release),
            status_code=rp_resp.status_code,
            headers=headers,
            media_type="application/x-ndjson",
            background=BackgroundTask(rp_resp.aclose),
        )

    @app.post("/api/embed")
    async def proxy_embed(body: dict = Body(...)):
        """Forward an embed request to Ollama, serializing through the queue.

        Queue-specific fields (extracted from body, not forwarded to Ollama):
          _priority: int (default 0) — job priority (lower = higher priority)
          _source: str (default "proxy") — caller identifier for history/debugging
          _timeout: int (default 600) — request timeout in seconds; increase for slow reasoning models

        Supports both single-string and array input:
          {"model": "nomic-embed-text", "input": "text"}
          {"model": "nomic-embed-text", "input": ["text1", "text2"]}
        """
        body["stream"] = False  # Embed API does not stream, but force it defensively
        model = body.get("model", "")
        # Use OllamaModels.classify() to derive resource_profile from the model name,
        # matching the daemon's own concurrency logic (embed profile = 4 concurrent slots,
        # no VRAM gate). classify() returns "embed" for embed/nomic/bge/mxbai/all-minilm
        # models; if model is empty or unknown, classify() returns "ollama" so we fall back
        # to "embed" since /api/embed is always an embed workload.
        resource_profile = OllamaModels().classify(model)["resource_profile"] if model else "embed"
        return await _proxy_ollama_request(
            endpoint="/api/embed",
            command="proxy:/api/embed",
            body=body,
            resource_profile=resource_profile,
            extract_stdout_fn=lambda r: f"embeddings: {len(r.get('embeddings', []))} vectors",
            error_prefix="Ollama embed request failed",
        )

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

    @app.get("/api/schedule/suggest")
    def suggest_schedule_time(priority: int = 5, top_n: int = 3):
        from ollama_queue.scheduler import Scheduler

        suggestions = Scheduler(db).suggest_time(priority=priority, top_n=top_n)
        results = []
        for cron_expr, score in suggestions:
            parts = cron_expr.split()
            minute, hour = int(parts[0]), int(parts[1])
            slot = (hour * 60 + minute) // 30
            results.append({"cron": cron_expr, "score": score, "slot": slot})
        return {"suggestions": results}

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
        import threading as _threading

        from ollama_queue.scheduler import Scheduler

        rj_id = db.add_recurring_job(**body.model_dump())
        Scheduler(db).rebalance()
        rj = db.get_recurring_job(rj_id)
        # Auto-generate description in background if not already provided
        if rj and not rj.get("description"):
            _threading.Thread(
                target=_call_generate_description,
                args=(rj_id, rj["name"], rj.get("tag"), rj["command"], db),
                daemon=True,
            ).start()
        return rj

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

    @app.post("/api/schedule/{rj_id}/generate-description")
    def generate_description(rj_id: int):
        """Ask local Ollama (qwen3:8b) to write a layman description for this recurring job.

        Plain English: The caller waits while the AI model writes 2 plain-English sentences
        about what the job does. The description is saved to the DB and returned so the UI
        can update immediately without a second fetch.
        Decision it drives: Lets the owner understand any job's purpose without reading its command.
        """
        rj = db.get_recurring_job(rj_id)
        if not rj:
            raise HTTPException(status_code=404, detail="Recurring job not found")
        _call_generate_description(rj_id, rj["name"], rj.get("tag"), rj["command"], db)
        updated = db.get_recurring_job(rj_id)
        return {"ok": True, "description": updated.get("description") if updated else None}

    @app.get("/api/schedule/{rj_id}/runs")
    def get_schedule_runs(rj_id: int, limit: int = 5):
        with db._lock:
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

    # --- Eval: helpers ---

    def _get_eval_variant(conn, variant_id: str) -> dict:
        """Fetch a single eval_variant row; raise 404 if missing."""
        row = conn.execute("SELECT * FROM eval_variants WHERE id = ?", (variant_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Variant '{variant_id}' not found")
        return dict(row)

    def _get_eval_template(conn, template_id: str) -> dict:
        """Fetch a single eval_prompt_templates row; raise 404 if missing."""
        row = conn.execute("SELECT * FROM eval_prompt_templates WHERE id = ?", (template_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
        return dict(row)

    # --- Eval: Variants ---
    # NOTE: fixed-path routes (/generate, /generate/preview, /export, /import)
    # must come before parameterized routes (/{variant_id}) to avoid shadowing.

    @app.get("/api/eval/variants")
    def list_eval_variants():
        """Returns all eval_variants rows with latest_f1 from the most recent complete run."""
        conn = db._connect()
        variants = [dict(r) for r in conn.execute("SELECT * FROM eval_variants ORDER BY created_at").fetchall()]
        # Compute latest_f1 per variant from eval_runs.metrics (JSON column)
        runs = conn.execute("SELECT metrics FROM eval_runs WHERE status = 'complete' ORDER BY id ASC").fetchall()
        latest_f1: dict[str, float | None] = {}
        for run_row in runs:
            if not run_row["metrics"]:
                continue
            try:
                metrics = json.loads(run_row["metrics"])
            except (ValueError, TypeError):
                continue
            for var_id, var_metrics in metrics.items():
                if isinstance(var_metrics, dict) and "f1" in var_metrics:
                    latest_f1[var_id] = var_metrics["f1"]
        for v in variants:
            v["latest_f1"] = latest_f1.get(v["id"])
        return variants

    @app.get("/api/eval/variants/generate/preview")
    def preview_eval_variants_generate(models: str = "", template_id: str | None = None):
        """Returns proposed variant labels and count WITHOUT creating anything.

        What it shows: What would be bulk-created if the user triggers /generate.
        Decision it drives: Lets the user confirm the count and names before committing.
        """
        model_list = [m.strip() for m in models.split(",") if m.strip()]
        tmpl_id = template_id or "zero-shot-causal"
        names = [f"Auto: {m} ({tmpl_id})" for m in model_list]
        return {"would_create": len(names), "names": names}

    @app.get("/api/eval/variants/export")
    def export_eval_variants():
        """Returns all user (is_system=0) variants and their templates as JSON.

        What it shows: Portable variant config for backup or cross-machine transfer.
        Decision it drives: Enables cloning a tuned variant set to another setup.
        """
        import datetime as _dt

        conn = db._connect()
        variants = [dict(r) for r in conn.execute("SELECT * FROM eval_variants WHERE is_system = 0").fetchall()]
        # Collect only the templates referenced by user variants
        tmpl_ids = {v["prompt_template_id"] for v in variants}
        templates = []
        if tmpl_ids:
            placeholders = ",".join("?" * len(tmpl_ids))
            templates = [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM eval_prompt_templates WHERE id IN ({placeholders})",
                    list(tmpl_ids),
                ).fetchall()
            ]
        return JSONResponse(
            content={
                "variants": variants,
                "templates": templates,
                "exported_at": _dt.datetime.now(_dt.UTC).isoformat(),
            }
        )

    @app.post("/api/eval/variants/import")
    def import_eval_variants(body: dict = Body(...)):
        """Bulk-import variants and templates (non-destructive, skips existing IDs).

        What it shows: N/A — write-only endpoint.
        Decision it drives: Enables restoring or copying variant configs without manual re-entry.
        """
        import datetime as _dt

        variants = body.get("variants", [])
        templates = body.get("templates", [])
        conn = db._connect()
        now = _dt.datetime.now(_dt.UTC).isoformat()
        variants_imported = 0
        templates_imported = 0
        with db._lock:
            for tmpl in templates:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO eval_prompt_templates
                       (id, label, instruction, format_spec, examples, is_chunked, is_system, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tmpl.get("id"),
                        tmpl.get("label"),
                        tmpl.get("instruction"),
                        tmpl.get("format_spec"),
                        tmpl.get("examples"),
                        tmpl.get("is_chunked", 0),
                        0,  # imported = user-owned
                        tmpl.get("created_at") or now,
                    ),
                )
                templates_imported += cur.rowcount
            for var in variants:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO eval_variants
                       (id, label, prompt_template_id, model, temperature, num_ctx,
                        is_recommended, is_system, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        var.get("id"),
                        var.get("label"),
                        var.get("prompt_template_id"),
                        var.get("model"),
                        var.get("temperature", 0.6),
                        var.get("num_ctx", 8192),
                        var.get("is_recommended", 0),
                        0,  # imported = user-owned
                        var.get("created_at") or now,
                    ),
                )
                variants_imported += cur.rowcount
            conn.commit()
        return {"variants_imported": variants_imported, "templates_imported": templates_imported}

    @app.post("/api/eval/variants/generate")
    def generate_eval_variants(body: dict = Body(...)):
        """Bulk-create one user variant per model in the provided list.

        What it shows: N/A — write-only; created variants appear in GET /api/eval/variants.
        Decision it drives: Lets the user quickly populate variant configs for all installed models.
        """
        import datetime as _dt
        import uuid

        models_list = body.get("models", [])
        tmpl_id = body.get("template_id") or "zero-shot-causal"
        if not models_list:
            raise HTTPException(status_code=400, detail="models list is required")
        conn = db._connect()
        # Validate template exists
        _get_eval_template(conn, tmpl_id)
        now = _dt.datetime.now(_dt.UTC).isoformat()
        created = []
        with db._lock:
            for model_name in models_list:
                new_id = str(uuid.uuid4())[:8]
                label = f"Auto: {model_name} ({tmpl_id})"
                conn.execute(
                    """INSERT INTO eval_variants
                       (id, label, prompt_template_id, model, temperature, num_ctx,
                        is_recommended, is_system, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (new_id, label, tmpl_id, model_name, 0.6, 8192, 0, 0, now),
                )
                created.append(
                    {
                        "id": new_id,
                        "label": label,
                        "prompt_template_id": tmpl_id,
                        "model": model_name,
                        "temperature": 0.6,
                        "num_ctx": 8192,
                        "is_recommended": 0,
                        "is_system": 0,
                        "created_at": now,
                    }
                )
            conn.commit()
        return {"created": len(created), "variants": created}

    @app.post("/api/eval/variants")
    def create_eval_variant(body: dict = Body(...)):
        """Create a new user eval variant.

        What it shows: N/A — write-only; created variant appears in GET /api/eval/variants.
        Decision it drives: Lets the user test a custom model x template x parameter combination.
        """
        import datetime as _dt
        import uuid

        label = body.get("label")
        tmpl_id = body.get("prompt_template_id")
        model = body.get("model")
        if not label or not tmpl_id or not model:
            raise HTTPException(status_code=400, detail="label, prompt_template_id, and model are required")
        conn = db._connect()
        _get_eval_template(conn, tmpl_id)
        now = _dt.datetime.now(_dt.UTC).isoformat()
        new_id = str(uuid.uuid4())[:8]
        with db._lock:
            conn.execute(
                """INSERT INTO eval_variants
                   (id, label, prompt_template_id, model, temperature, num_ctx,
                    is_recommended, is_system, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    label,
                    tmpl_id,
                    model,
                    body.get("temperature", 0.6),
                    body.get("num_ctx", 8192),
                    1 if body.get("is_recommended") else 0,
                    0,  # user-created
                    now,
                ),
            )
            conn.commit()
        row = _get_eval_variant(conn, new_id)
        return JSONResponse(content=row, status_code=201)

    @app.get("/api/eval/variants/{variant_id}/history")
    def eval_variant_history(variant_id: str):
        """Returns F1/recall/precision history across completed eval_runs for one variant.

        What it shows: Per-run quality scores for a single variant over time.
        Decision it drives: Lets the user see whether a variant is improving, stable, or regressing.
        """
        conn = db._connect()
        _get_eval_variant(conn, variant_id)
        runs = conn.execute(
            "SELECT id, started_at, metrics FROM eval_runs WHERE status = 'complete' ORDER BY id ASC"
        ).fetchall()
        history = []
        for run_row in runs:
            if not run_row["metrics"]:
                continue
            try:
                metrics = json.loads(run_row["metrics"])
            except (ValueError, TypeError):
                continue
            var_metrics = metrics.get(variant_id)
            if not var_metrics or not isinstance(var_metrics, dict):
                continue
            history.append(
                {
                    "run_id": run_row["id"],
                    "started_at": run_row["started_at"],
                    "f1": var_metrics.get("f1"),
                    "recall": var_metrics.get("recall"),
                    "precision": var_metrics.get("precision"),
                }
            )
        return history

    @app.post("/api/eval/variants/{variant_id}/clone")
    def clone_eval_variant(variant_id: str, body: dict = Body(default={})):
        """Clone any variant (system or user) into a new user variant.

        What it shows: N/A — write-only; the new variant appears in GET /api/eval/variants.
        Decision it drives: Lets the user safely experiment by copying a baseline without losing the original.
        """
        import datetime as _dt
        import uuid

        conn = db._connect()
        original = _get_eval_variant(conn, variant_id)
        now = _dt.datetime.now(_dt.UTC).isoformat()
        new_id = str(uuid.uuid4())[:8]
        label = body.get("label") or f"{original['label']} (copy)"
        with db._lock:
            conn.execute(
                """INSERT INTO eval_variants
                   (id, label, prompt_template_id, model, temperature, num_ctx,
                    is_recommended, is_system, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    label,
                    original["prompt_template_id"],
                    original["model"],
                    original["temperature"],
                    original["num_ctx"],
                    0,
                    0,  # always user-owned
                    now,
                ),
            )
            conn.commit()
        row = _get_eval_variant(conn, new_id)
        return JSONResponse(content=row, status_code=201)

    @app.put("/api/eval/variants/{variant_id}")
    def update_eval_variant(variant_id: str, body: dict = Body(...)):
        """Update a user variant (partial update OK). Rejects system variants.

        What it shows: N/A — write-only; updated row returned.
        Decision it drives: Lets the user tune parameters without creating a new variant.
        """
        conn = db._connect()
        variant = _get_eval_variant(conn, variant_id)
        if variant["is_system"]:
            raise HTTPException(status_code=422, detail="Cannot modify system variant — clone it first.")
        updatable_fields = {"label", "prompt_template_id", "model", "temperature", "num_ctx", "is_recommended"}
        updates = {k: v for k, v in body.items() if k in updatable_fields}
        if not updates:
            return dict(variant)
        # Validate prompt_template_id if provided
        if "prompt_template_id" in updates:
            _get_eval_template(conn, updates["prompt_template_id"])
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = [*list(updates.values()), variant_id]
        with db._lock:
            conn.execute(f"UPDATE eval_variants SET {set_clause} WHERE id = ?", values)
            conn.commit()
        return _get_eval_variant(conn, variant_id)

    @app.delete("/api/eval/variants/{variant_id}")
    def delete_eval_variant(variant_id: str):
        """Delete a user variant. Rejects system variants.

        What it shows: N/A — delete operation; variant disappears from GET /api/eval/variants.
        Decision it drives: Lets the user remove experiments they no longer need.
        """
        conn = db._connect()
        variant = _get_eval_variant(conn, variant_id)
        if variant["is_system"]:
            raise HTTPException(status_code=422, detail="Cannot modify system variant — clone it first.")
        with db._lock:
            conn.execute("DELETE FROM eval_variants WHERE id = ?", (variant_id,))
            conn.commit()
        return JSONResponse(content=None, status_code=204)

    # --- Eval: Templates ---

    @app.get("/api/eval/templates")
    def list_eval_templates():
        """Returns all eval_prompt_templates rows.

        What it shows: All available prompt templates (system + user).
        Decision it drives: Lets the user pick or clone a template when creating variants.
        """
        conn = db._connect()
        return [dict(r) for r in conn.execute("SELECT * FROM eval_prompt_templates ORDER BY created_at").fetchall()]

    @app.put("/api/eval/templates/{template_id}")
    def update_eval_template(template_id: str, body: dict = Body(...)):
        """Update a user template (partial update OK). Rejects system templates.

        What it shows: N/A — write-only; updated row returned.
        Decision it drives: Lets the user refine prompt instructions without losing the system originals.
        """
        conn = db._connect()
        template = _get_eval_template(conn, template_id)
        if template["is_system"]:
            raise HTTPException(status_code=422, detail="Cannot modify system template — clone it first.")
        updatable_fields = {"label", "instruction", "format_spec", "examples", "is_chunked"}
        updates = {k: v for k, v in body.items() if k in updatable_fields}
        if not updates:
            return dict(template)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = [*list(updates.values()), template_id]
        with db._lock:
            conn.execute(f"UPDATE eval_prompt_templates SET {set_clause} WHERE id = ?", values)
            conn.commit()
        return _get_eval_template(conn, template_id)

    @app.post("/api/eval/templates/{template_id}/clone")
    def clone_eval_template(template_id: str, body: dict = Body(default={})):
        """Clone any template (system or user) into a new user template.

        What it shows: N/A — write-only; new template appears in GET /api/eval/templates.
        Decision it drives: Lets the user safely customize a prompt without altering system defaults.
        """
        import datetime as _dt
        import uuid

        conn = db._connect()
        original = _get_eval_template(conn, template_id)
        now = _dt.datetime.now(_dt.UTC).isoformat()
        new_id = str(uuid.uuid4())[:8]
        label = body.get("label") or f"{original['label']} (copy)"
        with db._lock:
            conn.execute(
                """INSERT INTO eval_prompt_templates
                   (id, label, instruction, format_spec, examples, is_chunked, is_system, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    label,
                    original["instruction"],
                    original.get("format_spec"),
                    original.get("examples"),
                    original["is_chunked"],
                    0,  # always user-owned
                    now,
                ),
            )
            conn.commit()
        row = _get_eval_template(conn, new_id)
        return JSONResponse(content=row, status_code=201)

    # --- Eval: Trends ---

    @app.get("/api/eval/trends")
    def get_eval_trends():
        """Returns per-variant trend data: run history, stability, trend direction, judge agreement.

        What it shows: How each variant's F1 quality score has changed across recent completed runs.
        Decision it drives: Lets the user identify improving vs. regressing variants and when to promote.
        """
        import statistics

        with db._lock:
            conn = db._connect()
            runs = conn.execute(
                """SELECT id, started_at, metrics, item_ids, item_count
                   FROM eval_runs WHERE status = 'complete' ORDER BY id ASC"""
            ).fetchall()
            # Fetch agreement counts inside the same lock to avoid racing with
            # background eval threads that hold db._lock while inserting results.
            _agreed_expr = "SUM(CASE WHEN COALESCE(override_score_transfer, score_transfer) > 1 THEN 1 ELSE 0 END)"
            agreement_rows = conn.execute(
                f"SELECT variant, COUNT(*) as total, {_agreed_expr} as agreed" " FROM eval_results GROUP BY variant"
            ).fetchall()

        # Build per-variant run list
        variant_runs: dict[str, list[dict]] = {}
        item_id_sets: list[str] = []

        for run_row in runs:
            if not run_row["metrics"]:
                continue
            try:
                metrics = json.loads(run_row["metrics"])
            except (ValueError, TypeError):
                continue
            item_ids_str = run_row["item_ids"] or ""
            item_id_sets.append(item_ids_str)
            for var_id, var_metrics in metrics.items():
                if not isinstance(var_metrics, dict):
                    continue
                variant_runs.setdefault(var_id, [])
                variant_runs[var_id].append(
                    {
                        "run_id": run_row["id"],
                        "started_at": run_row["started_at"],
                        "f1": var_metrics.get("f1"),
                        "recall": var_metrics.get("recall"),
                        "precision": var_metrics.get("precision"),
                        "item_count": run_row["item_count"],
                    }
                )

        # Judge agreement: fraction of eval_results where score_transfer > 1
        # (query already executed inside db._lock above to avoid data race)
        agreement_by_variant: dict[str, float] = {}
        for ar in agreement_rows:
            total = ar["total"] or 0
            agreed = ar["agreed"] or 0
            agreement_by_variant[ar["variant"]] = round(agreed / total, 4) if total > 0 else 0.0

        result: dict[str, dict] = {}
        for var_id, run_list in variant_runs.items():
            # Limit to last 10 runs
            recent = run_list[-10:]
            f1_values = [r["f1"] for r in recent if r["f1"] is not None]
            latest_f1 = f1_values[-1] if f1_values else None

            # Stability: 1 - stddev(last 3 F1s) if >= 3 runs
            stability = None
            if len(f1_values) >= 3:
                last3 = f1_values[-3:]
                try:
                    stability = round(max(0.0, 1.0 - statistics.stdev(last3)), 4)
                except statistics.StatisticsError:
                    stability = None

            # Trend direction: slope of F1 values
            trend_direction = "stable"
            if len(f1_values) >= 2:
                n = len(f1_values)
                x_mean = (n - 1) / 2
                y_mean = sum(f1_values) / n
                numerator = sum((i - x_mean) * (f1_values[i] - y_mean) for i in range(n))
                denominator = sum((i - x_mean) ** 2 for i in range(n))
                slope = numerator / denominator if denominator != 0 else 0.0
                if slope > 0.02:
                    trend_direction = "improving"
                elif slope < -0.02:
                    trend_direction = "regressing"

            result[var_id] = {
                "runs": recent,
                "stability": stability,
                "trend_direction": trend_direction,
                "latest_f1": latest_f1,
                "judge_agreement_rate": agreement_by_variant.get(var_id),
            }

        # item_sets_differ: true if not all completed runs share the same item_ids JSON
        item_sets_differ = len(set(item_id_sets)) > 1 if item_id_sets else False

        return {"variants": result, "item_sets_differ": item_sets_differ}

    # --- Eval: Datasource test ---

    @app.get("/api/eval/datasource/test")
    def test_eval_datasource():
        """Makes a live HTTP GET to the configured data source health endpoint.

        What it shows: Whether the external data source is reachable and how many items it has.
        Decision it drives: Confirms setup is correct before triggering an eval run.
        """
        data_source_url = db.get_setting("eval.data_source_url") or "http://127.0.0.1:7685"
        url = f"{data_source_url}/eval/health"
        t0 = _time.time()
        try:
            resp = httpx.get(url, timeout=5.0)
            response_ms = int((_time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "ok": True,
                    "item_count": data.get("item_count"),
                    "cluster_count": data.get("cluster_count"),
                    "response_ms": response_ms,
                    "error": None,
                }
            return {
                "ok": False,
                "item_count": None,
                "cluster_count": None,
                "response_ms": response_ms,
                "error": f"HTTP {resp.status_code}",
            }
        except Exception as exc:
            _log.warning("eval datasource check failed: %s", exc)
            response_ms = int((_time.time() - t0) * 1000)
            return {
                "ok": False,
                "item_count": None,
                "cluster_count": None,
                "response_ms": response_ms,
                "error": str(exc)[:200],
            }

    @app.post("/api/eval/datasource/prime")
    def prime_eval_datasource():
        """Trigger cluster_seed backfill on the lessons-db data source.

        What it shows: nothing — fires a POST to the configured data source's /eval/prime endpoint.
        Decision it drives: after this runs, /eval/items returns lessons that were previously
          invisible because they had cluster set but cluster_seed missing.
        Calls POST {data_source_url}/eval/prime with a 15s timeout and returns the result.
        Returns ok=False with error message if the data source is unreachable.
        """
        data_source_url = db.get_setting("eval.data_source_url") or "http://127.0.0.1:7685"
        token = db.get_setting("eval.data_source_token") or ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"{data_source_url.rstrip('/')}/eval/prime"
        try:
            resp = httpx.post(url, headers=headers, timeout=15.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            _log.warning("eval datasource prime: upstream returned %d", exc.response.status_code)
            raise HTTPException(
                status_code=502,
                detail=f"Data source returned {exc.response.status_code}",
            )
        except Exception as exc:
            _log.warning("eval datasource prime failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"Data source unreachable: {str(exc)[:200]}")

    # --- Eval: Settings ---

    @app.get("/api/eval/settings")
    def get_eval_settings():
        """Returns all settings where key starts with 'eval.'.

        What it shows: Current eval pipeline configuration (data source, judge model, thresholds).
        Decision it drives: Lets the user review and adjust settings before running an eval.
        """
        all_settings = db.get_all_settings()
        result = {k: v for k, v in all_settings.items() if k.startswith("eval.")}
        # Mask token — bearer credential must not be readable via API
        if result.get("eval.data_source_token"):
            result["eval.data_source_token"] = "***"  # noqa: S105
        return result

    @app.put("/api/eval/settings")
    def put_eval_settings(body: dict = Body(...)):
        """Bulk-update eval.* settings (validated, all-or-nothing).

        What it shows: N/A — write-only; returns updated settings dict on success.
        Decision it drives: Lets the user configure the eval pipeline without editing the DB directly.
        """
        # Allowlist of known eval settings (bare keys without "eval." prefix)
        _known_eval_keys = {
            "data_source_url",
            "data_source_token",
            "per_cluster",
            "same_cluster_targets",
            "diff_cluster_targets",
            "judge_model",
            "judge_backend",
            "judge_temperature",
            "f1_threshold",
            "stability_window",
            "error_budget",
            "setup_complete",
            "analysis_model",
            "auto_promote",
            "auto_promote_min_improvement",
        }

        # Validation rules — validate ALL before writing any
        validation_errors = []
        for key, value in body.items():
            bare_key = key.removeprefix("eval.")
            if bare_key not in _known_eval_keys:
                validation_errors.append(f"unknown eval setting: {key!r}")
                continue
            if bare_key == "judge_backend":
                if value not in ("ollama", "openai"):
                    validation_errors.append(f"judge_backend must be 'ollama' or 'openai', got {value!r}")
            elif bare_key == "per_cluster":
                if not isinstance(value, int) or not (1 <= value <= 20):
                    validation_errors.append(f"per_cluster must be an integer 1-20, got {value!r}")
            elif bare_key in ("same_cluster_targets", "diff_cluster_targets"):
                if not isinstance(value, int) or not (1 <= value <= 10):
                    validation_errors.append(f"{bare_key} must be an integer 1-10, got {value!r}")
            elif bare_key == "judge_temperature":
                if not isinstance(value, int | float) or not (0.0 <= float(value) <= 2.0):
                    validation_errors.append(f"{bare_key} must be a float 0.0-2.0, got {value!r}")
            elif bare_key in ("f1_threshold", "error_budget"):
                if not isinstance(value, int | float) or not (0.0 <= float(value) <= 1.0):
                    validation_errors.append(f"{bare_key} must be a float 0.0-1.0, got {value!r}")
            elif bare_key == "data_source_url":
                if not isinstance(value, str) or not (value.startswith("http://") or value.startswith("https://")):
                    validation_errors.append("data_source_url must start with http:// or https://")
                elif not any(
                    value.startswith(f"http://{h}") or value.startswith(f"https://{h}")
                    for h in ("127.0.0.1", "localhost")
                ):
                    validation_errors.append("data_source_url must target 127.0.0.1 or localhost only")
            elif bare_key == "stability_window" and not (isinstance(value, int) and (1 <= value <= 20)):
                validation_errors.append(f"stability_window must be an integer 1-20, got {value!r}")
            elif bare_key == "auto_promote" and not isinstance(value, bool):
                validation_errors.append(f"auto_promote must be a boolean, got {value!r}")
            elif bare_key == "auto_promote_min_improvement" and (
                not isinstance(value, int | float) or not (0.0 <= float(value) <= 1.0)
            ):
                validation_errors.append(f"auto_promote_min_improvement must be 0.0-1.0, got {value!r}")

        if validation_errors:
            raise HTTPException(status_code=422, detail=validation_errors)

        # All-or-nothing write: only update keys prefixed with 'eval.'
        for key, value in body.items():
            full_key = key if key.startswith("eval.") else f"eval.{key}"
            db.set_setting(full_key, value)

        return get_eval_settings()

    # --- Eval: Schedule ---

    @app.post("/api/eval/schedule")
    def create_eval_schedule(body: dict = Body(...)):
        """Create a recurring eval job.

        What it shows: N/A — write-only; job appears in GET /api/schedule after creation.
        Decision it drives: Lets the user schedule regular eval runs to accumulate trend data automatically.
        """
        variants = body.get("variants", [])
        per_cluster = body.get("per_cluster", 4)
        run_mode = body.get("run_mode", "batch")
        recurrence = body.get("recurrence", "off")

        # --- Input validation (prevents shell injection via shell=True in daemon) ---
        import re as _re

        if not isinstance(variants, list) or not all(
            isinstance(v, str) and _re.fullmatch(r"[A-Za-z0-9_-]+", v) for v in variants
        ):
            raise HTTPException(status_code=400, detail="variants must be a list of alphanumeric strings")
        if not isinstance(per_cluster, int) or not (1 <= per_cluster <= 20):
            raise HTTPException(status_code=400, detail="per_cluster must be an integer 1-20")
        if run_mode not in ("batch", "opportunistic", "fill-open-slots", "scheduled"):
            raise HTTPException(
                status_code=400, detail="run_mode must be one of: batch, opportunistic, fill-open-slots, scheduled"
            )

        if recurrence == "daily":
            interval_seconds = 86400
        elif recurrence == "weekly":
            interval_seconds = 7 * 86400
        else:
            raise HTTPException(status_code=400, detail="recurrence must be 'daily' or 'weekly'")

        command = (
            f"ollama-queue eval-run --variants {','.join(variants)} "
            f"--per-cluster {per_cluster} --run-mode {run_mode}"
        )
        import sqlite3 as _sqlite3

        try:
            rj_id = db.add_recurring_job(
                name=f"eval-session-{recurrence}",
                command=command,
                interval_seconds=interval_seconds,
                tag="eval",
                source="eval-schedule",
            )
        except (_sqlite3.IntegrityError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail=f"eval-session-{recurrence} already exists — delete it first or use PUT /api/schedule to update",
            ) from exc
        return {"job_id": rj_id}

    # --- Eval: Runs lifecycle ---
    # NOTE: fixed-path routes (/runs) must be declared before parameterized routes
    # (/{run_id} sub-routes) to prevent shadowing.

    @app.get("/api/eval/runs")
    def list_eval_runs(limit: int = 20, offset: int = 0):
        """Returns a paginated list of eval runs.

        # What it shows: All eval runs in reverse-creation order with summary metrics.
        # Decision it drives: Lets the user review run history, spot failures, and pick
        #   a run to promote or judge-rerun.
        """

        with db._lock:
            conn = db._connect()
            rows = conn.execute(
                """SELECT id, status, variants, variant_id, winner_variant, metrics,
                          item_count, item_ids, started_at, completed_at,
                          judge_model, analysis_md, error, label, scheduled_by,
                          error_budget, run_mode
                   FROM eval_runs
                   ORDER BY id DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            # Parse metrics JSON so RunRow can render the per-variant table directly
            parsed_metrics = None
            if r.get("metrics"):
                try:
                    parsed_metrics = json.loads(r["metrics"])
                except (ValueError, TypeError):
                    _log.warning("list_eval_runs: failed to parse metrics for run %s", r.get("id"))
            result.append(
                {
                    "id": r["id"],
                    "status": r["status"],
                    "variants": r.get("variants"),
                    "variant_id": r.get("variant_id"),
                    "winner_variant": r.get("winner_variant"),
                    "metrics": parsed_metrics,
                    "item_count": r.get("item_count"),
                    "item_ids": r.get("item_ids"),
                    "started_at": r.get("started_at"),
                    "completed_at": r.get("completed_at"),
                    "judge_model": r.get("judge_model"),
                    "analysis_md": r.get("analysis_md"),
                    "error": r.get("error"),
                    "label": r.get("label"),
                    "scheduled_by": r.get("scheduled_by"),
                    "error_budget": r.get("error_budget"),
                    "run_mode": r.get("run_mode"),
                }
            )
        return result

    @app.post("/api/eval/runs")
    def trigger_eval_run(body: dict = Body(...)):
        """Trigger a new eval run for a given variant.

        # What it shows: N/A — write-only; new run appears in GET /api/eval/runs.
        # Decision it drives: Lets the user kick off a fresh evaluation for any variant
        #   without touching the CLI.
        """
        from ollama_queue import eval_engine as _ee

        # Accept either variants (list, from SPA) or variant_id (single, legacy/API)
        variants_list = body.get("variants")
        variant_id = body.get("variant_id")
        cluster_id = body.get("cluster_id")
        run_mode = body.get("run_mode", "batch")
        label = body.get("label")
        per_cluster = body.get("per_cluster", 4)

        # Normalise: convert list → primary variant_id + variants list
        if variants_list and isinstance(variants_list, list) and not variant_id:
            variant_id = variants_list[0]

        if not variant_id:
            raise HTTPException(status_code=400, detail="variant_id or variants list is required")

        # Validate all requested variants exist
        all_ids = variants_list if variants_list else [variant_id]
        with db._lock:
            conn = db._connect()
            for vid in all_ids:
                row = conn.execute("SELECT id FROM eval_variants WHERE id = ?", (vid,)).fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail=f"Variant '{vid}' not found")

        valid_modes = ("batch", "opportunistic", "fill-open-slots", "scheduled")
        if run_mode not in valid_modes:
            raise HTTPException(status_code=400, detail=f"run_mode must be one of: {', '.join(valid_modes)}")

        # fill-open-slots limits (None = unlimited; frontend sends null when not applicable)
        max_runs_raw = body.get("max_runs")
        max_time_s_raw = body.get("max_time_s")
        max_runs = int(max_runs_raw) if max_runs_raw is not None else None
        max_time_s = int(max_time_s_raw) if max_time_s_raw is not None else None

        # Create the run row
        run_id = _ee.create_eval_run(
            db,
            variant_id=variant_id,
            run_mode=run_mode,
            label=label,
            cluster_id=cluster_id,
            scheduled_by="api",
            variants=variants_list,
            per_cluster=int(per_cluster) if per_cluster else 4,
            max_runs=max_runs,
            max_time_s=max_time_s,
        )

        # Persist judge_model from request body so run_eval_judge uses it instead of the setting default
        judge_model = body.get("judge_model")
        if judge_model and isinstance(judge_model, str):
            _ee.update_eval_run(db, run_id, judge_model=judge_model)

        # Run the session in a background thread — NOT as a queued job.
        # Running as a queued job would deadlock: the daemon sets current_job_id while
        # the subprocess runs, which blocks try_claim_for_proxy() when the engine
        # calls /api/generate. Background thread avoids that contention.
        import threading as _threading

        _captured_run_id = run_id

        def _run_session_in_background() -> None:
            try:
                _ee.run_eval_session(_captured_run_id, db)
            except Exception:
                _log.exception("run_eval_session failed for run_id=%d", _captured_run_id)

        _threading.Thread(target=_run_session_in_background, daemon=True).start()

        return JSONResponse(content={"run_id": run_id}, status_code=201)

    @app.get("/api/eval/runs/{run_id}")
    def get_eval_run_detail(run_id: int):
        """Returns full detail for one eval run, including parsed metrics JSON.

        # What it shows: All fields for a single eval run — status, metrics, error, item list.
        # Decision it drives: Lets the user inspect an individual run's outcome and decide
        #   whether to promote, judge-rerun, or investigate failures.
        """
        from ollama_queue import eval_engine as _ee

        run = _ee.get_eval_run(db, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")

        # Parse metrics JSON field
        if run.get("metrics"):
            try:
                run["metrics"] = json.loads(run["metrics"])
            except (ValueError, TypeError):
                _log.warning("get_eval_run_detail: failed to parse metrics for run %d", run_id)
        return run

    @app.delete("/api/eval/runs/{run_id}")
    def cancel_eval_run(run_id: int):
        """Cancel a queued or running eval run.

        # What it shows: N/A — state change; updated status visible in GET /api/eval/runs/{id}.
        # Decision it drives: Lets the user abort a run that is stuck or no longer needed
        #   without waiting for it to time out.
        """
        from ollama_queue import eval_engine as _ee

        run = _ee.get_eval_run(db, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")

        terminal_statuses = {"complete", "failed", "cancelled"}
        if run["status"] in terminal_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel run {run_id}: already in terminal status '{run['status']}'",
            )

        from datetime import UTC
        from datetime import datetime as _cdt

        _ee.update_eval_run(db, run_id, status="cancelled", completed_at=_cdt.now(UTC).isoformat())
        return {"ok": True, "run_id": run_id}

    @app.post("/api/eval/runs/{run_id}/analyze")
    def analyze_eval_run(run_id: int):
        """Trigger on-demand Ollama analysis for a completed eval run.

        # What it shows: N/A — write-only; analysis_md appears in GET /api/eval/runs after completion.
        # Decision it drives: Lets the user request analysis for any completed run, including
        #   runs that completed before this feature was introduced or when the model was unavailable.
        """
        import threading as _threading_analyze

        from ollama_queue import eval_engine as _ee

        run = _ee.get_eval_run(db, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")
        if run.get("status") != "complete":
            raise HTTPException(status_code=400, detail="Analysis requires a completed run")

        def _run_analysis() -> None:
            try:
                _ee.generate_eval_analysis(db, run_id)
            except Exception:
                _log.exception("generate_eval_analysis failed for run_id=%d", run_id)
                try:
                    _ee.update_eval_run(db, run_id, analysis_md="[Analysis failed — see server logs]")
                except Exception:
                    _log.exception("could not record analysis failure for run_id=%d", run_id)

        _threading_analyze.Thread(target=_run_analysis, daemon=True).start()
        return {"ok": True, "run_id": run_id, "message": "Analysis started in background"}

    @app.get("/api/eval/runs/{run_id}/results")
    def get_eval_run_results(run_id: int, row_type: str | None = None):
        """Returns all eval_results rows for an eval run, with optional row_type filter.

        # What it shows: Per-item judge scores for one run — source, target, scores, errors.
        # Decision it drives: Lets the user drill into which items scored well or poorly
        #   to identify weak spots in a variant's principle transfer.
        """
        from ollama_queue import eval_engine as _ee

        run = _ee.get_eval_run(db, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")

        with db._lock:
            conn = db._connect()
            if row_type:
                rows = conn.execute(
                    "SELECT * FROM eval_results WHERE run_id = ? AND row_type = ? ORDER BY id",
                    (run_id, row_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM eval_results WHERE run_id = ? ORDER BY id",
                    (run_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/eval/runs/{run_id}/progress")
    def get_eval_run_progress(run_id: int):
        """Returns live progress for an active eval run (generated, judged, failed counts).

        # What it shows: How far along a running eval is — useful for frontend polling every 5s.
        # Decision it drives: Lets the user know if a run is progressing normally or stalled.
        """
        from ollama_queue import eval_engine as _ee

        run = _ee.get_eval_run(db, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")

        with db._lock:
            conn = db._connect()
            # Total items expected (from item_count or item_ids JSON length)
            total = run.get("item_count") or 0
            if not total and run.get("item_ids"):
                try:
                    total = len(json.loads(run["item_ids"]))
                except (ValueError, TypeError):
                    _log.warning("get_eval_run_progress: failed to parse item_ids for run %d", run_id)

            # Count rows by row_type
            count_rows = conn.execute(
                """SELECT row_type, COUNT(*) as cnt
                   FROM eval_results WHERE run_id = ? GROUP BY row_type""",
                (run_id,),
            ).fetchall()
            counts = {r["row_type"]: r["cnt"] for r in count_rows}

            # Count errors
            failed_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM eval_results WHERE run_id = ? AND error IS NOT NULL",
                (run_id,),
            ).fetchone()
            failed = failed_count["cnt"] if failed_count else 0

            # Per-variant breakdown: generated count = expected judging targets per variant
            per_variant_rows = conn.execute(
                """SELECT variant,
                          SUM(CASE WHEN row_type = 'generate' THEN 1 ELSE 0 END) as gen_cnt,
                          SUM(CASE WHEN row_type = 'judge'    THEN 1 ELSE 0 END) as judge_cnt,
                          SUM(CASE WHEN error IS NOT NULL     THEN 1 ELSE 0 END) as error_cnt
                   FROM eval_results WHERE run_id = ? GROUP BY variant""",
                (run_id,),
            ).fetchall()

            # Resolve gen_model from variant (for swimlane model badge)
            _raw_variants = run.get("variants") or ""
            try:
                _parsed = json.loads(_raw_variants)
                _fallback_id = _parsed[0] if isinstance(_parsed, list) and _parsed else _raw_variants
            except (ValueError, TypeError):
                _fallback_id = _raw_variants.strip()
            _variant_id = run.get("variant_id") or _fallback_id
            _variant_row = conn.execute("SELECT model FROM eval_variants WHERE id = ?", (_variant_id,)).fetchone()
            gen_model = _variant_row["model"] if _variant_row else None

        generated = counts.get("generate", 0)
        judged = counts.get("judge", 0)
        run_status = run["status"]
        is_judging = run_status in ("judging",) or run.get("stage") in ("judging", "fetch_targets")
        phase_count = judged if is_judging else generated
        pct = round(phase_count / total * 100, 1) if total > 0 else 0.0

        # per_variant dict: {variant_id: {completed, total, failed}}
        # "total" per variant = number of generate rows (each needs a judge call)
        per_variant: dict = {}
        for row in per_variant_rows:
            per_variant[row["variant"]] = {
                "completed": row["judge_cnt"],
                "total": row["gen_cnt"],
                "failed": row["error_cnt"],
            }

        # Determine which stage we're in to compute the "completed" counter shown in the UI
        completed = phase_count

        failure_rate = round(failed / total, 4) if total > 0 else 0.0

        return {
            # Legacy fields (keep for API compatibility)
            "generated": generated,
            "judged": judged,
            "pct_complete": pct,
            # Fields the frontend progress panel reads
            "run_id": run_id,
            "status": run_status,
            "stage": run.get("stage"),
            "completed": completed,
            "total": total,
            "failed": failed,
            "pct": pct,
            "failure_rate": failure_rate,
            "per_variant": per_variant,
            "eta_s": None,
            # Swimlane model badge
            "gen_model": gen_model,
            "judge_model": run.get("judge_model"),
        }

    @app.post("/api/eval/runs/{run_id}/repeat")
    def repeat_eval_run(run_id: int):
        """Create a new eval run that exactly replicates a completed run's item set and seed.

        # What it shows: N/A — write-only; the new run appears in GET /api/eval/runs.
        # Decision it drives: Lets the user re-run an identical eval to verify result stability
        #   or compare against a configuration change while holding all other variables constant.
        """
        import datetime as _dt

        with db._lock:
            conn = db._connect()
            orig_row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        if orig_row is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        orig = dict(orig_row)

        # Require reproducibility data — item_ids and seed must both be present.
        if not orig.get("item_ids") or orig.get("seed") is None:
            raise HTTPException(
                status_code=422,
                detail="original run has no reproducibility data",
            )

        started_at = _dt.datetime.now(_dt.UTC).isoformat()
        with db._lock:
            conn = db._connect()
            cur = conn.execute(
                """INSERT INTO eval_runs
                   (data_source_url, variants, per_cluster, status, run_mode,
                    item_ids, seed, judge_model, judge_backend, error_budget,
                    started_at)
                   VALUES (?, ?, ?, 'queued', ?,
                           ?, ?, ?, ?, ?,
                           ?)""",
                (
                    orig["data_source_url"],
                    orig["variants"],
                    orig["per_cluster"],
                    orig.get("run_mode") or "batch",
                    orig["item_ids"],
                    orig["seed"],
                    orig.get("judge_model"),
                    orig.get("judge_backend"),
                    orig.get("error_budget") or 0.30,
                    started_at,
                ),
            )
            conn.commit()
            new_run_id = cur.lastrowid

        _log.info(
            "repeat_eval_run: created run_id=%d as repeat of run_id=%d (seed=%d)",
            new_run_id,
            run_id,
            orig["seed"],
        )

        import threading as _threading

        _captured_new_id = new_run_id

        def _run_repeat_in_background() -> None:
            try:
                from ollama_queue import eval_engine as _ee_repeat

                _ee_repeat.run_eval_session(_captured_new_id, db)
            except Exception:
                _log.exception("run_eval_session failed for repeat run_id=%d", _captured_new_id)

        _threading.Thread(target=_run_repeat_in_background, daemon=True).start()

        return {"run_id": new_run_id}

    @app.post("/api/eval/runs/{run_id}/promote")
    def promote_eval_run(run_id: int, body: dict = Body(default={})):
        """Mark a completed run's winner variant as the production variant.

        # What it shows: N/A — write action; updates lessons-db + local eval_variants.
        # Decision it drives: Promotes the winning eval config to production so the system
        #   uses it for future inference without manual DB edits.

        Accepts an empty body {}. Resolves the model/template/temperature/num_ctx
        automatically from the run's winner_variant in eval_variants.
        """
        from ollama_queue import eval_engine as _ee

        try:
            result = _ee.do_promote_eval_run(db, run_id)
            return result
        except ValueError as exc:
            msg = str(exc)
            # "Eval run N not found" → 404; "Variant X not found in eval_variants" → 400
            if "not found" in msg and "eval_variants" not in msg:
                raise HTTPException(status_code=404, detail=msg)
            raise HTTPException(status_code=400, detail=msg)
        except httpx.HTTPError as exc:
            _log.warning("promote_eval_run: HTTP error for run %d: %s", run_id, exc)
            raise HTTPException(status_code=502, detail=f"Failed to reach lessons-db: {exc}") from exc

    @app.post("/api/eval/runs/{run_id}/judge-rerun")
    def judge_rerun_eval_run(run_id: int, body: dict = Body(default={})):
        """Re-run the judge phase on an existing completed run with new judge settings.

        # What it shows: N/A — creates a new run; visible in GET /api/eval/runs.
        # Decision it drives: Lets the user upgrade the judge model or temperature and
        #   see whether scores change without re-running generation.
        """
        from ollama_queue import eval_engine as _ee

        run = _ee.get_eval_run(db, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")

        if run["status"] not in ("complete", "failed"):
            raise HTTPException(
                status_code=400,
                detail=f"judge-rerun only allowed on complete or failed runs (current: {run['status']})",
            )

        # Create a new run copying item_ids and seed from the original, starting at judging
        new_run_id = _ee.create_eval_run(
            db,
            variant_id=run.get("variant_id") or run.get("variants", ""),
            run_mode=run.get("run_mode") or "batch",
            label=f"Judge rerun of #{run_id}",
            cluster_id=run.get("cluster_id"),
            scheduled_by="judge-rerun",
            data_source_url=run.get("data_source_url"),
            data_source_token=run.get("data_source_token"),
            seed=run.get("seed"),
            item_ids=run.get("item_ids"),
        )

        # Copy gen_results from original run so run_eval_judge can find them.
        # Without this the new run has no eval_results rows and judge produces empty metrics.
        # Scores are intentionally NOT copied — judge-rerun must score fresh.
        # (INSERT OR IGNORE on a unique key would preserve old scores and make re-judging a no-op.)
        with db._lock:
            conn = db._connect()
            conn.execute(
                """INSERT OR IGNORE INTO eval_results
                   (run_id, variant, source_item_id, principle, target_item_id,
                    is_same_cluster, row_type, generation_time_s, queue_job_id,
                    score_transfer, score_precision, score_action, error)
                   SELECT ?, variant, source_item_id, principle, target_item_id,
                          is_same_cluster, row_type, generation_time_s, queue_job_id,
                          NULL, NULL, NULL, NULL
                   FROM eval_results
                   WHERE run_id = ? AND principle IS NOT NULL AND error IS NULL""",
                (new_run_id, run_id),
            )
            conn.commit()

        # Set status to 'judging' (override 'queued' set by create_eval_run)
        _ee.update_eval_run(db, new_run_id, status="judging")

        # Spawn background thread for the judge phase — same pattern as trigger_eval_run.
        # (The `ollama-queue eval-run` CLI subcommand does not exist; queue-job approach
        # would fail with exit code 2.)
        import threading as _threading_jr

        _captured_judge_id = new_run_id

        def _run_judge_in_background() -> None:
            try:
                from ollama_queue import eval_engine as _ee_jr

                _ee_jr.run_eval_judge(_captured_judge_id, db)
            except Exception as _exc:
                _log.exception("run_eval_judge failed for judge-rerun run_id=%d", _captured_judge_id)
                try:
                    import datetime as _dt_jr

                    from ollama_queue import eval_engine as _ee_jr2

                    _ee_jr2.update_eval_run(
                        db,
                        _captured_judge_id,
                        status="failed",
                        error=str(_exc)[:200],
                        completed_at=_dt_jr.datetime.now(_dt_jr.UTC).isoformat(),
                    )
                except Exception:
                    _log.exception(
                        "run_eval_judge: also failed to mark run_id=%d as failed",
                        _captured_judge_id,
                    )

        _threading_jr.Thread(target=_run_judge_in_background, daemon=True).start()

        return JSONResponse(content={"run_id": new_run_id}, status_code=201)

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
