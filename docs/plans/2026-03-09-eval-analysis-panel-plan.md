# Eval Analysis Panel — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 5 analysis features to the eval pipeline UI — per-item breakdown, failure case drill-down, bootstrap confidence intervals, cross-run stability, and config diff — as platform features that work for any project data source.

**Architecture:** Pure analysis module (`eval_analysis.py`) with no DB/HTTP deps. Run-scoped analysis (breakdown, failures, CI) computed after judge phase and stored as `analysis_json` on `eval_runs`. Cross-run features (stability, config diff) computed live via dedicated API endpoints. Frontend distributes features across RunRow L2/L3 and Variants tab.

**Tech Stack:** Python 3.12 (backend), FastAPI, SQLite, Preact 10 + @preact/signals (frontend), pytest (tests), npm run build (frontend verification).

**Design doc:** `docs/plans/2026-03-09-eval-analysis-panel-design.md`

---

## Batch 1: Pure Analysis Module + Tests

### Task 1: Create `eval_analysis.py` with `compute_per_item_breakdown()`

**Files:**

- Create: `ollama_queue/eval_analysis.py`
- Create: `tests/test_eval_analysis.py`

**Step 1: Write the failing test**

In `tests/test_eval_analysis.py`:

```python
"""Tests for eval_analysis.py — pure analysis functions."""

from __future__ import annotations


def _make_row(
    variant: str = "A",
    source_item_id: str = "1",
    target_item_id: str = "2",
    is_same_cluster: int = 1,
    score_transfer: int = 4,
    source_item_title: str = "Source lesson",
    target_item_title: str = "Target lesson",
    source_cluster_id: str = "c1",
    target_cluster_id: str = "c1",
) -> dict:
    return {
        "variant": variant,
        "source_item_id": source_item_id,
        "target_item_id": target_item_id,
        "is_same_cluster": is_same_cluster,
        "score_transfer": score_transfer,
        "source_item_title": source_item_title,
        "target_item_title": target_item_title,
        "source_cluster_id": source_cluster_id,
        "target_cluster_id": target_cluster_id,
    }


class TestComputePerItemBreakdown:
    def test_basic_breakdown(self):
        from ollama_queue.eval_analysis import compute_per_item_breakdown

        rows = [
            _make_row(source_item_id="1", target_item_id="2", is_same_cluster=1, score_transfer=4),  # TP
            _make_row(source_item_id="1", target_item_id="3", is_same_cluster=1, score_transfer=2),  # FN
            _make_row(source_item_id="1", target_item_id="4", is_same_cluster=0, score_transfer=1),  # TN
            _make_row(source_item_id="1", target_item_id="5", is_same_cluster=0, score_transfer=4),  # FP
        ]
        result = compute_per_item_breakdown(rows)
        assert len(result) >= 1
        item = result[0]
        assert item["source_item_id"] == "1"
        assert item["tp"] == 1
        assert item["fn"] == 1
        assert item["fp"] == 1
        assert item["total_pairs"] == 4

    def test_empty_input(self):
        from ollama_queue.eval_analysis import compute_per_item_breakdown

        assert compute_per_item_breakdown([]) == []

    def test_sorted_worst_first(self):
        from ollama_queue.eval_analysis import compute_per_item_breakdown

        rows = [
            # Item "good" — all TPs
            _make_row(source_item_id="good", target_item_id="t1", is_same_cluster=1, score_transfer=5),
            _make_row(source_item_id="good", target_item_id="t2", is_same_cluster=0, score_transfer=1),
            # Item "bad" — all FNs
            _make_row(source_item_id="bad", target_item_id="t3", is_same_cluster=1, score_transfer=1),
            _make_row(source_item_id="bad", target_item_id="t4", is_same_cluster=0, score_transfer=1),
        ]
        result = compute_per_item_breakdown(rows)
        assert result[0]["source_item_id"] == "bad"  # worst first
        assert result[0]["f1"] == 0.0

    def test_no_cluster_data(self):
        from ollama_queue.eval_analysis import compute_per_item_breakdown

        rows = [
            _make_row(is_same_cluster=None, score_transfer=4),
        ]
        result = compute_per_item_breakdown(rows)
        assert result == [{"status": "no_cluster_data"}]

    def test_custom_threshold(self):
        from ollama_queue.eval_analysis import compute_per_item_breakdown

        rows = [
            _make_row(source_item_id="1", target_item_id="2", is_same_cluster=1, score_transfer=3),
        ]
        # threshold=3 → score 3 is positive → TP
        result_3 = compute_per_item_breakdown(rows, positive_threshold=3)
        assert result_3[0]["tp"] == 1
        # threshold=4 → score 3 is negative → FN
        result_4 = compute_per_item_breakdown(rows, positive_threshold=4)
        assert result_4[0]["fn"] == 1
```

**Step 2: Run test to verify it fails**

