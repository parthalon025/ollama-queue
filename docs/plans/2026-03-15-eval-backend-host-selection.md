# Eval Backend Host Selection

## Goal

Give users visibility and control over which backend host runs each eval role (generator, judge, analysis), with per-run override capability and transparency in run history.

## Architecture

Per-role backend settings stored in the `settings` table, enforced via a new `_backend` proxy param, exposed in the Eval Settings UI with per-run override in the trigger panel.

## Data Layer

Three settings in the `settings` table:

| Key | Default | Values |
|-----|---------|--------|
| `eval.generator_backend` | `"auto"` | `"auto"` or backend URL |
| `eval.judge_backend` | `"auto"` (stub exists) | `"auto"` or backend URL |
| `eval.analysis_backend` | `"auto"` | `"auto"` or backend URL |

Two new columns on `eval_runs`:

| Column | Type | Purpose |
|--------|------|---------|
| `gen_backend` | TEXT | Backend URL used for generation (recorded for transparency) |
| `judge_backend` | TEXT | Backend URL used for judging (recorded for transparency) |

## API Layer

### Proxy: `_backend` param

`proxy.py` extracts `_backend` from request body (alongside `_priority`, `_source`, `_timeout`). When present and not `"auto"`:
- Validate it's a registered backend URL
- Validate it's healthy (return 422 if not)
- Skip `select_backend()` and use the specified URL directly

When `"auto"` or absent: existing 5-tier smart routing unchanged.

### Eval engine wiring

- `run_eval_generate()` reads `eval.generator_backend` (or run-level override) and passes `_backend` in proxy request body
- `run_eval_judge()` reads `eval.judge_backend` (or run-level override) and passes `_backend`
- `compute_run_analysis()` reads `eval.analysis_backend` and passes `_backend`
- All three record the actual backend used on the `eval_runs` row

### Run trigger override

`POST /api/eval/runs` accepts optional body params:
- `gen_backend` — overrides `eval.generator_backend` for this run
- `judge_backend` — overrides `eval.judge_backend` for this run

Stored on the run row. Default: use settings.

### Settings validation

`PUT /api/eval/settings` validates backend URLs against registered backends (same pattern as model validation from PR #133). Rejects unknown URLs with 422 + list of registered backends.

## UI Layer

### Eval Settings tab

Each `ProviderRoleSection` (Generator, Judge, Analysis) gets a backend dropdown below the model selector:
- Options: "Auto (smart routing)" + list of healthy backends with GPU labels (e.g., "RTX 5080 — 100.114.197.57")
- Only shown when provider = ollama (external providers don't use local backends)
- Fetches from existing `GET /api/backends`

### Run Trigger panel

Two optional backend dropdowns (gen, judge) defaulting to "Use settings". Allows one-off override without changing persistent config.

### Run History (transparency)

- Run detail (L2 progressive disclosure): show `gen_backend` and `judge_backend` with GPU name labels
- Active run progress: add backend labels alongside existing `gen_model` / `judge_model`

## Out of Scope

- Per-variant backend (variants stay focused on prompt evaluation)
- Proxy-level backend override outside eval (queue routing stays automatic)
- Backend preference for non-eval queue jobs

## Tech Stack

- Backend: Python (FastAPI, SQLite)
- Frontend: Preact + @preact/signals
- Existing patterns: `ProviderRoleSection.jsx`, `ModelSelect` component, `stores/health.js` backends signal
