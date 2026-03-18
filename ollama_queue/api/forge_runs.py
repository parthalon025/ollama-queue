"""Forge run API endpoints — CRUD, cancel, results, calibration."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import ollama_queue.api as _api

router = APIRouter(tags=["forge"])


class CreateForgeRunRequest(BaseModel):
    data_source_url: str
    variant_id: str
    judge_model: str
    oracle_model: str
    pairs_per_quartile: int = 20
    label: str | None = None
    seed: int | None = None


@router.get("/api/forge/runs")
def list_forge_runs(limit: int = 50):
    return _api.db.list_forge_runs(limit=limit)


@router.post("/api/forge/runs", status_code=201)
def create_forge_run(req: CreateForgeRunRequest):
    run_id = _api.db.create_forge_run(
        data_source_url=req.data_source_url,
        variant_id=req.variant_id,
        judge_model=req.judge_model,
        oracle_model=req.oracle_model,
        pairs_per_quartile=req.pairs_per_quartile,
        label=req.label,
        seed=req.seed,
    )
    return {"id": run_id, "status": "queued"}


@router.get("/api/forge/runs/{run_id}")
def get_forge_run(run_id: int):
    run = _api.db.get_forge_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Forge run not found")
    return run


@router.post("/api/forge/runs/{run_id}/cancel")
def cancel_forge_run(run_id: int):
    run = _api.db.get_forge_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Forge run not found")
    if run["status"] in ("complete", "failed", "cancelled"):
        raise HTTPException(409, detail="Run is in a terminal state")
    _api.db.update_forge_run(run_id, status="cancelled", completed_at=time.time())
    return {"ok": True}


@router.get("/api/forge/runs/{run_id}/results")
def get_forge_run_results(run_id: int):
    run = _api.db.get_forge_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Forge run not found")
    return _api.db.get_forge_results(run_id)


@router.get("/api/forge/runs/{run_id}/calibration")
def get_forge_calibration(run_id: int):
    run = _api.db.get_forge_run(run_id)
    if run is None:
        raise HTTPException(404, detail="Forge run not found")
    return {
        "oracle": json.loads(run["oracle_json"]) if run.get("oracle_json") else None,
        "calibration": json.loads(run["calibration_json"]) if run.get("calibration_json") else None,
        "metrics": json.loads(run["metrics_json"]) if run.get("metrics_json") else None,
    }