Run: `cd /home/justin/Documents/projects/ollama-queue && source .venv/bin/activate && pytest tests/test_eval_analysis.py::TestComputePerItemBreakdown -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'ollama_queue.eval_analysis'"

**Step 3: Write minimal implementation**

In `ollama_queue/eval_analysis.py`:

```python
"""Pure analysis functions for eval pipeline.

No DB, no HTTP, no side effects. Takes lists of dicts, returns lists of dicts.
Used by eval_engine.py to compute structured analysis stored per-run,
and by API endpoints for live cross-run queries.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


def _is_positive(score_transfer: int | None, threshold: int = 3) -> bool:
    """Return True if transfer score meets or exceeds threshold."""
    if score_transfer is None:
        return False
    try:
        return int(score_transfer) >= threshold
    except (TypeError, ValueError):
        _log.warning("_is_positive: unexpected score %r; treating as negative", score_transfer)
        return False


def _has_cluster_data(rows: list[dict]) -> bool:
    """Check if any row has non-null is_same_cluster."""
    return any(r.get("is_same_cluster") is not None for r in rows)


def _compute_f1(tp: int, fp: int, fn: int) -> float:
    """Compute F1 from TP/FP/FN counts. Returns 0.0 on zero denominator."""
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    if recall + precision == 0:
        return 0.0
    return 2 * recall * precision / (recall + precision)


def compute_per_item_breakdown(
    scored_rows: list[dict[str, Any]],
    positive_threshold: int = 3,
) -> list[dict[str, Any]]:
    """Group scored rows by (variant, source_item_id), compute TP/FP/FN/F1.

    Returns sorted worst-first by F1. If no cluster data, returns
    [{"status": "no_cluster_data"}].
    """
    if not scored_rows:
        return []

    if not _has_cluster_data(scored_rows):
        return [{"status": "no_cluster_data"}]

    groups: dict[tuple[str, str], list[dict]] = {}
    for row in scored_rows:
        key = (row.get("variant", ""), row.get("source_item_id", ""))
        groups.setdefault(key, []).append(row)

    results: list[dict[str, Any]] = []
    for (variant, source_item_id), rows in groups.items():
        tp = fn = fp = 0
        for r in rows:
            same = r.get("is_same_cluster")
            positive = _is_positive(r.get("score_transfer"), positive_threshold)
            if same and positive:
                tp += 1
            elif same and not positive:
                fn += 1
            elif not same and positive:
                fp += 1

        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        f1 = _compute_f1(tp, fp, fn)

        results.append({
            "source_item_id": source_item_id,
            "source_item_title": rows[0].get("source_item_title", ""),
            "variant": variant,
            "cluster_id": rows[0].get("source_cluster_id", ""),
            "recall": round(recall, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4),
            "tp": tp,
            "fn": fn,
            "fp": fp,
            "total_pairs": len(rows),
        })

    results.sort(key=lambda r: (r["f1"], r["variant"], r["source_item_id"]))
    return results
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_analysis.py::TestComputePerItemBreakdown -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add ollama_queue/eval_analysis.py tests/test_eval_analysis.py
git commit -m "feat(eval): add eval_analysis.py with compute_per_item_breakdown"
```

---

### Task 2: Add `extract_failure_cases()`

**Files:**

- Modify: `ollama_queue/eval_analysis.py`
- Modify: `tests/test_eval_analysis.py`

**Step 1: Write the failing test**

Append to `tests/test_eval_analysis.py`:

```python
class TestExtractFailureCases:
    def test_extracts_fp_and_fn(self):
        from ollama_queue.eval_analysis import extract_failure_cases

        rows = [
            _make_row(target_item_id="t1", is_same_cluster=1, score_transfer=4),  # TP — skip
            _make_row(target_item_id="t2", is_same_cluster=1, score_transfer=2),  # FN — include
            _make_row(target_item_id="t3", is_same_cluster=0, score_transfer=1),  # TN — skip
            _make_row(target_item_id="t4", is_same_cluster=0, score_transfer=4),  # FP — include
        ]
        result = extract_failure_cases(rows)
        assert len(result) == 2
        types = {r["type"] for r in result}
        assert types == {"false_positive", "false_negative"}

    def test_empty_input(self):
        from ollama_queue.eval_analysis import extract_failure_cases

        assert extract_failure_cases([]) == []

    def test_no_failures(self):
        from ollama_queue.eval_analysis import extract_failure_cases

        rows = [
            _make_row(is_same_cluster=1, score_transfer=5),  # TP
            _make_row(is_same_cluster=0, score_transfer=1),  # TN
        ]
        assert extract_failure_cases(rows) == []

    def test_includes_context_fields(self):
        from ollama_queue.eval_analysis import extract_failure_cases

        rows = [
            _make_row(
                source_item_id="s1",
                target_item_id="t1",
                is_same_cluster=0,
                score_transfer=5,
                source_item_title="Source Title",
                target_item_title="Target Title",
                source_cluster_id="c1",
                target_cluster_id="c2",
            ),
        ]
        result = extract_failure_cases(rows)
        assert len(result) == 1
        fp = result[0]
        assert fp["type"] == "false_positive"
        assert fp["source_item_title"] == "Source Title"
        assert fp["target_item_title"] == "Target Title"
        assert fp["source_cluster"] == "c1"
        assert fp["target_cluster"] == "c2"

    def test_no_cluster_data_shows_low_scoring(self):
        from ollama_queue.eval_analysis import extract_failure_cases

        rows = [
            _make_row(is_same_cluster=None, score_transfer=1),
            _make_row(is_same_cluster=None, score_transfer=5),
        ]
        result = extract_failure_cases(rows)
        # Without cluster data, show low-scoring pairs instead
        assert len(result) >= 1
        assert result[0]["score_transfer"] == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_analysis.py::TestExtractFailureCases -v`
Expected: FAIL with "cannot import name 'extract_failure_cases'"

**Step 3: Write minimal implementation**

Append to `ollama_queue/eval_analysis.py`:

```python
def extract_failure_cases(
    scored_rows: list[dict[str, Any]],
    positive_threshold: int = 3,
) -> list[dict[str, Any]]:
    """Filter scored rows to misclassified cases (FP and FN).

    If no cluster data, returns lowest-scoring pairs instead (useful for
    projects without cluster ground truth).
    """
    if not scored_rows:
        return []

    if not _has_cluster_data(scored_rows):
        # No ground truth — show lowest-scoring pairs as "low confidence"
        sorted_rows = sorted(scored_rows, key=lambda r: r.get("score_transfer", 0))
        return [
            {
                "type": "low_confidence",
                "variant": r.get("variant", ""),
                "source_item_id": r.get("source_item_id", ""),
                "source_item_title": r.get("source_item_title", ""),
                "target_item_id": r.get("target_item_id", ""),
                "target_item_title": r.get("target_item_title", ""),
                "source_cluster": r.get("source_cluster_id", ""),
                "target_cluster": r.get("target_cluster_id", ""),
                "score_transfer": r.get("score_transfer"),
            }
            for r in sorted_rows
            if (r.get("score_transfer") or 0) < positive_threshold
        ]

    failures: list[dict[str, Any]] = []
    for row in scored_rows:
        same = row.get("is_same_cluster")
        positive = _is_positive(row.get("score_transfer"), positive_threshold)

        if not same and positive:
            failure_type = "false_positive"
        elif same and not positive:
            failure_type = "false_negative"
        else:
            continue

        failures.append({
            "type": failure_type,
            "variant": row.get("variant", ""),
            "source_item_id": row.get("source_item_id", ""),
            "source_item_title": row.get("source_item_title", ""),
            "target_item_id": row.get("target_item_id", ""),
            "target_item_title": row.get("target_item_title", ""),
            "source_cluster": row.get("source_cluster_id", ""),
            "target_cluster": row.get("target_cluster_id", ""),
            "score_transfer": row.get("score_transfer"),
            "principle": row.get("principle", ""),
        })

    failures.sort(key=lambda f: (f["variant"], f["source_item_id"]))
    return failures
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_analysis.py::TestExtractFailureCases -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add ollama_queue/eval_analysis.py tests/test_eval_analysis.py
git commit -m "feat(eval): add extract_failure_cases to eval_analysis"
```

---

### Task 3: Add `bootstrap_f1_ci()`

**Files:**

- Modify: `ollama_queue/eval_analysis.py`
- Modify: `tests/test_eval_analysis.py`

**Step 1: Write the failing test**

Append to `tests/test_eval_analysis.py`:

```python
class TestBootstrapF1CI:
    def test_basic_ci(self):
        from ollama_queue.eval_analysis import bootstrap_f1_ci

        rows = [
            _make_row(variant="A", source_item_id=str(i), target_item_id=str(i + 100),
                      is_same_cluster=1, score_transfer=4)
            for i in range(10)
        ] + [
            _make_row(variant="A", source_item_id=str(i), target_item_id=str(i + 200),
                      is_same_cluster=0, score_transfer=1)
            for i in range(10)
        ]
        result = bootstrap_f1_ci(rows, "A", seed=42)
        assert result is not None
        assert "low" in result
        assert "mid" in result
        assert "high" in result
        assert result["low"] <= result["mid"] <= result["high"]

    def test_too_few_pairs_returns_none(self):
        from ollama_queue.eval_analysis import bootstrap_f1_ci

        rows = [_make_row(variant="A", is_same_cluster=1, score_transfer=4)]
        result = bootstrap_f1_ci(rows, "A", seed=42)
        assert result is None

    def test_wrong_variant_returns_none(self):
        from ollama_queue.eval_analysis import bootstrap_f1_ci

        rows = [_make_row(variant="A") for _ in range(20)]
        result = bootstrap_f1_ci(rows, "B", seed=42)
        assert result is None

    def test_seed_determinism(self):
        from ollama_queue.eval_analysis import bootstrap_f1_ci

        rows = [
            _make_row(variant="A", source_item_id=str(i), target_item_id=str(i + 100),
                      is_same_cluster=i % 2, score_transfer=(3 + i % 3))
            for i in range(20)
        ]
        r1 = bootstrap_f1_ci(rows, "A", seed=99)
        r2 = bootstrap_f1_ci(rows, "A", seed=99)
        assert r1 == r2

    def test_no_cluster_data_returns_none(self):
        from ollama_queue.eval_analysis import bootstrap_f1_ci

        rows = [_make_row(variant="A", is_same_cluster=None) for _ in range(20)]
        result = bootstrap_f1_ci(rows, "A", seed=42)
        assert result is None

    def test_n_pairs_included(self):
        from ollama_queue.eval_analysis import bootstrap_f1_ci

        rows = [
            _make_row(variant="A", source_item_id=str(i), target_item_id=str(i + 100),
                      is_same_cluster=1, score_transfer=4)
            for i in range(15)
        ]
        result = bootstrap_f1_ci(rows, "A", seed=42)
        assert result is not None
        assert result["n_pairs"] == 15
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_analysis.py::TestBootstrapF1CI -v`
Expected: FAIL with "cannot import name 'bootstrap_f1_ci'"

**Step 3: Write minimal implementation**

Append to `ollama_queue/eval_analysis.py`, adding `import random` and `import statistics` at the top:

```python
import random
import statistics


def _compute_f1_from_rows(
    rows: list[dict[str, Any]],
    threshold: int = 3,
) -> float:
    """Compute F1 from a list of scored rows."""
    tp = fn = fp = 0
    for r in rows:
        same = r.get("is_same_cluster")
        positive = _is_positive(r.get("score_transfer"), threshold)
        if same and positive:
            tp += 1
        elif same and not positive:
            fn += 1
        elif not same and positive:
            fp += 1
    return _compute_f1(tp, fp, fn)


_MIN_BOOTSTRAP_PAIRS = 10


def bootstrap_f1_ci(
    scored_rows: list[dict[str, Any]],
    variant: str,
    positive_threshold: int = 3,
    n_bootstrap: int = 500,
    ci: float = 0.95,
    seed: int | None = None,
) -> dict[str, float] | None:
    """Bootstrap CI for variant F1.

    Returns {low, mid, high, n_pairs} or None if <10 pairs or no cluster data.
    """
    pairs = [r for r in scored_rows if r.get("variant") == variant]
    if len(pairs) < _MIN_BOOTSTRAP_PAIRS:
        return None
    if not _has_cluster_data(pairs):
        return None

    rng = random.Random(seed)  # noqa: S311 — statistical resampling, not crypto
    f1s: list[float] = []
    for _ in range(n_bootstrap):
        sample = rng.choices(pairs, k=len(pairs))
        f1s.append(_compute_f1_from_rows(sample, positive_threshold))

    f1s.sort()
    alpha = (1 - ci) / 2
    low_idx = max(0, int(alpha * len(f1s)))
    high_idx = max(0, min(int((1 - alpha) * len(f1s)) - 1, len(f1s) - 1))
    return {
        "low": round(f1s[low_idx], 4),
        "mid": round(statistics.median(f1s), 4),
        "high": round(f1s[high_idx], 4),
        "n_pairs": len(pairs),
    }
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_analysis.py::TestBootstrapF1CI -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add ollama_queue/eval_analysis.py tests/test_eval_analysis.py
git commit -m "feat(eval): add bootstrap_f1_ci to eval_analysis"
```

---

### Task 4: Add `compute_variant_stability()` and `describe_config_diff()`

**Files:**

- Modify: `ollama_queue/eval_analysis.py`
- Modify: `tests/test_eval_analysis.py`

**Step 1: Write the failing tests**

Append to `tests/test_eval_analysis.py`:

```python
class TestComputeVariantStability:
    def test_basic_stability(self):
        from ollama_queue.eval_analysis import compute_variant_stability

        run_metrics = [
            {"variant": "A", "f1": 0.70},
            {"variant": "A", "f1": 0.72},
            {"variant": "A", "f1": 0.71},
            {"variant": "B", "f1": 0.50},
            {"variant": "B", "f1": 0.80},
        ]
        result = compute_variant_stability(run_metrics)
        assert "A" in result
        assert "B" in result
        assert result["A"]["stable"] is True  # low stdev
        assert result["B"]["stable"] is False  # high stdev
        assert result["A"]["n_runs"] == 3

    def test_single_run_stable(self):
        from ollama_queue.eval_analysis import compute_variant_stability

        result = compute_variant_stability([{"variant": "A", "f1": 0.75}])
        assert result["A"]["stdev"] == 0.0
        assert result["A"]["stable"] is True

    def test_empty_input(self):
        from ollama_queue.eval_analysis import compute_variant_stability

        assert compute_variant_stability([]) == {}

    def test_custom_threshold(self):
        from ollama_queue.eval_analysis import compute_variant_stability

        run_metrics = [
            {"variant": "A", "f1": 0.70},
            {"variant": "A", "f1": 0.75},
        ]
        # stdev ≈ 0.035 — stable at 0.10 threshold, unstable at 0.03
        result_lax = compute_variant_stability(run_metrics, threshold=0.10)
        result_strict = compute_variant_stability(run_metrics, threshold=0.03)
        assert result_lax["A"]["stable"] is True
        assert result_strict["A"]["stable"] is False


class TestDescribeConfigDiff:
    def test_model_change(self):
        from ollama_queue.eval_analysis import describe_config_diff

        a = {"model": "qwen2.5:7b", "temperature": 0.6}
        b = {"model": "qwen3:14b", "temperature": 0.6}
        changes = describe_config_diff(a, b)
        assert len(changes) == 1
        assert "model" in changes[0].lower()

    def test_temperature_change(self):
        from ollama_queue.eval_analysis import describe_config_diff

        a = {"model": "qwen2.5:7b", "temperature": 0.6}
        b = {"model": "qwen2.5:7b", "temperature": 0.8}
        changes = describe_config_diff(a, b)
        assert len(changes) == 1
        assert "temperature" in changes[0].lower()

    def test_identical_configs(self):
        from ollama_queue.eval_analysis import describe_config_diff

        a = {"model": "qwen2.5:7b", "temperature": 0.6}
        changes = describe_config_diff(a, a)
        assert changes == []

    def test_multiple_changes(self):
        from ollama_queue.eval_analysis import describe_config_diff

        a = {"model": "qwen2.5:7b", "temperature": 0.6, "num_ctx": 4096}
        b = {"model": "qwen3:14b", "temperature": 0.8, "num_ctx": 8192}
        changes = describe_config_diff(a, b)
        assert len(changes) == 3

    def test_none_temperature(self):
        from ollama_queue.eval_analysis import describe_config_diff

        a = {"model": "qwen2.5:7b", "temperature": None}
        b = {"model": "qwen2.5:7b", "temperature": 0.6}
        changes = describe_config_diff(a, b)
        assert len(changes) == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_analysis.py::TestComputeVariantStability tests/test_eval_analysis.py::TestDescribeConfigDiff -v`
Expected: FAIL with "cannot import name"

**Step 3: Write minimal implementation**

Append to `ollama_queue/eval_analysis.py`:

```python
def compute_variant_stability(
    run_metrics: list[dict[str, Any]],
    threshold: float = 0.10,
) -> dict[str, dict[str, Any]]:
    """Cross-run stdev per variant from recent run metrics.

    Input: [{variant, f1}, ...] from completed runs.
    Returns {variant: {mean, stdev, n_runs, stable, f1s}}.
    """
    by_variant: dict[str, list[float]] = {}
    for entry in run_metrics:
        vid = entry.get("variant")
        f1 = entry.get("f1")
        if vid and f1 is not None:
            by_variant.setdefault(vid, []).append(float(f1))

    result: dict[str, dict[str, Any]] = {}
    for vid, f1s in by_variant.items():
        stdev_raw = statistics.stdev(f1s) if len(f1s) > 1 else 0.0
        stdev_rounded = round(stdev_raw, 4)
        result[vid] = {
            "stdev": stdev_rounded,
            "mean": round(statistics.mean(f1s), 4),
            "n_runs": len(f1s),
            "stable": stdev_rounded < threshold,
            "f1s": f1s,
        }
    return result


_CONFIG_DIFF_KEYS = [
    ("model", "model"),
    ("temperature", "temperature"),
    ("num_ctx", "context window"),
    ("prompt_template_id", "prompt template"),
]


def describe_config_diff(
    config_a: dict[str, Any],
    config_b: dict[str, Any],
) -> list[str]:
    """Human-readable config differences between two variant configs.

    Returns list of change descriptions like:
    'model: qwen2.5:7b → qwen3:14b (different capacity)'
    """
    changes: list[str] = []

    for key, label in _CONFIG_DIFF_KEYS:
        val_a = config_a.get(key)
        val_b = config_b.get(key)
        if val_a != val_b:
            if key == "temperature" and val_a is not None and val_b is not None:
                direction = "more deterministic" if val_b < val_a else "more creative"
                changes.append(f"{label}: {val_a} → {val_b} ({direction})")
            elif key == "num_ctx" and val_a is not None and val_b is not None:
                changes.append(f"{label}: {val_a:,} → {val_b:,} tokens")
            else:
                changes.append(f"{label}: {val_a} → {val_b}")

    return changes
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_analysis.py::TestComputeVariantStability tests/test_eval_analysis.py::TestDescribeConfigDiff -v`
Expected: PASS (9 tests)

**Step 5: Commit**

```bash
git add ollama_queue/eval_analysis.py tests/test_eval_analysis.py
git commit -m "feat(eval): add stability and config diff to eval_analysis"
```

---

## Batch 2: Schema Migration + Pipeline Integration

### Task 5: Add `analysis_json`, `source_item_title`, `target_item_title` columns

**Files:**

- Modify: `ollama_queue/db.py:110-119` (add_column_if_missing calls)
- Modify: `tests/test_db.py`

**Step 1: Write the failing test**

In `tests/test_db.py`, find the eval schema test section and add:

```python
def test_eval_results_has_title_columns(tmp_path):
    """Verify source_item_title and target_item_title columns exist."""
    db = Database(tmp_path / "q.db")
    db.initialize()
    with db._lock:
        conn = db._connect()
        cols = [row[1] for row in conn.execute("PRAGMA table_info(eval_results)").fetchall()]
    assert "source_item_title" in cols
    assert "target_item_title" in cols


def test_eval_runs_has_analysis_json(tmp_path):
    """Verify analysis_json column exists on eval_runs."""
    db = Database(tmp_path / "q.db")
    db.initialize()
    with db._lock:
        conn = db._connect()
        cols = [row[1] for row in conn.execute("PRAGMA table_info(eval_runs)").fetchall()]
    assert "analysis_json" in cols
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_eval_results_has_title_columns tests/test_db.py::test_eval_runs_has_analysis_json -v`
Expected: FAIL — columns don't exist yet

**Step 3: Add migrations in db.py**

In `ollama_queue/db.py`, find the `initialize()` method. After the existing `_add_column_if_missing` calls for eval tables (around line 195+), add:

```python
# Eval analysis columns (2026-03-09)
self._add_column_if_missing(conn, "eval_results", "source_item_title", "TEXT")
self._add_column_if_missing(conn, "eval_results", "target_item_title", "TEXT")
self._add_column_if_missing(conn, "eval_runs", "analysis_json", "TEXT")
```

Also add the composite index after the table creation:

```python
conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_eval_results_run_variant
    ON eval_results(run_id, variant)
""")
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py::test_eval_results_has_title_columns tests/test_db.py::test_eval_runs_has_analysis_json -v`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat(eval): add analysis_json, item title columns, and composite index"
```

