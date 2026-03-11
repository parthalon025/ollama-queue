"""Eval settings and datasource endpoints."""

from __future__ import annotations

import logging
import time as _time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Body, HTTPException

import ollama_queue.api as _api

_log = logging.getLogger(__name__)

router = APIRouter()


# --- Eval: Datasource test ---


@router.get("/api/eval/datasource/test")
def test_eval_datasource():
    """Makes a live HTTP GET to the configured data source health endpoint.

    What it shows: Whether the external data source is reachable and how many items it has.
    Decision it drives: Confirms setup is correct before triggering an eval run.
    """
    db = _api.db
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


@router.post("/api/eval/datasource/prime")
def prime_eval_datasource():
    """Trigger cluster_seed backfill on the lessons-db data source.

    What it shows: nothing — fires a POST to the configured data source's /eval/prime endpoint.
    Decision it drives: after this runs, /eval/items returns lessons that were previously
      invisible because they had cluster set but cluster_seed missing.
    Calls POST {data_source_url}/eval/prime with a 15s timeout and returns the result.
    Returns ok=False with error message if the data source is unreachable.
    """
    db = _api.db
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

# Keys whose values must never be returned in plaintext — show first 6 chars + *** if non-empty
_MASKED_SETTINGS = {"eval.data_source_token", "eval.claude_api_key", "eval.openai_api_key"}

# Provider-role settings — values must be one of _VALID_PROVIDERS
_PROVIDER_SETTINGS = {
    "eval.generator_provider",
    "eval.judge_provider",
    "eval.optimizer_provider",
    "eval.oracle_provider",
}
_VALID_PROVIDERS = {"ollama", "claude", "openai"}


def _mask_value(key: str, value: str) -> str:
    """Return masked form of a sensitive setting value.

    What it shows: N/A — pure helper used by get_eval_settings.
    Decision it drives: Preserves enough context (first 6 chars) to identify which key
      is set without exposing the full credential.
    """
    if key == "eval.data_source_token":
        # Legacy: token was always fully masked as "***"
        return "***"
    # API keys: show first 6 chars so user can identify which key is configured
    return value[:6] + "***"


@router.get("/api/eval/settings")
def get_eval_settings():
    """Returns all settings where key starts with 'eval.'.

    What it shows: Current eval pipeline configuration (data source, judge model, thresholds).
    Decision it drives: Lets the user review and adjust settings before running an eval.
    """
    db = _api.db
    all_settings = db.get_all_settings()
    result = {k: v for k, v in all_settings.items() if k.startswith("eval.")}
    # Mask sensitive credentials — never return raw values via API
    for key in _MASKED_SETTINGS:
        if result.get(key):
            result[key] = _mask_value(key, result[key])
    return result


@router.put("/api/eval/settings")
def put_eval_settings(body: dict = Body(...)):
    """Bulk-update eval.* settings (validated, all-or-nothing).

    What it shows: N/A — write-only; returns updated settings dict on success.
    Decision it drives: Lets the user configure the eval pipeline without editing the DB directly.
    """
    db = _api.db
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
        "positive_threshold",
        # Provider settings
        "generator_provider",
        "generator_model",
        "judge_provider",
        "optimizer_provider",
        "optimizer_model",
        "oracle_provider",
        "oracle_model",
        "oracle_enabled",
        "claude_api_key",
        "openai_api_key",
        "openai_base_url",
        "max_cost_per_run_usd",
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
            else:
                parsed_url = urlparse(value)
                if parsed_url.hostname not in ("127.0.0.1", "localhost"):
                    validation_errors.append(
                        "data_source_url must target 127.0.0.1 or localhost only (SSRF protection)"
                    )
        elif bare_key == "stability_window" and not (isinstance(value, int) and (1 <= value <= 20)):
            validation_errors.append(f"stability_window must be an integer 1-20, got {value!r}")
        elif bare_key == "auto_promote" and not isinstance(value, bool):
            validation_errors.append(f"auto_promote must be a boolean, got {value!r}")
        elif bare_key == "auto_promote_min_improvement" and (
            not isinstance(value, int | float) or not (0.0 <= float(value) <= 1.0)
        ):
            validation_errors.append(f"auto_promote_min_improvement must be 0.0-1.0, got {value!r}")
        elif bare_key == "positive_threshold" and (not isinstance(value, int) or not (1 <= value <= 5)):
            validation_errors.append(f"positive_threshold must be an integer 1-5, got {value!r}")
        elif f"eval.{bare_key}" in _PROVIDER_SETTINGS and value not in _VALID_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid provider {value!r}: must be one of {sorted(_VALID_PROVIDERS)}",
            )

    if validation_errors:
        raise HTTPException(status_code=422, detail=validation_errors)

    # All-or-nothing write: only update keys prefixed with 'eval.'
    for key, value in body.items():
        full_key = key if key.startswith("eval.") else f"eval.{key}"
        db.set_setting(full_key, value)

    return get_eval_settings()


# --- Eval: Schedule ---


@router.post("/api/eval/schedule")
def create_eval_schedule(body: dict = Body(...)):
    """Create a recurring eval job.

    What it shows: N/A — write-only; job appears in GET /api/schedule after creation.
    Decision it drives: Lets the user schedule regular eval runs to accumulate trend data automatically.
    """
    db = _api.db
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

    command = f"ollama-queue eval-run --variants {','.join(variants)} --per-cluster {per_cluster} --run-mode {run_mode}"
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
