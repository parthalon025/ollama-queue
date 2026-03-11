"""Pure analysis functions for eval pipeline.

No DB, no HTTP, no side effects. Takes lists of dicts, returns lists of dicts.
Used by eval.engine to compute structured analysis stored per-run,
and by API endpoints for live cross-run queries.
"""

from __future__ import annotations

import json
import logging
import random
import statistics
from collections import defaultdict
from typing import Any

_MIN_BOOTSTRAP_PAIRS = 10
_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_score(r: dict) -> Any:
    """Get score_transfer with explicit None check (not falsy check).

    score_transfer=0 is a valid score — must not fall through to effective_score_transfer.
    """
    s = r.get("score_transfer")
    return s if s is not None else r.get("effective_score_transfer")


def _is_positive(score_transfer: Any, threshold: int = 3) -> bool:
    """True if score >= threshold. Handle None and non-int defensively."""
    if score_transfer is None:
        return False
    try:
        return int(score_transfer) >= threshold
    except (ValueError, TypeError):
        return False


def _has_cluster_data(rows: list[dict]) -> bool:
    """True if any row has non-null is_same_cluster."""
    return any(r.get("is_same_cluster") is not None for r in rows)


def _compute_f1(tp: int, fp: int, fn: int) -> float:
    """F1 from counts. Return 0.0 on zero denominator."""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _compute_f1_from_rows(rows: list[dict], threshold: int = 3) -> float:
    """F1 from list of scored rows."""
    tp = fp = fn = 0
    for r in rows:
        is_same = bool(r.get("is_same_cluster"))
        positive = _is_positive(_get_score(r), threshold)
        if is_same and positive:
            tp += 1
        elif is_same and not positive:
            fn += 1
        elif not is_same and positive:
            fp += 1
        # TN: not is_same and not positive — ignored for F1
    return _compute_f1(tp, fp, fn)


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def compute_per_item_breakdown(
    scored_rows: list[dict],
    positive_threshold: int = 3,
) -> list[dict]:
    """Group scored rows by (variant, source_item_id), compute TP/FP/FN/F1.

    Returns sorted worst-first (lowest F1).
    Returns [{"status": "no_cluster_data"}] when is_same_cluster is NULL for all rows.
    """
    if not scored_rows:
        return []

    if not _has_cluster_data(scored_rows):
        return [{"status": "no_cluster_data"}]

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in scored_rows:
        key = (r.get("variant", ""), r.get("source_item_id", ""))
        groups[key].append(r)

    results = []
    for (variant, source_item_id), rows in groups.items():
        tp = fp = fn = 0
        for r in rows:
            is_same = bool(r.get("is_same_cluster"))
            positive = _is_positive(_get_score(r), positive_threshold)
            if is_same and positive:
                tp += 1
            elif is_same and not positive:
                fn += 1
            elif not is_same and positive:
                fp += 1
        f1 = _compute_f1(tp, fp, fn)
        results.append(
            {
                "variant": variant,
                "source_item_id": source_item_id,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "f1": f1,
                "n_pairs": len(rows),
            }
        )

    # Sort worst-first (lowest F1), then by source_item_id for stability
    results.sort(key=lambda x: (x["f1"], x["source_item_id"]))
    return results


def extract_failure_cases(
    scored_rows: list[dict],
    positive_threshold: int = 3,
) -> list[dict]:
    """Filter to FP and FN cases.

    When no cluster data, return lowest-scoring pairs as "low_confidence" type instead.
    Includes context fields: type, variant, source_item_id, source_item_title,
    target_item_id, target_item_title, source_cluster, target_cluster,
    score_transfer, principle.
    """
    if not scored_rows:
        return []

    if not _has_cluster_data(scored_rows):
        # No cluster data: return lowest-scoring pairs as low_confidence
        scored_with_values = [r for r in scored_rows if _get_score(r) is not None]
        scored_with_values.sort(
            key=lambda r: int(_get_score(r) if _get_score(r) is not None else 0),
        )
        results = []
        for r in scored_with_values:
            score = _get_score(r)
            if _is_positive(score, positive_threshold):
                continue
            results.append(
                {
                    "type": "low_confidence",
                    "variant": r.get("variant", ""),
                    "source_item_id": r.get("source_item_id", ""),
                    "source_item_title": r.get("source_item_title", ""),
                    "target_item_id": r.get("target_item_id", ""),
                    "target_item_title": r.get("target_item_title", ""),
                    "source_cluster": r.get("source_cluster_id") or r.get("source_cluster", ""),
                    "target_cluster": r.get("target_cluster_id") or r.get("target_cluster", ""),
                    "score_transfer": score,
                    "principle": r.get("principle", ""),
                }
            )
        return results

    failures = []
    for r in scored_rows:
        is_same = bool(r.get("is_same_cluster"))
        score = _get_score(r)
        positive = _is_positive(score, positive_threshold)

        if is_same and not positive:
            failure_type = "fn"
        elif not is_same and positive:
            failure_type = "fp"
        else:
            continue

        failures.append(
            {
                "type": failure_type,
                "variant": r.get("variant", ""),
                "source_item_id": r.get("source_item_id", ""),
                "source_item_title": r.get("source_item_title", ""),
                "target_item_id": r.get("target_item_id", ""),
                "target_item_title": r.get("target_item_title", ""),
                "source_cluster": r.get("source_cluster_id") or r.get("source_cluster", ""),
                "target_cluster": r.get("target_cluster_id") or r.get("target_cluster", ""),
                "score_transfer": score,
                "principle": r.get("principle", ""),
            }
        )

    return failures


