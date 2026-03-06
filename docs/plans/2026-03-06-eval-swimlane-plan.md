# Eval Pipeline Swimlane Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the static "Working…" label and broken progress bar in the eval tab with a horizontal swimlane showing all pipeline stages, the active step highlighted, per-phase progress, and the active model.

**Architecture:** Two-layer change — (1) enrich the `/api/eval/runs/{id}/progress` backend response with `gen_model`, `judge_model`, and fix `pct` to be per-phase accurate; (2) new `EvalPipelineSwimline` Preact component that renders the stage nodes and replaces the stage label in `ActiveRunProgress.jsx`.

**Tech Stack:** Python/FastAPI (backend), Preact 10 + signals (frontend), CSS design tokens (no new dependencies)

---

## Context

- **Design doc:** `docs/plans/2026-03-06-eval-swimlane-design.md`
- **Progress endpoint:** `ollama_queue/api.py` function `get_eval_run_progress` (~line 1697)
- **Progress component:** `ollama_queue/dashboard/spa/src/components/eval/ActiveRunProgress.jsx`
- **Test file:** `tests/test_api_eval_runs.py`
- **System variants:** Variant "A" = `deepseek-r1:8b`, "D"/"E" = `qwen3:14b` — seeded in `db.py:451`
- **`pct` bug:** currently `judged / total * 100` — stays 0% during entire generating phase. Must be per-phase.
- **h-shadowing rule:** Never use `h` as a `.map()` callback parameter — esbuild injects it as JSX factory.
- **Layman comments required** on every new JSX component (What it shows / Decision it drives).

---

## Task 1: Backend — enrich progress response + fix pct

**Files:**
- Modify: `ollama_queue/api.py` (function `get_eval_run_progress`, ~line 1697)
- Test: `tests/test_api_eval_runs.py`

### Step 1: Write failing tests

Add to `tests/test_api_eval_runs.py` after the existing `test_get_eval_run_progress_pct_complete` test:

```python
def test_get_eval_run_progress_includes_model_fields(client_and_db):
    """Progress response includes gen_model and judge_model fields."""
    client, db = client_and_db
    run_id = _make_run(db, variant_id="A", status="generating")
    update_eval_run(db, run_id, item_count=10)

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    data = resp.json()
    # Variant A is seeded with model "deepseek-r1:8b"
    assert data["gen_model"] == "deepseek-r1:8b"
    assert "judge_model" in data


def test_get_eval_run_progress_pct_is_per_phase_generating(client_and_db):
    """During generating phase, pct reflects generated/total (not judged/total)."""
    client, db = client_and_db
    run_id = _make_run(db, status="generating")
    update_eval_run(db, run_id, item_count=10)

    # Insert 3 generate rows (0 judge rows)
    for i in range(3):
        insert_eval_result(
            db,
            run_id=run_id,
            variant="A",
            source_item_id=f"src-{i}",
            target_item_id=f"tgt-{i}",
            is_same_cluster=1,
            row_type="generate",
        )

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    data = resp.json()
    # 3/10 generated = 30%, not 0% (which it would be if using judged/total)
    assert data["pct"] == 30.0


def test_get_eval_run_progress_pct_is_per_phase_judging(client_and_db):
    """During judging phase, pct reflects judged/total."""
    client, db = client_and_db
    run_id = _make_run(db, status="judging")
    update_eval_run(db, run_id, item_count=10)

    for i in range(4):
        insert_eval_result(
            db,
            run_id=run_id,
            variant="A",
            source_item_id=f"src-{i}",
            target_item_id=f"tgt-{i}",
            is_same_cluster=1,
            row_type="judge",
            score_transfer=3,
        )

    resp = client.get(f"/api/eval/runs/{run_id}/progress")
    assert resp.status_code == 200
    assert resp.json()["pct"] == 40.0
```

### Step 2: Run tests to verify they fail

```bash
cd ~/Documents/projects/ollama-queue && source .venv/bin/activate
pytest tests/test_api_eval_runs.py::test_get_eval_run_progress_includes_model_fields tests/test_api_eval_runs.py::test_get_eval_run_progress_pct_is_per_phase_generating tests/test_api_eval_runs.py::test_get_eval_run_progress_pct_is_per_phase_judging -v
```

