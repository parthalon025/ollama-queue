"""Schedule (recurring job) endpoints."""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

import ollama_queue.api as _api
from ollama_queue.db import Database
from ollama_queue.models.client import OllamaModels
from ollama_queue.models.estimator import DurationEstimator

_log = logging.getLogger(__name__)

router = APIRouter()


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
        "model": "qwen3.5:9b",
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
    except httpx.ReadTimeout:
        # Expected when Ollama is busy — not an error, just skip; next schedule add will retry
        _log.warning("generate-description: Ollama timed out for recurring job %d — will retry on next trigger", rj_id)
    except Exception:
        _log.exception("generate-description failed for recurring job %s", rj_id)


# NOTE: fixed routes (/rebalance, /events) must come before parameterized /{rj_id}


@router.get("/api/schedule")
def list_schedule():
    db = _api.db
    jobs = db.list_recurring_jobs()
    est = DurationEstimator(db)
    om = OllamaModels()

    # Batch-fetch last exit codes so we avoid N+1 queries.
    # last_job_id is the queue job that most recently ran for each recurring job.
    last_job_ids = [rj["last_job_id"] for rj in jobs if rj.get("last_job_id")]
    last_exit_map: dict[int, int | None] = {}
    if last_job_ids:
        with db._lock:
            conn = db._connect()
            placeholders = ",".join("?" * len(last_job_ids))
            rows = conn.execute(
                f"SELECT id, exit_code FROM jobs WHERE id IN ({placeholders})",
                last_job_ids,
            ).fetchall()
            for row in rows:
                last_exit_map[row["id"]] = row["exit_code"]

    # Batch-query skip counts (skipped_duplicate events) per job in the last 24h.
    # Shows as a ↻N badge on Gantt bars — high count means the job regularly overruns its interval.
    _since_24h = time.time() - 86400
    skip_count_map: dict[int, int] = {}
    with db._lock:
        _skip_conn = db._connect()
        for row in _skip_conn.execute(
            "SELECT recurring_job_id, COUNT(*) as cnt FROM schedule_events "
            "WHERE event_type = 'skipped_duplicate' AND timestamp >= ? "
            "GROUP BY recurring_job_id",
            (_since_24h,),
        ).fetchall():
            skip_count_map[row["recurring_job_id"]] = row["cnt"]

    for rj in jobs:
        rj["estimated_duration"] = est.estimate(
            rj.get("name") or rj.get("source") or "",
            model=rj.get("model"),
        )
        if rj.get("model"):
            classification = om.classify(rj["model"])
            rj["model_profile"] = classification["resource_profile"]
            rj["model_type"] = classification["type_tag"]
            vram_mb = round(om.estimate_vram_mb(rj["model"], db), 1)
            rj["model_vram_mb"] = vram_mb
            # Warmup estimate: time to cold-load this model's weights into VRAM.
            # Uses ~200 MB/s effective rate (NVMe + PCIe + OS overhead), min 3s.
            # Lets the Gantt candlestick show load overhead before inference starts.
            rj["warmup_estimate"] = max(3, round(vram_mb / 200)) if vram_mb else 0
        else:
            rj["model_profile"] = "ollama"
            rj["model_type"] = "general"
            rj["model_vram_mb"] = None
            rj["warmup_estimate"] = 0

        # Last-run outcome: real exit code from the most recent job execution.
        # Replaces the timing-only heuristic used in the old runStatus() function.
        last_job_id = rj.get("last_job_id")
        rj["last_exit_code"] = last_exit_map.get(last_job_id) if last_job_id else None

        # Skip count: how many times this job was skipped in the last 24h because a previous
        # run was still in progress. High counts indicate the job regularly overruns its interval.
        rj["skip_count_24h"] = skip_count_map.get(rj["id"], 0)

    return jobs


@router.post("/api/schedule/rebalance")
def trigger_rebalance():
    db = _api.db
    from ollama_queue.scheduling.scheduler import Scheduler

    changes = Scheduler(db).rebalance()
    return {"rebalanced": len(changes), "changes": changes}


@router.get("/api/schedule/events")
def get_schedule_events(limit: int = 100):
    db = _api.db
    return db.get_schedule_events(limit=limit)


@router.get("/api/schedule/load-map")
def get_load_map():
    db = _api.db
    from ollama_queue.scheduling.scheduler import Scheduler

    slots = Scheduler(db).load_map()
    return {"slots": slots, "slot_minutes": 30, "count": len(slots)}


