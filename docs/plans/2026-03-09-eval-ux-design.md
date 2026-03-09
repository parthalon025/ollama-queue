# Eval UI User-Friendliness Pass — Design

**Date:** 2026-03-09
**Status:** Approved
**Branch:** feature/eval-ux-improvements

## Problem

The eval pipeline UI has accumulated several broken, misleading, and jargon-heavy elements that make it hard to use without reading the source code:

1. Two stub buttons ("Score again", "Export") with no `onClick` — clicking does nothing
2. `alert()` calls for tooltip info — jarring browser dialogs that steal focus
3. Variant checkboxes show "A — baseline-fewshot" with no plain-English description of what the config tests
4. Hardcoded ASCII sparkline `▁▃▅▇█` in VariantRow — never reflects real data
5. SetupChecklist step 2 is `const step2Complete = false` — permanently stuck; and step 3 gate (`variants.length > 5`) is already satisfied by 9 built-in variants, making the checklist nonsensical
6. `analysis_md` rendered in `<pre>` tag — raw `**bold**` and `## Header` asterisks/hashes visible
7. `confirm()` dialog for variant delete — old-school browser popup
8. "Winner: Config B" — cryptic, no description of what B is
9. Judge mode selector has no per-option description — user doesn't know when to use Tournament vs Binary vs Rubric
10. "Lessons per Group" silently clamps out-of-range values with no feedback

## Design

### 1 — Remove stub buttons (RunRow.jsx)

Remove the two `<button>` elements with no `onClick` ("Score again" and "Export"). Dead UI creates confusion. The judge-rerun and repeat functionality already exist as working buttons in the same row. "Export" has no backend endpoint. File a GH issue for export if needed later.

### 2 — Inline tooltip reveal (RunTriggerPanel.jsx)

Replace three `alert(EVAL_TRANSLATIONS.*.tooltip)` calls with a single `const [activeTooltip, setActiveTooltip] = useState(null)` keyed by field name. Clicking `?` sets the key; second click clears it. Render tooltip text inline below the field in a dim `<div>`. No floating overlay, no new component, no focus steal.

### 3 — Variant descriptions from API (backend + frontend)

Add a `description` field to the `eval_variants` DB schema and `GET /api/eval/variants` response. Populate descriptions for the 9 built-in system variants from `VARIANT_CONFIGS` in `variants.py`. Render the description as a secondary dim line under each checkbox row in RunTriggerPanel.

Frontend falls back gracefully if `description` is null (custom user variants may not have one).

This is authoritative — one source of truth. Avoids a parallel JS map that would drift from Python.

### 4 — Fix SetupChecklist (SetupChecklist.jsx)

Simplify to 2 gates:
- Step 1: data source connected (auto-tests on open)
- Step 2: first run exists (`runs.length > 0`)

Remove the permanent `step2Complete = false` and the already-satisfied `variants.length > 5` gate. The checklist now auto-progresses correctly: step 1 checks on mount, step 2 completes once any run exists.

### 5 — Replace sparkline with best score (VariantRow.jsx)

Replace the hardcoded `▁▃▅▇█` string with `latest_f1`-derived text: `"Best quality score: 72%"` or `"No runs yet."`. `latest_f1` is already in scope (comes with the variant row). No new API call needed.

### 6 — Structure-preserving markdown renderer (RunRow.jsx)

Replace `<pre>` with `<div style="white-space: pre-line">`. Add a `function simpleRenderMd(text)` (10 lines, no library) that:
- Converts `## Heading` → a bold label line
- Converts `**x**` → plain `x`
- Converts `- item` → `• item`
- Collapses 3+ blank lines to 2

Preserves structure, eliminates raw syntax characters.

### 7 — Inline two-click delete (VariantRow.jsx)

Replace `confirm('Delete variant "X"?')` with inline state: `const [pendingDelete, setPendingDelete] = useState(false)`. First "Delete" click sets `pendingDelete=true` and renders "Confirm delete?" + "Yes, delete" (red) + "Cancel" inline. Second click executes. No browser dialog.

### 8 — Winner label with lookup (RunRow.jsx)

Replace `"Winner: Config B"` with a variant lookup:
```js
const variantLabel = evalVariants.value?.find(v => v.id === winner_variant)?.label;
```
Render `"Winner: B — ${variantLabel}"` in L1. Guard with `?? winner_variant` fallback if variants not loaded. Show full `label · model` in L2 where there's space.

### 9 — Judge mode inline description (RunTriggerPanel.jsx)

Below the judge mode `<select>`, render a one-line description that updates with the selected value:

| Mode | Description |
|------|-------------|
| bayesian | Uses multiple signals (paired, embedding, scope, mechanism) — most accurate, recommended for final decisions |
| tournament | Head-to-head comparisons between configs — good for ranking when you have 3+ variants |
| binary | Simple YES/NO per principle — fastest, least compute, use for quick sanity checks |
| rubric | 1–5 scores — legacy mode, less reliable than Bayesian |

### 10 — Input hint for Lessons per Group (RunTriggerPanel.jsx)

Add a dim inline note below the number input: `"1–20 · higher = slower but more reliable results"`. Already has `min/max` attrs — this just makes the constraint visible to the user.

## Files Changed

### Backend
- `ollama_queue/db.py` — add `description TEXT` to `eval_variants` table; populate for system variants in `initialize()`; add to `get_eval_variants()` return shape
- `ollama_queue/eval_engine.py` — populate `description` for system variants from `VARIANT_CONFIGS`
- `ollama_queue/api.py` — include `description` in `GET /api/eval/variants` response

### Frontend
- `spa/src/components/eval/RunRow.jsx` — sections 1, 6, 8
- `spa/src/components/eval/RunTriggerPanel.jsx` — sections 2, 9, 10
- `spa/src/components/eval/SetupChecklist.jsx` — section 4
- `spa/src/components/eval/VariantRow.jsx` — sections 5, 7

## Non-Goals

- Full markdown library (marked, remark) — overkill for a local dashboard
- Floating tooltip component — inline is sufficient
- Export functionality — separate feature
- "Score again" reimplementation — separate feature (judge-rerun already exists)