Expected: 3 FAIL (fields missing / pct still 0.0 during generate)

### Step 3: Implement backend changes

In `ollama_queue/api.py`, find `get_eval_run_progress`. Make these two changes:

**Change A** — inside the `with db._lock:` block, after the `per_variant_rows` query (before the closing `)`), add the variant lookup:

```python
            # Resolve gen_model from variant (for swimlane model badge)
            _variant_id = run.get("variant_id") or (run.get("variants") or "").split(",")[0].strip()
            _variant_row = conn.execute(
                "SELECT model FROM eval_variants WHERE id = ?", (_variant_id,)
            ).fetchone()
            gen_model = _variant_row["model"] if _variant_row else None
```

**Change B** — replace the `pct` calculation and the `is_judging` / `completed` block that comes after the lock. Change from:

```python
        generated = counts.get("generate", 0)
        judged = counts.get("judge", 0)
        pct = round(judged / total * 100, 1) if total > 0 else 0.0
        ...
        run_status = run["status"]
        is_judging = run_status in ("judging",) or run.get("stage") in ("judging", "fetch_targets")
        completed = judged if is_judging else generated
```

To (unified: compute `is_judging` once, use for both `pct` and `completed`):

```python
        generated = counts.get("generate", 0)
        judged = counts.get("judge", 0)
        run_status = run["status"]
        is_judging = run_status in ("judging",) or run.get("stage") in ("judging", "fetch_targets")
        phase_count = judged if is_judging else generated
        pct = round(phase_count / total * 100, 1) if total > 0 else 0.0
        ...
        completed = phase_count
```

**Change C** — add `gen_model` and `judge_model` to the return dict:

```python
        return {
            # Legacy fields (keep for API compatibility)
            "generated": generated,
            "judged": judged,
            "pct_complete": pct,
            # Fields the frontend progress panel reads
            "run_id": run_id,
            "status": run_status,
            "stage": run.get("stage"),
            "completed": completed,
            "total": total,
            "failed": failed,
            "pct": pct,
            "failure_rate": failure_rate,
            "per_variant": per_variant,
            "eta_s": None,
            # Swimlane model badge
            "gen_model": gen_model,
            "judge_model": run.get("judge_model"),
        }
```

### Step 4: Run tests to verify they pass

```bash
pytest tests/test_api_eval_runs.py::test_get_eval_run_progress_includes_model_fields tests/test_api_eval_runs.py::test_get_eval_run_progress_pct_is_per_phase_generating tests/test_api_eval_runs.py::test_get_eval_run_progress_pct_is_per_phase_judging -v
```

Expected: 3 PASS

### Step 5: Run full test suite

```bash
pytest --timeout=120 -x -q
```

Expected: 535+ passed

### Step 6: Commit

```bash
git add ollama_queue/api.py tests/test_api_eval_runs.py
git commit -m "feat(api): enrich eval progress with gen_model/judge_model + fix per-phase pct"
```

---

## Task 2: EvalPipelineSwimline component + CSS

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/eval/EvalPipelineSwimline.jsx`
- Modify: `ollama_queue/dashboard/spa/src/index.css`

No backend tests. Verify via `npm run build` (no errors = pass).

### Step 1: Create `EvalPipelineSwimline.jsx`

Create `ollama_queue/dashboard/spa/src/components/eval/EvalPipelineSwimline.jsx`:

```jsx
// What it shows: A horizontal pipeline with four nodes — Fetch, Generate, Judge, Done —
//   where the current stage is highlighted, completed stages get a checkmark, and
//   a one-line summary below shows which model is working and how far through the phase it is.
// Decision it drives: User knows exactly where in the eval pipeline the run is,
//   which AI model is active, and how many items remain in the current phase.

import { h } from 'preact';

// NOTE: .map() callbacks use descriptive param names — never 'h' (shadows JSX factory).

