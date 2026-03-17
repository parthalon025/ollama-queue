"""Rule-based post-run suggestions.

What it shows: Actionable recommendations based on run metrics.
Decision it drives: Guides users on improving next eval run.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import ollama_queue.eval.engine as _eng

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)


def generate_suggestions(db: Database, run_id: int) -> None:
    """Generate rule-based suggestions for a completed eval run.

    Reads metrics_json and analysis_json from the run, applies heuristic rules,
    and stores a list of suggestion dicts as suggestions_json on the run row.

    Each suggestion has:
        - priority: "high" | "medium" | "low"
        - message: str  (actionable, specific)

    Args:
        db: Database instance.
        run_id: The eval run to generate suggestions for.
    """
    run = _eng.get_eval_run(db, run_id)
    if run is None:
        _log.warning("suggestions: run %d not found", run_id)
        return

    # Parse metrics
    metrics: dict[str, Any] = {}
    raw_metrics = run.get("metrics")
    if raw_metrics:
        try:
            metrics = json.loads(raw_metrics) if isinstance(raw_metrics, str) else (raw_metrics or {})
        except (json.JSONDecodeError, TypeError):
            _log.warning("suggestions: could not parse metrics for run %d", run_id)

    # Parse analysis_json (optional — used for richer signals)
    analysis: dict[str, Any] = {}
    raw_analysis = run.get("analysis_json")
    if raw_analysis:
        try:
            analysis = json.loads(raw_analysis) if isinstance(raw_analysis, str) else (raw_analysis or {})
        except (json.JSONDecodeError, TypeError):
            _log.debug("suggestions: could not parse analysis_json for run %d", run_id)

    winner_variant = run.get("winner_variant")

    suggestions: list[dict[str, str]] = []

    # --- Rule 1: low quality score ---
    # Checks the winner's F1 (rubric mode) or AUC (Bayesian mode).
    # F1 key: 'f1' — from compute_metrics(). AUC key: 'auc' — from compute_bayesian_metrics().
    if winner_variant and metrics:
        winner_m = metrics.get(winner_variant) or {}
        # Detect metric type: Bayesian runs have 'auc', rubric runs have 'f1'
        if "auc" in winner_m:
            quality = winner_m.get("auc")
            quality_label = "AUC"
        else:
            quality = winner_m.get("f1")
            quality_label = "quality (F1)"

        if quality is not None:
            if quality < 0.5:
                suggestions.append(
                    {
                        "priority": "high",
                        "message": (
                            f"{quality_label} score is low ({quality:.2f}) — "
                            "try increasing per_cluster or switching to a stronger judge model"
                        ),
                    }
                )
            elif quality > 0.85:
                suggestions.append(
                    {
                        "priority": "low",
                        "message": (
                            f"Strong result ({quality_label}={quality:.2f}) — "
                            "consider enabling auto-promote in Settings"
                        ),
                    }
                )

    # --- Rule 2: no clear winner ---
    if not winner_variant:
        suggestions.append(
            {
                "priority": "medium",
                "message": (
                    "No clear winner variant — try a wider model or temperature range "
                    "to increase differentiation between variants"
                ),
            }
        )

    # --- Rule 3: high recall but low precision (too many false positives) ---
    if winner_variant and metrics:
        winner_m = metrics.get(winner_variant) or {}
        recall = winner_m.get("recall")
        precision = winner_m.get("precision")
        if recall is not None and precision is not None:
            if recall > 0.75 and precision < 0.5:
                suggestions.append(
                    {
                        "priority": "medium",
                        "message": (
                            f"High recall ({recall:.2f}) but low precision ({precision:.2f}) — "
                            "add a contrastive prompt template (variant F or G) to reduce false positives"
                        ),
                    }
                )
            elif precision > 0.75 and recall < 0.5:
                suggestions.append(
                    {
                        "priority": "medium",
                        "message": (
                            f"High precision ({precision:.2f}) but low recall ({recall:.2f}) — "
                            "principles may be too narrow; try increasing per_cluster or a more permissive template"
                        ),
                    }
                )

    # --- Rule 4: large number of failures in analysis ---
    failures = analysis.get("failures") or []
    if isinstance(failures, list) and len(failures) > 10:
        fn_count = sum(1 for f in failures if f.get("type") == "fn")
        fp_count = sum(1 for f in failures if f.get("type") == "fp")
        if fn_count > fp_count and fn_count > 5:
            suggestions.append(
                {
                    "priority": "medium",
                    "message": (
                        f"{fn_count} false negatives detected — "
                        "model is under-recognizing same-cluster transfers; "
                        "increase same_cluster_targets or use a chunked template"
                    ),
                }
            )

    _eng.update_eval_run(db, run_id, suggestions_json=json.dumps(suggestions))
    _log.info(
        "suggestions: run %d — generated %d suggestion(s)",
        run_id,
        len(suggestions),
    )
