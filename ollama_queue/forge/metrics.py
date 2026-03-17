"""Forge metrics — oracle-ground-truth F1, Spearman, score variance.

F1 uses oracle score as ground truth (oracle >= threshold = positive).
Spearman measures judge-embedding correlation (acquiescence diagnostic).
Variance measures score spread (all-same = acquiescing).
"""

from __future__ import annotations

from ollama_queue.forge.oracle import compute_kappa


def spearman_rank_correlation(a: list, b: list) -> float:
    """Spearman's rank correlation coefficient. Returns -1.0 to 1.0."""
    n = len(a)
    if n < 2:
        return 0.0

    def _rank(vals):
        indexed = sorted(enumerate(vals), key=lambda x: x[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and indexed[j + 1][1] == indexed[j][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks

    ra = _rank(a)
    rb = _rank(b)

    d_sq = sum((x - y) ** 2 for x, y in zip(ra, rb, strict=False))
    return 1 - (6 * d_sq) / (n * (n * n - 1))


def score_variance(scores: list[int | float]) -> float:
    """Population variance of scores. Returns 0.0 for empty/single."""
    n = len(scores)
    if n < 2:
        return 0.0
    mean = sum(scores) / n
    return sum((s - mean) ** 2 for s in scores) / n


def compute_forge_metrics(
    results: list[dict],
    *,
    positive_threshold: int = 3,
) -> dict:
    """Compute Forge metrics from scored results.

    Oracle-as-ground-truth: oracle_score >= threshold = positive class.
    Judge's job: agree with the oracle.

    Returns dict with: f1, precision, recall, kappa, spearman,
    score_variance, sample_size, oracle_sample_size.
    """
    judge_scores = [r["judge_score"] for r in results if r.get("judge_score") is not None]
    sims = [r["embedding_similarity"] for r in results if r.get("embedding_similarity") is not None]

    # Always computable
    spearman = spearman_rank_correlation(judge_scores, sims) if len(judge_scores) >= 2 and len(sims) >= 2 else None
    variance = score_variance(judge_scores) if judge_scores else None

    # Oracle-dependent metrics
    oracle_pairs = [r for r in results if r.get("oracle_score") is not None and r.get("judge_score") is not None]

    if not oracle_pairs:
        return {
            "f1": None,
            "precision": None,
            "recall": None,
            "kappa": None,
            "spearman": round(spearman, 4) if spearman is not None else None,
            "score_variance": round(variance, 4) if variance is not None else None,
            "sample_size": len(judge_scores),
            "oracle_sample_size": 0,
        }

    # F1 with oracle as ground truth
    tp = fp = fn = tn = 0
    j_scores = []
    o_scores = []

    for r in oracle_pairs:
        j = r["judge_score"]
        o = r["oracle_score"]
        j_scores.append(j)
        o_scores.append(o)

        j_pos = j >= positive_threshold
        o_pos = o >= positive_threshold

        if j_pos and o_pos:
            tp += 1
        elif j_pos and not o_pos:
            fp += 1
        elif not j_pos and o_pos:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    kappa = compute_kappa(j_scores, o_scores, tolerance=1)

    return {
        "f1": round(f1, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "kappa": round(kappa, 4),
        "spearman": round(spearman, 4) if spearman is not None else None,
        "score_variance": round(variance, 4) if variance is not None else None,
        "sample_size": len(judge_scores),
        "oracle_sample_size": len(oracle_pairs),
    }