def bootstrap_f1_ci(
    scored_rows: list[dict],
    variant: str,
    positive_threshold: int = 3,
    n_bootstrap: int = 500,
    ci: float = 0.95,
    seed: int | None = None,
) -> dict | None:
    """Bootstrap CI for variant F1.

    Returns {low, mid, high, n_pairs} or None if <10 pairs or no cluster data.
    """
    # Filter to the requested variant
    rows = [r for r in scored_rows if r.get("variant") == variant]

    if len(rows) < _MIN_BOOTSTRAP_PAIRS:
        return None

    if not _has_cluster_data(rows):
        return None

    rng = random.Random(seed)  # noqa: S311 — statistical bootstrap, not crypto
    f1_samples = []
    n = len(rows)

    for _ in range(n_bootstrap):
        sample = [rows[rng.randint(0, n - 1)] for _ in range(n)]
        f1_samples.append(_compute_f1_from_rows(sample, positive_threshold))

    f1_samples.sort()
    alpha = 1 - ci
    low_idx = int(n_bootstrap * alpha / 2)
    high_idx = int(n_bootstrap * (1 - alpha / 2))
    # Clamp indices
    low_idx = max(0, min(low_idx, n_bootstrap - 1))
    high_idx = max(0, min(high_idx, n_bootstrap - 1))

    return {
        "low": f1_samples[low_idx],
        "mid": statistics.median(f1_samples),
        "high": f1_samples[high_idx],
        "n_pairs": n,
    }


def compute_variant_stability(
    run_metrics: list[dict],
    threshold: float = 0.10,
) -> dict[str, dict]:
    """Cross-run stdev per variant.

    Input: [{variant, f1}, ...].
    Returns {variant: {mean, stdev, n_runs, stable, f1s}}.
    """
    if not run_metrics:
        return {}

    by_variant: dict[str, list[float]] = defaultdict(list)
    for m in run_metrics:
        v = m.get("variant", "")
        f1 = m.get("f1")
        if f1 is not None:
            by_variant[v].append(float(f1))

    result = {}
    for v, f1s in by_variant.items():
        mean = statistics.mean(f1s)
        stdev = statistics.pstdev(f1s) if len(f1s) > 1 else 0.0
        result[v] = {
            "mean": mean,
            "stdev": stdev,
            "n_runs": len(f1s),
            "stable": stdev <= threshold,
            "f1s": f1s,
        }
    return result


def describe_config_diff(
    config_a: dict,
    config_b: dict,
) -> list[str]:
    """Human-readable config differences.

    Compare model, temperature, num_ctx, prompt_template_id.
    Returns list of strings describing changes.
    """
    diffs: list[str] = []

    # Model
    ma = config_a.get("model")
    mb = config_b.get("model")
    if ma != mb:
        diffs.append(f"Model changed from {ma} to {mb}")

    # Temperature
    ta = config_a.get("temperature")
    tb = config_b.get("temperature")
    if ta != tb:
        direction = ""
        try:
            fa = float(ta) if ta is not None else None
            fb = float(tb) if tb is not None else None
            if fa is not None and fb is not None:
                direction = " (more creative)" if fb > fa else " (more deterministic)"
            elif fa is None and fb is not None:
                direction = " (more creative)" if fb > 0.5 else " (more deterministic)"
            elif fa is not None and fb is None:
                direction = ""
        except (ValueError, TypeError):
            pass
        diffs.append(f"Temperature changed from {ta} to {tb}{direction}")

    # num_ctx
    na = config_a.get("num_ctx")
    nb = config_b.get("num_ctx")
    if na != nb:
        diffs.append(f"Context window changed from {na} to {nb}")

    # prompt_template_id
    pa = config_a.get("prompt_template_id")
    pb = config_b.get("prompt_template_id")
    if pa != pb:
        diffs.append(f"Prompt template changed from {pa} to {pb}")

    # System prompt diff
    sp_a = config_a.get("system_prompt")
    sp_b = config_b.get("system_prompt")
    if sp_a != sp_b:
        if sp_a is None:
            diffs.append(f"System prompt: (none) → added ({len(sp_b or '')} chars)")
        elif sp_b is None:
            diffs.append(f"System prompt: removed ({len(sp_a or '')} chars) → (none)")
        else:
            diffs.append(f"System prompt: changed ({len(sp_a)} → {len(sp_b)} chars)")

    # Params diff — key-by-key comparison
    try:
        params_a = json.loads(config_a.get("params") or "{}")
    except (json.JSONDecodeError, ValueError):
        _log.warning("describe_config_diff: invalid JSON in params for config_a")
        params_a = {}
    try:
        params_b = json.loads(config_b.get("params") or "{}")
    except (json.JSONDecodeError, ValueError):
        _log.warning("describe_config_diff: invalid JSON in params for config_b")
        params_b = {}
    if params_a != params_b:
        all_keys = set(params_a) | set(params_b)
        for k in sorted(all_keys):
            va, vb = params_a.get(k), params_b.get(k)
            if va != vb:
                diffs.append(f"Param {k}: {va!r} → {vb!r}")

    # Provider diff
    prov_a = config_a.get("provider") or "ollama"
    prov_b = config_b.get("provider") or "ollama"
    if prov_a != prov_b:
        diffs.append(f"Provider: {prov_a} → {prov_b}")

    # Training config diff
    tc_a = config_a.get("training_config")
    tc_b = config_b.get("training_config")
    if tc_a != tc_b:
        diffs.append("Training config: changed")

    return diffs
