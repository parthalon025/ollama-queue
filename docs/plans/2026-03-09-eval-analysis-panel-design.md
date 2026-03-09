# Eval Analysis Panel — Design Document

**Date:** 2026-03-09
**Goal:** Add 5 analysis features to the eval pipeline UI — per-item breakdown, failure case drill-down, bootstrap confidence intervals, cross-run stability, and config diff — as platform features that work for any project data source.

**Architecture:** Backend-computed analysis stored per-run as JSON alongside existing AI prose. Pure analysis module (`eval_analysis.py`) with no DB/HTTP dependencies. Cross-run features (stability, config diff) computed live via dedicated API endpoints. Frontend distributes features across existing UI locations (RunRow L2, ResultsTable L3, Variants tab) rather than adding new panels.

**Multi-project principle:** Analysis operates on generic `(source_item, target_item, is_same_cluster, score)` tuples. No lessons-db-specific assumptions. Projects without clusters get graceful degradation. Positive threshold is configurable per eval settings.

---

## 1. Backend: `eval_analysis.py` (new module)

Pure functions, ~200 lines. No DB, no HTTP, no side effects.

### Functions

```python
def compute_per_item_breakdown(
    scored_rows: list[dict],
    positive_threshold: int = 3,
) -> list[dict]:
    """Group scored rows by (variant, source_item_id), compute TP/FP/FN/F1.
    Returns sorted worst-first by F1. Skips items with no same-cluster pairs."""

def extract_failure_cases(
    scored_rows: list[dict],
    positive_threshold: int = 3,
) -> list[dict]:
    """Filter to FP and FN only. Returns [{type, variant, source_item_id,
    source_item_title, target_item_id, target_item_title, source_cluster,
    target_cluster, score_transfer, principle}]."""

def bootstrap_f1_ci(
    scored_rows: list[dict],
    variant: str,
    positive_threshold: int = 3,
    n_bootstrap: int = 500,
    ci: float = 0.95,
    seed: int | None = None,
) -> dict | None:
    """Bootstrap CI for variant F1. Returns {low, mid, high} or None if <10 pairs."""

def compute_variant_stability(
    run_metrics: list[dict],
    threshold: float = 0.10,
) -> dict:
    """Cross-run stdev per variant. Input: [{variant, f1}, ...] from recent runs.
    Returns {variant: {mean, stdev, n_runs, stable, f1s}}."""

def describe_config_diff(
    config_a: dict,
    config_b: dict,
) -> list[str]:
    """Human-readable config differences. Returns list of change descriptions."""
```

### Classification logic

- `score_transfer >= positive_threshold` → positive match
- `is_same_cluster AND positive` → TP
- `is_same_cluster AND NOT positive` → FN
- `NOT is_same_cluster AND positive` → FP
- `NOT is_same_cluster AND NOT positive` → TN (not tracked)

If `is_same_cluster` is NULL for all rows, return `{"status": "no_cluster_data"}` for breakdown and CI. Failure cases still work (show lowest-scoring pairs regardless).

---

## 2. Schema Changes

### eval_results — 2 new columns

```sql
ALTER TABLE eval_results ADD COLUMN source_item_title TEXT;
ALTER TABLE eval_results ADD COLUMN target_item_title TEXT;
```

Populated during `run_eval_generate()` (source title) and `run_eval_judge()` (target title) from the data source response. Old runs have NULL titles — UI shows item ID as fallback.

### eval_runs — 1 new column

```sql
ALTER TABLE eval_runs ADD COLUMN analysis_json TEXT;
```

Stores structured analysis (per_item, failures, CI) as JSON. Separate from `analysis_md` (AI prose). Both nullable.

### New index

```sql
CREATE INDEX IF NOT EXISTS idx_eval_results_run_variant
    ON eval_results(run_id, variant);
```

Speeds up per-variant metric queries and analysis computation.

### Migration

