# eval/ LLM Guide

## What You Must Know

The eval pipeline A/B tests prompt variants for principle extraction. It generates principles from a lessons-db data source, scores them with an LLM judge, and optionally auto-promotes winners to production. Four phase modules are orchestrated by `engine.py`.

## Phase Flow

```
engine.py:run_eval_session()
  1. generate.py:run_eval_generate()  -- build prompts, call Ollama, store principles
  2. judge.py:run_eval_judge()        -- score principles against transfer targets
  3. promote.py:generate_eval_analysis()  -- Ollama-powered markdown analysis
  4. promote.py:check_auto_promote()      -- 3-gate auto-promotion (never raises)
  5. engine.py:compute_run_analysis()     -- calls analysis.py pure functions, stores analysis_json
```

All DB helpers live in `engine.py`: `get_eval_run()`, `create_eval_run()`, `update_eval_run()`, `insert_eval_result()`. Metric computation lives in `metrics.py`: `compute_metrics()`, `render_report()`. Phase modules import from `engine.py`.

## Cooperative Cancellation

Both `run_eval_generate` and `run_eval_judge` run in background threads. **Every loop iteration must re-check the run status:**

```python
run = _eng.get_eval_run(db, run_id)
if not run or run["status"] in ("failed", "cancelled"):
    return  # exit immediately
```

This handles: user cancellation, daemon restart (orphan recovery marks runs failed), and cancellation during sleep (re-check after `_sleep_fn()` wakes).

## analysis.py Is Pure

This is the strict purity boundary:

- No `import` of `db`, `api`, or `httpx`
- No side effects, no logging with state
- Takes `list[dict]`, returns `list[dict]`
- All DB access for analysis happens in `engine.py:compute_run_analysis()`

Five public functions: `compute_per_item_breakdown()`, `extract_failure_cases()`, `bootstrap_f1_ci()`, `compute_variant_stability()`, `describe_config_diff()`.

## Falsy-Zero: score=0 Is Valid

```python
# WRONG -- drops valid zero scores
score = r.get("score_transfer") or r.get("effective_score_transfer")

# RIGHT -- explicit None check
s = r.get("score_transfer")
score = s if s is not None else r.get("effective_score_transfer")
```

`_get_score()` and `_is_positive()` in `analysis.py` use this pattern. Apply everywhere scores are read.

## Proxy Calls to Self

Generation and judging call `POST http://127.0.0.1:7683/api/generate` (the queue's own proxy) to serialize Ollama access. Never call Ollama directly at `:11434` from eval code.

If a queue job calls back through the proxy, it deadlocks (daemon holds the sentinel for the running job). Eval sessions run in background threads, not queue jobs, specifically to avoid this.

## Auto-Promote Gates

`check_auto_promote()` wraps `_check_auto_promote_inner()` in `try/except` and never raises. Three gates:

1. Winner F1 >= `eval.f1_threshold`
2. Winner F1 > production F1 + `eval.auto_promote_min_improvement`
3. `error_budget_used` <= `eval.error_budget`

Optional: stability window gate. Auto-promote is off by default (`eval.auto_promote = false`).

`do_promote_eval_run()` is the shared core -- both the API endpoint and auto-promote call it. Sets `is_production=1` on winner, clears all others to 0, in one `db._lock` block.

## Key Gotchas

- `create_eval_run()` uses `status='queued'`, never `'pending'`. Orphan recovery kills pending runs.
- `repeat_eval_run` must start a `threading.Thread` -- the DB row alone does nothing; the daemon does not poll eval_runs.
- `judge_rerun` must copy `gen_results` from the source run before judging, or the judge has nothing to score (F1=0).
- `generate_eval_analysis()` never raises -- wraps in `try/except` and logs.
- `completed_at` must be set on `failed`, `cancelled`, and `complete` transitions.
- `eval.positive_threshold` (1-5, default 3) controls TP/FP/FN classification.
- `eval.analysis_model` defaults to empty string (falls back to judge model).

## Adding a New Judge Mode

1. Add the mode name to the `CHECK` constraint in `schema.py` (`eval_runs.judge_mode`)
2. Implement scoring logic in `judge.py` within `run_eval_judge()`
3. Add prompt builder in `judge.py` (e.g., `build_<mode>_prompt()`)
4. Handle the mode in `parse_judge_response()` response parsing
5. Add inline description in the SPA Settings view judge mode selector

## Testing

```bash
pytest tests/test_eval_engine.py -x     # 92 tests: session orchestration
pytest tests/test_eval_analysis.py -x   # pure analysis functions
pytest tests/test_api_eval_runs.py -x   # 45 tests: run endpoints
pytest tests/test_api_eval_variants.py -x
pytest tests/test_api_eval_settings.py -x
```

## Dependencies

- **Depends on**: db/ (via engine.py helpers), api/proxy (HTTP calls for Ollama)
- **Depended on by**: api/eval_runs.py, api/eval_settings.py, api/eval_variants.py, api/eval_trends.py