@router.get("/api/schedule/suggest")
def suggest_schedule_time(priority: int = 5, top_n: int = 3):
    db = _api.db
    from ollama_queue.scheduling.scheduler import Scheduler

    suggestions = Scheduler(db).suggest_time(priority=priority, top_n=top_n)
    results = []
    for cron_expr, score in suggestions:
        parts = cron_expr.split()
        minute, hour = int(parts[0]), int(parts[1])
        slot = (hour * 60 + minute) // 30
        results.append({"cron": cron_expr, "score": score, "slot": slot})
    return {"suggestions": results}


@router.post("/api/schedule/batch-toggle")
def batch_toggle_schedule(body: dict = Body(...)):
    db = _api.db
    tag = body.get("tag")
    enabled = body.get("enabled")
    if not tag or enabled is None:
        raise HTTPException(status_code=400, detail="tag and enabled are required")
    jobs = db.list_recurring_jobs()
    matched = [rj for rj in jobs if rj.get("tag") == tag]
    for rj in matched:
        db.update_recurring_job(rj["id"], enabled=bool(enabled))
    return {"updated": len(matched)}


@router.post("/api/schedule/batch-run")
def batch_run_schedule(body: dict = Body(...)):
    db = _api.db
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


@router.post("/api/schedule")
def add_schedule(body: RecurringJobCreate):
    db = _api.db
    import threading as _threading

    from ollama_queue.scheduling.scheduler import Scheduler

    try:
        rj_id = db.add_recurring_job(**body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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


# Fields that affect the timing of scheduled jobs and require a rebalance after edit.
# Cosmetic/runtime fields (description, tag, command, model, timeout, etc.) do not.
_REBALANCE_FIELDS = frozenset({"interval_seconds", "cron_expression", "priority", "pinned"})


@router.put("/api/schedule/{rj_id}")
def update_schedule(rj_id: int, body: RecurringJobUpdate):
    db = _api.db
    updates = body.model_dump(exclude_unset=True)
    updated = db.update_recurring_job(rj_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Recurring job not found")
    # Only rebalance when a schedule-affecting field changed (interval, cron, priority, pinned).
    # Skipping rebalance for cosmetic edits avoids O(N) DB writes on every description save.
    if any(k in _REBALANCE_FIELDS for k in updates):
        try:
            from ollama_queue.scheduling.scheduler import Scheduler

            Scheduler(db).rebalance()
        except Exception:
            _log.exception("rebalance after update_schedule failed")
    return {"ok": True}


@router.post("/api/schedule/jobs/{name}/enable")
def enable_schedule_by_name(name: str):
    """Re-enable a recurring job that was auto-disabled, clearing outcome_reason."""
    db = _api.db
    if not db.set_recurring_job_enabled(name, True):
        raise HTTPException(status_code=404, detail="Recurring job not found")
    return {"ok": True}


@router.post("/api/schedule/{rj_id}/run-now")
def run_schedule_now(rj_id: int):
    db = _api.db
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


@router.post("/api/schedule/{rj_id}/generate-description")
def generate_description(rj_id: int):
    """Ask local Ollama (qwen3.5:9b) to write a layman description for this recurring job.

    Plain English: Kicks off a background Ollama call (same pattern as job creation).
    Returns immediately with ok=True; the description arrives via the next GET /api/schedule
    poll (typically within 5-15s). The UI will see it on the next 10s refresh.
    Decision it drives: Lets the owner understand any job's purpose without reading its command.
    """
    import threading as _threading

    db = _api.db
    rj = db.get_recurring_job(rj_id)
    if not rj:
        raise HTTPException(status_code=404, detail="Recurring job not found")
    _threading.Thread(
        target=_call_generate_description,
        args=(rj_id, rj["name"], rj.get("tag"), rj["command"], db),
        daemon=True,
    ).start()
    return {"ok": True, "description": None}


@router.get("/api/schedule/{rj_id}/runs")
def get_schedule_runs(rj_id: int, limit: int = 5):
    db = _api.db
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


@router.delete("/api/schedule/{rj_id}")
def delete_schedule(rj_id: int):
    db = _api.db
    deleted = db.delete_recurring_job_by_id(rj_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Recurring job not found")
    return {"ok": True}
