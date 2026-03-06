# Eval Pipeline Swimlane Design

**Date:** 2026-03-06
**Status:** Approved

## Problem

The eval progress panel shows a static "Working…" label and a progress bar that stays at 0% through the entire generating phase (because `pct = judged/total`, which is 0 until judging begins). No model info is shown. Users can't tell which step the pipeline is on or how far through that step it is.

## Solution

Replace the flat stage label + broken progress bar with a horizontal pipeline swimlane showing all 4 stages, the active step, per-phase accurate progress, and the currently-running model.

## Visual Layout

```
┌─────────────────────────────────────────────────────────────┐
│  ✓ Fetch     ✓ Generate   ◎ Judge          ○ Done           │
│  ─────────────────────────────────────────────────────       │
│  Scoring results · deepseek-r1:8b · 12 / 100 (12%)  ~8 min  │
│  ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  [Cancel run]    │
└─────────────────────────────────────────────────────────────┘
```

**Node states:**
- Completed: checkmark + dimmed/muted color
- Active: accent color + CSS pulse dot animation
- Pending: ghost/muted, no icon

**Info line (below swimlane):**
`{stage label} · {model} · {count} / {total} ({pct}%)`
- During generating: gen model from variant; count = `generated / total`
- During judging: `judge_model` from run; count = `judged / total`
- `fetch_items` and `fetch_targets` are instantaneous — shown as completed immediately, no count needed

**Progress bar:**
- Per-phase accurate: `generated/total` during generate, `judged/total` during judge
- Replaces the broken global `pct` (which is 0% for the entire generating phase)

## Architecture

### Backend change — enrich `/api/eval/runs/{run_id}/progress`

Add two fields to the progress response so the frontend needs only one fetch:

```python
# In get_eval_run_progress (api.py)
# Resolve gen_model from variant row
variant_id = run.get("variant_id") or (run.get("variants") or "").split(",")[0].strip()
variant_row = conn.execute(
    "SELECT model FROM eval_variants WHERE id = ?", (variant_id,)
).fetchone()
gen_model = variant_row["model"] if variant_row else None
judge_model = run.get("judge_model")
```

Response additions:
```json
{
  "gen_model": "qwen2.5:7b",
  "judge_model": "deepseek-r1:8b"
}
```

Also fix `pct` to be per-phase accurate:
- During generating: `pct = round(generated / total * 100, 1)`
- During judging: `pct = round(judged / total * 100, 1)` (current behavior — already correct for judging phase)

### Frontend — new `EvalPipelineSwimline` component

**File:** `src/components/eval/EvalPipelineSwimline.jsx`

Props: `{ stage, generated, judged, total, pct, gen_model, judge_model }`

Stage → pipeline position mapping:
```js
const STAGES = [
  { id: 'fetch_items',    label: 'Fetch' },
  { id: 'generating',     label: 'Generate' },
  { id: 'judging',        label: 'Judge' },
  { id: 'complete',       label: 'Done' },
];

// Stage ordering for "is this stage complete?" check
const STAGE_ORDER = ['fetch_items', 'generating', 'fetch_targets', 'judging', 'complete'];
```

Note: `fetch_targets` is collapsed — it's instantaneous and runs between generating and judging. When `stage === 'fetch_targets'`, treat it as "generating complete, judging pending" visually.

**Node state logic:**
```js
function nodeState(nodeId, currentStage) {
  const current = STAGE_ORDER.indexOf(normalizeStage(currentStage));
  const node    = STAGE_ORDER.indexOf(nodeId);
  if (node < current) return 'done';
  if (node === current) return 'active';
  return 'pending';
}
function normalizeStage(stage) {
  if (stage === 'fetch_targets') return 'judging'; // collapse
  return stage || 'fetch_items';
}
```

**Model/count info line:**
```js
const isJudging = ['judging', 'fetch_targets'].includes(stage);
const model = isJudging ? judge_model : gen_model;
const count = isJudging ? judged : generated;
const label = isJudging ? 'Scoring results' : 'Writing principles';
```

**CSS (add to index.css):**
```css
.eval-swimlane { display: flex; align-items: center; gap: 0; margin-bottom: 0.75rem; }
.eval-swimlane-node { display: flex; flex-direction: column; align-items: center; gap: 2px; flex: 1; }
.eval-swimlane-node-icon { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; }
.eval-swimlane-node--done .eval-swimlane-node-icon { background: var(--surface-raised); color: var(--text-tertiary); }
.eval-swimlane-node--active .eval-swimlane-node-icon { background: var(--accent); color: var(--bg); animation: eval-pulse 1.5s ease-in-out infinite; }
.eval-swimlane-node--pending .eval-swimlane-node-icon { background: var(--surface); border: 1px solid var(--border); color: var(--text-tertiary); }
.eval-swimlane-node-label { font-size: var(--type-label); color: var(--text-tertiary); font-family: var(--font-mono); }
.eval-swimlane-node--active .eval-swimlane-node-label { color: var(--accent); }
.eval-swimlane-connector { flex: 1; height: 1px; background: var(--border); margin-bottom: 14px; }
.eval-swimlane-connector--done { background: var(--accent); opacity: 0.4; }
@keyframes eval-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.eval-info-line { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.5rem; font-family: var(--font-mono); font-size: var(--type-label); color: var(--text-secondary); }
.eval-model-badge { color: var(--text-tertiary); }
```

### Integration — `ActiveRunProgress.jsx`

Replace the stage label text block with `<EvalPipelineSwimline ... />`.

Pass new fields from `activeRun` (which comes from the enriched progress poll):
```jsx
<EvalPipelineSwimline
  stage={stage}
  generated={activeRun.generated ?? 0}
  judged={activeRun.judged ?? 0}
  total={total}
  pct={pct}
  gen_model={activeRun.gen_model}
  judge_model={activeRun.judge_model}
/>
```

Keep the existing overall progress bar below the swimlane (it now uses the fixed per-phase `pct`).

## Scope

| File | Change |
|------|--------|
| `ollama_queue/api.py` | Add `gen_model`, `judge_model` to progress response; fix `pct` to be per-phase |
| `tests/test_api_eval_runs.py` | Update progress endpoint tests for new fields |
| `src/components/eval/EvalPipelineSwimline.jsx` | New component |
| `src/components/eval/ActiveRunProgress.jsx` | Replace stage label with swimlane component |
| `src/index.css` | Add swimlane CSS |

## Constraints

- No new API endpoints
- No new npm dependencies
- `fetch_targets` stage collapsed visually (instantaneous, not user-visible)
- Node icons: `✓` done, `◎` active (or Unicode bullet), `○` pending — no SVG import
- CSS uses existing design token variables only
- Layman comments required on new component
