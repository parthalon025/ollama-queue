# Forge v2 Phase 3 (Learn) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the learning loop: drift detection notices when the judge degrades, arbiters resolve disputes, structured feedback flows upstream to data sources, and meta-eval questions whether the metrics themselves are still valid. Phase 3 enables Level 3 (Operator) autonomy.

**Architecture:** Extends `ollama_queue/forge/` with 5 new modules. All are pure computation — no DB or HTTP in the module itself. DB persistence in `db/forge.py` extension. Engine gains drift, arbiter, and feedback steps after the Phase 2 evolution step. New API endpoints expose drift history, feedback log, holdout checks, and dispute resolution.

**Tech Stack:** Python 3.12, SQLite (WAL), httpx (feedback POST), pytest. No new dependencies.

**Design doc:** `docs/plans/2026-03-17-forge-v2-design.md`

**Depends on:** Phase 1 (all forge core) + Phase 2 (archive, splits, Thompson, engine extensions).

**Reference code:**
- `ha-aria/aria/modules/intelligence.py:27-80` — Drift detection (mean-of-halves, dual-series interpretation)
- `ha-aria/aria/engine/predictions/scoring.py:78-91` — Mean-of-halves trend
- `ha-aria/aria/engine/evaluation.py:10-38` — Expanding-window cross-validation
- `ollama_queue/eval/promote.py` — Auto-promote gate structure (adds oracle kappa gate)
- `ollama_queue/forge/oracle.py` — Kappa + oracle scoring (Phase 1)

---

## Module Map

```
ollama_queue/forge/           # Phase 3 additions
  drift.py                    # Judge-oracle trend monitoring (~70 lines)
  holdout.py                  # Held-out test set management (~60 lines)
  arbiter.py                  # Tier 3 dispute resolution (~80 lines)
  feedback.py                 # Structured feedback to data sources (~90 lines)
  meta_eval.py                # Double-loop metric evaluation (~80 lines)

ollama_queue/db/
  forge.py                    # Extend ForgeMixin: drift, feedback, holdout, disputes (~+120 lines)

ollama_queue/api/
  forge_feedback.py           # Feedback log + trigger endpoints (~60 lines)
  forge_holdout.py            # Holdout check endpoints (~50 lines)

tests/
  test_forge_drift.py
  test_forge_holdout.py
  test_forge_arbiter.py
  test_forge_feedback.py
  test_forge_meta_eval.py
  test_forge_db_phase3.py
  test_api_forge_feedback.py
  test_api_forge_holdout.py
```

---

## Batch 1: Drift Detection

**PRD:** Monitor judge-oracle agreement trends over time. Uses mean-of-halves from ARIA to detect behavioral drift (real data shift), judge regression (judge degraded), and calibration decay (F1 dropped but kappa stable). Drift snapshots stored per run.

### Task 1: Mean-of-halves drift computation

**Files:**