All changes use existing `_add_column_if_missing()` pattern (idempotent). Index uses `CREATE INDEX IF NOT EXISTS`. Safe on live DB — no data loss risk.

---

## 3. Pipeline Integration

```
run_eval_session():
  run_eval_generate()        # populates source_item_title
  run_eval_judge()           # populates target_item_title
  compute_run_analysis()     # NEW → analysis_json
  generate_eval_analysis()   # existing → analysis_md
  check_auto_promote()
```

`compute_run_analysis()` and `generate_eval_analysis()` are **independent** — each wrapped in try/except, neither blocks the other. Both catch all exceptions and log (same pattern as `check_auto_promote()`).

### analysis_json schema

```json
{
  "computed_at": "2026-03-09T15:30:00Z",
  "positive_threshold": 3,
  "per_item": [
    {"source_item_id": "42", "source_item_title": "Silent failure in...",
     "variant": "A", "cluster_id": "c1",
     "recall": 0.75, "precision": 1.0, "f1": 0.857,
     "tp": 3, "fn": 1, "fp": 0, "total_pairs": 5}
  ],
  "failures": [
    {"type": "false_positive", "variant": "A",
     "source_item_id": "42", "source_item_title": "Silent failure...",
     "target_item_id": "99", "target_item_title": "Race condition...",
     "source_cluster": "c1", "target_cluster": "c3",
     "score_transfer": 4, "principle": "Always log before..."}
  ],
  "confidence_intervals": {
    "A": {"low": 0.61, "mid": 0.72, "high": 0.83, "n_pairs": 42},
    "B": {"low": 0.55, "mid": 0.68, "high": 0.79, "n_pairs": 38}
  }
}
```

If no cluster data: `{"status": "no_cluster_data", "computed_at": "..."}`.

---

## 4. API Endpoints (4 new)

### Run-scoped (stored data)

**`GET /api/eval/runs/{id}/analysis`**
- Returns stored `analysis_json` or `{"status": "not_computed"}` if NULL
- No computation — just reads from DB

**`POST /api/eval/runs/{id}/reanalyze`**
- Recomputes `analysis_json` only (not AI prose)
- Reads scored pairs from eval_results, runs analysis functions, stores result
- Returns `{"ok": true}` (synchronous — analysis is fast, <2s)

### Platform-scoped (computed live)

**`GET /api/eval/variants/stability?data_source=<url>`**
- Queries last 20 completed runs filtered by data_source_url
- Groups by variant, computes stdev(F1)
- Returns `{variant: {mean, stdev, n_runs, stable, f1s}}`
- `data_source` param defaults to current eval settings value

**`GET /api/eval/variants/{a}/diff/{b}`**
- Reads configs from eval_variants table
- Returns `{"changes": ["model: qwen2.5:7b → qwen3:14b", ...]}`
- 404 if either variant not found; empty list if identical

---

## 5. Frontend: Feature Distribution

### RunRow L2 — Per-run analysis

**CI inline in metrics table:**
- Add ±range after F1 value: `"72% ±8"` in parenthetical
- Source: `analysis_json.confidence_intervals[variant]`
- Fallback: show F1 without CI if analysis_json is NULL

**Per-item breakdown panel** (new collapsible, between scorer info and AI analysis):
- Title: "Item Difficulty"
- Shows top 5 worst items by F1, sorted ascending
- Columns: Item (title or ID), F1 (with inline bar), TP, FN, FP
- "Show all N items" expander if >5
- Source: `analysis_json.per_item`
- Layman comment: Shows which test items were hardest for this variant to handle

**Analysis status indicator:**
- If `analysis_json` is null and status is complete: show "Analysis not computed" + "Compute" button
- If computing: show spinner
- If computed: show panels normally

### RunRow L3 — Failure case drill-down

