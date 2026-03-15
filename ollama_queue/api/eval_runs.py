"""Eval run lifecycle endpoints: CRUD, progress, results, cancel, repeat, judge-rerun, promote, analysis, confusion."""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

import ollama_queue.api as _api
from ollama_queue.eval.engine import (
    compute_run_analysis,
    create_eval_run,
    get_eval_run,
    run_eval_session,
    update_eval_run,
)
from ollama_queue.eval.judge import run_eval_judge
from ollama_queue.eval.promote import do_promote_eval_run, generate_eval_analysis

_log = logging.getLogger(__name__)

router = APIRouter()

# --- Eval: Runs lifecycle ---
# NOTE: fixed-path routes (/runs) must be declared before parameterized routes
# (/{run_id} sub-routes) to prevent shadowing.


@router.get("/api/eval/runs")
def list_eval_runs(limit: int = 20, offset: int = 0):
    """Returns a paginated list of eval runs.

    # What it shows: All eval runs in reverse-creation order with summary metrics.
    # Decision it drives: Lets the user review run history, spot failures, and pick
    #   a run to promote or judge-rerun.
    """
    db = _api.db

    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")
    limit = max(1, min(limit, 1000))

    with db._lock:
        conn = db._connect()
        rows = conn.execute(
            """SELECT id, status, variants, variant_id, winner_variant, metrics,
                      item_count, item_ids, started_at, completed_at,
                      judge_model, analysis_md, error, label, scheduled_by,
                      error_budget, run_mode, judge_mode,
                      gen_backend_url, judge_backend_url
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
                "judge_mode": r.get("judge_mode"),
                "analysis_md": r.get("analysis_md"),
                "error": r.get("error"),
                "label": r.get("label"),
                "scheduled_by": r.get("scheduled_by"),
                "error_budget": r.get("error_budget"),
                "run_mode": r.get("run_mode"),
                "gen_backend_url": r.get("gen_backend_url"),
                "judge_backend_url": r.get("judge_backend_url"),
            }
        )
    return result


@router.post("/api/eval/runs")
def trigger_eval_run(body: dict = Body(...)):
    """Trigger a new eval run for a given variant.

    # What it shows: N/A — write-only; new run appears in GET /api/eval/runs.
    # Decision it drives: Lets the user kick off a fresh evaluation for any variant
    #   without touching the CLI.
    """
    db = _api.db

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

    # Backend URL overrides (optional — per-run override of eval settings)
    gen_backend_url = body.get("gen_backend_url")
    judge_backend_url = body.get("judge_backend_url")

    # Create the run row
    run_id = create_eval_run(
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
        gen_backend_url=gen_backend_url,
        judge_backend_url=judge_backend_url,
    )

    # Persist judge_model from request body so run_eval_judge uses it instead of the setting default
    judge_model = body.get("judge_model")
    if judge_model and isinstance(judge_model, str):
        update_eval_run(db, run_id, judge_model=judge_model)

    # Persist judge_mode from request body (default: "bayesian")
    judge_mode = body.get("judge_mode", "bayesian")
    valid_judge_modes = ("rubric", "binary", "tournament", "bayesian")
    if judge_mode not in valid_judge_modes:
        raise HTTPException(
            status_code=400,
            detail=f"judge_mode must be one of: {', '.join(valid_judge_modes)}",
        )
    update_eval_run(db, run_id, judge_mode=judge_mode)

    # Run the session in a background thread — NOT as a queued job.
    # Running as a queued job would deadlock: the daemon sets current_job_id while
    # the subprocess runs, which blocks try_claim_for_proxy() when the engine
    # calls /api/generate. Background thread avoids that contention.
    import threading as _threading

    _captured_run_id = run_id

    def _run_session_in_background() -> None:
        try:
            run_eval_session(_captured_run_id, db)
        except Exception as exc:
            _log.exception("run_eval_session failed for run_id=%d", _captured_run_id)
            try:
                import time as _time_bg

                update_eval_run(
                    db,
                    _captured_run_id,
                    status="failed",
                    error=f"background thread crash: {type(exc).__name__}: {exc}",
                    completed_at=_time_bg.time(),
                )
            except Exception:
                _log.exception("Failed to mark run %d as failed", _captured_run_id)

    _threading.Thread(target=_run_session_in_background, daemon=True).start()

    return JSONResponse(content={"run_id": run_id}, status_code=201)


@router.get("/api/eval/runs/{run_id}")
def get_eval_run_detail(run_id: int):
    """Returns full detail for one eval run, including parsed metrics JSON.

    # What it shows: All fields for a single eval run — status, metrics, error, item list.
    # Decision it drives: Lets the user inspect an individual run's outcome and decide
    #   whether to promote, judge-rerun, or investigate failures.
    """
    db = _api.db

    run = get_eval_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")

    # Never leak the data source token via the API
    run.pop("data_source_token", None)

    # Parse metrics JSON field
    if run.get("metrics"):
        try:
            run["metrics"] = json.loads(run["metrics"])
        except (ValueError, TypeError):
            _log.warning("get_eval_run_detail: failed to parse metrics for run %d", run_id)
    return run


@router.delete("/api/eval/runs/{run_id}")
def cancel_eval_run(run_id: int):
    """Cancel a queued or running eval run.

    # What it shows: N/A — state change; updated status visible in GET /api/eval/runs/{id}.
    # Decision it drives: Lets the user abort a run that is stuck or no longer needed
    #   without waiting for it to time out.
    """
    db = _api.db

    run = get_eval_run(db, run_id)
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

    update_eval_run(db, run_id, status="cancelled", completed_at=_cdt.now(UTC).isoformat())
    return {"ok": True, "run_id": run_id}


@router.post("/api/eval/runs/{run_id}/analyze")
def analyze_eval_run(run_id: int):
    """Trigger on-demand Ollama analysis for a completed eval run.

    # What it shows: N/A — write-only; analysis_md appears in GET /api/eval/runs after completion.
    # Decision it drives: Lets the user request analysis for any completed run, including
    #   runs that completed before this feature was introduced or when the model was unavailable.
    """
    db = _api.db
    import threading as _threading_analyze

    run = get_eval_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")
    if run.get("status") != "complete":
        raise HTTPException(status_code=400, detail="Analysis requires a completed run")

    def _run_analysis() -> None:
        try:
            generate_eval_analysis(db, run_id)
        except Exception:
            _log.exception("generate_eval_analysis failed for run_id=%d", run_id)
            try:
                update_eval_run(db, run_id, analysis_md="[Analysis failed — see server logs]")
            except Exception:
                _log.exception("could not record analysis failure for run_id=%d", run_id)

    _threading_analyze.Thread(target=_run_analysis, daemon=True).start()
    return {"ok": True, "run_id": run_id, "message": "Analysis started in background"}


@router.get("/api/eval/runs/{run_id}/analysis")
def get_eval_run_analysis(run_id: int):
    """Return pre-computed structured analysis for a run.

    # What it shows: Per-item breakdown, failure cases, and bootstrap CIs from analysis_json.
    # Decision it drives: Shows which items are hardest, which pairs are misclassified,
    #   and how confident we should be in F1 scores — without needing another Ollama call.
    """
    db = _api.db
    with db._lock:
        conn = db._connect()
        row = conn.execute("SELECT analysis_json FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Run {run_id} not found")
    if not row["analysis_json"]:
        return {"status": "not_computed"}
    return json.loads(row["analysis_json"])


@router.post("/api/eval/runs/{run_id}/reanalyze")
def reanalyze_eval_run(run_id: int):
    """Recompute structured analysis for a completed run (synchronous).

    # What it shows: N/A — write-only; triggers recomputation of analysis_json.
    # Decision it drives: Lets the user refresh analysis after threshold changes
    #   or when analysis wasn't computed during the original run.
    """
    db = _api.db

    run = get_eval_run(db, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    if run["status"] != "complete":
        raise HTTPException(400, f"Run must be complete (current: {run['status']})")
    compute_run_analysis(run_id, db)
    return {"ok": True}


@router.get("/api/eval/runs/{run_id}/confusion")
def get_eval_run_confusion(run_id: int):
    """Returns cluster confusion matrix for a completed eval run.

    # What it shows: Cross-cluster transfer score heatmap — which cluster pairs
    #   have principle "bleed" where principles from one cluster falsely match another.
    # Decision it drives: Identifies ambiguous cluster boundaries that need either
    #   merging (if semantically similar) or more discriminative prompts.
    """
    db = _api.db

    run = get_eval_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")

    with db._lock:
        conn = db._connect()
        rows = [
            dict(r)
            for r in conn.execute(
                """SELECT source_cluster_id, target_cluster_id,
                          COALESCE(override_score_transfer, score_transfer) AS transfer,
                          is_same_cluster
                   FROM eval_results
                   WHERE run_id = ? AND row_type = 'judge'
                     AND score_transfer IS NOT NULL AND error IS NULL
                     AND source_cluster_id IS NOT NULL
                     AND target_cluster_id IS NOT NULL""",
                (run_id,),
            ).fetchall()
        ]

    if not rows:
        return {"matrix": {}, "flagged": [], "clusters": []}

    # Build (source, target) → [transfer scores]
    buckets: dict[str, dict[str, list[int]]] = {}
    for r in rows:
        src = r["source_cluster_id"]
        tgt = r["target_cluster_id"]
        buckets.setdefault(src, {}).setdefault(tgt, []).append(r["transfer"])

    clusters = sorted({r["source_cluster_id"] for r in rows} | {r["target_cluster_id"] for r in rows})

    matrix: dict[str, dict[str, dict]] = {}
    flagged: list[dict] = []
    for src in clusters:
        matrix[src] = {}
        for tgt in clusters:
            scores = buckets.get(src, {}).get(tgt, [])
            if scores:
                avg = round(sum(scores) / len(scores), 2)
                matrix[src][tgt] = {"avg_transfer": avg, "count": len(scores)}
                if src != tgt and avg >= 3.0:
                    flagged.append({"source": src, "target": tgt, "avg_transfer": avg, "count": len(scores)})

    flagged.sort(key=lambda x: -x["avg_transfer"])
    return {"matrix": matrix, "flagged": flagged, "clusters": clusters}


@router.get("/api/eval/runs/{run_id}/results")
def get_eval_run_results(
    run_id: int,
    row_type: str | None = None,
    classification: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """Returns eval_results rows for an eval run with optional filters.

    # What it shows: Per-item judge scores for one run — source, target, scores, errors.
    # Decision it drives: Lets the user drill into which items scored well or poorly
    #   to identify weak spots in a variant's principle transfer.
    #   classification param filters to tp/tn/fp/fn error classes.
    """
    db = _api.db

    run = get_eval_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")

    where_clauses = ["run_id = ?"]
    params: list = [run_id]

    if row_type:
        where_clauses.append("row_type = ?")
        params.append(row_type)

    # Classification filter (tp/tn/fp/fn) — requires judge rows with scores
    if classification in ("fp", "fn", "tp", "tn"):
        threshold = 3
        try:
            with db._lock:
                conn2 = db._connect()
                t_row = conn2.execute("SELECT value FROM settings WHERE key = 'eval.positive_threshold'").fetchone()
                if t_row:
                    threshold = int(json.loads(t_row["value"]))
        except Exception:
            _log.debug("get_eval_run_results: could not read positive_threshold, using default=%d", threshold)
        score_col = "COALESCE(override_score_transfer, score_transfer)"
        if classification == "fp":
            where_clauses.append(f"(is_same_cluster = 0 OR is_same_cluster IS NULL) AND {score_col} >= ?")
        elif classification == "fn":
            where_clauses.append(f"is_same_cluster = 1 AND {score_col} < ?")
        elif classification == "tp":
            where_clauses.append(f"is_same_cluster = 1 AND {score_col} >= ?")
        elif classification == "tn":
            where_clauses.append(f"(is_same_cluster = 0 OR is_same_cluster IS NULL) AND {score_col} < ?")
        params.append(threshold)

    where_sql = " AND ".join(where_clauses)
    params.extend([limit, offset])

    with db._lock:
        conn = db._connect()
        rows = conn.execute(
            f"SELECT * FROM eval_results WHERE {where_sql} ORDER BY id LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/eval/runs/{run_id}/progress")
def get_eval_run_progress(run_id: int):
    """Returns live progress for an active eval run (generated, judged, failed counts).

    # What it shows: How far along a running eval is — useful for frontend polling every 5s.
    # Decision it drives: Lets the user know if a run is progressing normally or stalled.
    """
    db = _api.db

    run = get_eval_run(db, run_id)
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


@router.post("/api/eval/runs/{run_id}/repeat")
def repeat_eval_run(run_id: int):
    """Create a new eval run that exactly replicates a completed run's item set and seed.

    # What it shows: N/A — write-only; the new run appears in GET /api/eval/runs.
    # Decision it drives: Lets the user re-run an identical eval to verify result stability
    #   or compare against a configuration change while holding all other variables constant.
    """
    db = _api.db
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
               (data_source_url, data_source_token, variants, per_cluster, status, run_mode,
                item_ids, seed, judge_model, judge_backend, error_budget,
                judge_mode, started_at)
               VALUES (?, ?, ?, ?, 'queued', ?,
                       ?, ?, ?, ?, ?,
                       ?, ?)""",
            (
                orig["data_source_url"],
                orig.get("data_source_token"),
                orig["variants"],
                orig["per_cluster"],
                orig.get("run_mode") or "batch",
                orig["item_ids"],
                orig["seed"],
                orig.get("judge_model"),
                orig.get("judge_backend"),
                orig.get("error_budget") or 0.30,
                orig.get("judge_mode") or "rubric",
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
            run_eval_session(_captured_new_id, db)
        except Exception as exc:
            _log.exception("run_eval_session failed for repeat run_id=%d", _captured_new_id)
            try:
                import time as _time_repeat

                update_eval_run(
                    db,
                    _captured_new_id,
                    status="failed",
                    error=f"background thread crash: {type(exc).__name__}: {exc}",
                    completed_at=_time_repeat.time(),
                )
            except Exception:
                _log.exception("Failed to mark repeat run %d as failed", _captured_new_id)

    _threading.Thread(target=_run_repeat_in_background, daemon=True).start()

    return JSONResponse(content={"run_id": new_run_id}, status_code=201)


@router.post("/api/eval/runs/{run_id}/promote")
def promote_eval_run(run_id: int, body: dict = Body(default={})):
    """Mark a completed run's winner variant as the production variant.

    # What it shows: N/A — write action; updates lessons-db + local eval_variants.
    # Decision it drives: Promotes the winning eval config to production so the system
    #   uses it for future inference without manual DB edits.

    Accepts an empty body {}. Resolves the model/template/temperature/num_ctx
    automatically from the run's winner_variant in eval_variants.
    """
    db = _api.db

    try:
        result = do_promote_eval_run(db, run_id)
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


@router.post("/api/eval/runs/{run_id}/judge-rerun")
def judge_rerun_eval_run(run_id: int, body: dict = Body(default={})):
    """Re-run the judge phase on an existing completed run with new judge settings.

    # What it shows: N/A — creates a new run; visible in GET /api/eval/runs.
    # Decision it drives: Lets the user upgrade the judge model or temperature and
    #   see whether scores change without re-running generation.
    """
    db = _api.db

    run = get_eval_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Eval run {run_id} not found")

    if run["status"] not in ("complete", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"judge-rerun only allowed on complete or failed runs (current: {run['status']})",
        )

    # Create a new run copying item_ids and seed from the original, starting at judging
    new_run_id = create_eval_run(
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

    # Propagate judge_mode from original run (or allow override from body)
    rerun_judge_mode = body.get("judge_mode") or run.get("judge_mode") or "rubric"
    update_eval_run(db, new_run_id, judge_mode=rerun_judge_mode)

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
    update_eval_run(db, new_run_id, status="judging")

    # Spawn background thread for the judge phase — same pattern as trigger_eval_run.
    # (The `ollama-queue eval-run` CLI subcommand does not exist; queue-job approach
    # would fail with exit code 2.)
    import threading as _threading_jr

    _captured_judge_id = new_run_id

    def _run_judge_in_background() -> None:
        try:
            run_eval_judge(_captured_judge_id, db)
        except Exception as _exc:
            _log.exception("run_eval_judge failed for judge-rerun run_id=%d", _captured_judge_id)
            try:
                import datetime as _dt_jr

                update_eval_run(
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
