# models/ LLM Guide

## What You Must Know

Four components manage Ollama models and estimate job performance. `OllamaModels` is the primary client (VRAM estimation, model classification, cache). Three estimators provide duration predictions at different accuracy levels.

## Client Cache (`_list_local_cache`)

`OllamaModels.list_local()` caches results for 60s at the **class level** (not instance level).

**Test teardown requirement**: Any test that mocks `list_local()` must call `OllamaModels._invalidate_list_cache()` in teardown. Without this, the cached mock result bleeds into subsequent tests.

```python
def teardown_method(self):
    OllamaModels._invalidate_list_cache()
```

## VRAM Estimation Cascade

`estimate_vram_mb(model, db)` checks three sources in order:

1. **Observed VRAM delta** from `model_registry.vram_observed_mb` (written after successful jobs)
2. **Registry size_bytes** with `vram_safety_factor` (default 1.3x)
3. **Regex heuristic** from model name (e.g., `7b` -> 4.5GB Q4 quantized)
4. **Fallback**: 4.0GB

The regex (`_PARAM_TO_VRAM`) maps common sizes: `0.5b`->0.5GB, `1b`->1.0GB, `3b`->2.0GB, `7b`->4.5GB, `8b`->5.0GB, `14b`->9.0GB, `32b`->20.0GB, `70b`->40.0GB.

## Model Classification

First-match keyword rules in `_PROFILE_RULES` and `_TYPE_RULES`:

| Profile | Keywords | Behavior |
|---------|----------|----------|
| `embed` | embed, nomic, mxbai, bge-m3, all-minilm | 4 concurrent slots, no VRAM gate |
| `heavy` | 70b, 34b, 32b, :671b, deepseek-r1:14... | Must run alone |
| `ollama` | (default) | Standard serialization |

`classify(model)` returns `(resource_profile, type_tag)`. The daemon's `_can_admit()` uses the profile for concurrency decisions.

## Estimator Hierarchy

### DurationEstimator (estimator.py)
Simple rolling average. 3-tier lookup: DB rolling avg > model defaults > 600s generic. Used for queue ETAs and SJF scheduling.

- `estimate(source, model)` -> seconds
- `estimate_with_variance(source, model)` -> (mean, cv_squared) for risk-adjusted SJF
- `queue_etas(jobs)` -> per-job ETA offsets

### RuntimeEstimator (runtime_estimator.py)
Bayesian log-normal with precision-weighted posterior. 4-tier hierarchy:
1. (model, command) -- most specific
2. model -- aggregated across commands
3. resource_profile -- aggregated across models
4. global -- all data

Returns `Estimate` dataclass: `warmup_mean`, `generation_mean`, `total_mean`, `confidence`, `uncertainty_low`, `uncertainty_high`.

**Key formula**: `post_precision = prior_precision + sample_precision`, `post_std = sqrt(1/post_precision)`. The old pseudo-count formula over-estimated uncertainty.

### PerformanceCurve (performance_curve.py)
Log-linear OLS regression: `log(tok_per_min)` vs `log(size_gb)`. Predicts tok/s and warmup for never-run models.

**State reset requirement**: `fit()` must set all parameters to `None` at entry. Without this, stale parameters from a previous fit survive if the new fit has insufficient data.

## Adding a New Model Feature

1. If it's a property of the model itself (e.g., quantization level), add to `model_registry` table (see db/ LLM.md for column migration)
2. If it's a runtime measurement, add to `job_metrics` table
3. If it affects scheduling, update `_can_admit()` in `daemon/executor.py`
4. If it affects estimation, add to the appropriate estimator
5. Update `classify()` keyword rules if the model needs special handling

## Testing

```bash
pytest tests/test_models.py -x              # client: list, classify, VRAM
pytest tests/test_estimator.py -x           # duration estimator
pytest tests/test_runtime_estimator.py -x   # Bayesian estimator
pytest tests/test_performance_curve.py -x   # regression curve
```

Model tests mock `subprocess.run` (for `ollama list`) and HTTP calls to Ollama API. Always invalidate `_list_local_cache` in teardown.

## Dependencies

- **Depends on**: db/ (model_registry, job_metrics, duration_history)
- **Depended on by**: daemon/ (admission, stall detection), api/ (ETAs, model list), scheduling/ (VRAM-aware slot scoring)
