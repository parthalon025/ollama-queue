# Promote & Auto-Promote Design

**Date:** 2026-03-07
**Status:** Complete — PR #37
**Feature:** Wire "Use this config" button + auto-promote winning eval variant to production based on quality criteria

---

## Goal

Close the feedback loop: when an eval run identifies a winning variant, apply it to production — either manually via the "Use this config" button or automatically when quality criteria are met.

---

## Architecture

```
run_eval_session()
  └─ run_eval_generate()
  └─ run_eval_judge()         → stores winner_variant, metrics
  └─ generate_eval_analysis() → stores analysis_md
  └─ check_auto_promote()     → calls promote_eval_run() if criteria pass
```

Manual path: User reads analysis panel → clicks "Use this config" → calls promote_eval_run().

---

## Promote Logic (`promote_eval_run`)

**Current state:** Requires `model` and `prompt_template_id` in request body.

**New behaviour:**
1. Accept empty body `{}`
2. Resolve winner variant: `run.winner_variant` → fetch full row from `eval_variants` (model, prompt_template_id, temperature, num_ctx)
3. Validate: run must be `complete`, must have `winner_variant`, variant must exist in `eval_variants`
4. Call lessons-db `POST /eval/production-variant` with resolved fields
5. Locally update `eval_variants`:
   - Set `is_recommended=1`, `is_production=1` on winner
   - Clear `is_recommended=0`, `is_production=0` on all other variants
6. Return `{ok: true, run_id, variant_id, label}`

Failure modes: run not found (404), not complete (400), no winner_variant (400), variant not found in DB (400), lessons-db unreachable (502).

---

## Auto-Promote Logic (`check_auto_promote`)

Called from `run_eval_session()` after `generate_eval_analysis()` completes.

### Three-Gate Criteria (all must pass)

| Gate | Setting | Default | Rationale |
|------|---------|---------|-----------|
| Winner F1 ≥ threshold | `eval.f1_threshold` | 0.75 | Absolute quality floor |
| Winner F1 > production F1 + min_improvement | `eval.auto_promote_min_improvement` | 0.05 | Prevents sideways/regressive promotions |
| error_budget_used ≤ error_budget | `eval.error_budget` | 0.3 | Run must be within cost tolerance |

**Stability check:** If `eval.stability_window > 0`, the winner variant must have cleared `f1_threshold` in the last `stability_window` completed runs (for that variant). A single lucky run does not auto-promote.

### Production Baseline

- Query `eval_variants` for the currently `is_production=1` variant
- Fetch its latest F1 from recent `eval_runs.metrics`
- If no production variant exists: skip delta gate (only apply threshold + budget gates)

### Guard Rails

- Auto-promote only fires if `eval.auto_promote = true` (default: `false` — explicit opt-in)
- `check_auto_promote` NEVER raises — logs and returns on any error (same pattern as `generate_eval_analysis`)
- Auto-promote records reason in logs: `"Auto-promoted variant E (F1=0.82, +0.07 over production=0.75)"`

---

## New Settings

| Key | Default | Notes |
|-----|---------|-------|
| `eval.auto_promote` | `false` | Enable/disable auto-promotion |
| `eval.auto_promote_min_improvement` | `0.05` | Minimum F1 delta over current production |

Both added to `EVAL_SETTINGS_DEFAULTS` in `db.py` and to allowed keys in `PUT /api/eval/settings`.

---

## SPA Changes

### RunRow.jsx

- Add `const [promoteFb, promoteAct] = useActionFeedback()` (alongside existing feedback hooks)
- `handlePromote`: POST `${API}/eval/runs/${id}/promote` with `{}`
- Success label: `` `Config ${winner_variant} promoted to production` ``
- On success: call `fetchEvalRuns()` + `fetchEvalVariants()` so Variants tab updates immediately
- Button disabled while `promoteFb.phase === 'loading'`
- Button shown when: `status === 'complete' && winner_variant` (already gated)

### Settings Tab

- `eval.auto_promote` rendered as a toggle (boolean)
- `eval.auto_promote_min_improvement` rendered as a number input (0.00–1.00)
- Both grouped under a new "Auto-promote" section in the Settings sub-view

### Variants Tab (no changes needed)

`VariantRow.jsx` already renders `★ Recommended` and `Production` badges from `is_recommended` and `is_production`. These will appear automatically after promote fires.

---

## Implementation

### Files Changed

| File | Change |
|------|--------|
| `ollama_queue/db.py` | 2 new `EVAL_SETTINGS_DEFAULTS` entries |
| `ollama_queue/eval_engine.py` | `check_auto_promote(db, run_id, http_base)` function |
| `ollama_queue/api.py` | `promote_eval_run` — auto-resolve body; local DB updates; 2 new settings keys; call `check_auto_promote` from `run_eval_session` |
| `ollama_queue/dashboard/spa/src/components/eval/RunRow.jsx` | Wire "Use this config" button |
| `ollama_queue/dashboard/spa/src/pages/EvalSettings.jsx` (or Settings sub-component) | Auto-promote toggle + min_improvement input |
| `tests/test_eval_engine.py` | TestCheckAutoPromote |
| `tests/test_api_eval_runs.py` | Promote endpoint tests (auto-resolve, missing winner, local DB update) |

---

## Success Criteria

- "Use this config" promotes winner → Variants tab shows Production + Recommended badges
- Auto-promote fires when all three gates pass; skips when any gate fails
- Auto-promote never changes run status on failure
- `eval.auto_promote` defaults to false; must be explicitly enabled
- All existing tests pass; new tests cover promote + auto-promote