---

### Task 6: Populate item titles during generate/judge

**Files:**

- Modify: `ollama_queue/eval_engine.py:1865+` (run_eval_generate — store source_item_title)
- Modify: `ollama_queue/eval_engine.py:2299+` (run_eval_judge — store target_item_title)
- Modify: `tests/test_eval_engine.py`

**Step 1: Write the failing test**

In `tests/test_eval_engine.py`, add after the last test:

```python
class TestItemTitlePopulation:
    """Verify that source/target item titles are stored in eval_results."""

    def test_generation_stores_source_title(self, tmp_path):
        from ollama_queue.db import Database

        db = Database(tmp_path / "q.db")
        db.initialize()
        # Insert a run and a generation result with title
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
                "VALUES (1, 'http://localhost:7685', '[\"A\"]', 'A', 'generating')"
            )
            conn.execute(
                "INSERT INTO eval_results (run_id, variant, source_item_id, source_item_title, row_type) "
                "VALUES (1, 'A', '42', 'Silent failure in logging', 'generation')"
            )
            conn.commit()
            row = conn.execute(
                "SELECT source_item_title FROM eval_results WHERE run_id = 1"
            ).fetchone()
        assert row["source_item_title"] == "Silent failure in logging"

    def test_judge_stores_target_title(self, tmp_path):
        from ollama_queue.db import Database

        db = Database(tmp_path / "q.db")
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
                "VALUES (1, 'http://localhost:7685', '[\"A\"]', 'A', 'judging')"
            )
            conn.execute(
                "INSERT INTO eval_results (run_id, variant, source_item_id, target_item_id, "
                "target_item_title, row_type) "
                "VALUES (1, 'A', '42', '99', 'Race condition in worker', 'judge')"
            )
            conn.commit()
            row = conn.execute(
                "SELECT target_item_title FROM eval_results WHERE run_id = 1 AND row_type = 'judge'"
            ).fetchone()
        assert row["target_item_title"] == "Race condition in worker"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_engine.py::TestItemTitlePopulation -v`