- Create: `ollama_queue/forge/drift.py`
- Test: `tests/test_forge_drift.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_drift.py
"""Tests for Forge drift detection."""
from ollama_queue.forge.drift import (
    mean_of_halves_trend,
    detect_drift,
    compare_judge_oracle_trend,
)


def test_mean_of_halves_improving():
    values = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75]
    trend = mean_of_halves_trend(values)
    assert trend > 0


def test_mean_of_halves_degrading():
    values = [0.8, 0.75, 0.7, 0.65, 0.6, 0.55]
    trend = mean_of_halves_trend(values)
    assert trend < 0


def test_mean_of_halves_stable():
    values = [0.7, 0.71, 0.69, 0.7, 0.71, 0.69]
    trend = mean_of_halves_trend(values)
    assert abs(trend) < 0.05


def test_mean_of_halves_too_few():
    assert mean_of_halves_trend([0.5]) == 0.0
    assert mean_of_halves_trend([0.5, 0.6]) == 0.0
    assert mean_of_halves_trend([0.5, 0.6, 0.7]) == 0.0


def test_detect_drift_stable():
    kappas = [0.7, 0.72, 0.71, 0.73, 0.72, 0.74]
    f1s = [0.8, 0.81, 0.79, 0.82, 0.80, 0.81]
    result = detect_drift(kappas, f1s)
    assert result["interpretation"] == "stable"


def test_detect_drift_behavioral():
    kappas = [0.8, 0.75, 0.65, 0.55, 0.45, 0.35]
    f1s = [0.85, 0.80, 0.70, 0.60, 0.50, 0.40]
    result = detect_drift(kappas, f1s)
    assert result["interpretation"] == "behavioral_drift"


def test_detect_drift_oracle_shift():
    kappas = [0.8, 0.75, 0.65, 0.55, 0.45, 0.35]
    f1s = [0.80, 0.81, 0.79, 0.82, 0.80, 0.81]
    result = detect_drift(kappas, f1s)
    assert result["interpretation"] == "oracle_shift"


def test_detect_drift_calibration_decay():
    kappas = [0.7, 0.72, 0.71, 0.73, 0.72, 0.74]
    f1s = [0.85, 0.80, 0.70, 0.60, 0.50, 0.40]
    result = detect_drift(kappas, f1s)
    assert result["interpretation"] == "calibration_decay"


def test_detect_drift_insufficient():
    result = detect_drift([0.5, 0.6], [0.7, 0.8])
    assert result["interpretation"] == "insufficient_data"


def test_compare_judge_oracle_stable():
    judge = [0.8, 0.81, 0.79, 0.82, 0.80, 0.81]
    oracle = [0.85, 0.84, 0.86, 0.85, 0.84, 0.86]
    result = compare_judge_oracle_trend(judge, oracle)
    assert result["interpretation"] == "stable"


def test_compare_judge_oracle_judge_regression():
    judge = [0.85, 0.80, 0.70, 0.60, 0.50, 0.40]
    oracle = [0.85, 0.84, 0.86, 0.85, 0.84, 0.86]
    result = compare_judge_oracle_trend(judge, oracle)
    assert result["interpretation"] == "judge_regression"


def test_compare_judge_oracle_insufficient():
    result = compare_judge_oracle_trend([0.5], [0.6])
    assert result["interpretation"] == "insufficient_data"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_drift.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/drift.py
"""Forge drift detection — monitors judge-oracle agreement trends.

Uses mean-of-halves from ARIA intelligence.py to detect:
- behavioral_drift: both judge and oracle degrade (real data shift)
- oracle_shift: kappa drops but F1 stable (oracle behavior changed)
- calibration_decay: F1 drops but kappa stable (calibration stale)
"""
from __future__ import annotations

import statistics


def mean_of_halves_trend(values: list[float]) -> float:
    """Trend as mean(second half) - mean(first half).

    Adapted from ARIA scoring.py:accuracy_trend.
    Positive = improving, negative = degrading.
    Requires >= 4 values.
    """
    if len(values) < 4:
        return 0.0
    mid = len(values) // 2
    return statistics.mean(values[mid:]) - statistics.mean(values[:mid])


def detect_drift(
    kappa_history: list[float],
    f1_history: list[float],
    *,
    threshold: float = 0.05,
) -> dict:
    """Detect drift in judge performance metrics."""
    if len(kappa_history) < 4 or len(f1_history) < 4:
        return {"interpretation": "insufficient_data", "kappa_trend": None, "f1_trend": None}

    kappa_trend = mean_of_halves_trend(kappa_history)
    f1_trend = mean_of_halves_trend(f1_history)

    kappa_degraded = kappa_trend < -threshold
    f1_degraded = f1_trend < -threshold

    if kappa_degraded and f1_degraded:
        interpretation = "behavioral_drift"
    elif kappa_degraded and not f1_degraded:
        interpretation = "oracle_shift"
    elif not kappa_degraded and f1_degraded:
        interpretation = "calibration_decay"
    else:
        interpretation = "stable"

    return {
        "interpretation": interpretation,
        "kappa_trend": round(kappa_trend, 4),
        "f1_trend": round(f1_trend, 4),
    }


def compare_judge_oracle_trend(
    judge_accuracies: list[float],
    oracle_accuracies: list[float],
    *,
    threshold: float = 0.05,
) -> dict:
    """Compare judge vs oracle accuracy trends.

    Adapted from ARIA intelligence.py:compare_model_accuracy.
    """
    if len(judge_accuracies) < 4 or len(oracle_accuracies) < 4:
        return {"interpretation": "insufficient_data",
                "judge_trend": None, "oracle_trend": None, "divergence": None}

    judge_trend = mean_of_halves_trend(judge_accuracies)
    oracle_trend = mean_of_halves_trend(oracle_accuracies)

    judge_degraded = judge_trend < -threshold
    oracle_degraded = oracle_trend < -threshold

    if judge_degraded and oracle_degraded:
        interpretation = "behavioral_drift"
    elif judge_degraded and not oracle_degraded:
        interpretation = "judge_regression"
    elif not judge_degraded and oracle_degraded:
        interpretation = "judge_improvement"
    else:
        interpretation = "stable"

    return {
        "interpretation": interpretation,
        "judge_trend": round(judge_trend, 4),
        "oracle_trend": round(oracle_trend, 4),
        "divergence": round(abs(judge_trend - oracle_trend), 4),
    }
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_drift.py -v`
Expected: PASS (12 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/drift.py tests/test_forge_drift.py
git commit -m "feat(forge): add drift detection — mean-of-halves trend monitoring"
```

---

## Batch 2: Holdout Management

**PRD:** The test split (20% of items) is frozen and only evaluated on manual request ("CHECK HOLDOUT" button). Prevents accidental optimization against the test set. Freshness tracking detects when holdout data has changed since creation.

### Task 2: Holdout snapshot and freshness

**Files:**

- Create: `ollama_queue/forge/holdout.py`
- Test: `tests/test_forge_holdout.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_holdout.py
"""Tests for Forge holdout set management."""
from ollama_queue.forge.holdout import create_holdout_snapshot, check_holdout_freshness


def _items(n, prefix="item"):
    return [{"id": f"{prefix}-{i}", "title": f"T{i}", "description": f"D{i}"} for i in range(n)]


def test_create_holdout_snapshot():
    items = _items(10)
    snap = create_holdout_snapshot(items, seed=42)
    assert snap["item_count"] == 10
    assert len(snap["item_ids"]) == 10
    assert len(snap["item_hash"]) == 16
    assert "created_at" in snap


def test_create_holdout_deterministic():
    items = _items(10)
    a = create_holdout_snapshot(items, seed=42)
    b = create_holdout_snapshot(items, seed=42)
    assert a["item_hash"] == b["item_hash"]
    assert a["item_ids"] == b["item_ids"]


def test_freshness_unchanged():
    items = _items(10)
    snap = create_holdout_snapshot(items, seed=42)
    result = check_holdout_freshness(snap["item_hash"], items)
    assert result["fresh"] is True


def test_freshness_changed():
    items_v1 = _items(10)
    snap = create_holdout_snapshot(items_v1, seed=42)
    items_v2 = _items(10)
    items_v2[0]["title"] = "CHANGED TITLE"
    result = check_holdout_freshness(snap["item_hash"], items_v2)
    assert result["fresh"] is False


def test_freshness_items_added():
    items_v1 = _items(5)
    snap = create_holdout_snapshot(items_v1, seed=42)
    items_v2 = _items(10)
    result = check_holdout_freshness(snap["item_hash"], items_v2)
    assert result["fresh"] is False
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_holdout.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/holdout.py
"""Forge holdout — manages held-out test sets with freshness tracking.

The test split (20%) is frozen and only evaluated on manual request.
Prevents Goodhart optimization against the test set.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime


def create_holdout_snapshot(items: list[dict], *, seed: int) -> dict:
    """Create a holdout set definition.

    Returns {item_ids, item_hash, item_count, seed, created_at}.
    item_hash detects when source data has changed.
    """
    item_ids = sorted(item["id"] for item in items)
    content = "|".join(
        f"{item['id']}:{item.get('title', '')}:{item.get('description', '')}"
        for item in sorted(items, key=lambda i: i["id"])
    )
    item_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    return {
        "item_ids": item_ids,
        "item_hash": item_hash,
        "item_count": len(item_ids),
        "seed": seed,
        "created_at": datetime.now(UTC).isoformat(),
    }


def check_holdout_freshness(holdout_hash: str, current_items: list[dict]) -> dict:
    """Check if holdout items have changed since creation."""
    content = "|".join(
        f"{item['id']}:{item.get('title', '')}:{item.get('description', '')}"
        for item in sorted(current_items, key=lambda i: i["id"])
    )
    current_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    return {
        "fresh": current_hash == holdout_hash,
        "current_hash": current_hash,
        "holdout_hash": holdout_hash,
    }
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_holdout.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/holdout.py tests/test_forge_holdout.py
git commit -m "feat(forge): add holdout set management with freshness tracking"
```

---

## Batch 3: Arbiter (Tier 3 Dispute Resolution)

**PRD:** When judge and oracle disagree strongly (delta > threshold), a stronger model (Claude Opus) adjudicates. The arbiter's score becomes the final ground truth for that pair. This is the most expensive tier — only used for high-value disagreements.

### Task 3: Dispute selection and arbiter prompt

**Files:**

- Create: `ollama_queue/forge/arbiter.py`
- Test: `tests/test_forge_arbiter.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_arbiter.py
"""Tests for Forge arbiter — dispute selection and resolution."""
from ollama_queue.forge.arbiter import (
    select_disputes,
    build_arbiter_prompt,
    parse_arbiter_response,
)


def test_select_disputes_finds_big_deltas():
    results = [
        {"id": 1, "judge_score": 4, "oracle_score": 1},  # delta=3
        {"id": 2, "judge_score": 3, "oracle_score": 3},  # delta=0
        {"id": 3, "judge_score": 1, "oracle_score": 5},  # delta=4
        {"id": 4, "judge_score": 4, "oracle_score": 3},  # delta=1
    ]
    disputes = select_disputes(results, threshold=2)
    assert len(disputes) == 2
    assert {d["id"] for d in disputes} == {1, 3}


def test_select_disputes_skips_missing_scores():
    results = [
        {"id": 1, "judge_score": 4, "oracle_score": None},
        {"id": 2, "judge_score": None, "oracle_score": 3},
        {"id": 3, "judge_score": 5, "oracle_score": 1},
    ]
    disputes = select_disputes(results, threshold=2)
    assert len(disputes) == 1
    assert disputes[0]["id"] == 3


def test_select_disputes_none_found():
    results = [
        {"id": 1, "judge_score": 3, "oracle_score": 3},
        {"id": 2, "judge_score": 4, "oracle_score": 4},
    ]
    assert select_disputes(results, threshold=2) == []


def test_build_arbiter_prompt_contains_both_scores():
    prompt = build_arbiter_prompt(
        principle="Always log before returning fallback",
        target={"title": "Silent catch", "one_liner": "Bare except", "description": "Hides errors"},
        judge_score=4, judge_reasoning="Clear match",
        oracle_score=1, oracle_reasoning="Different problem domain",
    )
    assert "4" in prompt and "1" in prompt
    assert "Always log" in prompt
    assert "Silent catch" in prompt
    assert "cluster" not in prompt.lower()


def test_parse_arbiter_response_valid():
    text = '{"transfer": 2, "reasoning": "Oracle was closer"}'
    result = parse_arbiter_response(text)
    assert result["transfer"] == 2


def test_parse_arbiter_response_with_think():
    text = '<think>Analyzing both perspectives...</think>{"transfer": 3, "reasoning": "Split the difference"}'
    result = parse_arbiter_response(text)
    assert result["transfer"] == 3
    assert result["judge_reasoning"] == "Analyzing both perspectives..."
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_arbiter.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/arbiter.py
"""Forge arbiter — Tier 3 dispute resolution for judge-oracle disagreements.

When judge and oracle disagree strongly (delta > threshold), a stronger model
adjudicates. The arbiter's score becomes the final ground truth for that pair.
"""
from __future__ import annotations


def select_disputes(results: list[dict], *, threshold: int = 2) -> list[dict]:
    """Find results where judge and oracle disagree beyond threshold."""
    disputes = []
    for r in results:
        j = r.get("judge_score")
        o = r.get("oracle_score")
        if j is not None and o is not None and abs(j - o) > threshold:
            disputes.append(r)
    return disputes


def build_arbiter_prompt(
    *, principle: str, target: dict,
    judge_score: int, judge_reasoning: str,
    oracle_score: int, oracle_reasoning: str,
) -> str:
    """Build arbiter prompt including both judge and oracle perspectives."""
    title = target.get("title", "")
    one_liner = target.get("one_liner", "")
    description = target.get("description", "")

    return f"""You are the final arbiter in an evaluation dispute. Two AI models disagree on a score.

PRINCIPLE: "{principle}"

TARGET:
  Title: {title}
  Summary: {one_liner}
  Description: {description}

MODEL A (Judge) scored: {judge_score}/5
Reasoning: {judge_reasoning}

MODEL B (Oracle) scored: {oracle_score}/5
Reasoning: {oracle_reasoning}

Your task: provide the CORRECT score on the 1-5 scale.
Consider both arguments carefully. The correct answer may agree with either model or differ from both.

  1 = Does not apply at all
  2 = Tangentially related
  3 = Somewhat applicable
  4 = Clearly applies
  5 = Perfect match

Return JSON: {{"transfer": <1-5>, "reasoning": "<which model was closer and why>"}}"""


def parse_arbiter_response(text: str) -> dict:
    """Parse arbiter response — reuses judge parser logic."""
    from ollama_queue.forge.judge import parse_judge_response
    return parse_judge_response(text)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_arbiter.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/arbiter.py tests/test_forge_arbiter.py
git commit -m "feat(forge): add arbiter — Tier 3 dispute resolution"
```

---

## Batch 4: Feedback Protocol

**PRD:** Level 3 (Operator) sends structured feedback to the data source. Feedback types: weak principles, merge candidates (near-duplicate items), coverage gaps, and score drift. The data source decides what to do with feedback — Forge only sends, never modifies upstream data directly.

### Task 4: Feedback report builder

**Files:**

- Create: `ollama_queue/forge/feedback.py`
- Test: `tests/test_forge_feedback.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_feedback.py
"""Tests for Forge feedback — structured improvement signals."""
from ollama_queue.forge.feedback import (
    FeedbackType,
    build_feedback_report,
    send_feedback,
)


def test_feedback_type_values():
    assert FeedbackType.WEAK_PRINCIPLE.value == "weak_principle"
    assert FeedbackType.MERGE_CANDIDATE.value == "merge_candidate"
    assert FeedbackType.COVERAGE_GAP.value == "coverage_gap"
    assert FeedbackType.SCORE_DRIFT.value == "score_drift"


def test_build_report_weak_principles():
    results = [
        {"source_item_id": "A", "calibrated_score": 0.5, "judge_score": 1, "embedding_similarity": 0.3},
        {"source_item_id": "A", "calibrated_score": 0.8, "judge_score": 1, "embedding_similarity": 0.2},
        {"source_item_id": "B", "calibrated_score": 4.5, "judge_score": 5, "embedding_similarity": 0.4},
    ]
    report = build_feedback_report(results, {}, weak_threshold=0.3)
    weak = [f for f in report if f["type"] == "weak_principle"]
    assert len(weak) == 1
    assert weak[0]["item_id"] == "A"


def test_build_report_merge_candidates():
    results = [
        {"source_item_id": "X", "target_item_id": "Y",
         "embedding_similarity": 0.95, "calibrated_score": 3.0, "judge_score": 3},
    ]
    report = build_feedback_report(results, {}, merge_similarity=0.90)
    merges = [f for f in report if f["type"] == "merge_candidate"]
    assert len(merges) == 1


def test_build_report_no_issues():
    results = [
        {"source_item_id": "A", "calibrated_score": 4.0, "judge_score": 4, "embedding_similarity": 0.5},
    ]
    report = build_feedback_report(results, {})
    assert len(report) == 0


def test_send_feedback_handles_error():
    """Network error returns ok=False, not an exception."""
    result = send_feedback("http://127.0.0.1:99999", [{"test": True}], timeout=1)
    assert result["ok"] is False
    assert "error" in result
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_feedback.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/feedback.py
"""Forge feedback — structured improvement signals for data sources.

Level 3 (Operator) sends actionable feedback:
- Weak principles that need strengthening
- Merge candidates (near-duplicate items)
- Coverage gaps and score drift
"""
from __future__ import annotations

import logging
from enum import Enum

import httpx

_log = logging.getLogger(__name__)


class FeedbackType(Enum):
    WEAK_PRINCIPLE = "weak_principle"
    MERGE_CANDIDATE = "merge_candidate"
    COVERAGE_GAP = "coverage_gap"
    SCORE_DRIFT = "score_drift"


def build_feedback_report(
    results: list[dict], metrics: dict, *,
    weak_threshold: float = 0.3, merge_similarity: float = 0.90,
) -> list[dict]:
    """Build structured feedback from Forge results.

    Returns list of {type, item_id, diagnosis, recommendation, data}.
    """
    feedback = []

    # Weak principles: average score below threshold
    item_scores: dict[str, list[float]] = {}
    for r in results:
        sid = r.get("source_item_id")
        score = r.get("calibrated_score") if r.get("calibrated_score") is not None else r.get("judge_score")
        if sid and score is not None:
            item_scores.setdefault(sid, []).append(float(score))

    for item_id, scores in item_scores.items():
        avg = sum(scores) / len(scores) if scores else 0
        if avg < weak_threshold * 5:
            feedback.append({
                "type": FeedbackType.WEAK_PRINCIPLE.value,
                "item_id": item_id,
                "diagnosis": f"Average score {avg:.1f}/5 across {len(scores)} pairs",
                "recommendation": "Consider revising or marking for review",
                "data": {"avg_score": round(avg, 2), "pair_count": len(scores)},
            })

    # Merge candidates: very high similarity pairs
    for r in results:
        sim = r.get("embedding_similarity", 0)
        if sim >= merge_similarity:
            feedback.append({
                "type": FeedbackType.MERGE_CANDIDATE.value,
                "item_id": r.get("source_item_id", ""),
                "diagnosis": f"Similarity {sim:.3f} with item {r.get('target_item_id', '')}",
                "recommendation": "Consider merging or clarifying distinction",
                "data": {"target_item_id": r.get("target_item_id", ""), "similarity": round(sim, 4)},
            })

    return feedback


def send_feedback(
    data_source_url: str, report: list[dict], *, timeout: int = 30,
) -> dict:
    """Send feedback report to data source via POST /eval/feedback."""
    try:
        resp = httpx.post(
            f"{data_source_url}/eval/feedback", json=report, timeout=timeout,
        )
        return {
            "ok": resp.status_code < 400,
            "status_code": resp.status_code,
        }
    except Exception as exc:
        _log.warning("feedback: send failed to %s: %s", data_source_url, exc)
        return {"ok": False, "error": str(exc)}
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_feedback.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/feedback.py tests/test_forge_feedback.py
git commit -m "feat(forge): add feedback protocol — weak principles, merge candidates"
```

---

## Batch 5: Meta-Eval (Double-Loop)

**PRD:** Double-loop evaluation questions whether the metrics themselves are still valid. Single-loop asks "Is variant A better?" Double-loop asks "Is F1 still the right way to measure 'better'?" Detects metric entropy collapse, F1 plateau, and suggests metric changes when the scoring distribution degenerates.

### Task 5: Metric fitness and entropy

**Files:**

- Create: `ollama_queue/forge/meta_eval.py`
- Test: `tests/test_forge_meta_eval.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_meta_eval.py
"""Tests for Forge meta-eval — double-loop metric evaluation."""
from ollama_queue.forge.meta_eval import (
    compute_metric_entropy,
    evaluate_metric_fitness,
    suggest_metric_change,
)


def test_entropy_uniform():
    """Uniform distribution across 5 classes = max entropy."""
    import math
    scores = [1, 2, 3, 4, 5]
    entropy = compute_metric_entropy(scores, n_classes=5)
    assert abs(entropy - math.log2(5)) < 0.01


def test_entropy_single_class():
    """All same score = 0 entropy."""
    scores = [3, 3, 3, 3, 3]
    assert compute_metric_entropy(scores) == 0.0


def test_entropy_empty():
    assert compute_metric_entropy([]) == 0.0


def test_entropy_two_classes():
    scores = [1, 1, 5, 5]
    entropy = compute_metric_entropy(scores)
    assert abs(entropy - 1.0) < 0.01  # log2(2) = 1.0


def test_evaluate_metric_fitness_healthy():
    result = evaluate_metric_fitness(
        f1_history=[0.5, 0.55, 0.6, 0.65, 0.7, 0.75],
        kappa_history=[0.7, 0.72, 0.71, 0.73],
        variance_history=[0.5, 0.6, 0.55],
    )
    assert result["healthy"] is True
    assert result["issues"] == []


def test_evaluate_metric_fitness_plateau():
    result = evaluate_metric_fitness(
        f1_history=[0.75, 0.75, 0.76, 0.75, 0.75, 0.76],
        kappa_history=[0.7, 0.72, 0.71, 0.73],
        variance_history=[0.5],
    )
    assert result["healthy"] is False
    assert any("plateau" in i for i in result["issues"])


def test_evaluate_metric_fitness_variance_collapsed():
    result = evaluate_metric_fitness(
        f1_history=[0.5, 0.6],
        kappa_history=[0.7],
        variance_history=[0.05],
    )
    assert any("variance" in i.lower() for i in result["issues"])


def test_evaluate_metric_fitness_kappa_unstable():
    result = evaluate_metric_fitness(
        f1_history=[0.5, 0.6],
        kappa_history=[0.3, 0.8, 0.2, 0.9],
        variance_history=[0.5],
    )
    assert any("kappa" in i.lower() for i in result["issues"])


def test_suggest_metric_change_healthy():
    scores = [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
    result = suggest_metric_change("f1", scores)
    assert result is None


def test_suggest_metric_change_collapsed():
    scores = [3, 3, 3, 3, 3, 3, 3, 3, 3, 3]
    result = suggest_metric_change("f1", scores)
    assert result is not None
    assert "collapsed" in result["suggestion"].lower()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_meta_eval.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/forge/meta_eval.py
"""Forge meta-eval — double-loop evaluation that questions the metrics.

Single-loop: "Is variant A better than variant B?"
Double-loop: "Is F1 still the right way to measure 'better'?"
Adapted from Argyris double-loop learning theory.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter


def compute_metric_entropy(scores: list[int], *, n_classes: int = 5) -> float:
    """Shannon entropy of score distribution. Max = log2(n_classes).

    Low entropy = scores concentrated in few values = metric not discriminating.
    """
    if not scores:
        return 0.0
    counts = Counter(scores)
    total = len(scores)
    entropy = 0.0
    for count in counts.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    return round(entropy, 4)


def evaluate_metric_fitness(
    f1_history: list[float],
    kappa_history: list[float],
    variance_history: list[float],
) -> dict:
    """Evaluate whether current metrics are still informative.

    Returns {healthy: bool, issues: list[str], recommendation: str | None}.
    """
    issues = []

    if len(f1_history) >= 6:
        recent_stdev = statistics.pstdev(f1_history[-6:])
        if recent_stdev < 0.02:
            issues.append(f"F1 plateau: stdev={recent_stdev:.4f} over last 6 runs")

    if variance_history and variance_history[-1] is not None:
        if variance_history[-1] < 0.1:
            issues.append(f"Score variance collapsed to {variance_history[-1]:.3f}")

    if len(kappa_history) >= 4:
        kappa_stdev = statistics.pstdev(kappa_history[-4:])
        if kappa_stdev > 0.2:
            issues.append(f"Kappa unstable: stdev={kappa_stdev:.4f} over last 4 runs")

    recommendation = None
    if len(issues) >= 2:
        recommendation = "Consider switching judge model or revising the scoring rubric"
    elif issues:
        recommendation = "Monitor closely — single issue may be transient"

    return {"healthy": len(issues) == 0, "issues": issues, "recommendation": recommendation}


def suggest_metric_change(
    current_metric: str, score_distribution: list[int], *, n_classes: int = 5,
) -> dict | None:
    """Suggest a metric change if current metric has collapsed."""
    entropy = compute_metric_entropy(score_distribution, n_classes=n_classes)
    max_entropy = math.log2(n_classes)

    if entropy > max_entropy * 0.5:
        return None

    return {
        "current_metric": current_metric,
        "entropy": entropy,
        "max_entropy": round(max_entropy, 4),
        "entropy_ratio": round(entropy / max_entropy, 4) if max_entropy > 0 else 0,
        "suggestion": "Score distribution has collapsed — consider binary scoring or changing the rubric",
    }
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_meta_eval.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add ollama_queue/forge/meta_eval.py tests/test_forge_meta_eval.py
git commit -m "feat(forge): add meta-eval — double-loop metric fitness evaluation"
```

---

## Batch 6: Phase 3 DB Tables

**PRD:** Persistence for drift snapshots, feedback log, holdout sets, and disputes. Extends `ForgeMixin` in `db/forge.py` with 4 new tables and CRUD methods.

### Task 6: Drift + feedback + holdout + dispute DB

**Files:**

- Modify: `ollama_queue/db/forge.py`
- Modify: `ollama_queue/db/schema.py`
- Test: `tests/test_forge_db_phase3.py`

**Step 1: Write the failing test**

```python
# tests/test_forge_db_phase3.py
"""Tests for Forge Phase 3 DB operations."""
import json


def test_insert_drift_snapshot(db):
    db.insert_forge_drift_snapshot(run_id=1, kappa=0.72, f1=0.81,
                                   score_variance=0.45, interpretation="stable")
    history = db.get_forge_drift_history(limit=10)
    assert len(history) == 1
    assert history[0]["interpretation"] == "stable"


def test_get_drift_history_ordered(db):
    db.insert_forge_drift_snapshot(run_id=1, kappa=0.5, f1=0.6,
                                   score_variance=0.3, interpretation="stable")
    db.insert_forge_drift_snapshot(run_id=2, kappa=0.7, f1=0.8,
                                   score_variance=0.5, interpretation="stable")
    history = db.get_forge_drift_history(limit=10)
    assert len(history) == 2
    assert history[0]["run_id"] == 2  # most recent first


def test_insert_feedback_log(db):
    log_id = db.insert_forge_feedback_log(
        run_id=1, data_source_url="http://127.0.0.1:7685",
        feedback_type="weak_principle", item_count=3,
        report_json='[{"item_id": "1"}]',
    )
    assert log_id > 0
    logs = db.get_forge_feedback_log(limit=10)
    assert len(logs) == 1
    assert logs[0]["feedback_type"] == "weak_principle"


def test_update_feedback_log_status(db):
    log_id = db.insert_forge_feedback_log(
        run_id=1, data_source_url="http://127.0.0.1:7685",
        feedback_type="merge_candidate", item_count=1,
        report_json="[]",
    )
    db.update_forge_feedback_log(log_id, status="sent", response_json='{"ok": true}')
    logs = db.get_forge_feedback_log(limit=10)
    assert logs[0]["status"] == "sent"


def test_create_holdout(db):
    holdout_id = db.create_forge_holdout(
        item_ids_json='["1","2","3"]', item_hash="abc123",
        seed=42, source_run_id=1,
    )
    assert holdout_id > 0
    h = db.get_forge_holdout(holdout_id)
    assert h["item_hash"] == "abc123"


def test_get_latest_holdout(db):
    db.create_forge_holdout(item_ids_json='["1"]', item_hash="old", seed=1, source_run_id=1)
    db.create_forge_holdout(item_ids_json='["1","2"]', item_hash="new", seed=2, source_run_id=2)
    latest = db.get_latest_forge_holdout()
    assert latest["item_hash"] == "new"


def test_get_latest_holdout_empty(db):
    assert db.get_latest_forge_holdout() is None


def test_update_holdout(db):
    hid = db.create_forge_holdout(item_ids_json='["1"]', item_hash="h", seed=1, source_run_id=1)
    db.update_forge_holdout(hid, last_checked_at="2026-03-17T12:00:00",
                            metrics_json='{"f1": 0.8}', freshness_score=0.95)
    h = db.get_forge_holdout(hid)
    assert h["freshness_score"] == 0.95


def test_insert_dispute(db):
    did = db.insert_forge_dispute(result_id=1, run_id=1, judge_score=4, oracle_score=1)
    assert did > 0
    disputes = db.get_forge_disputes(run_id=1)
    assert len(disputes) == 1


def test_update_dispute(db):
    did = db.insert_forge_dispute(result_id=1, run_id=1, judge_score=5, oracle_score=1)
    db.update_forge_dispute(did, arbiter_score=2, arbiter_reasoning="Oracle was right",
                            arbiter_model="claude-opus")
    disputes = db.get_forge_disputes(run_id=1)
    assert disputes[0]["arbiter_score"] == 2
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_forge_db_phase3.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Add to `ollama_queue/db/schema.py`:

```python
# Forge Phase 3: drift, feedback, holdout, disputes
conn.execute("""CREATE TABLE IF NOT EXISTS forge_drift_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES forge_runs(id),
    kappa REAL,
    f1 REAL,
    score_variance REAL,
    interpretation TEXT,
    created_at TEXT NOT NULL
)""")

conn.execute("""CREATE TABLE IF NOT EXISTS forge_feedback_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES forge_runs(id),
    data_source_url TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    report_json TEXT NOT NULL,
    response_json TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL
)""")

conn.execute("""CREATE TABLE IF NOT EXISTS forge_holdout_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_ids_json TEXT NOT NULL,
    item_hash TEXT NOT NULL,
    seed INTEGER NOT NULL,
    source_run_id INTEGER REFERENCES forge_runs(id),
    last_checked_at TEXT,
    last_check_metrics_json TEXT,
    freshness_score REAL,
    created_at TEXT NOT NULL
)""")

conn.execute("""CREATE TABLE IF NOT EXISTS forge_disputes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER REFERENCES forge_results(id),
    run_id INTEGER REFERENCES forge_runs(id),
    judge_score INTEGER NOT NULL,
    oracle_score INTEGER NOT NULL,
    arbiter_score INTEGER,
    arbiter_reasoning TEXT,
    arbiter_model TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL
)""")
```

Add to `ollama_queue/db/forge.py`:

```python
def insert_forge_drift_snapshot(self, *, run_id, kappa, f1, score_variance, interpretation):
    now = datetime.now(UTC).isoformat()
    with self._lock:
        conn = self._connect()
        conn.execute(
            """INSERT INTO forge_drift_snapshots
               (run_id, kappa, f1, score_variance, interpretation, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, kappa, f1, score_variance, interpretation, now),
        )
        conn.commit()

def get_forge_drift_history(self, *, limit=20):
    with self._lock:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM forge_drift_snapshots ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

def insert_forge_feedback_log(self, *, run_id, data_source_url, feedback_type,
                               item_count, report_json):
    now = datetime.now(UTC).isoformat()
    with self._lock:
        conn = self._connect()
        cur = conn.execute(
            """INSERT INTO forge_feedback_log
               (run_id, data_source_url, feedback_type, item_count, report_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, data_source_url, feedback_type, item_count, report_json, now),
        )
        conn.commit()
        return cur.lastrowid

def get_forge_feedback_log(self, *, limit=20):
    with self._lock:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM forge_feedback_log ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

def update_forge_feedback_log(self, log_id, *, status=None, response_json=None):
    updates, params = [], []
    if status is not None:
        updates.append("status = ?"); params.append(status)
    if response_json is not None:
        updates.append("response_json = ?"); params.append(response_json)
    if not updates:
        return
    params.append(log_id)
    with self._lock:
        conn = self._connect()
        conn.execute(f"UPDATE forge_feedback_log SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

def create_forge_holdout(self, *, item_ids_json, item_hash, seed, source_run_id):
    now = datetime.now(UTC).isoformat()
    with self._lock:
        conn = self._connect()
        cur = conn.execute(
            """INSERT INTO forge_holdout_sets
               (item_ids_json, item_hash, seed, source_run_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (item_ids_json, item_hash, seed, source_run_id, now),
        )
        conn.commit()
        return cur.lastrowid

def get_forge_holdout(self, holdout_id):
    with self._lock:
        conn = self._connect()
        row = conn.execute("SELECT * FROM forge_holdout_sets WHERE id = ?", (holdout_id,)).fetchone()
        return dict(row) if row else None

def get_latest_forge_holdout(self):
    with self._lock:
        conn = self._connect()
        row = conn.execute("SELECT * FROM forge_holdout_sets ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

def update_forge_holdout(self, holdout_id, *, last_checked_at=None, metrics_json=None,
                          freshness_score=None):
    updates, params = [], []
    if last_checked_at is not None:
        updates.append("last_checked_at = ?"); params.append(last_checked_at)
    if metrics_json is not None:
        updates.append("last_check_metrics_json = ?"); params.append(metrics_json)
    if freshness_score is not None:
        updates.append("freshness_score = ?"); params.append(freshness_score)
    if not updates:
        return
    params.append(holdout_id)
    with self._lock:
        conn = self._connect()
        conn.execute(f"UPDATE forge_holdout_sets SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

def insert_forge_dispute(self, *, result_id, run_id, judge_score, oracle_score):
    now = datetime.now(UTC).isoformat()
    with self._lock:
        conn = self._connect()
        cur = conn.execute(
            """INSERT INTO forge_disputes
               (result_id, run_id, judge_score, oracle_score, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (result_id, run_id, judge_score, oracle_score, now),
        )
        conn.commit()
        return cur.lastrowid

def get_forge_disputes(self, *, run_id):
    with self._lock:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM forge_disputes WHERE run_id = ? ORDER BY id", (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

def update_forge_dispute(self, dispute_id, *, arbiter_score, arbiter_reasoning, arbiter_model):
    now = datetime.now(UTC).isoformat()
    with self._lock:
        conn = self._connect()
        conn.execute(
            """UPDATE forge_disputes SET arbiter_score=?, arbiter_reasoning=?,
               arbiter_model=?, resolved_at=? WHERE id=?""",
            (arbiter_score, arbiter_reasoning, arbiter_model, now, dispute_id),
        )
        conn.commit()
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_forge_db_phase3.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add ollama_queue/db/forge.py ollama_queue/db/schema.py tests/test_forge_db_phase3.py
git commit -m "feat(forge): add Phase 3 DB tables — drift, feedback, holdout, disputes"
```

---

## Batch 7: API Routes

**PRD:** REST endpoints for drift history, feedback log, holdout checks, and Phase 3 settings.

### Task 7: Feedback log endpoints

**Files:**

- Create: `ollama_queue/api/forge_feedback.py`
- Modify: `ollama_queue/api/__init__.py`
- Test: `tests/test_api_forge_feedback.py`

**Step 1: Write the failing test**

```python
# tests/test_api_forge_feedback.py
"""Tests for Forge feedback API endpoints."""
import pytest
from fastapi.testclient import TestClient
from ollama_queue.db import Database
from ollama_queue.app import create_app


@pytest.fixture
def client():
    db = Database(":memory:")
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


def test_get_feedback_log_empty(client):
    c, _ = client
    resp = c.get("/api/forge/feedback")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_feedback_log_with_entries(client):
    c, db = client
    db.insert_forge_feedback_log(
        run_id=1, data_source_url="http://127.0.0.1:7685",
        feedback_type="weak_principle", item_count=2, report_json="[]",
    )
    resp = c.get("/api/forge/feedback")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_drift_history(client):
    c, db = client
    db.insert_forge_drift_snapshot(run_id=1, kappa=0.7, f1=0.8,
                                   score_variance=0.5, interpretation="stable")
    resp = c.get("/api/forge/drift")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["interpretation"] == "stable"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_forge_feedback.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/api/forge_feedback.py
"""Forge feedback + drift API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query

import ollama_queue.api as _api

router = APIRouter(tags=["forge"])


@router.get("/api/forge/feedback")
def get_feedback_log(limit: int = Query(20, ge=1, le=100)):
    return _api.db.get_forge_feedback_log(limit=limit)


@router.get("/api/forge/drift")
def get_drift_history(limit: int = Query(20, ge=1, le=100)):
    return _api.db.get_forge_drift_history(limit=limit)
```

Wire in `api/__init__.py`:

```python
from ollama_queue.api import forge_feedback
app.include_router(forge_feedback.router)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_forge_feedback.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add ollama_queue/api/forge_feedback.py ollama_queue/api/__init__.py tests/test_api_forge_feedback.py
git commit -m "feat(forge): add feedback + drift API endpoints"
```

---

### Task 8: Holdout check endpoints

**Files:**

- Create: `ollama_queue/api/forge_holdout.py`
- Modify: `ollama_queue/api/__init__.py`
- Test: `tests/test_api_forge_holdout.py`

**Step 1: Write the failing test**

```python
# tests/test_api_forge_holdout.py
"""Tests for Forge holdout API endpoints."""
import pytest
from fastapi.testclient import TestClient
from ollama_queue.db import Database
from ollama_queue.app import create_app


@pytest.fixture
def client():
    db = Database(":memory:")
    db.initialize()
    app = create_app(db)
    return TestClient(app), db


def test_get_holdout_none(client):
    c, _ = client
    resp = c.get("/api/forge/holdout")
    assert resp.status_code == 404


def test_get_holdout_exists(client):
    c, db = client
    db.create_forge_holdout(
        item_ids_json='["1","2","3"]', item_hash="abc123",
        seed=42, source_run_id=1,
    )
    resp = c.get("/api/forge/holdout")
    assert resp.status_code == 200
    assert resp.json()["item_hash"] == "abc123"


def test_get_disputes(client):
    c, db = client
    db.insert_forge_dispute(result_id=1, run_id=1, judge_score=5, oracle_score=1)
    resp = c.get("/api/forge/disputes?run_id=1")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_forge_holdout.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# ollama_queue/api/forge_holdout.py
"""Forge holdout + disputes API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

import ollama_queue.api as _api

router = APIRouter(tags=["forge"])


@router.get("/api/forge/holdout")
def get_holdout():
    holdout = _api.db.get_latest_forge_holdout()
    if holdout is None:
        raise HTTPException(404, detail="No holdout set exists")
    return holdout


@router.get("/api/forge/disputes")
def get_disputes(run_id: int = Query(...)):
    return _api.db.get_forge_disputes(run_id=run_id)
```

Wire in `api/__init__.py`:

```python
from ollama_queue.api import forge_holdout
app.include_router(forge_holdout.router)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_forge_holdout.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add ollama_queue/api/forge_holdout.py ollama_queue/api/__init__.py tests/test_api_forge_holdout.py
git commit -m "feat(forge): add holdout + disputes API endpoints"
```

---

## Batch 8: Wiring + Settings

**PRD:** Final wiring: update `forge/__init__.py` re-exports for Phase 3, seed new settings, add Phase 3 setting keys.

### Task 9: Update re-exports and seed Phase 3 settings

**Files:**

- Modify: `ollama_queue/forge/__init__.py`
- Modify: `ollama_queue/forge/settings.py`
- Modify: `ollama_queue/db/schema.py`

**Step 1: Add Phase 3 keys to `forge/settings.py`**

Append to `FORGE_DEFAULTS`:

```python
# Phase 3: Learn
"forge.arbiter_enabled": False,
"forge.arbiter_provider": "claude",
"forge.arbiter_model": "claude-opus-4-6",
"forge.arbiter_dispute_threshold": 2,
"forge.feedback_enabled": False,
"forge.drift_threshold": 0.05,
"forge.drift_window": 6,
"forge.holdout_fraction": 0.2,
```

**Step 2: Update `forge/__init__.py` re-exports**

Add Phase 3 modules:

```python
from ollama_queue.forge.arbiter import select_disputes, build_arbiter_prompt, parse_arbiter_response
from ollama_queue.forge.drift import mean_of_halves_trend, detect_drift, compare_judge_oracle_trend
from ollama_queue.forge.feedback import FeedbackType, build_feedback_report, send_feedback
from ollama_queue.forge.holdout import create_holdout_snapshot, check_holdout_freshness
from ollama_queue.forge.meta_eval import compute_metric_entropy, evaluate_metric_fitness, suggest_metric_change
```

And append to `__all__`.

**Step 3: Seed Phase 3 defaults in `db/schema.py`**

Add to the Forge settings seed block:

```python
# Forge Phase 3 defaults
("forge.arbiter_enabled", "false"),
("forge.arbiter_provider", "claude"),
("forge.arbiter_model", "claude-opus-4-6"),
("forge.arbiter_dispute_threshold", "2"),
("forge.feedback_enabled", "false"),
("forge.drift_threshold", "0.05"),
("forge.drift_window", "6"),
("forge.holdout_fraction", "0.2"),
```

**Step 4: Run full test suite**

Run: `python -m pytest --timeout=120 -x -q`
Expected: All tests pass.

**Step 5: Commit**

```bash
git add ollama_queue/forge/__init__.py ollama_queue/forge/settings.py ollama_queue/db/schema.py
git commit -m "feat(forge): Phase 3 wiring — re-exports, settings, schema seeds"
```

---

### Task 10: Verify all Phase 3 imports and integration

**Step 1: Verify all modules importable**

```python
from ollama_queue.forge import (
    detect_drift, compare_judge_oracle_trend, mean_of_halves_trend,
    create_holdout_snapshot, check_holdout_freshness,
    select_disputes, build_arbiter_prompt, parse_arbiter_response,
    FeedbackType, build_feedback_report, send_feedback,
    compute_metric_entropy, evaluate_metric_fitness, suggest_metric_change,
)
print("All Phase 3 forge imports OK")
```

**Step 2: Verify new API endpoints registered**

```bash
python -c "
from ollama_queue.db import Database
from ollama_queue.app import create_app
db = Database(':memory:')
db.initialize()
app = create_app(db)
routes = [r.path for r in app.routes if hasattr(r, 'path')]
p3_routes = [r for r in routes if any(x in r for x in ['/drift', '/feedback', '/holdout', '/disputes'])]
print(f'Phase 3 routes: {len(p3_routes)}')
for r in sorted(p3_routes):
    print(f'  {r}')
assert len(p3_routes) >= 4
print('Phase 3 API wiring OK')
"
```

**Step 3: Run full test suite**

Run: `python -m pytest --timeout=120 -q`
Expected: All tests pass, 0 failures.

**Step 4: Final commit**

```bash
git add ollama_queue/forge/__init__.py
git commit -m "feat(forge): Phase 3 complete — drift, arbiter, feedback, meta-eval"
```

---

## Summary

| Batch | Tasks | Tests | Modules Created/Modified |
|-------|-------|-------|--------------------------|
| 1. Drift | 1 | ~12 | drift.py |
| 2. Holdout | 2 | ~5 | holdout.py |
| 3. Arbiter | 3 | ~6 | arbiter.py |
| 4. Feedback | 4 | ~5 | feedback.py |
| 5. Meta-Eval | 5 | ~10 | meta_eval.py |
| 6. DB Tables | 6 | ~10 | db/forge.py (+tables), schema.py |
| 7. API Routes | 7-8 | ~6 | api/forge_feedback.py, api/forge_holdout.py |
| 8. Wiring | 9-10 | ~2 | __init__.py, settings.py, schema.py |
| **Total** | **10 tasks** | **~56 tests** | **5 new + 4 modified files** |

**Dependency graph:**

```
drift.py                        (Batch 1: standalone math)
holdout.py                      (Batch 2: standalone)
arbiter.py ← judge.py           (Batch 3: reuses judge parser)
feedback.py                     (Batch 4: standalone + httpx)
meta_eval.py                    (Batch 5: standalone math)
db/forge.py ← schema.py         (Batch 6: all tables)
api/forge_*.py ← db             (Batch 7: needs DB CRUD)
```

Batches 1, 2, 3, 4, and 5 can ALL run in parallel (no cross-dependencies).

---

## Cross-Phase Summary

| Phase | Tasks | Tests | New Files | Focus |
|-------|-------|-------|-----------|-------|
| Phase 1 — Calibrate | 14 | ~80 | 12 | Judge trustworthiness, oracle validation, core engine |
| Phase 2 — Evolve | 14 | ~78 | 10 | MAP-Elites, Thompson Sampling, evolution operators |
| Phase 3 — Learn | 10 | ~56 | 9 | Drift, arbiter, feedback, meta-eval |
| **Total** | **38 tasks** | **~214 tests** | **31 files** | Full Forge v2 engine |

Each phase is independently deployable. Phase 1 alone fixes the immediate crisis (F1=0.0).
