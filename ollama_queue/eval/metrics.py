"""Eval metrics computation and report rendering.

Pure computation: F1/recall/precision from scored results, tournament/Bayesian
aggregate metrics, and markdown report generation. The only DB access is in
render_report() for variant config details.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ollama_queue.db import Database


# Prior: P(transfers) = 0.25 — most principles DON'T transfer to arbitrary targets
_PRIOR_LOG_ODDS = math.log(0.25 / 0.75)  # approx -1.10


def _compute_f1_block(same: list[dict], diff: list[dict], all_pairs: list[dict]) -> dict[str, float]:
    """Compute F1/recall/precision/actionability from same- and diff-cluster pair lists."""
    recall = sum(p["effective_score_transfer"] for p in same) / (len(same) * 5.0) if same else 0.0
    precision = 1.0 - sum(p["effective_score_transfer"] for p in diff) / (len(diff) * 5.0) if diff else 0.0
    f1 = 2.0 * recall * precision / (recall + precision) if (recall + precision) > 0 else 0.0
    all_act = [p["effective_score_action"] for p in all_pairs]
    actionability = sum(all_act) / len(all_act) if all_act else 0.0
    return {
        "f1": round(f1, 4),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "actionability": round(actionability, 4),
        "sample_count": len(all_pairs),
    }


def compute_metrics(results: list[dict]) -> dict[str, dict[str, Any]]:
    """Compute per-variant F1, recall, precision, actionability from scored results.

    results: list of dicts with keys:
        variant, is_same_cluster,
        effective_score_transfer, effective_score_precision, effective_score_action
        (optional) source_cluster_id — when present, per_cluster breakdown is included

    effective_score = COALESCE(override_score, score) — caller pre-computes this.

    Returns: {variant_id: {f1, recall, precision, actionability, sample_count, per_cluster?}}

    F1 definition (spec):
      recall    = avg transfer score on same_cluster pairs / 5.0
      precision = 1 - avg transfer score on diff_cluster pairs / 5.0
      f1        = 2 * recall * precision / (recall + precision)
    """
    by_variant: dict[str, list[dict]] = {}
    for r in results:
        by_variant.setdefault(r["variant"], []).append(r)

    metrics: dict[str, dict[str, Any]] = {}
    for variant, pairs in by_variant.items():
        same = [p for p in pairs if p["is_same_cluster"]]
        diff = [p for p in pairs if not p["is_same_cluster"]]

        m = _compute_f1_block(same, diff, pairs)

        # Per-cluster breakdown (when source_cluster_id is available)
        has_clusters = any(p.get("source_cluster_id") for p in pairs)
        if has_clusters:
            by_cluster: dict[str, list[dict]] = {}
            for p in pairs:
                cid = p.get("source_cluster_id") or ""
                if cid:
                    by_cluster.setdefault(cid, []).append(p)
            per_cluster: dict[str, dict[str, float]] = {}
            for cid, cpairs in sorted(by_cluster.items()):
                csame = [p for p in cpairs if p["is_same_cluster"]]
                cdiff = [p for p in cpairs if not p["is_same_cluster"]]
                # Skip clusters with no same-cluster pairs (would show misleading metrics)
                if not csame:
                    continue
                per_cluster[cid] = _compute_f1_block(csame, cdiff, cpairs)
            m["per_cluster"] = per_cluster

        metrics[variant] = m

    return metrics


# ---------------------------------------------------------------------------
# Tournament and Bayesian aggregate metrics
# (ported from lessons-db/eval.py)
# ---------------------------------------------------------------------------


def compute_tournament_metrics(
    tournament_results: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Compute aggregate metrics from tournament results, grouped by variant.

    Returns dict of variant_id -> metrics dict with:
        mean_win_rate, discriminating_frac, principle_count,
        comparison_count, total_wins, total_losses, total_neithers
    """
    by_variant: dict[str, list[dict]] = defaultdict(list)
    for r in tournament_results:
        by_variant[r["variant"]].append(r)

    metrics: dict[str, dict[str, float]] = {}
    for variant_id, results in sorted(by_variant.items()):
        win_rates = [r["win_rate"] for r in results]
        total_comparisons = sum(r["comparisons"] for r in results)
        total_wins = sum(r["wins"] for r in results)
        total_losses = sum(r["losses"] for r in results)
        total_neithers = sum(r["neithers"] for r in results)

        metrics[variant_id] = {
            "mean_win_rate": sum(win_rates) / len(win_rates) if win_rates else 0.0,
            "discriminating_frac": (sum(1 for wr in win_rates if wr > 0.5) / len(win_rates) if win_rates else 0.0),
            "principle_count": len(results),
            "comparison_count": total_comparisons,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "total_neithers": total_neithers,
        }

    return metrics


