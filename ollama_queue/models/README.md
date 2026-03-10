# models/ — Ollama Model Management + Performance Estimation

## Purpose

Knows which AI models are installed locally, how much VRAM each needs, and how
long jobs will take. Provides the admission gate, queue ETAs, and performance
analytics with estimates that improve as the system observes real runs.

## Architecture

Four components form a hierarchy of estimation accuracy:

```
client.py              -- Live: Ollama API client, VRAM estimation, model classification
estimator.py           -- Simple: rolling average + model defaults for queue ETAs
runtime_estimator.py   -- Bayesian: hierarchical log-normal with 4-tier fallback
performance_curve.py   -- Cross-model: log-linear regression from empirical data
```

`OllamaModels` is the primary class, used throughout the codebase for model
queries and VRAM estimation. The estimators are used by the daemon (admission),
scheduler (slot scoring), and API (ETAs, performance charts).

## Modules

| File | Key Exports | Role |
|------|-------------|------|
| `__init__.py` | `OllamaModels` | Re-export |
| `client.py` | `OllamaModels` | `list_local()` (60s TTL cache), `get_loaded()` (Ollama /api/ps), `classify(model)` (resource_profile + type_tag via keyword rules), `estimate_vram_mb(model, db)` (observed > registry > name-regex heuristic), `pull()` / `cancel_pull()` / `get_pull_status()`, `record_observed_vram()` |
| `estimator.py` | `DurationEstimator` | `estimate(source, model)`: 3-tier lookup (DB rolling avg > model defaults > 600s generic). `estimate_with_variance(source, model)`: returns (mean, cv_squared) for SJF risk-adjusted sorting. `queue_etas(jobs)`: per-job ETA offsets for the pending queue. |
| `runtime_estimator.py` | `RuntimeEstimator`, `Estimate` | Bayesian log-normal with precision-weighted posterior. 4-tier hierarchy: (model, command) > model > resource profile > global. Returns `Estimate` dataclass with warmup_mean, generation_mean, total_mean, confidence, uncertainty bounds. |
| `performance_curve.py` | `PerformanceCurve` | `fit(model_stats)`: log-linear OLS regression on (log(size_gb), log(tok_per_min)). `predict_tok_per_min(size_gb)` and `predict_warmup(size_gb)` for never-run models. `get_curve_data()` returns fitted parameters + data points for the dashboard chart. |

## Key Patterns

- **VRAM estimation cascade**: `estimate_vram_mb()` checks three sources in order:
  (1) observed VRAM delta from `model_registry` (written after successful jobs),
  (2) registry `size_bytes` with safety factor, (3) regex extraction of param count
  from model name (e.g. `7b` -> 4.5GB Q4 heuristic). Falls back to 4GB.

- **Classification by keyword rules**: `_PROFILE_RULES` and `_TYPE_RULES` use
  first-match semantics. Embed models (nomic, mxbai, bge) get `resource_profile=embed`
  (4 concurrent slots, no VRAM gate). Heavy models (70B+) get `resource_profile=heavy`
  (must run alone).

- **Cache invalidation**: `_list_local_cache` is class-level with 60s TTL. Tests
  that mock `list_local()` must call `OllamaModels._invalidate_list_cache()` in
  teardown to prevent bleed.

- **`performance_curve.fit()` resets state at entry**: All fitted parameters are set
  to `None` before fitting. Without this, stale parameters from a previous fit
  survive if the new fit has insufficient data.

- **Precision-weighted posterior** (`RuntimeEstimator`): Uses
  `post_precision = prior_precision + sample_precision` for correct Bayesian update.
  The old pseudo-count formula over-estimated uncertainty when sample data was abundant.

## Dependencies

**Depends on**: `db/` (model_registry, job_metrics, duration_history queries)
**Depended on by**: `daemon/` (admission, stall detection), `api/` (ETAs, model list, metrics), `scheduling/` (VRAM-aware slot scoring)
