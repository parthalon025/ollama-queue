# Eval Analysis Design

**Date:** 2026-03-07
**Status:** Implemented
**Feature:** Ollama-powered post-run analysis — explains why evals succeeded/failed and recommends next steps

---

## Goal

After each eval run completes, automatically use Ollama to generate a plain-language analysis:
- **SUMMARY:** one-sentence verdict
- **WHY:** 2-3 sentences interpreting the metrics and example pairs
- **RECOMMENDATIONS:** 3 numbered, concrete next steps referencing specific config IDs

---

## Architecture

**Approach B — Separate `generate_eval_analysis()` step** (chosen over inline-in-report-md or on-demand-only).

```
run_eval_session()
  └─ run_eval_generate()
  └─ run_eval_judge()         → stores status='complete', metrics, report_md
  └─ generate_eval_analysis() → stores analysis_md
```

Analysis failure **never** affects the completed run record — `generate_eval_analysis` logs and returns on any error.

---

## Implementation

### DB (`db.py`)
- `_add_column_if_missing(conn, "eval_runs", "analysis_md", "TEXT")` in `_run_migrations()` — idempotent on existing DBs
- `"eval.analysis_model": ""` added to `EVAL_SETTINGS_DEFAULTS` (empty = fall back to judge model)

### eval_engine.py

Three new functions:

**`build_analysis_prompt(run_id, variants, item_count, judge_model, metrics, winner, top_pairs, bottom_pairs)`**
- Assembles: run context + metrics table + top-4 same-cluster pairs (highest transfer scores) + bottom-4 same-cluster pairs (lowest transfer scores)
- Instructs the model to respond in three plain-text sections: `SUMMARY:` / `WHY:` / `RECOMMENDATIONS:`
- Principle snippets truncated at 180 chars
- Both high and low examples provided: high = what worked (positive exemplars), low = where it failed (diagnostic signal for recall)

**`_fetch_analysis_samples(db, run_id, n=4)`**
- Fetches top-N and bottom-N same-cluster judge pairs by effective transfer score
- Same-cluster only: these are the true positives / false negatives with the most diagnostic signal

**`generate_eval_analysis(db, run_id, http_base)`**
- Model resolution: `eval.analysis_model` setting → run's `judge_model` → `eval.judge_model` default
- Calls proxy at `temperature=0.3, num_ctx=4096, priority=1`
- Writes result to `eval_runs.analysis_md`
- Silent on: run not found, run not complete, no metrics, empty response, proxy down

`run_eval_session()` updated to call `generate_eval_analysis()` after judge completes (re-fetches run to check status='complete').

### api.py

**`GET /api/eval/runs`** — list endpoint updated to return richer fields:
- New: `winner_variant`, `started_at`, `judge_model`, `item_ids`, `metrics` (parsed), `analysis_md`, `error`, `run_mode`, `error_budget`
- Old scalar fields (`f1_score`, `recall`, `precision`, `error_budget_used`) removed; `metrics` JSON is now passed directly so RunRow can render the full per-variant table

**`POST /api/eval/runs/{id}/analyze`** — on-demand re-generation
- Returns 404 for unknown run, 400 for non-complete run
- Starts `generate_eval_analysis` in background thread, returns `{ok: true}` immediately

**`PUT /api/eval/settings`** — `"analysis_model"` added to allowed keys

### RunRow.jsx (SPA)

- `analysis_md` destructured from `run` prop
- Analysis panel rendered in L2 (below scorer info, above action buttons) when `status === 'complete' && analysis_md`
- Panel: left-border accent, `<pre>` with `white-space: pre-wrap` for plain-text section rendering
- `useActionFeedback` hook for "✦ Analyze" / "↺ Re-analyze" button
- On analyze success: `setTimeout(() => fetchEvalRuns(), 8000)` to refresh list once background analysis likely completes

---

## Prompt Design Rationale

- **Structured plain text** (not markdown): prompt instructs model to output `SUMMARY:` / `WHY:` / `RECOMMENDATIONS:` sections. Renders cleanly with `pre-wrap` without requiring a markdown library.
- **Both high and low examples**: best pairs show what the model does well (anchor for "why it worked"); worst pairs expose vague or wrong-cluster principles (anchor for "why it failed"). Without both, models give generic advice.
- **Low temperature (0.3)**: analysis should be deterministic and consistent, not creative. Same run should produce similar analysis on re-runs.
- **250-word limit**: enough for specific recommendations without padding.

---

## Settings

| Key | Default | Notes |
|-----|---------|-------|
| `eval.analysis_model` | `""` | Empty = use judge model. Any Ollama model name. |

---

## Success Criteria

- Analysis generated automatically for every completed run
- Analysis failure never changes run status (always logs + returns)
- Re-analysis available on demand via button or `POST /api/eval/runs/{id}/analyze`
- Configurable model via `eval.analysis_model` setting
- 538/538 tests pass

---

## Files Changed

| File | Change |
|------|--------|
| `ollama_queue/db.py` | Migration + EVAL_SETTINGS_DEFAULTS |
| `ollama_queue/eval_engine.py` | 3 new functions + run_eval_session update |
| `ollama_queue/api.py` | List endpoint schema + analyze endpoint + settings key |
| `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx` | Analysis panel + re-analyze button |
| `tests/test_eval_engine.py` | 20 new tests (TestBuildAnalysisPrompt, TestGenerateEvalAnalysis) |
| `tests/test_api_eval_runs.py` | 3 new tests + list schema updated |