**Filter tabs on ResultsTable:**
- Row of preset buttons above the table: All | TP | TN | FP | FN
- Counts in parentheses: `FP (7)`
- Clicking a tab filters the paginated results
- "FP" = `is_same_cluster = 0 AND score_transfer >= threshold`
- "FN" = `is_same_cluster = 1 AND score_transfer < threshold`
- Source: existing `GET /api/eval/runs/{id}/results` endpoint with new `?filter=fp|fn|tp|tn` query param

**Failure row expansion:**
- Click any result row to expand inline
- Shows: principle text, judge reasoning, source item title, target item title
- No modal, no navigation (Braintrust pattern)

### Variants Tab — Cross-run analysis

**Stability in VariantStabilityTable:**
- New columns: Stdev, Stable (badge: ✓/✗), N Runs
- Source: `GET /api/eval/variants/stability`
- Replaces current placeholder ("coming in a future update")

**Config diff button:**
- "Compare" button in variant toolbar
- Select two variants → inline diff panel shows list of changes
- Source: `GET /api/eval/variants/{a}/diff/{b}`
- Changes displayed as: `"temperature: 0.6 → 0.8 (more creative)"`

---

## 6. Edge Cases & Mitigations

| Edge Case | Mitigation |
|-----------|------------|
| 0 same-cluster pairs | Return `null` for recall/F1; show "No same-cluster data" |
| <10 pairs for bootstrap | Skip CI; show "Insufficient data" |
| Division by zero (recall+precision=0) | Return F1=0.0 |
| Old runs without item titles | Show item ID with "(title unavailable)" |
| Old runs without analysis_json | "Not computed" + Compute button |
| `positive_threshold` changed between runs | Store threshold in analysis_json; display uses stored value |
| Data source down during stability query | Filter to runs that already have metrics; no live data source call needed |
| Deleted variant in config diff | Return 404 with clear message |
| Concurrent reanalyze clicks | Idempotent; last write wins |
| No cluster data (multi-project) | Return `{"status": "no_cluster_data"}` for breakdown/CI; failures show low-score pairs |
| 10K+ scored pairs | Paginate per-item breakdown (top 10 worst); cap bootstrap at 500 resamples |

---

## 7. Existing Bug Fixes (discovered during audit)

These should be fixed as part of this work since they affect the analysis features:

1. **`_compute_f1_block` division by zero** — guard when recall + precision = 0
2. **ResultsTable infinite "Loading…" on API error** — add error state + retry
3. **ConfusionMatrix error boundary** — catch API failures without collapsing L2
4. **Per-cluster breakdown F1=1.0 on 0 items** — skip empty clusters
5. **Missing `eval_results(run_id, variant)` index** — add for query performance
6. **ResultsTable has no sorting/filtering** — prerequisite for FP/FN filter tabs

---

## 8. Testing Strategy

- **`eval_analysis.py`**: ~25 unit tests (pure functions with fixture data)
  - Per-item breakdown: happy path, empty input, no cluster data, all same-cluster, all diff-cluster, threshold edge
  - Failure cases: FP/FN classification, empty, no failures
  - Bootstrap CI: happy path, <10 pairs (returns null), seed determinism, single variant
  - Stability: happy path, single run (stdev=0), mixed data sources
  - Config diff: identical, model change, temperature change, multiple changes, missing variant

- **API endpoints**: ~12 integration tests
  - GET analysis (null, computed, not_found)
  - POST reanalyze (happy, already computed, run not found)
  - GET stability (happy, no runs, data source filter)
  - GET diff (happy, same config, missing variant)

- **Pipeline integration**: ~4 tests
  - compute_run_analysis called after judge and stores result
  - compute_run_analysis failure doesn't block AI prose
  - Item titles populated during generate/judge
  - Schema migration is idempotent

- **Frontend**: Manual verification (no JS test framework in place)
  - CI renders inline in metrics table
  - Per-item breakdown panel expands/collapses
  - FP/FN filter tabs work on ResultsTable
  - Stability columns appear in VariantStabilityTable
  - Config diff modal shows changes