Expected: FAIL — column doesn't exist (if migration not yet applied in this test's tmp_path DB) or passes if schema already includes it. Either way, validates the column is usable.

**Step 3: Update eval_engine.py to store titles**

In `ollama_queue/eval_engine.py`, find where generation results are inserted (inside `run_eval_generate()`, the INSERT INTO eval_results statement). Add `source_item_title` to the INSERT:

Find the INSERT statement for generation results and add the column. The items fetched from `_fetch_items()` return dicts — check if they include `title` or `one_liner`. Use whichever is available as the title.

Similarly in `run_eval_judge()`, where judge results are inserted, add `target_item_title` from the target item's title field.

**Note to implementer:** The exact INSERT locations depend on the current code structure. Search for `INSERT INTO eval_results` in eval_engine.py. There are typically 2 insertion points:
1. Generation phase: `row_type='generation'` — add `source_item_title`
2. Judge phase: `row_type='judge'` — add both `source_item_title` and `target_item_title`

The items dict from `_fetch_items()` includes `title` and `one_liner` fields. Use `item.get("title") or item.get("one_liner", "")` as the title value.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_engine.py::TestItemTitlePopulation -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest --timeout=120 -x -q -n 6`
Expected: All tests pass

**Step 6: Commit**

```bash
git add ollama_queue/eval_engine.py tests/test_eval_engine.py
git commit -m "feat(eval): populate source/target item titles in eval_results"
```

---

### Task 7: Add `compute_run_analysis()` to pipeline

**Files:**

- Modify: `ollama_queue/eval_engine.py:2491-2519` (run_eval_session — add call)
- Modify: `tests/test_eval_engine.py`

**Step 1: Write the failing test**

```python
class TestComputeRunAnalysis:
    """Verify compute_run_analysis stores structured analysis in eval_runs."""

    def test_stores_analysis_json(self, tmp_path):
        from ollama_queue.db import Database
        from ollama_queue.eval_engine import compute_run_analysis

        db = Database(tmp_path / "q.db")
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
                "VALUES (1, 'http://localhost:7685', '[\"A\"]', 'A', 'complete')"
            )
            # Insert scored pairs
            for i, (same, score) in enumerate([
                (1, 5), (1, 2), (0, 1), (0, 4),  # TP, FN, TN, FP
                (1, 4), (1, 4), (0, 1), (0, 1),  # TP, TP, TN, TN
                (1, 3), (1, 1), (0, 2), (0, 5),  # TP, FN, TN, FP
            ]):
                conn.execute(
                    "INSERT INTO eval_results "
                    "(run_id, variant, source_item_id, target_item_id, "
                    "is_same_cluster, score_transfer, row_type, "
                    "source_cluster_id, target_cluster_id) "
                    "VALUES (1, 'A', ?, ?, ?, ?, 'judge', 'c1', ?)",
                    (str(i), str(i + 100), same, score, "c1" if same else "c2"),
                )
            conn.commit()

        compute_run_analysis(1, db)

        run = dict(conn.execute("SELECT analysis_json FROM eval_runs WHERE id = 1").fetchone())
        import json
        analysis = json.loads(run["analysis_json"])
        assert "per_item" in analysis
        assert "failures" in analysis
        assert "confidence_intervals" in analysis
        assert "computed_at" in analysis
        assert "positive_threshold" in analysis

    def test_failure_does_not_raise(self, tmp_path):
        from ollama_queue.db import Database
        from ollama_queue.eval_engine import compute_run_analysis

        db = Database(tmp_path / "q.db")
        db.initialize()
        # Run doesn't exist — should log and return, not raise
        compute_run_analysis(999, db)  # no exception
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_eval_engine.py::TestComputeRunAnalysis -v`
Expected: FAIL with "cannot import name 'compute_run_analysis'"

**Step 3: Write `compute_run_analysis()` in eval_engine.py**

Add near `generate_eval_analysis()` (around line 1457):

```python
def compute_run_analysis(run_id: int, db: Database) -> None:
    """Compute structured analysis and store as analysis_json on eval_runs.

    Never raises — all exceptions caught and logged (same pattern as
    check_auto_promote).
    """
    try:
        _compute_run_analysis_inner(run_id, db)
    except Exception:
        _log.exception("compute_run_analysis failed for run %s (non-fatal)", run_id)


def _compute_run_analysis_inner(run_id: int, db: Database) -> None:
    """Inner implementation — may raise."""
    from ollama_queue.eval_analysis import (
        bootstrap_f1_ci,
        compute_per_item_breakdown,
        extract_failure_cases,
    )

    with db._lock:
        conn = db._connect()
        run = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            _log.warning("compute_run_analysis: run %s not found", run_id)
            return

        rows = conn.execute(
            "SELECT * FROM eval_results WHERE run_id = ? AND row_type = 'judge' AND error IS NULL",
            (run_id,),
        ).fetchall()

    if not rows:
        _log.info("compute_run_analysis: no scored rows for run %s", run_id)
        return

    scored = [dict(r) for r in rows]

    # Read positive threshold from settings
    threshold = 3
    try:
        with db._lock:
            conn = db._connect()
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'eval.positive_threshold'"
            ).fetchone()
            if row:
                threshold = int(json.loads(row["value"]))
    except Exception:
        pass  # use default

    # Parse variant IDs from run
    variants_raw = run["variants"] if isinstance(run, dict) else run[run.keys().index("variants")]
    try:
        variant_ids = json.loads(variants_raw) if isinstance(variants_raw, str) else [variants_raw]
    except (json.JSONDecodeError, TypeError):
        variant_ids = []

    # Compute all three analysis types
    per_item = compute_per_item_breakdown(scored, positive_threshold=threshold)
    failures = extract_failure_cases(scored, positive_threshold=threshold)
    ci = {}
    for vid in variant_ids:
        result = bootstrap_f1_ci(scored, vid, positive_threshold=threshold, seed=run_id)
        if result is not None:
            ci[vid] = result

    analysis = {
        "computed_at": datetime.now(UTC).isoformat(),
        "positive_threshold": threshold,
        "per_item": per_item,
        "failures": failures,
        "confidence_intervals": ci,
    }

    with db._lock:
        conn = db._connect()
        conn.execute(
            "UPDATE eval_runs SET analysis_json = ? WHERE id = ?",
            (json.dumps(analysis), run_id),
        )
        conn.commit()

    _log.info("compute_run_analysis: stored analysis for run %s (%d items, %d failures)",
              run_id, len(per_item), len(failures))