def compute_bayesian_metrics(
    scored_pairs: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Compute AUC and separation metrics from Bayesian fusion posteriors.

    Input: list of dicts with keys: variant, is_same_group (bool), posterior (float)
    Output: per-variant metrics dict with:
        same_mean_posterior, diff_mean_posterior, separation,
        auc (Mann-Whitney U), calibration_error, pair_count
    """
    by_variant: dict[str, list[dict]] = defaultdict(list)
    for entry in scored_pairs:
        by_variant[entry["variant"]].append(entry)

    metrics: dict[str, dict[str, float]] = {}
    for variant_id, entries in sorted(by_variant.items()):
        same_posteriors = [e["posterior"] for e in entries if e["is_same_group"]]
        diff_posteriors = [e["posterior"] for e in entries if not e["is_same_group"]]

        same_mean = sum(same_posteriors) / len(same_posteriors) if same_posteriors else 0.0
        diff_mean = sum(diff_posteriors) / len(diff_posteriors) if diff_posteriors else 0.0

        # AUC via Mann-Whitney U statistic
        if same_posteriors and diff_posteriors:
            u_count = 0
            ties = 0
            for s in same_posteriors:
                for d in diff_posteriors:
                    if s > d:
                        u_count += 1
                    elif s == d:
                        ties += 1
            auc = (u_count + 0.5 * ties) / (len(same_posteriors) * len(diff_posteriors))
        else:
            auc = 0.5  # degenerate: can't compute

        # Calibration error
        all_posteriors = [e["posterior"] for e in entries]
        mean_posterior = sum(all_posteriors) / len(all_posteriors) if all_posteriors else 0.0
        actual_positive_frac = len(same_posteriors) / len(entries) if entries else 0.0
        calibration_error = abs(mean_posterior - actual_positive_frac)

        metrics[variant_id] = {
            "same_mean_posterior": same_mean,
            "diff_mean_posterior": diff_mean,
            "separation": same_mean - diff_mean,
            "auc": auc,
            "calibration_error": calibration_error,
            "pair_count": len(entries),
        }

    return metrics


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_report(run_id: int, metrics: dict[str, dict[str, float]], db: Database) -> str:
    """Generate a markdown report summarizing the eval run.

    Shows per-variant F1, recall, precision, actionability in a table.
    Shows winner (highest F1). Returns markdown string (caller stores to DB).
    """
    from ollama_queue.eval.engine import get_eval_template, get_eval_variant

    lines: list[str] = []
    lines.append(f"# Transfer-Test Evaluation Report — Run #{run_id}\n")
    lines.append(f"Generated: {datetime.now(UTC).isoformat()}\n")

    if not metrics:
        lines.append("_No scored pairs — metrics unavailable._\n")
        return "\n".join(lines) + "\n"

    # Detect V2 (Bayesian/tournament) metrics by checking for 'auc' key
    first_variant_metrics = next(iter(metrics.values()), {})
    is_v2 = "auc" in first_variant_metrics

    # Summary table
    lines.append("## Summary\n")
    if is_v2:
        lines.append("| Variant | AUC | Separation | Same Mean Posterior | Diff Mean Posterior | Pairs |")
        lines.append("|---------|-----|------------|--------------------|--------------------|-------|")
        for vid in sorted(metrics.keys()):
            m = metrics[vid]
            lines.append(
                f"| {vid} "
                f"| {m.get('auc', 0):.3f} "
                f"| {m.get('separation', 0):.3f} "
                f"| {m.get('same_mean_posterior', 0):.3f} "
                f"| {m.get('diff_mean_posterior', 0):.3f} "
                f"| {m.get('pair_count', 0)} |"
            )
    else:
        lines.append(
            "| Variant | Quality (F1) | Catches Right (Recall)"
            " | Avoids False (Precision) | Useful (Actionability) | Samples |"
        )
        lines.append(
            "|---------|-------------|------------------------|--------------------------|------------------------|---------|"
        )
        for vid in sorted(metrics.keys()):
            m = metrics[vid]
            lines.append(
                f"| {vid} "
                f"| {m['f1']:.2f} "
                f"| {m['recall']:.2f} "
                f"| {m['precision']:.2f} "
                f"| {m['actionability']:.2f} "
                f"| {m['sample_count']} |"
            )

    # Winner
    lines.append("\n## Winner\n")
    if is_v2:
        winner = max(metrics.keys(), key=lambda v: metrics[v].get("auc", 0))
        wm = metrics[winner]
        lines.append(
            f"**Variant {winner}** — AUC: {wm.get('auc', 0):.3f} "
            f"(Separation: {wm.get('separation', 0):.3f}, "
            f"Same posterior: {wm.get('same_mean_posterior', 0):.3f}, "
            f"Diff posterior: {wm.get('diff_mean_posterior', 0):.3f})"
        )
    else:
        winner = max(metrics.keys(), key=lambda v: metrics[v]["f1"])
        wm = metrics[winner]
        lines.append(
            f"**Variant {winner}** — Quality: {wm['f1']:.2f} "
            f"(Catches right: {wm['recall']:.2f}, Avoids false: {wm['precision']:.2f}, "
            f"Useful: {wm['actionability']:.2f})"
        )

    # Per-cluster breakdown (if available)
    first_m = next(iter(metrics.values()), {})
    if not is_v2 and "per_cluster" in first_m:
        lines.append("\n## Per-Cluster Breakdown\n")
        lines.append("| Cluster | Quality (F1) | Catches Right (Recall) | Avoids False (Precision) | Samples |")
        lines.append("|---------|-------------|------------------------|--------------------------|---------|")
        # Use winner variant for the breakdown
        winner_pc = metrics.get(winner, {}).get("per_cluster", {})
        for cid in sorted(winner_pc.keys()):
            cm = winner_pc[cid]
            lines.append(
                f"| {cid} | {cm['f1']:.2f} | {cm['recall']:.2f} | {cm['precision']:.2f} | {cm['sample_count']} |"
            )

    # Variant config details from DB
    variant_row = get_eval_variant(db, winner)
    if variant_row:
        template_row = get_eval_template(db, variant_row.get("prompt_template_id", ""))
        lines.append(f"\nModel: `{variant_row.get('model', 'N/A')}`")
        if template_row:
            lines.append(f"Template: `{template_row.get('label', 'N/A')}`")
        params = json.loads(variant_row.get("params") or "{}")
        params_str = f", params={params}" if params else ""
        provider = variant_row.get("provider") or "ollama"
        provider_str = f", provider={provider}" if provider != "ollama" else ""
        system_str = (
            f", system_prompt=({len(variant_row.get('system_prompt') or '')} chars)"
            if variant_row.get("system_prompt")
            else ""
        )
        lines.append(
            f"Settings: temperature={variant_row.get('temperature', 'N/A')}, "
            f"num_ctx={variant_row.get('num_ctx', 'N/A')}"
            f"{params_str}{provider_str}{system_str}"
        )

    return "\n".join(lines) + "\n"