const STAGES = [
  { id: 'fetch_items', label: 'Fetch' },
  { id: 'generating',  label: 'Generate' },
  { id: 'judging',     label: 'Judge' },
  { id: 'done',        label: 'Done' },
];

// Ordered list used to determine "is this stage before/at/after current"
const STAGE_ORDER = ['fetch_items', 'generating', 'judging', 'done'];

// Map raw stage/status values to the 4 display nodes
function normalizeStage(stage, status) {
  if (status === 'complete') return 'done';
  if (stage === 'fetch_targets') return 'judging'; // instantaneous — collapse into judging
  return stage || 'fetch_items';
}

// Returns 'done' | 'active' | 'pending' for a node given the current pipeline position
function nodeState(nodeId, currentStage) {
  const curr = STAGE_ORDER.indexOf(currentStage);
  const node = STAGE_ORDER.indexOf(nodeId);
  if (curr < 0 || node < 0) return 'pending';
  if (node < curr) return 'done';
  if (node === curr) return 'active';
  return 'pending';
}

export default function EvalPipelineSwimline({ stage, status, generated, judged, total, pct, gen_model, judge_model }) {
  const current = normalizeStage(stage, status);
  const isJudging = current === 'judging' || current === 'done';
  const model = isJudging ? judge_model : gen_model;
  const count = isJudging ? (judged ?? 0) : (generated ?? 0);
  const phaseLabel = isJudging ? 'Scoring' : 'Writing';
  const showInfo = current !== 'fetch_items' && current !== 'done';

  return (
    <div class="eval-swimlane-wrap">
      {/* Horizontal stage nodes with connecting lines */}
      <div class="eval-swimlane">
        {STAGES.map((stg, idx) => {
          const state = nodeState(stg.id, current);
          const isLast = idx === STAGES.length - 1;
          // Connector after this node is "done" when the node itself is done
          const connDone = state === 'done';
          return [
            <div key={stg.id} class={`eval-swimlane-node eval-swimlane-node--${state}`}>
              <div class="eval-swimlane-node-icon">
                {state === 'done' ? '✓' : state === 'active' ? '◎' : '○'}
              </div>
              <div class="eval-swimlane-node-label">{stg.label}</div>
            </div>,
            !isLast ? (
              <div
                key={`conn-${idx}`}
                class={`eval-swimlane-connector${connDone ? ' eval-swimlane-connector--done' : ''}`}
              />
            ) : null,
          ];
        })}
      </div>

      {/* Info line: phase label · model · N / total (pct%) */}
      {showInfo && (
        <div class="eval-info-line">
          <span>
            {phaseLabel}
            {model && <span class="eval-model-badge"> · {model}</span>}
            {total > 0 && <span> · {count} / {total} ({pct}%)</span>}
          </span>
        </div>
      )}
    </div>
  );
}
```

### Step 2: Add swimlane CSS to `index.css`

Append to `ollama_queue/dashboard/spa/src/index.css`:

```css
/* ── Eval Pipeline Swimlane ───────────────────────────────────────────────── */
.eval-swimlane-wrap { margin-bottom: 0.75rem; }
.eval-swimlane {
  display: flex;
  align-items: flex-start;
  margin-bottom: 0.4rem;
}
.eval-swimlane-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  flex-shrink: 0;
}
.eval-swimlane-node-icon {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  line-height: 1;
}
.eval-swimlane-node--done .eval-swimlane-node-icon {
  background: var(--surface-raised, #2a2a2a);
  color: var(--text-tertiary, #666);
}
.eval-swimlane-node--active .eval-swimlane-node-icon {
  background: var(--accent, #4fc3f7);
  color: var(--bg, #111);
  animation: eval-node-pulse 1.6s ease-in-out infinite;
}
.eval-swimlane-node--pending .eval-swimlane-node-icon {
  background: transparent;
  border: 1px solid var(--border, #333);
  color: var(--text-tertiary, #555);
}
.eval-swimlane-node-label {
  font-size: var(--type-label, 11px);
  font-family: var(--font-mono, monospace);
  color: var(--text-tertiary, #666);
  white-space: nowrap;
}
.eval-swimlane-node--active .eval-swimlane-node-label {
  color: var(--accent, #4fc3f7);
}
.eval-swimlane-node--done .eval-swimlane-node-label {
  color: var(--text-tertiary, #555);
}
.eval-swimlane-connector {
  flex: 1;
  height: 1px;
  background: var(--border, #333);
  margin-top: 13px; /* vertically center with icon (icon is 26px, half = 13px) */
  min-width: 16px;
}
.eval-swimlane-connector--done {
  background: var(--accent, #4fc3f7);
  opacity: 0.35;
}
@keyframes eval-node-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.65; transform: scale(0.92); }
}
.eval-info-line {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-family: var(--font-mono, monospace);
  font-size: var(--type-label, 11px);
  color: var(--text-secondary, #999);
  margin-bottom: 0.4rem;
}
.eval-model-badge { color: var(--text-tertiary, #666); }
```

### Step 3: Verify build passes

```bash
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -10
```

Expected: clean build, no errors or warnings about missing imports.

### Step 4: Commit

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/eval/EvalPipelineSwimline.jsx
git add ollama_queue/dashboard/spa/src/index.css
git commit -m "feat(spa): add EvalPipelineSwimline component + swimlane CSS"
```

---

## Task 3: Wire swimlane into ActiveRunProgress

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/eval/ActiveRunProgress.jsx`

### Step 1: Locate and replace the stage indicator block

In `ActiveRunProgress.jsx`, find this block (~line 85 of the component):

```jsx
      {/* Stage indicator */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
        <span class="cursor-working" style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', color: 'var(--accent)' }}>
          {stageLabel} ({completed}/{total})
        </span>
        {etaLabel && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
            {etaLabel}
          </span>
        )}
      </div>
```

Replace it with the swimlane import + component call.

**Add import** at the top of `ActiveRunProgress.jsx` (after the existing imports):

```js
import EvalPipelineSwimline from './EvalPipelineSwimline.jsx';
```

**Replace the stage indicator block** with:

```jsx
      {/* Pipeline swimlane: shows all stages, active step, model, per-phase progress */}
      <EvalPipelineSwimline
        stage={stage}
        status={status}
        generated={activeRun.generated ?? 0}
        judged={activeRun.judged ?? 0}
        total={total}
        pct={pct}
        gen_model={activeRun.gen_model}
        judge_model={activeRun.judge_model}
      />
      {etaLabel && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem', textAlign: 'right' }}>
          {etaLabel}
        </div>
      )}
```

Also remove the now-unused `stageLabel` and `stageContext` variables (they were only used in the stage indicator block). Remove these lines:

```js
  const stageContext = stage || status;
  const stageLabel = (stageContext === 'generating' || stageContext === 'generate')
    ? EVAL_TRANSLATIONS.generating?.label ?? 'Writing principles…'
    : (stageContext === 'judging' || stageContext === 'judge' || stageContext === 'fetch_targets')
    ? EVAL_TRANSLATIONS.judging?.label ?? 'Scoring results…'
    : 'Working…';
```

And remove the `EVAL_TRANSLATIONS` import if it's no longer used elsewhere in the file. Check first:

```bash
grep -n "EVAL_TRANSLATIONS" ollama_queue/dashboard/spa/src/components/eval/ActiveRunProgress.jsx
```

If only used in the removed block, remove the import line too.

### Step 2: Verify build

```bash
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -15
```

Expected: clean build, no import errors.

### Step 3: Run full test suite

```bash
cd ~/Documents/projects/ollama-queue && source .venv/bin/activate
pytest --timeout=120 -x -q
```

Expected: 538+ passed (535 existing + 3 new from Task 1)

### Step 4: Commit

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/eval/ActiveRunProgress.jsx
git commit -m "feat(spa): replace stage label with EvalPipelineSwimline in ActiveRunProgress"
```

---

## Final verification

```bash
cd ~/Documents/projects/ollama-queue && source .venv/bin/activate
pytest --timeout=120 -q
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -5
```

Both must pass cleanly before declaring done.