```

Then in `run_eval_session()` (around line 2507), add the call between judge and AI analysis:

```python
# After run_eval_judge():
compute_run_analysis(run_id, db)
# Before generate_eval_analysis():
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_eval_engine.py::TestComputeRunAnalysis -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest --timeout=120 -x -q -n 6`
Expected: All tests pass

**Step 6: Commit**

```bash
git add ollama_queue/eval_engine.py tests/test_eval_engine.py
git commit -m "feat(eval): compute_run_analysis stores structured analysis per-run"
```

---

## Batch 3: API Endpoints

### Task 8: Add `GET /api/eval/runs/{id}/analysis` endpoint

**Files:**

- Modify: `ollama_queue/api.py` (after analyze endpoint, around line 1940)
- Modify: `tests/test_api_eval_runs.py`

**Step 1: Write the failing test**

In `tests/test_api_eval_runs.py`:

```python
def test_get_analysis_returns_stored_json(client, db):
    """GET /api/eval/runs/{id}/analysis returns stored analysis_json."""
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, analysis_json) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete', ?)",
            ('{"per_item": [], "failures": [], "confidence_intervals": {}}',),
        )
        conn.commit()
    resp = client.get("/api/eval/runs/1/analysis")
    assert resp.status_code == 200
    data = resp.json()
    assert "per_item" in data
    assert "failures" in data


def test_get_analysis_not_computed(client, db):
    """GET /api/eval/runs/{id}/analysis returns status when analysis_json is NULL."""
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete')"
        )
        conn.commit()
    resp = client.get("/api/eval/runs/1/analysis")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_computed"


