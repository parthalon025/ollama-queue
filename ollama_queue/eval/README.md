# eval/ — Prompt Evaluation Pipeline

## Purpose

A/B testing infrastructure for prompt variants. Generates principles from a
lessons-db data source using configurable prompt templates and models, then scores
them with an LLM judge. Tracks F1/precision/recall across runs, supports
auto-promotion of winning variants to production.

## Architecture

The pipeline has four phases, each in its own module:

```
engine.py (orchestrator)
    |
    +---> generate.py  -- Phase 1: Build prompts, call Ollama, store principles
    +---> judge.py     -- Phase 2: Score principles against transfer targets
    +---> promote.py   -- Phase 3: Auto-promote winners, generate Ollama analysis
    +---> analysis.py  -- Phase 4: Pure analysis (no DB/HTTP) for structured metrics
```

`engine.py` contains all DB helper functions (`get_eval_run`, `create_eval_run`,
`update_eval_run`, `insert_eval_result`, `compute_run_analysis`) and the
`run_eval_session()` orchestrator. `metrics.py` holds pure metric computation
(`compute_metrics`, `render_report`). The phase modules import from `engine.py`
for shared infrastructure.

## Modules

| File | Key Exports | Role |
|------|-------------|------|
| `__init__.py` | All public names | Re-exports for `from ollama_queue.eval import X` |
| `engine.py` | `run_eval_session`, `create_eval_run`, `get_eval_run`, `update_eval_run`, `insert_eval_result`, `compute_run_analysis` | Session orchestrator + all DB helpers |
| `metrics.py` | `compute_metrics`, `render_report`, `compute_tournament_metrics`, `compute_bayesian_metrics` | Pure metric computation: F1/precision/recall, tournament/Bayesian aggregates, report rendering |
| `generate.py` | `run_eval_generate`, `build_generation_prompt` | Prompt construction and generation loop |
| `judge.py` | `run_eval_judge`, `build_judge_prompt`, `parse_judge_response`, `build_analysis_prompt` | LLM scoring with 4 judge modes (rubric, binary, tournament, bayesian) |
| `promote.py` | `do_promote_eval_run`, `check_auto_promote`, `generate_eval_analysis` | Winner resolution, 3-gate auto-promote, Ollama-powered analysis |
| `analysis.py` | `compute_per_item_breakdown`, `extract_failure_cases`, `bootstrap_f1_ci`, `compute_variant_stability`, `describe_config_diff` | Pure functions (no DB, no HTTP, no side effects) |

## Key Patterns

- **Cooperative cancellation**: Both `run_eval_generate` and `run_eval_judge` run in
  background threads. Each loop iteration re-fetches the run row from the DB and
  returns immediately if the status is `failed`, `cancelled`, or the row is deleted.
  This handles cancellation during sleep and after daemon restart.

- **analysis.py is pure**: No DB imports, no HTTP calls, no side effects. Takes lists
  of dicts, returns lists of dicts. All DB access happens in `engine.py:compute_run_analysis()`.

- **Proxy calls to self**: Generation and judging call `POST http://127.0.0.1:7683/api/generate`
  to route through the queue's own proxy. This serializes Ollama access and prevents
  concurrent model loading. Never call Ollama directly at port 11434.

- **Error budget**: Runs track a configurable error budget (default 30%). If the
  fraction of failed items exceeds this threshold, the run is marked failed early.

- **Auto-promote gates** (`check_auto_promote`, never raises):
  1. Winner F1 >= `eval.f1_threshold`
  2. Winner F1 > production F1 + `eval.auto_promote_min_improvement`
  3. `error_budget_used` <= `eval.error_budget`
  Optional stability window gate: winner must have passed threshold in last N runs.

- **score=0 is valid**: `_get_score()` in `analysis.py` uses explicit `None` checks,
  not falsy checks. `x or fallback` silently drops valid zero scores.

## Dependencies

**Depends on**: `db/` (via engine.py helpers), `api/proxy` (HTTP calls for Ollama access)
**Depended on by**: `api/eval_runs.py`, `api/eval_settings.py`, `api/eval_variants.py`, `api/eval_trends.py`
