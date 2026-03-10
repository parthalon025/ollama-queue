"""Eval trends endpoint."""

from __future__ import annotations

import json
import statistics

from fastapi import APIRouter

import ollama_queue.api as _api

router = APIRouter()


@router.get("/api/eval/trends")
def get_eval_trends():
    """Returns per-variant trend data: run history, stability, trend direction, judge agreement.

    What it shows: How each variant's F1 quality score has changed across recent completed runs.
    Decision it drives: Lets the user identify improving vs. regressing variants and when to promote.
    """
    db = _api.db

    with db._lock:
        conn = db._connect()
        runs = conn.execute(
            """SELECT id, started_at, metrics, item_ids, item_count, judge_mode
               FROM eval_runs WHERE status = 'complete' ORDER BY id ASC"""
        ).fetchall()
        # Fetch agreement counts inside the same lock to avoid racing with
        # background eval threads that hold db._lock while inserting results.
        _agreed_expr = "SUM(CASE WHEN COALESCE(override_score_transfer, score_transfer) > 1 THEN 1 ELSE 0 END)"
        agreement_rows = conn.execute(
            f"SELECT variant, COUNT(*) as total, {_agreed_expr} as agreed FROM eval_results GROUP BY variant"
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
            entry = {
                "run_id": run_row["id"],
                "started_at": run_row["started_at"],
                "f1": var_metrics.get("f1"),
                "recall": var_metrics.get("recall"),
                "precision": var_metrics.get("precision"),
                "item_count": run_row["item_count"],
                "judge_mode": run_row["judge_mode"],
                "auc": var_metrics.get("auc"),
                "separation": var_metrics.get("separation"),
                "same_mean_posterior": var_metrics.get("same_mean_posterior"),
                "diff_mean_posterior": var_metrics.get("diff_mean_posterior"),
            }
            variant_runs[var_id].append(entry)

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

        # Use AUC as the quality metric for bayesian/tournament runs, F1 for legacy
        has_bayesian = any(r.get("judge_mode") in ("bayesian", "tournament") for r in recent)
        quality_key = "auc" if has_bayesian else "f1"
        quality_values = [r[quality_key] for r in recent if r.get(quality_key) is not None]
        latest_quality = quality_values[-1] if quality_values else None

        # Stability: 1 - stddev(last 3 quality scores) if >= 3 runs
        stability = None
        if len(quality_values) >= 3:
            last3 = quality_values[-3:]
            try:
                stability = round(max(0.0, 1.0 - statistics.stdev(last3)), 4)
            except statistics.StatisticsError:
                stability = None

        # Trend direction: slope of quality values
        trend_direction = "stable"
        if len(quality_values) >= 2:
            n = len(quality_values)
            x_mean = (n - 1) / 2
            y_mean = sum(quality_values) / n
            numerator = sum((i - x_mean) * (quality_values[i] - y_mean) for i in range(n))
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
            "latest_f1": latest_quality,
            "judge_agreement_rate": agreement_by_variant.get(var_id),
        }

    # item_sets_differ: true if not all completed runs share the same item_ids JSON
    item_sets_differ = len(set(item_id_sets)) > 1 if item_id_sets else False

    return {"variants": result, "item_sets_differ": item_sets_differ}