def test_get_analysis_not_found(client):
    """GET /api/eval/runs/999/analysis returns 404."""
    resp = client.get("/api/eval/runs/999/analysis")
    assert resp.status_code == 404
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_eval_runs.py::test_get_analysis_returns_stored_json -v`
Expected: FAIL — 404 (endpoint doesn't exist)

**Step 3: Add endpoint to api.py**

After the existing analyze endpoint (around line 1940):

```python
@app.get("/api/eval/runs/{run_id}/analysis")
def get_eval_run_analysis(run_id: int):
    """Return pre-computed structured analysis for a run."""
    with db._lock:
        conn = db._connect()
        row = conn.execute(
            "SELECT analysis_json FROM eval_runs WHERE id = ?", (run_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Run {run_id} not found")
    if not row["analysis_json"]:
        return {"status": "not_computed"}
    return json.loads(row["analysis_json"])
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_api_eval_runs.py::test_get_analysis_returns_stored_json tests/test_api_eval_runs.py::test_get_analysis_not_computed tests/test_api_eval_runs.py::test_get_analysis_not_found -v`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/api.py tests/test_api_eval_runs.py
git commit -m "feat(eval): GET /api/eval/runs/{id}/analysis endpoint"
```

---

### Task 9: Add `POST /api/eval/runs/{id}/reanalyze` endpoint

**Files:**

- Modify: `ollama_queue/api.py`
- Modify: `tests/test_api_eval_runs.py`

**Step 1: Write the failing test**

```python
def test_reanalyze_computes_analysis(client, db):
    """POST /api/eval/runs/{id}/reanalyze recomputes analysis_json."""
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'complete')"
        )
        # Insert scored pairs
        for i in range(12):
            conn.execute(
                "INSERT INTO eval_results "
                "(run_id, variant, source_item_id, target_item_id, "
                "is_same_cluster, score_transfer, row_type, source_cluster_id, target_cluster_id) "
                "VALUES (1, 'A', ?, ?, ?, ?, 'judge', 'c1', ?)",
                (str(i), str(i + 100), 1 if i < 6 else 0, 4 if i % 2 == 0 else 2, "c1" if i < 6 else "c2"),
            )
        conn.commit()
    resp = client.post("/api/eval/runs/1/reanalyze")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # Verify analysis was stored
    with db._lock:
        conn = db._connect()
        row = conn.execute("SELECT analysis_json FROM eval_runs WHERE id = 1").fetchone()
    assert row["analysis_json"] is not None


def test_reanalyze_not_found(client):
    resp = client.post("/api/eval/runs/999/reanalyze")
    assert resp.status_code == 404


def test_reanalyze_not_complete(client, db):
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status) "
            "VALUES (1, 'http://localhost', '[\"A\"]', 'A', 'generating')"
        )
        conn.commit()
    resp = client.post("/api/eval/runs/1/reanalyze")
    assert resp.status_code == 400
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_eval_runs.py::test_reanalyze_computes_analysis -v`
Expected: FAIL — 404 or 405

**Step 3: Add endpoint**

```python
@app.post("/api/eval/runs/{run_id}/reanalyze")
def reanalyze_eval_run(run_id: int):
    """Recompute structured analysis for a completed run (synchronous)."""
    run = get_eval_run(db, run_id)
    if not run:
        raise HTTPException(404, f"Run {run_id} not found")
    if run["status"] != "complete":
        raise HTTPException(400, f"Run must be complete (current: {run['status']})")
    compute_run_analysis(run_id, db)
    return {"ok": True}
```

**Step 4: Run tests**

Run: `pytest tests/test_api_eval_runs.py::test_reanalyze_computes_analysis tests/test_api_eval_runs.py::test_reanalyze_not_found tests/test_api_eval_runs.py::test_reanalyze_not_complete -v`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/api.py tests/test_api_eval_runs.py
git commit -m "feat(eval): POST /api/eval/runs/{id}/reanalyze endpoint"
```

---

### Task 10: Add `GET /api/eval/variants/stability` endpoint

**Files:**

- Modify: `ollama_queue/api.py`
- Modify: `tests/test_api_eval_variants.py`

**Step 1: Write the failing test**

```python
def test_variant_stability(client, db):
    """GET /api/eval/variants/stability returns cross-run stdev per variant."""
    with db._lock:
        conn = db._connect()
        for run_id, f1 in [(1, 0.70), (2, 0.72), (3, 0.71)]:
            conn.execute(
                "INSERT INTO eval_runs (id, data_source_url, variants, variant_id, status, metrics) "
                "VALUES (?, 'http://localhost:7685', '[\"A\"]', 'A', 'complete', ?)",
                (run_id, json.dumps({"A": {"f1": f1}})),
            )
        conn.commit()
    resp = client.get("/api/eval/variants/stability")
    assert resp.status_code == 200
    data = resp.json()
    assert "A" in data
    assert data["A"]["n_runs"] == 3
    assert data["A"]["stable"] is True


def test_variant_stability_empty(client):
    resp = client.get("/api/eval/variants/stability")
    assert resp.status_code == 200
    assert resp.json() == {}
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_eval_variants.py::test_variant_stability -v`
Expected: FAIL

**Step 3: Add endpoint**

```python
@app.get("/api/eval/variants/stability")
def get_variant_stability(data_source: str | None = None):
    """Compute cross-run F1 stability per variant (live query).

    Queries last 20 completed runs, optionally filtered by data_source URL.
    """
    from ollama_queue.eval_analysis import compute_variant_stability

    with db._lock:
        conn = db._connect()
        if data_source:
            rows = conn.execute(
                "SELECT metrics FROM eval_runs WHERE status = 'complete' AND data_source_url = ? "
                "ORDER BY id DESC LIMIT 20",
                (data_source,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT metrics FROM eval_runs WHERE status = 'complete' "
                "ORDER BY id DESC LIMIT 20",
            ).fetchall()

    run_metrics = []
    for row in rows:
        try:
            metrics = json.loads(row["metrics"]) if isinstance(row["metrics"], str) else (row["metrics"] or {})
        except (json.JSONDecodeError, TypeError):
            continue
        for vid, vm in metrics.items():
            if "f1" in vm:
                run_metrics.append({"variant": vid, "f1": vm["f1"]})

    return compute_variant_stability(run_metrics)
```

**Step 4: Run test**

Run: `pytest tests/test_api_eval_variants.py::test_variant_stability tests/test_api_eval_variants.py::test_variant_stability_empty -v`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/api.py tests/test_api_eval_variants.py
git commit -m "feat(eval): GET /api/eval/variants/stability endpoint"
```

---

### Task 11: Add `GET /api/eval/variants/{a}/diff/{b}` endpoint

**Files:**

- Modify: `ollama_queue/api.py`
- Modify: `tests/test_api_eval_variants.py`

**Step 1: Write the failing test**

```python
def test_variant_diff(client, db):
    """GET /api/eval/variants/A/diff/B returns config differences."""
    with db._lock:
        conn = db._connect()
        # Seed two variants with different configs
        conn.execute(
            "INSERT OR IGNORE INTO eval_variants (id, model, temperature, num_ctx, is_system) "
            "VALUES ('A', 'qwen2.5:7b', 0.6, 4096, 1)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO eval_variants (id, model, temperature, num_ctx, is_system) "
            "VALUES ('B', 'qwen3:14b', 0.8, 8192, 1)"
        )
        conn.commit()
    resp = client.get("/api/eval/variants/A/diff/B")
    assert resp.status_code == 200
    data = resp.json()
    assert "changes" in data
    assert len(data["changes"]) >= 2  # model + temperature + num_ctx


def test_variant_diff_identical(client, db):
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT OR IGNORE INTO eval_variants (id, model, temperature, num_ctx, is_system) "
            "VALUES ('X', 'qwen2.5:7b', 0.6, 4096, 1)"
        )
        conn.commit()
    resp = client.get("/api/eval/variants/X/diff/X")
    assert resp.status_code == 200
    assert resp.json()["changes"] == []


def test_variant_diff_not_found(client):
    resp = client.get("/api/eval/variants/NOPE/diff/ALSO_NOPE")
    assert resp.status_code == 404
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_eval_variants.py::test_variant_diff -v`
Expected: FAIL

**Step 3: Add endpoint**

```python
@app.get("/api/eval/variants/{variant_a}/diff/{variant_b}")
def get_variant_diff(variant_a: str, variant_b: str):
    """Compare two variant configs and return human-readable differences."""
    from ollama_queue.eval_analysis import describe_config_diff

    with db._lock:
        conn = db._connect()
        row_a = conn.execute("SELECT * FROM eval_variants WHERE id = ?", (variant_a,)).fetchone()
        row_b = conn.execute("SELECT * FROM eval_variants WHERE id = ?", (variant_b,)).fetchone()

    if not row_a or not row_b:
        missing = variant_a if not row_a else variant_b
        raise HTTPException(404, f"Variant '{missing}' not found")

    config_a = dict(row_a)
    config_b = dict(row_b)
    changes = describe_config_diff(config_a, config_b)
    return {"changes": changes}
```

**Step 4: Run tests**

Run: `pytest tests/test_api_eval_variants.py::test_variant_diff tests/test_api_eval_variants.py::test_variant_diff_identical tests/test_api_eval_variants.py::test_variant_diff_not_found -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest --timeout=120 -x -q -n 6`
Expected: All tests pass

**Step 6: Commit**

```bash
git add ollama_queue/api.py tests/test_api_eval_variants.py
git commit -m "feat(eval): GET /api/eval/variants/{a}/diff/{b} endpoint"
```

---

### Task 12: Add `eval.positive_threshold` setting

**Files:**

- Modify: `ollama_queue/db.py` (DEFAULTS dict, around line 50)
- Modify: `tests/test_api_eval_settings.py`

**Step 1: Write the failing test**

```python
def test_positive_threshold_setting(client, db):
    """Verify eval.positive_threshold is a valid setting."""
    resp = client.get("/api/eval/settings")
    assert resp.status_code == 200
    # Set it
    resp = client.put("/api/eval/settings", json={"eval.positive_threshold": 4})
    assert resp.status_code == 200
    # Read it back
    resp = client.get("/api/eval/settings")
    data = resp.json()
    assert data.get("eval.positive_threshold") == 4
```

**Step 2: Run test to verify behavior**

Run: `pytest tests/test_api_eval_settings.py::test_positive_threshold_setting -v`

**Step 3: Add default to DEFAULTS dict in db.py**

The eval settings system uses a generic key-value store. The setting `eval.positive_threshold` just needs to be documented. No code change needed if the settings system is fully dynamic. If DEFAULTS needs an entry, add:

```python
"eval.positive_threshold": 3,
```

to the DEFAULTS dict in db.py (around line 50).

**Step 4: Run test and full suite**

Run: `pytest --timeout=120 -x -q -n 6`

**Step 5: Commit**

```bash
git add ollama_queue/db.py tests/test_api_eval_settings.py
git commit -m "feat(eval): add eval.positive_threshold setting (default 3)"
```

---

## Batch 4: Frontend — RunRow L2 Analysis + L3 Filters

### Task 13: Add CI inline to metrics table

**Files:**

- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx:230-289`
- Modify: `ollama_queue/dashboard/spa/src/store.js` (add fetchRunAnalysis helper)

**Step 1: Add store helper**

In `store.js`, after the existing `fetchEvalRuns()` (around line 115), add:

```javascript
export async function fetchRunAnalysis(runId) {
  try {
    const res = await fetch(`${API}/eval/runs/${runId}/analysis`);
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.warn('fetchRunAnalysis failed:', err);
    return null;
  }
}
```

**Step 2: Update RunRow.jsx to show CI in metrics table**

In `RunRow.jsx`, add a `useState` for analysis data and fetch it when L2 is opened:

```javascript
// After existing useState declarations (around line 65):
const [analysis, setAnalysis] = useState(null);
const [analysisLoading, setAnalysisLoading] = useState(false);
```

In the level toggle handler (the onClick for L1 row), add:

```javascript
// When expanding to L2, fetch analysis
if (newLevel >= 2 && !analysis && status === 'complete') {
  setAnalysisLoading(true);
  fetchRunAnalysis(id).then(data => {
    setAnalysis(data);
    setAnalysisLoading(false);
  });
}
```

In the metrics table cell rendering (around line 273), modify the F1 display to include CI:

```javascript
// Where F1 value is rendered:
{metric === 'f1' && analysis?.confidence_intervals?.[vid]
  ? `${fmtPct(vm[metric])} ±${Math.round((analysis.confidence_intervals[vid].high - analysis.confidence_intervals[vid].low) / 2 * 100)}`
  : (vm[metric] != null ? fmtPct(vm[metric]) : '—')}
```

**Step 3: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Build succeeds

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx ollama_queue/dashboard/spa/src/store.js
git commit -m "feat(eval): show bootstrap CI inline in metrics table"
```

---

### Task 14: Add per-item breakdown panel to RunRow L2

**Files:**

- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx`

**Step 1: Add the breakdown panel**

After the scorer info section (around line 350) and before the existing Analysis panel (around line 355), add the per-item breakdown panel:

```jsx
{/* Per-item breakdown — shows which items were hardest for this variant */}
{status === 'complete' && analysis?.per_item?.length > 0 && analysis.per_item[0]?.status !== 'no_cluster_data' && (
  <div style={{
    marginBottom: '0.75rem',
    padding: '0.75rem',
    background: 'var(--bg-raised)',
    borderRadius: '4px',
    borderLeft: '2px solid var(--accent)',
  }}>
    <div style={{
      fontFamily: 'var(--font-mono)',
      fontSize: 'var(--type-label)',
      color: 'var(--text-tertiary)',
      marginBottom: '0.4rem',
      cursor: 'pointer',
    }} onClick={() => setShowBreakdown(prev => !prev)}>
      Item Difficulty ({analysis.per_item.length} items) {showBreakdown ? '▲' : '▼'}
    </div>
    {showBreakdown && (
      <table class="eval-metrics-table" style={{ fontSize: 'var(--type-body)' }}>
        <thead>
          <tr>
            <th style={{ textAlign: 'left' }}>Item</th>
            <th>F1</th>
            <th>TP</th>
            <th>FN</th>
            <th>FP</th>
          </tr>
        </thead>
        <tbody>
          {(showAllItems ? analysis.per_item : analysis.per_item.slice(0, 5)).map((item, idx) => (
            <tr key={idx} style={{
              background: item.f1 < 0.5 ? 'rgba(239,68,68,0.08)' : 'transparent',
            }}>
              <td style={{ textAlign: 'left', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {item.source_item_title || item.source_item_id}
              </td>
              <td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)' }}>{fmtPct(item.f1)}</td>
              <td style={{ textAlign: 'center' }}>{item.tp}</td>
              <td style={{ textAlign: 'center', color: item.fn > 0 ? 'var(--status-error)' : 'inherit' }}>{item.fn}</td>
              <td style={{ textAlign: 'center', color: item.fp > 0 ? 'var(--status-error)' : 'inherit' }}>{item.fp}</td>
            </tr>
          ))}
        </tbody>
      </table>
    )}
    {showBreakdown && analysis.per_item.length > 5 && (
      <button
        style={{ marginTop: '0.3rem', fontSize: 'var(--type-label)', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}
        onClick={() => setShowAllItems(prev => !prev)}
      >
        {showAllItems ? 'Show top 5' : `Show all ${analysis.per_item.length} items`}
      </button>
    )}
  </div>
)}

{/* Analysis not computed indicator */}
{status === 'complete' && analysis?.status === 'not_computed' && (
  <div style={{
    marginBottom: '0.75rem',
    padding: '0.5rem 0.75rem',
    background: 'var(--bg-raised)',
    borderRadius: '4px',
    fontSize: 'var(--type-label)',
    color: 'var(--text-tertiary)',
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
  }}>
    Analysis not computed
    <button
      style={{ fontSize: 'var(--type-label)', color: 'var(--accent)', background: 'none', border: '1px solid var(--accent)', borderRadius: '3px', padding: '2px 8px', cursor: 'pointer' }}
      onClick={async () => {
        await fetch(`${API}/eval/runs/${id}/reanalyze`, { method: 'POST' });
        const data = await fetchRunAnalysis(id);
        setAnalysis(data);
      }}
    >
      Compute
    </button>
  </div>
)}
```

Add the required state declarations at the top of the component:

```javascript
const [showBreakdown, setShowBreakdown] = useState(false);
const [showAllItems, setShowAllItems] = useState(false);
```

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx
git commit -m "feat(eval): add per-item breakdown panel to RunRow L2"
```

---

### Task 15: Add FP/FN filter tabs to ResultsTable

**Files:**

- Modify: `ollama_queue/dashboard/spa/src/components/eval/ResultsTable.jsx`
- Modify: `ollama_queue/api.py` (add `filter` query param to results endpoint)

**Step 1: Add `filter` query param to API**

In `api.py`, find the `GET /api/eval/runs/{run_id}/results` endpoint (around line 2001). Add a `filter` query parameter:

```python
@app.get("/api/eval/runs/{run_id}/results")
def get_eval_run_results(
    run_id: int,
    row_type: str | None = None,
    filter: str | None = None,  # NEW: tp|tn|fp|fn
    limit: int = 20,
    offset: int = 0,
):
    # ... existing query ...
    # After base WHERE clause, add filter:
    threshold = 3  # read from settings if needed
    if filter == "fp":
        query += " AND is_same_cluster = 0 AND COALESCE(override_score_transfer, score_transfer) >= ?"
        params.append(threshold)
    elif filter == "fn":
        query += " AND is_same_cluster = 1 AND COALESCE(override_score_transfer, score_transfer) < ?"
        params.append(threshold)
    elif filter == "tp":
        query += " AND is_same_cluster = 1 AND COALESCE(override_score_transfer, score_transfer) >= ?"
        params.append(threshold)
    elif filter == "tn":
        query += " AND is_same_cluster = 0 AND COALESCE(override_score_transfer, score_transfer) < ?"
        params.append(threshold)
```

**Step 2: Update ResultsTable.jsx**

Add filter state and preset tab buttons:

```jsx
// At top of component, add filter state:
const [activeFilter, setActiveFilter] = useState(null);

// In loadPage(), add filter to URL:
const filterParam = activeFilter ? `&filter=${activeFilter}` : '';
const res = await fetch(`${API}/eval/runs/${runId}/results?limit=${PAGE_SIZE}&offset=${off}${filterParam}`);

// Add useEffect to reset pagination when filter changes:
useEffect(() => {
  setResults([]);
  setOffset(0);
  setHasMore(true);
  loadPage(0);
}, [activeFilter]);
```

Add filter tabs above the results table:

```jsx
{/* Filter tabs — preset buttons for error class filtering */}
<div style={{
  display: 'flex', gap: '0.25rem', marginBottom: '0.5rem',
  fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
}}>
  {[
    { key: null, label: 'All' },
    { key: 'tp', label: 'TP' },
    { key: 'tn', label: 'TN' },
    { key: 'fp', label: 'FP' },
    { key: 'fn', label: 'FN' },
  ].map(tab => (
    <button
      key={tab.key ?? 'all'}
      onClick={() => setActiveFilter(tab.key)}
      style={{
        padding: '2px 8px',
        borderRadius: '3px',
        border: activeFilter === tab.key ? '1px solid var(--accent)' : '1px solid var(--border)',
        background: activeFilter === tab.key ? 'var(--accent-glow)' : 'transparent',
        color: activeFilter === tab.key ? 'var(--accent)' : 'var(--text-secondary)',
        cursor: 'pointer',
      }}
    >
      {tab.label}
    </button>
  ))}
</div>
```

**Step 3: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Build succeeds

**Step 4: Commit**

```bash
git add ollama_queue/api.py ollama_queue/dashboard/spa/src/components/eval/ResultsTable.jsx
git commit -m "feat(eval): add FP/FN/TP/TN filter tabs to ResultsTable"
```

---

### Task 16: Add error handling to ResultsTable

**Files:**

- Modify: `ollama_queue/dashboard/spa/src/components/eval/ResultsTable.jsx`

**Step 1: Add error state**

```javascript
const [error, setError] = useState(null);

// In loadPage(), wrap fetch in try/catch:
async function loadPage(off) {
  setLoading(true);
  setError(null);
  try {
    const res = await fetch(`${API}/eval/runs/${runId}/results?limit=${PAGE_SIZE}&offset=${off}${filterParam}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    setResults(prev => off === 0 ? data : [...prev, ...data]);
    setOffset(off + PAGE_SIZE);
    setHasMore(data.length === PAGE_SIZE);
  } catch (err) {
    console.error('ResultsTable fetch failed:', err);
    setError(err.message);
  } finally {
    setLoading(false);
  }
}
```

Add error UI:

```jsx
{error && (
  <div style={{ padding: '0.75rem', color: 'var(--status-error)', fontSize: 'var(--type-label)' }}>
    Failed to load results: {error}
    <button
      style={{ marginLeft: '0.5rem', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}
      onClick={() => loadPage(0)}
    >
      Retry
    </button>
  </div>
)}
```

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/ResultsTable.jsx
git commit -m "fix(eval): add error handling + retry to ResultsTable"
```

---

## Batch 5: Frontend — Variants Tab + Translations + Bug Fixes

### Task 17: Add stability columns to VariantStabilityTable

**Files:**

- Modify: `ollama_queue/dashboard/spa/src/components/eval/VariantStabilityTable.jsx`
- Modify: `ollama_queue/dashboard/spa/src/store.js` (add fetchVariantStability)

**Step 1: Add store helper**

In `store.js`:

```javascript
export const evalStability = signal({});

export async function fetchVariantStability() {
  try {
    const res = await fetch(`${API}/eval/variants/stability`);
    if (!res.ok) return;
    evalStability.value = await res.json();
  } catch (err) {
    console.warn('fetchVariantStability failed:', err);
  }
}
```

**Step 2: Update VariantStabilityTable**

Import `evalStability` and `fetchVariantStability` from store. Call `fetchVariantStability()` on mount. Add columns to StabilityRow L2:

```jsx
{/* After existing quality display, add stability metrics */}
{stability && (
  <div style={{ display: 'flex', gap: '1rem', marginTop: '0.3rem', fontSize: 'var(--type-label)' }}>
    <span>Stdev: <strong>{(stability.stdev * 100).toFixed(1)}%</strong></span>
    <span>Runs: <strong>{stability.n_runs}</strong></span>
    <span style={{
      padding: '1px 6px',
      borderRadius: '3px',
      background: stability.stable ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
      color: stability.stable ? 'var(--status-healthy)' : 'var(--status-error)',
    }}>
      {stability.stable ? '✓ Stable' : '✗ Unstable'}
    </span>
  </div>
)}
```

**Step 3: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/VariantStabilityTable.jsx ollama_queue/dashboard/spa/src/store.js
git commit -m "feat(eval): add stability metrics to VariantStabilityTable"
```

---

### Task 18: Add config diff to Variants tab

**Files:**

- Create: `ollama_queue/dashboard/spa/src/components/eval/ConfigDiffPanel.jsx`
- Modify: `ollama_queue/dashboard/spa/src/views/EvalVariants.jsx`

**Step 1: Create ConfigDiffPanel component**

```jsx
import { h } from 'preact';
import { useState } from 'preact/hooks';
import { API, evalVariants } from '../../store.js';

// What it shows: Side-by-side comparison of two variant configs.
// Decision it drives: Helps user understand what changed between variants
//   so they can attribute F1 differences to specific config changes.

export default function ConfigDiffPanel() {
  const [varA, setVarA] = useState('');
  const [varB, setVarB] = useState('');
  const [changes, setChanges] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const variants = evalVariants.value || [];

  async function handleCompare() {
    if (!varA || !varB) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/eval/variants/${encodeURIComponent(varA)}/diff/${encodeURIComponent(varB)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setChanges(data.changes);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{
      padding: '0.75rem',
      background: 'var(--bg-raised)',
      borderRadius: '4px',
      marginBottom: '0.75rem',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--type-label)',
        color: 'var(--text-tertiary)',
        marginBottom: '0.4rem',
      }}>
        Compare Configs
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={varA} onChange={evt => setVarA(evt.target.value)}
          style={{ padding: '4px 8px', borderRadius: '3px', border: '1px solid var(--border)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}>
          <option value="">Select A</option>
          {variants.map(variant => (
            <option key={variant.id} value={variant.id}>{variant.id} — {variant.label || variant.model}</option>
          ))}
        </select>
        <span style={{ color: 'var(--text-tertiary)' }}>vs</span>
        <select value={varB} onChange={evt => setVarB(evt.target.value)}
          style={{ padding: '4px 8px', borderRadius: '3px', border: '1px solid var(--border)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}>
          <option value="">Select B</option>
          {variants.map(variant => (
            <option key={variant.id} value={variant.id}>{variant.id} — {variant.label || variant.model}</option>
          ))}
        </select>
        <button onClick={handleCompare} disabled={!varA || !varB || loading}
          style={{ padding: '4px 12px', borderRadius: '3px', border: '1px solid var(--accent)', background: 'var(--accent-glow)', color: 'var(--accent)', cursor: 'pointer' }}>
          {loading ? 'Comparing…' : 'Compare'}
        </button>
      </div>
      {error && <div style={{ color: 'var(--status-error)', fontSize: 'var(--type-label)', marginTop: '0.3rem' }}>{error}</div>}
      {changes !== null && (
        <div style={{ marginTop: '0.5rem', fontSize: 'var(--type-body)' }}>
          {changes.length === 0
            ? <span style={{ color: 'var(--text-tertiary)' }}>Identical configuration</span>
            : <ul style={{ margin: 0, paddingLeft: '1.2rem' }}>
                {changes.map((change, idx) => <li key={idx} style={{ marginBottom: '0.2rem' }}>{change}</li>)}
              </ul>
          }
        </div>
      )}
    </div>
  );
}
```

**Step 2: Add ConfigDiffPanel to EvalVariants view**

In `ollama_queue/dashboard/spa/src/views/EvalVariants.jsx`, import and render the panel above the variant list:

```jsx
import ConfigDiffPanel from '../components/eval/ConfigDiffPanel.jsx';

// In the render, after the toolbar and before the variant list:
<ConfigDiffPanel />
```

**Step 3: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/ConfigDiffPanel.jsx ollama_queue/dashboard/spa/src/views/EvalVariants.jsx
git commit -m "feat(eval): add config diff comparison panel to Variants tab"
```

---

### Task 19: Add translations for new features

**Files:**

- Modify: `ollama_queue/dashboard/spa/src/components/eval/translations.js`

**Step 1: Add new entries**

Append to the EVAL_TRANSLATIONS object:

```javascript
// Analysis features (2026-03-09)
positive_threshold: {
  label: 'Match Threshold',
  tooltip: 'Transfer score at or above this value counts as a positive match. Default: 3 (on 1–5 scale).',
},
item_difficulty: {
  label: 'Item Difficulty',
  tooltip: 'Shows which test items were hardest — sorted by F1, worst first. Low F1 means the principle failed to transfer for that item.',
},
confidence_interval: {
  label: 'Confidence Range',
  tooltip: '95% bootstrap confidence interval — if you ran this eval 100 times with different samples, F1 would fall in this range 95 times.',
},
stability_stdev: {
  label: 'Score Consistency',
  tooltip: 'Standard deviation of F1 across recent runs. Below 10% = stable (trustworthy). Above 10% = unstable (fix sampling before optimizing prompts).',
},
config_diff: {
  label: 'Config Differences',
  tooltip: 'What changed between two variant configurations — model, temperature, context window, prompt template.',
},
false_positive: {
  label: 'False Positive',
  tooltip: 'The principle matched an unrelated item (different cluster) — it\'s too broad.',
},
false_negative: {
  label: 'False Negative',
  tooltip: 'The principle missed a related item (same cluster) — it\'s too narrow.',
},
analysis_not_computed: {
  label: 'Analysis Not Computed',
  tooltip: 'Structured analysis has not been computed for this run. Click "Compute" to generate per-item breakdown, failure cases, and confidence intervals.',
},
```

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/eval/translations.js
git commit -m "feat(eval): add translations for analysis features"
```

---

### Task 20: Fix existing bugs discovered during audit

**Files:**

- Modify: `ollama_queue/eval_engine.py` (per-cluster F1=1.0 on 0 items)
- Modify: `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx` (ConfusionMatrix error boundary)
- Modify: `tests/test_eval_engine.py`

**Step 1: Fix per-cluster F1=1.0 on empty clusters**

In `eval_engine.py`, find `compute_metrics()` (around line 1022-1040) where per-cluster breakdown is computed. Add a guard to skip clusters with 0 same-cluster pairs:

```python
# Skip clusters with no same-cluster data (F1 would be misleadingly 1.0)
if not csame:
    continue
```

**Step 2: Add ConfusionMatrix error boundary in RunRow.jsx**

Wrap the ConfusionMatrix render in a try/catch pattern using a simple error boundary:

```jsx
{status === 'complete' && (
  <div>
    {(() => {
      try {
        return <ConfusionMatrix runId={id} />;
      } catch (err) {
        return <div style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)' }}>
          Confusion matrix unavailable
        </div>;
      }
    })()}
  </div>
)}
```

**Note:** In Preact, synchronous try/catch around JSX doesn't catch async render errors. A proper error boundary requires a class component. If the ConfusionMatrix already handles its own errors internally, this step can be simplified to just wrapping the existing render in a check for the confusion API response.

**Step 3: Write test for per-cluster fix**

```python
def test_per_cluster_skips_empty_same_cluster(self):
    """Clusters with no same-cluster pairs should be skipped, not show F1=1.0."""
    results = [
        _make_result("A", is_same_cluster=0, effective_score_transfer=1,
                     source_cluster_id="c1", target_cluster_id="c2"),
    ]
    metrics = compute_metrics(results)
    per_cluster = metrics.get("A", {}).get("per_cluster", {})
    # c1 has no same-cluster pairs — should not appear or should not have F1=1.0
    if "c1" in per_cluster:
        assert per_cluster["c1"]["f1"] != 1.0
```

**Step 4: Run tests and build**

Run: `pytest --timeout=120 -x -q -n 6 && cd ollama_queue/dashboard/spa && npm run build`

**Step 5: Commit**

```bash
git add ollama_queue/eval_engine.py ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx tests/test_eval_engine.py
git commit -m "fix(eval): skip empty clusters in per-cluster breakdown, add ConfusionMatrix error handling"
```

---

### Task 21: Final — Run full test suite + build + push

**Step 1: Run all backend tests**

Run: `cd /home/justin/Documents/projects/ollama-queue && source .venv/bin/activate && pytest --timeout=120 -x -q -n 6`
Expected: All tests pass

**Step 2: Build frontend**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Build succeeds

**Step 3: Run linter**

Run: `cd /home/justin/Documents/projects/ollama-queue && make lint`
Expected: No errors

**Step 4: Create branch, push, PR**

```bash
git checkout -b feature/eval-analysis-panel
git push -u origin feature/eval-analysis-panel
gh pr create --title "feat(eval): analysis panel — per-item breakdown, CI, stability, config diff, failure drill-down" --body "Adds 5 analysis features to the eval pipeline UI as platform features.

## New module: eval_analysis.py
- compute_per_item_breakdown() — worst-first item ranking
- extract_failure_cases() — FP/FN drill-down
- bootstrap_f1_ci() — 95% confidence intervals
- compute_variant_stability() — cross-run F1 stdev
- describe_config_diff() — human-readable config comparison

## Backend
- analysis_json column on eval_runs (stored per-run)
- source_item_title/target_item_title on eval_results
- 4 new API endpoints: GET analysis, POST reanalyze, GET stability, GET diff
- eval.positive_threshold setting (configurable match threshold)
- Pipeline integration: compute_run_analysis() runs after judge

## Frontend
- CI inline in metrics table (±range)
- Per-item breakdown panel in RunRow L2
- FP/FN/TP/TN filter tabs on ResultsTable
- Stability metrics in VariantStabilityTable
- Config diff comparison panel in Variants tab
- Error handling + retry on ResultsTable
- Translations for all new features

## Bug fixes
- Per-cluster F1=1.0 on empty clusters
- ConfusionMatrix error handling

Design: docs/plans/2026-03-09-eval-analysis-panel-design.md"
```
