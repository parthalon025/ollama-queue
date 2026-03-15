"""Eval promote phase: auto-promote logic and post-run Ollama analysis generation.

do_promote_eval_run: shared core for manual and auto-promote.
check_auto_promote: gate-checked auto-promotion (never raises).
generate_eval_analysis: Ollama-powered post-run analysis (never raises).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx

import ollama_queue.eval.engine as _eng
from ollama_queue.eval.judge import build_analysis_prompt

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Promote core
# ---------------------------------------------------------------------------


def do_promote_eval_run(db: Database, run_id: int) -> dict:
    """Core promote logic: resolve winner variant, call lessons-db, update local DB.

    Returns {"ok": True, "run_id": run_id, "variant_id": variant_id, "label": label}.
    Raises ValueError for validation failures, httpx.HTTPError for lessons-db failures.
    Both callers (promote_eval_run API endpoint and check_auto_promote) use this function.
    """
    run = _eng.get_eval_run(db, run_id)
    if run is None:
        raise ValueError(f"Eval run {run_id} not found")
    if run["status"] != "complete":
        raise ValueError(f"Run {run_id} is not complete (status: {run['status']})")

    winner_variant = run.get("winner_variant")
    if not winner_variant:
        raise ValueError(f"Run {run_id} has no winner_variant")

    variant = _eng.get_eval_variant(db, winner_variant)
    if variant is None:
        raise ValueError(f"Variant {winner_variant!r} not found in eval_variants")

    # Call lessons-db to register the new production variant
    data_source_url = run.get("data_source_url") or db.get_setting("eval.data_source_url") or "http://127.0.0.1:7685"
    promote_url = f"{data_source_url.rstrip('/')}/eval/production-variant"
    payload = {
        "model": variant["model"],
        "prompt_template_id": variant["prompt_template_id"],
        "temperature": variant.get("temperature"),
        "num_ctx": variant.get("num_ctx"),
        "system_prompt": variant.get("system_prompt"),
        "params": variant.get("params"),
        "provider": variant.get("provider", "ollama"),
        "training_config": variant.get("training_config"),
    }
    resp = httpx.post(promote_url, json=payload, timeout=10.0)
    if resp.status_code not in (200, 201, 204):
        raise httpx.HTTPStatusError(
            f"lessons-db promote endpoint returned HTTP {resp.status_code}",
            request=resp.request,
            response=resp,
        )

    # Update local eval_variants atomically: set winner and clear all others in one lock
    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE eval_variants SET is_recommended = 1, is_production = 1 WHERE id = ?",
            (winner_variant,),
        )
        conn.execute(
            "UPDATE eval_variants SET is_recommended = 0, is_production = 0 WHERE id != ?",
            (winner_variant,),
        )
        conn.commit()

    label = variant.get("label", winner_variant)
    _log.info("Promoted variant %s (label=%r) to production for run %d", winner_variant, label, run_id)
    return {"ok": True, "run_id": run_id, "variant_id": winner_variant, "label": label}


# ---------------------------------------------------------------------------
# Auto-promote
# ---------------------------------------------------------------------------


def check_auto_promote(db: Database, run_id: int, http_base: str) -> None:
    """Check whether a completed eval run qualifies for auto-promotion.

    Gate criteria depend on judge_mode:

    **Legacy (rubric/binary):**
    1. Winner F1 >= eval.f1_threshold
    2. Winner F1 > production_F1 + eval.auto_promote_min_improvement
    3. error_budget_used <= eval.error_budget

    **Bayesian/tournament:**
    1. Winner AUC >= eval.auc_threshold (default 0.85)
    1b. Winner separation >= eval.min_posterior_separation (default 0.4)
    2. Winner AUC > production_AUC + eval.auto_promote_min_improvement
    3. error_budget_used <= eval.error_budget

    Optional stability gate: winner must have cleared the quality threshold
    in the last eval.stability_window completed runs (if stability_window > 0).

    NEVER raises — all errors are logged and the function returns silently.
    Same contract as generate_eval_analysis.
    """
    try:
        _check_auto_promote_inner(db, run_id)
    except Exception:
        _log.exception("check_auto_promote: unhandled error for run_id=%d", run_id)


def _check_auto_promote_inner(db: Database, run_id: int) -> None:  # noqa: PLR0911
    """Inner implementation called by check_auto_promote. May raise."""
    # Gate 0: auto-promote enabled?
    if not db.get_setting("eval.auto_promote"):
        return

    run = _eng.get_eval_run(db, run_id)
    if run is None or run.get("status") != "complete":
        return

    winner_variant = run.get("winner_variant")
    if not winner_variant:
        _log.info("check_auto_promote: run %d has no winner_variant, skipping", run_id)
        return

    # Parse metrics from run
    metrics_raw = run.get("metrics")
    try:
        parsed_metrics = json.loads(metrics_raw) if isinstance(metrics_raw, str) else (metrics_raw or {})
    except (json.JSONDecodeError, TypeError):
        _log.warning("check_auto_promote: run %d metrics unparseable, skipping", run_id)
        return

    # Determine quality metric based on judge_mode
    judge_mode = run.get("judge_mode", "rubric")
    is_bayesian = judge_mode in ("bayesian", "tournament")

    if is_bayesian:
        quality_metric = "auc"
        _v = db.get_setting("eval.auc_threshold")
        quality_threshold = float(_v) if _v is not None else 0.85
    else:
        quality_metric = "f1"
        _v = db.get_setting("eval.f1_threshold")
        quality_threshold = float(_v) if _v is not None else 0.75

    winner_quality = (parsed_metrics.get(winner_variant) or {}).get(quality_metric)
    if winner_quality is None:
        _log.info("check_auto_promote: run %d winner %s has no %s, skipping", run_id, winner_variant, quality_metric)
        return

    # Gate 1: quality metric >= threshold
    if winner_quality < quality_threshold:
        _log.info(
            "check_auto_promote: run %d winner %s=%.3f < threshold %.3f, skipping",
            run_id,
            quality_metric,
            winner_quality,
            quality_threshold,
        )
        return

    # Bayesian-specific gate: posterior separation must exceed minimum
    if is_bayesian:
        _v = db.get_setting("eval.min_posterior_separation")
        min_separation = float(_v) if _v is not None else 0.4
        winner_separation = (parsed_metrics.get(winner_variant) or {}).get("separation")
        if winner_separation is None or winner_separation < min_separation:
            _log.info(
                "check_auto_promote: run %d winner separation=%s < min %.3f, skipping",
                run_id,
                winner_separation,
                min_separation,
            )
            return

    # Gate 2: quality > production_quality + min_improvement
    _v = db.get_setting("eval.auto_promote_min_improvement")
    min_improvement = float(_v) if _v is not None else 0.05
    production_quality: float | None = None

    with db._lock:
        conn = db._connect()
        prod_row = conn.execute("SELECT id FROM eval_variants WHERE is_production = 1 LIMIT 1").fetchone()
        prod_run_row = None
        if prod_row is not None:
            prod_id = prod_row["id"]
            prod_run_row = conn.execute(
                "SELECT metrics FROM eval_runs WHERE winner_variant = ? AND status = 'complete' "
                "ORDER BY id DESC LIMIT 1",
                (prod_id,),
            ).fetchone()

    if prod_row is None:
        # No production variant exists — this is the first-ever eval run.
        # Block auto-promote to require manual promotion establishing the baseline.
        _log.info(
            "check_auto_promote: run %d — no production baseline exists. "
            "First run requires manual promote to establish baseline.",
            run_id,
        )
        return

    if prod_run_row is None:
        _log.info(
            "check_auto_promote: production variant %s has no eval baseline — skipping Gate 2 as unsafe",
            prod_id,
        )
        return

    try:
        m = json.loads(prod_run_row["metrics"]) if isinstance(prod_run_row["metrics"], str) else {}
        production_quality = (m.get(prod_id) or {}).get(quality_metric)
    except (json.JSONDecodeError, TypeError):
        _log.warning(
            "check_auto_promote: production metrics unparseable for variant %s — gate 2 skipped as unsafe",
            prod_id,
        )
        return

    if production_quality is not None and winner_quality <= production_quality + min_improvement:
        _log.info(
            "check_auto_promote: run %d winner %s=%.3f not enough improvement over "
            "production %s=%.3f (need +%.3f), skipping",
            run_id,
            quality_metric,
            winner_quality,
            quality_metric,
            production_quality,
            min_improvement,
        )
        return

    # Gate 3: error_budget_used <= error_budget
    # Denominator is judge_row_count (rows actually judged in this run), NOT item_count
    # (total source dataset size).  Using item_count under-counts the error rate when
    # judging is partial: e.g. 5 errors / 100 items = 5% but 5 errors / 40 judged = 12.5%.
    _eb = db.get_setting("eval.error_budget")
    error_budget = float(_eb) if _eb is not None else 0.30
    with db._lock:
        conn = db._connect()
        judge_row_count = conn.execute(
            "SELECT COUNT(*) FROM eval_results WHERE run_id = ? AND row_type = 'judge'",
            (run_id,),
        ).fetchone()[0]
        failed_count = conn.execute(
            "SELECT COUNT(*) FROM eval_results WHERE run_id = ? AND score_transfer IS NULL AND row_type = 'judge'",
            (run_id,),
        ).fetchone()[0]
    if judge_row_count == 0:
        _log.info(
            "check_auto_promote: run %d has no judge rows — blocking Gate 3 (error rate undefined)",
            run_id,
        )
        return
    error_budget_used = failed_count / judge_row_count
    if error_budget_used > error_budget:
        _log.info(
            "check_auto_promote: run %d error_budget_used=%.3f > %.3f, skipping",
            run_id,
            error_budget_used,
            error_budget,
        )
        return

    # Stability window gate (optional)
    stability_window = int(db.get_setting("eval.stability_window") or 0)
    if stability_window > 0:
        with db._lock:
            conn = db._connect()
            recent_rows = conn.execute(
                "SELECT metrics FROM eval_runs WHERE winner_variant = ? AND status = 'complete' "
                "ORDER BY id DESC LIMIT ?",
                (winner_variant, stability_window),
            ).fetchall()
        if len(recent_rows) < stability_window:
            _log.info(
                "check_auto_promote: variant %s only has %d/%d runs in stability window, skipping",
                winner_variant,
                len(recent_rows),
                stability_window,
            )
            return
        for row in recent_rows:
            try:
                m = json.loads(row["metrics"]) if isinstance(row["metrics"], str) else {}
                row_quality = (m.get(winner_variant) or {}).get(quality_metric)
                if row_quality is None or row_quality < quality_threshold:
                    _log.info(
                        "check_auto_promote: variant %s stability check failed (%s=%s < %.3f), skipping",
                        winner_variant,
                        quality_metric,
                        row_quality,
                        quality_threshold,
                    )
                    return
            except (json.JSONDecodeError, TypeError):
                _log.warning("check_auto_promote: could not parse stability run metrics, skipping")
                return

    # All gates passed — auto-promote
    prod_str = (
        f", +{winner_quality - production_quality:.2f} over production={production_quality:.2f}"
        if production_quality is not None
        else ""
    )
    _log.info(
        "Auto-promoting variant %s (%s=%.2f%s) for run %d",
        winner_variant,
        quality_metric,
        winner_quality,
        prod_str,
        run_id,
    )
    do_promote_eval_run(db, run_id)


# ---------------------------------------------------------------------------
# Post-run Ollama analysis (analysis_md)
# ---------------------------------------------------------------------------


def generate_eval_analysis(  # noqa: PLR0911 — guard-and-return pattern is intentional
    db: Database,
    run_id: int,
    http_base: str = "http://127.0.0.1:7683",
) -> None:
    """Generate an Ollama-powered analysis of a completed eval run.

    Builds a prompt from the run metrics and a sample of best/worst-scoring
    pairs, calls the analysis model through the proxy, and stores the result
    in eval_runs.analysis_md.

    Called automatically at the end of run_eval_session() after judging.
    Also callable on demand via POST /api/eval/runs/{id}/analyze.
    Falls through silently on proxy/model errors so failures never affect
    the already-completed run record.
    """
    run = _eng.get_eval_run(db, run_id)
    if run is None:
        _log.error("generate_eval_analysis: run_id=%d not found", run_id)
        return

    if run.get("status") != "complete":
        _log.warning(
            "generate_eval_analysis: run_id=%d status=%s — only complete runs are analysed",
            run_id,
            run.get("status"),
        )
        return

    metrics: dict = {}
    raw_metrics = run.get("metrics")
    if raw_metrics:
        try:
            metrics = json.loads(raw_metrics) if isinstance(raw_metrics, str) else raw_metrics
        except (ValueError, TypeError):
            _log.warning("generate_eval_analysis: could not parse metrics for run_id=%d", run_id)
    if not metrics:
        _log.warning("generate_eval_analysis: run_id=%d has no metrics — skipping analysis", run_id)
        return

    # Resolve analysis model: dedicated setting -> run's judge model -> global judge default
    analysis_model: str = (
        _eng._get_eval_setting(db, "eval.analysis_model", "")
        or run.get("judge_model")
        or _eng._get_eval_setting(db, "eval.judge_model", "")
    )
    if not analysis_model:
        _log.warning("No analysis model configured — skipping analysis for run %d", run_id)
        return

    # Resolve analysis backend: setting value "auto" → None (normal proxy routing)
    analysis_backend = db.get_setting("eval.analysis_backend_url")
    if analysis_backend == "auto":
        analysis_backend = None

    try:
        variant_ids: list[str] = json.loads(run.get("variants") or "[]")
        if not isinstance(variant_ids, list):
            variant_ids = [str(variant_ids)]
    except (ValueError, TypeError) as exc:
        _log.warning(
            "generate_eval_analysis: could not parse variants for run_id=%d (%s) — proceeding with empty list",
            run_id,
            exc,
        )
        variant_ids = []

    try:
        top_pairs, bottom_pairs = _eng._fetch_analysis_samples(db, run_id)
    except Exception:
        _log.exception(
            "generate_eval_analysis: failed to fetch analysis samples for run_id=%d — skipping",
            run_id,
        )
        return

    try:
        prompt = build_analysis_prompt(
            run_id=run_id,
            variants=variant_ids,
            item_count=run.get("item_count") or 0,
            judge_model=run.get("judge_model") or "",
            metrics=metrics,
            winner=run.get("winner_variant"),
            top_pairs=top_pairs,
            bottom_pairs=bottom_pairs,
        )
    except (KeyError, TypeError) as exc:
        _log.error(
            "generate_eval_analysis: failed to build prompt for run_id=%d — malformed metrics: %s",
            run_id,
            exc,
        )
        return

    _log.info(
        "generate_eval_analysis: calling %s for run_id=%d (%d variants, %d+%d samples)",
        analysis_model,
        run_id,
        len(variant_ids),
        len(top_pairs),
        len(bottom_pairs),
    )

    try:
        analysis_text, _ = _eng._call_proxy(
            http_base=http_base,
            model=analysis_model,
            prompt=prompt,
            temperature=0.3,  # low temp for consistent, deterministic analysis
            num_ctx=4096,
            timeout=180,
            source=f"eval-analysis-{run_id}",
            priority=9,  # background — must not displace user work (critical tier = 1-2)
            backend=analysis_backend,
        )
    except _eng._ProxyDownError as exc:
        _log.warning("generate_eval_analysis: proxy down for run_id=%d: %s", run_id, exc)
        return

    if not analysis_text:
        _log.warning("generate_eval_analysis: empty response from %s for run_id=%d", analysis_model, run_id)
        return

    try:
        _eng.update_eval_run(db, run_id, analysis_md=analysis_text)
    except Exception:
        _log.exception("generate_eval_analysis: failed to store analysis for run_id=%d", run_id)
        return

    _log.info("generate_eval_analysis: stored %d chars for run_id=%d", len(analysis_text), run_id)
