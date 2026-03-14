# Dashboard Cohesion + Eval Phase 6 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 20 cross-page information cohesion improvements and rebuild the Eval tab as a Phase 6 "control room" with card grid, compare mode, sweep generator, optimization timeline, and provider config.

**Architecture:** 4 sequential batches — shared components first (everything depends on these), then global state strips, then cross-page data propagation, then the Eval tab Phase 6 redesign. All frontend work is in the Preact SPA at `ollama_queue/dashboard/spa/`. No backend schema changes in this plan.

**Tech Stack:** Preact 10 + @preact/signals, esbuild, Jest (direct vnode testing — see test patterns below), Tailwind v4, uPlot, superhot-ui theme.

**Design doc:** `docs/plans/2026-03-12-cohesion-eval-phase6-design.md`
**PRDs:** `tasks/prd-cohesion-batch*.json`, `tasks/prd-eval-phase6-control-room.json`

---

## Critical Gotchas — Read Before Writing Any Code

1. **NEVER use `h` or `Fragment` as a `.map()` callback parameter name.** esbuild injects `h` as the JSX factory. `items.map(h => ...)` silently shadows it and renders nothing. Use `item`, `entry`, `variant`, etc. instead.

2. **Test pattern is direct function calls, not @testing-library/preact.** Import the component, call it as a function with props, inspect the returned vnode tree with the `findText` helper. No `render()` or DOM needed.

3. **All fetch calls must check `res.ok`.** `fetch()` resolves on 4xx/5xx — it only rejects on network failure. Always: `if (!res.ok) throw new Error(...)`.

4. **Action buttons use `useActionFeedback`.** Import from `src/hooks/useActionFeedback.js`. Never build custom loading/error state for buttons.

5. **Every JSX component file must have a top-level layman comment block** with `What it shows:` and `Decision it drives:` (CLAUDE.md requirement).

6. **SPA working directory for all npm commands:** `ollama_queue/dashboard/spa/`

7. **Python test command:** `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q`

---

## Test Helper — Copy Into Every New Test File

```js
// Helper to traverse vnode tree and collect all text content
function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string') return vnode;
  if (typeof vnode === 'number') return String(vnode);
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode.props) {
    const children = vnode.props.children;
    return Array.isArray(children) ? children.map(findText).join('') : findText(children);
  }
  return '';
}

// Helper to find a vnode by tag name (e.g. 'button', 'span')
function findNode(vnode, tag) {
  if (!vnode) return null;
  if (vnode.type === tag) return vnode;
  if (Array.isArray(vnode)) { for (const c of vnode) { const r = findNode(c, tag); if (r) return r; } }
  if (vnode.props?.children) return findNode(vnode.props.children, tag);
  return null;
}
```

---

## Batch 1 — Shared Components + Vocabulary

> **What this does:** Creates 5 shared components (`StatusPill`, `PriorityPill`, `F1Score`, `ModelChip`, `VariantChip`) and standardizes `formatDuration()`. These are the building blocks everything else uses — finish this batch before starting Batch 2.

---

### Task 1.1: Extend formatDuration() edge cases

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/utils/time.js`
- Modify: `ollama_queue/dashboard/spa/src/utils/time.test.js`

The function already exists. Verify it handles all cases and add any missing ones.

**Step 1: Run existing tests to see current state**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=time
```
Expected: PASS (all 4 existing tests)

**Step 2: Add missing edge case tests**

Add to `src/utils/time.test.js`:
```js
test('handles zero', () => {
  expect(formatDuration(0)).toBe('0s');
});

test('formats exactly 1 hour', () => {
  expect(formatDuration(3600)).toBe('1h 0m');
});

test('handles very large values', () => {
  expect(formatDuration(7380)).toBe('2h 3m');
});
```

**Step 3: Run to verify they pass (function should already handle these)**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=time
```
Expected: PASS all tests

**Step 4: Commit**
```bash
cd ollama_queue/dashboard/spa
git add src/utils/time.test.js
git commit -m "test(spa): add edge case coverage for formatDuration"
```

---

### Task 1.2: Create StatusPill component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/StatusPill.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/StatusPill.test.js`

**Step 1: Write the failing test**

Create `src/components/StatusPill.test.js`:
```js
import _StatusPill from './StatusPill.jsx';
const StatusPill = _StatusPill.default || _StatusPill;

function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string') return vnode;
  if (typeof vnode === 'number') return String(vnode);
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode.props) {
    const c = vnode.props.children;
    return Array.isArray(c) ? c.map(findText).join('') : findText(c);
  }
  return '';
}

function findClass(vnode) {
  if (!vnode) return '';
  if (vnode.props?.class) return vnode.props.class;
  if (vnode.props?.className) return vnode.props.className;
  if (Array.isArray(vnode)) { for (const c of vnode) { const r = findClass(c); if (r) return r; } }
  if (vnode.props?.children) return findClass(vnode.props.children);
  return '';
}

test('renders queued status', () => {
  const vnode = StatusPill({ status: 'queued' });
  expect(findText(vnode)).toMatch(/queued/i);
});

test('renders running status', () => {
  const vnode = StatusPill({ status: 'running' });
  expect(findText(vnode)).toMatch(/running/i);
});

test('renders complete status', () => {
  const vnode = StatusPill({ status: 'complete' });
  expect(findText(vnode)).toMatch(/complete/i);
});

test('renders failed status', () => {
  const vnode = StatusPill({ status: 'failed' });
  expect(findText(vnode)).toMatch(/failed/i);
});

test('renders deferred status', () => {
  const vnode = StatusPill({ status: 'deferred' });
  expect(findText(vnode)).toMatch(/deferred/i);
});

test('renders cancelled status', () => {
  const vnode = StatusPill({ status: 'cancelled' });
  expect(findText(vnode)).toMatch(/cancelled/i);
});

test('applies color class for failed', () => {
  const vnode = StatusPill({ status: 'failed' });
  const cls = findClass(vnode);
  expect(cls).toMatch(/red|error|failed/i);
});

test('applies color class for running', () => {
  const vnode = StatusPill({ status: 'running' });
  const cls = findClass(vnode);
  expect(cls).toMatch(/blue|active|running/i);
});
```

**Step 2: Run to verify it fails**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=StatusPill
```
Expected: FAIL — "Cannot find module './StatusPill.jsx'"

**Step 3: Implement StatusPill**

Create `src/components/StatusPill.jsx`:
```jsx
/**
 * What it shows: A color-coded pill label for a job's current status.
 * Decision it drives: At a glance tells you whether a job is waiting, running,
 *   done, broken, delayed, or stopped — so you know if action is needed.
 */

const STATUS_STYLES = {
  queued:    { label: 'queued',    cls: 'status-pill status-queued' },
  running:   { label: 'running',   cls: 'status-pill status-running status-running-active' },
  complete:  { label: 'complete',  cls: 'status-pill status-complete' },
  failed:    { label: 'failed',    cls: 'status-pill status-failed status-error' },
  deferred:  { label: 'deferred',  cls: 'status-pill status-deferred' },
  cancelled: { label: 'cancelled', cls: 'status-pill status-cancelled' },
};

export default function StatusPill({ status }) {
  const s = STATUS_STYLES[status] || { label: status, cls: 'status-pill status-unknown' };
  return <span class={s.cls}>{s.label}</span>;
}
```

Add to `src/index.css` (in the `@layer components` block):
```css
.status-pill        { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 500; text-transform: uppercase; letter-spacing: 0.05em; }
.status-queued      { background: var(--color-muted, #6b7280); color: white; }
.status-running     { background: var(--color-info, #3b82f6); color: white; }
.status-running-active { animation: pulse 2s infinite; }
.status-complete    { background: var(--color-success, #22c55e); color: white; }
.status-failed      { background: var(--color-error, #ef4444); color: white; }
.status-deferred    { background: var(--color-warning, #f59e0b); color: white; }
.status-cancelled   { background: var(--color-muted, #6b7280); color: white; opacity: 0.7; }
```

**Step 4: Run tests**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=StatusPill
```
Expected: PASS all 8 tests

**Step 5: Commit**
```bash
cd ollama_queue/dashboard/spa
git add src/components/StatusPill.jsx src/components/StatusPill.test.js src/index.css
git commit -m "feat(spa): add StatusPill component — unified job status vocabulary"
```

---

### Task 1.3: Create PriorityPill component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/PriorityPill.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/PriorityPill.test.js`

**Step 1: Write the failing test**

Create `src/components/PriorityPill.test.js`:
```js
import _PriorityPill from './PriorityPill.jsx';
const PriorityPill = _PriorityPill.default || _PriorityPill;

function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string') return vnode;
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode.props) { const c = vnode.props.children; return Array.isArray(c) ? c.map(findText).join('') : findText(c); }
  return '';
}

test('renders critical', () => { expect(findText(PriorityPill({ level: 'critical' }))).toMatch(/critical/i); });
test('renders high', () => { expect(findText(PriorityPill({ level: 'high' }))).toMatch(/high/i); });
test('renders normal', () => { expect(findText(PriorityPill({ level: 'normal' }))).toMatch(/normal/i); });
test('renders low', () => { expect(findText(PriorityPill({ level: 'low' }))).toMatch(/low/i); });

test('accepts numeric level via numericToLevel', () => {
  // Priority 1-2 → critical
  const vnode = PriorityPill({ level: 1 });
  expect(findText(vnode)).toMatch(/critical/i);
});

test('renders unknown level gracefully', () => {
  const vnode = PriorityPill({ level: 'unknown' });
  expect(findText(vnode)).toBeTruthy();
});
```

**Step 2: Run to verify fails**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=PriorityPill
```
Expected: FAIL

**Step 3: Implement**

Create `src/components/PriorityPill.jsx`:
```jsx
/**
 * What it shows: A color-coded label showing how urgent a job is.
 * Decision it drives: Tells you at a glance whether to act on a job immediately
 *   (red = critical) or let it wait (grey = low). Used everywhere priority is shown.
 */

const LEVEL_STYLES = {
  critical: { label: 'critical', cls: 'priority-pill priority-critical' },
  high:     { label: 'high',     cls: 'priority-pill priority-high' },
  normal:   { label: 'normal',   cls: 'priority-pill priority-normal' },
  low:      { label: 'low',      cls: 'priority-pill priority-low' },
};

// Convert numeric priority (1-10 scale) to level string
export function numericToLevel(n) {
  const num = Number(n);
  if (num <= 2) return 'critical';
  if (num <= 4) return 'high';
  if (num <= 6) return 'normal';
  return 'low';
}

export default function PriorityPill({ level }) {
  const key = typeof level === 'number' ? numericToLevel(level) : level;
  const s = LEVEL_STYLES[key] || { label: key || 'normal', cls: 'priority-pill priority-normal' };
  return <span class={s.cls}>{s.label}</span>;
}
```

Add CSS to `src/index.css`:
```css
.priority-pill      { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; }
.priority-critical  { background: var(--color-error, #ef4444); color: white; }
.priority-high      { background: var(--color-warning, #f59e0b); color: white; }
.priority-normal    { background: var(--color-info, #3b82f6); color: white; }
.priority-low       { background: var(--color-muted, #6b7280); color: white; }
```

**Step 4: Run tests, commit**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=PriorityPill
git add src/components/PriorityPill.jsx src/components/PriorityPill.test.js src/index.css
git commit -m "feat(spa): add PriorityPill component — unified priority display"
```

---

### Task 1.4: Create F1Score component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/F1Score.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/F1Score.test.js`

**Step 1: Write the failing test**

Create `src/components/F1Score.test.js`:
```js
import _F1Score from './F1Score.jsx';
const F1Score = _F1Score.default || _F1Score;

function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string') return vnode;
  if (typeof vnode === 'number') return String(vnode);
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode.props) { const c = vnode.props.children; return Array.isArray(c) ? c.map(findText).join('') : findText(c); }
  return '';
}

function findClass(vnode) {
  if (!vnode) return '';
  if (vnode.props?.class) return vnode.props.class;
  if (vnode.props?.className) return vnode.props.className;
  if (Array.isArray(vnode)) { for (const c of vnode) { const r = findClass(c); if (r) return r; } }
  if (vnode.props?.children) return findClass(vnode.props.children);
  return '';
}

test('renders F1 value formatted to 2 decimal places', () => {
  expect(findText(F1Score({ value: 0.87 }))).toMatch(/0\.87/);
});

test('green class for value >= 0.80', () => {
  expect(findClass(F1Score({ value: 0.85 }))).toMatch(/green|good|high/i);
});

test('amber class for value 0.60-0.79', () => {
  expect(findClass(F1Score({ value: 0.72 }))).toMatch(/amber|warn|medium/i);
});

test('red class for value < 0.60', () => {
  expect(findClass(F1Score({ value: 0.45 }))).toMatch(/red|error|low/i);
});

test('shows positive delta', () => {
  expect(findText(F1Score({ value: 0.87, delta: 0.12 }))).toMatch(/\+0\.12/);
});

test('shows negative delta', () => {
  expect(findText(F1Score({ value: 0.70, delta: -0.05 }))).toMatch(/-0\.05/);
});

test('omits delta when not provided', () => {
  const text = findText(F1Score({ value: 0.87 }));
  expect(text).not.toMatch(/\+|-0/);
});

test('renders without crashing when value is null', () => {
  expect(() => F1Score({ value: null })).not.toThrow();
});
```

**Step 2: Run to verify fails**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=F1Score
```

**Step 3: Implement**

Create `src/components/F1Score.jsx`:
```jsx
/**
 * What it shows: An F1 score (0 to 1) for a prompt variant, color-coded by quality.
 *   Green = great (≥0.80), amber = okay (0.60–0.79), red = poor (<0.60).
 *   Optional delta shows how much it changed from the previous score.
 * Decision it drives: Is this variant good enough to promote? Is it improving?
 *
 * Plain-English tooltip: "F1 measures how often this variant correctly identifies
 *   relevant lessons. 1.0 = perfect, 0.0 = useless."
 */

function scoreClass(value) {
  if (value === null || value === undefined) return 'f1-score f1-unknown';
  if (value >= 0.80) return 'f1-score f1-high f1-green';
  if (value >= 0.60) return 'f1-score f1-medium f1-amber f1-warn';
  return 'f1-score f1-low f1-red f1-error';
}

const TOOLTIP = 'F1 measures how often this variant correctly identifies relevant lessons. 1.0 = perfect, 0.0 = useless.';

export default function F1Score({ value, delta, showTooltip = true }) {
  if (value === null || value === undefined) {
    return <span class="f1-score f1-unknown">—</span>;
  }
  const formatted = value.toFixed(2);
  const deltaEl = delta !== undefined && delta !== null
    ? <span class={delta >= 0 ? 'f1-delta f1-delta-pos' : 'f1-delta f1-delta-neg'}>
        {delta >= 0 ? '+' : ''}{delta.toFixed(2)}
      </span>
    : null;

  return (
    <span class={scoreClass(value)} title={showTooltip ? TOOLTIP : undefined}>
      {formatted}
      {deltaEl}
    </span>
  );
}
```

Add CSS to `src/index.css`:
```css
.f1-score       { display: inline-flex; align-items: center; gap: 4px; font-variant-numeric: tabular-nums; font-weight: 600; padding: 2px 6px; border-radius: 4px; }
.f1-high        { color: var(--color-success, #22c55e); }
.f1-medium      { color: var(--color-warning, #f59e0b); }
.f1-low         { color: var(--color-error, #ef4444); }
.f1-unknown     { color: var(--color-muted, #6b7280); }
.f1-delta       { font-size: 0.8em; font-weight: 400; }
.f1-delta-pos   { color: var(--color-success, #22c55e); }
.f1-delta-neg   { color: var(--color-error, #ef4444); }
```

**Step 4: Run tests, commit**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=F1Score
git add src/components/F1Score.jsx src/components/F1Score.test.js src/index.css
git commit -m "feat(spa): add F1Score component with threshold colors and delta display"
```

---

### Task 1.5: Create ModelChip component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/ModelChip.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/ModelChip.test.js`

**Note:** ModelChip imports `currentTab` and `modelFilter` from stores. Since `modelFilter` doesn't exist yet, define a local fallback signal inside the component. Batch 2 adds it to the store — the chip will use it automatically once the store export exists.

**Step 1: Write the failing test**

Create `src/components/ModelChip.test.js`:
```js
import _ModelChip from './ModelChip.jsx';
const ModelChip = _ModelChip.default || _ModelChip;

function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string') return vnode;
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode.props) { const c = vnode.props.children; return Array.isArray(c) ? c.map(findText).join('') : findText(c); }
  return '';
}

test('renders model name', () => {
  expect(findText(ModelChip({ name: 'deepseek-r1:8b' }))).toMatch(/deepseek-r1:8b/);
});

test('renders without crashing when no props', () => {
  expect(() => ModelChip({ name: 'qwen2.5:7b' })).not.toThrow();
});

test('shows live indicator when isLive=true', () => {
  const vnode = ModelChip({ name: 'deepseek-r1:8b', isLive: true });
  const text = findText(vnode);
  // LiveIndicator adds a dot — check component renders something extra
  expect(vnode).toBeTruthy();
});

test('shows evalRole label when provided', () => {
  const text = findText(ModelChip({ name: 'deepseek-r1:8b', evalRole: 'judge' }));
  expect(text).toMatch(/judge/i);
});

test('renders without evalRole when not provided', () => {
  const text = findText(ModelChip({ name: 'deepseek-r1:8b' }));
  expect(text).not.toMatch(/judge|generator/i);
});
```

**Step 2: Run to verify fails**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=ModelChip
```

**Step 3: Implement**

Create `src/components/ModelChip.jsx`:
```jsx
/**
 * What it shows: A clickable pill showing an AI model's name.
 *   Optional: a pulsing dot when the model is actively running,
 *   and a role badge if it's the current eval judge or generator.
 * Decision it drives: Click to jump to that model's detail page.
 *   The live dot tells you the model is busy right now.
 */
import { currentTab } from '../stores/index.js';

// modelFilter added in Batch 2 — graceful fallback if not yet exported
let modelFilter;
try { modelFilter = require('../stores/index.js').modelFilter; } catch { modelFilter = { value: null }; }

const ROLE_LABELS = { judge: 'judge', generator: 'gen' };

export default function ModelChip({ name, provider, isLive = false, evalRole = null }) {
  if (!name) return null;

  function handleClick(e) {
    e.stopPropagation();
    if (modelFilter) modelFilter.value = name;
    currentTab.value = 'models';
  }

  return (
    <button
      class={`model-chip${isLive ? ' model-chip--live' : ''}`}
      onClick={handleClick}
      title={`View ${name} on Models page`}
    >
      {isLive && <span class="model-chip__dot" aria-hidden="true" />}
      <span class="model-chip__name">{name}</span>
      {evalRole && <span class="model-chip__role">{ROLE_LABELS[evalRole] || evalRole}</span>}
    </button>
  );
}
```

Add CSS to `src/index.css`:
```css
.model-chip         { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 12px; border: 1px solid var(--border, #374151); background: var(--surface-2, #1f2937); color: var(--text, #f3f4f6); font-size: 0.75rem; cursor: pointer; transition: border-color 0.15s; white-space: nowrap; }
.model-chip:hover   { border-color: var(--accent, #6366f1); }
.model-chip--live   { border-color: var(--color-info, #3b82f6); }
.model-chip__dot    { width: 6px; height: 6px; border-radius: 50%; background: var(--color-info, #3b82f6); animation: pulse 1.5s infinite; flex-shrink: 0; }
.model-chip__role   { font-size: 0.65rem; padding: 0 4px; border-radius: 3px; background: var(--accent, #6366f1); color: white; }
```

**Step 4: Run tests, commit**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=ModelChip
git add src/components/ModelChip.jsx src/components/ModelChip.test.js src/index.css
git commit -m "feat(spa): add ModelChip — clickable model name with live indicator"
```

---

### Task 1.6: Create VariantChip component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/VariantChip.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/VariantChip.test.js`

**Step 1: Write the failing test**

Create `src/components/VariantChip.test.js`:
```js
import _VariantChip from './VariantChip.jsx';
const VariantChip = _VariantChip.default || _VariantChip;

function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string') return vnode;
  if (typeof vnode === 'number') return String(vnode);
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode.props) { const c = vnode.props.children; return Array.isArray(c) ? c.map(findText).join('') : findText(c); }
  return '';
}

test('renders variant label', () => {
  expect(findText(VariantChip({ id: 'C', label: 'variant-C' }))).toMatch(/variant-C/);
});

test('shows gold star for production variant', () => {
  expect(findText(VariantChip({ id: 'C', label: 'C', isProduction: true }))).toMatch(/★/);
});

test('shows silver star for recommended-only variant', () => {
  expect(findText(VariantChip({ id: 'C', label: 'C', isRecommended: true, isProduction: false }))).toMatch(/☆/);
});

test('shows no star for unranked variant', () => {
  const text = findText(VariantChip({ id: 'C', label: 'C', isProduction: false, isRecommended: false }));
  expect(text).not.toMatch(/★|☆/);
});

test('renders provider badge when provided', () => {
  expect(findText(VariantChip({ id: 'C', label: 'C', provider: 'claude' }))).toMatch(/claude/);
});

test('renders without crashing with minimal props', () => {
  expect(() => VariantChip({ id: 'A', label: 'A' })).not.toThrow();
});
```

**Step 2: Run to verify fails**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=VariantChip
```

**Step 3: Implement**

Create `src/components/VariantChip.jsx`:
```jsx
/**
 * What it shows: A clickable pill identifying a prompt variant by name.
 *   Gold star (★) = this variant is in production (the official best one).
 *   Silver star (☆) = recommended but not yet promoted to production.
 *   Provider badge shows whether it uses Ollama, Claude, or OpenAI.
 * Decision it drives: Click to jump to this variant's card in the Eval tab.
 *   Stars tell you which variant to trust when running new jobs.
 */
import F1Score from './F1Score.jsx';
import { currentTab } from '../stores/index.js';

// focusVariantId + evalSubTab added in Batch 2 / Batch 4 — graceful fallback
let evalSubTab, focusVariantId;
try {
  const evalStore = require('../stores/eval.js');
  evalSubTab = evalStore.evalSubTab;
  focusVariantId = evalStore.focusVariantId;
} catch { evalSubTab = { value: 'variants' }; focusVariantId = { value: null }; }

export default function VariantChip({ id, label, f1, provider, isProduction = false, isRecommended = false }) {
  if (!label) return null;

  const star = isProduction ? '★' : (isRecommended ? '☆' : null);

  function handleClick(e) {
    e.stopPropagation();
    if (focusVariantId) focusVariantId.value = id;
    if (evalSubTab) evalSubTab.value = 'variants';
    currentTab.value = 'eval';
  }

  return (
    <button class="variant-chip" onClick={handleClick} title={`View ${label} in Eval tab`}>
      {star && <span class={`variant-chip__star${isProduction ? ' variant-chip__star--gold' : ''}`}>{star}</span>}
      <span class="variant-chip__label">{label}</span>
      {provider && <span class={`variant-chip__provider variant-chip__provider--${provider}`}>{provider}</span>}
      {f1 !== undefined && f1 !== null && <F1Score value={f1} showTooltip={false} />}
    </button>
  );
}
```

Add CSS to `src/index.css`:
```css
.variant-chip                   { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 12px; border: 1px solid var(--border, #374151); background: var(--surface-2, #1f2937); color: var(--text, #f3f4f6); font-size: 0.75rem; cursor: pointer; transition: border-color 0.15s; white-space: nowrap; }
.variant-chip:hover             { border-color: var(--accent, #6366f1); }
.variant-chip__star             { color: var(--color-muted, #9ca3af); }
.variant-chip__star--gold       { color: var(--color-warning, #f59e0b); }
.variant-chip__provider         { font-size: 0.65rem; padding: 1px 4px; border-radius: 3px; background: var(--surface-3, #374151); }
.variant-chip__provider--claude { background: var(--color-claude, #8b5cf6); color: white; }
.variant-chip__provider--openai { background: var(--color-openai, #10b981); color: white; }
.variant-chip__provider--ollama { background: var(--surface-3, #374151); color: var(--text-muted, #9ca3af); }
```

**Step 4: Run tests, commit**
```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=VariantChip
git add src/components/VariantChip.jsx src/components/VariantChip.test.js src/index.css
git commit -m "feat(spa): add VariantChip — clickable variant identity with star/provider badges"
```

---

### Task 1.7: Audit and replace inline renders across all pages

**Files:** Modify all 8 page files + affected components

**Step 1: Run the audit**

Run this to find all targets:
```bash
cd ollama_queue/dashboard/spa/src
grep -rn "status === 'running'\|status === 'failed'\|status === 'complete'" pages/ components/ --include="*.jsx" | grep -v "StatusPill" | grep "className\|class=" | head -30
grep -rn "priority.*class\|class.*priority" pages/ components/ --include="*.jsx" | grep -v "PriorityPill" | head -20
grep -rn "\.toFixed\|duration.*min\|ms /" pages/ components/ --include="*.jsx" | grep -v "formatDuration" | head -20
grep -rn "f1.*toFixed\|toFixed.*f1\|\.f1}" pages/ components/ --include="*.jsx" | grep -v "F1Score" | head -20
```

**Step 2: Replace status renders**

In each file that renders status as a CSS-class string, replace with `<StatusPill status={job.status} />`.
Add import at top of each modified file: `import StatusPill from '../components/StatusPill.jsx';`
(Adjust relative path based on the file's location.)

Key files to update: `pages/Now.jsx`, `pages/History.jsx`, `pages/Plan/index.jsx`, `components/eval/RunHistoryTable.jsx`, `components/eval/ResultsTable.jsx`, `pages/Consumers.jsx`

**Step 3: Replace priority renders**

In files that render priority as a number or colored class:
Add import: `import PriorityPill, { numericToLevel } from '../components/PriorityPill.jsx';`
Replace inline priority renders with `<PriorityPill level={numericToLevel(job.priority)} />`

**Step 4: Replace duration renders**

Add import: `import { formatDuration } from '../utils/time.js';`
Replace all raw `ms`, `seconds`, `toFixed()` duration renders with `formatDuration(value)`.

**Step 5: Replace F1 renders**

Add import: `import F1Score from '../components/F1Score.jsx';`
Replace `{variant.latest_f1?.toFixed(2)}` and similar with `<F1Score value={variant.latest_f1} />`

**Step 6: Replace model name renders in key locations**

In `pages/Now.jsx` and `pages/History.jsx`, wrap model name strings with ModelChip:
```jsx
import ModelChip from '../components/ModelChip.jsx';
// Replace: <span>{job.model}</span>
// With:    <ModelChip name={job.model} isLive={job.status === 'running'} />
```

**Step 7: Verify build passes**
```bash
cd ollama_queue/dashboard/spa && npm run build
```
Expected: "Done in Xs"

**Step 8: Run full test suite**
```bash
cd ollama_queue/dashboard/spa && npm test
```
Expected: all tests pass

**Step 9: Commit**
```bash
git add -p  # stage selectively — review each diff
git commit -m "refactor(spa): replace inline status/priority/duration/F1/model renders with shared components"
```

---

### Task 1.8: Batch 1 quality gate

**Step 1: Verify all 5 component files exist**
```bash
test -f ollama_queue/dashboard/spa/src/components/StatusPill.jsx && \
test -f ollama_queue/dashboard/spa/src/components/PriorityPill.jsx && \
test -f ollama_queue/dashboard/spa/src/components/F1Score.jsx && \
test -f ollama_queue/dashboard/spa/src/components/ModelChip.jsx && \
test -f ollama_queue/dashboard/spa/src/components/VariantChip.jsx && \
echo "ALL FILES OK"
```

**Step 2: Run all frontend tests**
```bash
cd ollama_queue/dashboard/spa && npm test
```
Expected: PASS (note current count — it will increase after this batch)

**Step 3: Build**
```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 4: Run Python tests (regression check)**
```bash
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q
```
Expected: all pass (Python tests don't depend on SPA)

**Step 5: Check for h-shadowing**
```bash
grep -rn "map(h =>" ollama_queue/dashboard/spa/src/
```
Expected: no output (zero violations)

---

## Batch 2 — Global State Strips + Sidebar

> **What this does:** Adds the new signals, creates the global strip components (ActiveJobStrip, ActiveEvalStrip, EvalWinnerChip, SystemSummaryLine, CohesionHeader), wires them into app.jsx and Sidebar.jsx. After this batch, the system's state is always visible regardless of which tab is active.

---

### Task 2.1: Add new signals to stores

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/stores/eval.js`
- Modify: `ollama_queue/dashboard/spa/src/stores/index.js`

**Step 1: Add computed signals to eval.js**

Add after the existing signals in `src/stores/eval.js`:
```js
import { signal, computed } from '@preact/signals';

// ── New signals for Batch 2 ────────────────────────────────────────────────

// What it shows: The current recommended/production variant (the "winner").
// Decision it drives: Every place that shows "who's winning" reads this.
export const evalWinner = computed(() =>
  evalVariants.value.find(v => v.is_recommended || v.is_production) || null
);

// What it shows: Number of eval runs scheduled in the next 4 hours.
// Decision it drives: Plan tab badge — is there an eval coming that will use the GPU?
export const scheduledEvalCount = signal(0);

// What it shows: Which variant the user wants to focus on in the Variants tab.
// Decision it drives: VariantChip clicks set this; Variants tab scrolls to match.
export const focusVariantId = signal(null);
```

Also update `fetchEvalRuns` to poll for scheduled count:
```js
// Add this line inside fetchEvalRuns() after updating evalRuns.value:
fetch(`${API}/eval/runs?status=scheduled&within_hours=4`)
  .then(r => r.ok ? r.json() : [])
  .then(data => { scheduledEvalCount.value = Array.isArray(data) ? data.length : (data.items?.length || 0); })
  .catch(() => {});
```

**Step 2: Add navigation signals to stores/index.js**

Add after the existing signals:
```js
// What it shows: Which job ID to highlight when navigating from History to Now.
// Decision it drives: History "View context" button sets this; Now.jsx pulses that row.
export const highlightJobId = signal(null);

// What it shows: Which model name to filter to on the Models tab.
// Decision it drives: ModelChip clicks set this; ModelsTab filters/scrolls to match.
export const modelFilter = signal(null);
```

**Step 3: Verify build**
```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 4: Commit**
```bash
git add ollama_queue/dashboard/spa/src/stores/eval.js ollama_queue/dashboard/spa/src/stores/index.js
git commit -m "feat(spa): add evalWinner, scheduledEvalCount, highlightJobId, modelFilter signals"
```

---

### Task 2.2: Create LiveIndicator component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/LiveIndicator.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/LiveIndicator.test.js`

Create `src/components/LiveIndicator.jsx`:
```jsx
/**
 * What it shows: A small animated dot showing that something is active right now.
 *   Blue pulse = actively running, slow grey = queued, blue accent = in eval.
 * Decision it drives: "Is this model/job live right now? Safe to change settings?"
 */
export default function LiveIndicator({ state = 'running', pulse = true }) {
  const stateClass = {
    running:  'live-indicator live-indicator--running',
    queued:   'live-indicator live-indicator--queued',
    'in-eval': 'live-indicator live-indicator--eval',
  }[state] || 'live-indicator live-indicator--running';

  return <span class={`${stateClass}${pulse ? ' live-indicator--pulse' : ''}`} aria-label={`${state}`} />;
}
```

Add CSS to `src/index.css`:
```css
.live-indicator         { display: inline-block; width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.live-indicator--running { background: var(--color-info, #3b82f6); }
.live-indicator--queued  { background: var(--color-muted, #6b7280); }
.live-indicator--eval    { background: var(--accent, #6366f1); }
.live-indicator--pulse   { animation: pulse 1.5s ease-in-out infinite; }
```

Test file `src/components/LiveIndicator.test.js`:
```js
import _LiveIndicator from './LiveIndicator.jsx';
const LiveIndicator = _LiveIndicator.default || _LiveIndicator;

function findClass(vnode) {
  if (!vnode) return '';
  if (vnode.props?.class) return vnode.props.class;
  if (vnode.props?.className) return vnode.props.className;
  return '';
}

test('renders running state', () => { expect(findClass(LiveIndicator({ state: 'running' }))).toMatch(/running/); });
test('renders queued state', () => { expect(findClass(LiveIndicator({ state: 'queued' }))).toMatch(/queued/); });
test('renders in-eval state', () => { expect(findClass(LiveIndicator({ state: 'in-eval' }))).toMatch(/eval/); });
test('includes pulse class by default', () => { expect(findClass(LiveIndicator({}))).toMatch(/pulse/); });
test('omits pulse when pulse=false', () => { expect(findClass(LiveIndicator({ pulse: false }))).not.toMatch(/pulse/); });
```

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=LiveIndicator
git add src/components/LiveIndicator.jsx src/components/LiveIndicator.test.js src/index.css
git commit -m "feat(spa): add LiveIndicator component"
```

---

### Task 2.3: Create ActiveJobStrip component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/ActiveJobStrip.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/ActiveJobStrip.test.js`

Create `src/components/ActiveJobStrip.jsx`:
```jsx
/**
 * What it shows: A thin bar at the top of every page showing what job is
 *   running right now — model name, how long it's been running, and how
 *   many jobs are waiting. Disappears when nothing is running.
 * Decision it drives: "Is the system busy? Can I submit another job?"
 *   Visible even when you've switched away from the Now tab.
 */
import { currentJob, queueDepth } from '../stores/index.js';
import ModelChip from './ModelChip.jsx';
import { formatDuration } from '../utils/time.js';

export default function ActiveJobStrip() {
  const job = currentJob.value;
  if (!job) return null;

  const elapsed = job.started_at ? Date.now() / 1000 - job.started_at : null;

  return (
    <div class="active-job-strip" role="status" aria-live="polite">
      <LiveIndicator state="running" />
      <ModelChip name={job.model} isLive />
      {elapsed !== null && <span class="active-job-strip__time">{formatDuration(Math.floor(elapsed))}</span>}
      {queueDepth.value > 0 && (
        <span class="active-job-strip__queue">{queueDepth.value} waiting</span>
      )}
    </div>
  );
}
```

Note: Import `LiveIndicator` at top: `import LiveIndicator from './LiveIndicator.jsx';`

Add CSS to `src/index.css`:
```css
.active-job-strip        { display: flex; align-items: center; gap: 8px; padding: 4px 12px; background: var(--surface-2, #1f2937); border-bottom: 1px solid var(--border, #374151); font-size: 0.8rem; color: var(--text-muted, #9ca3af); }
.active-job-strip__time  { font-variant-numeric: tabular-nums; }
.active-job-strip__queue { margin-left: auto; }
@media (max-width: 768px) { .active-job-strip { display: none; } }
```

Write test `src/components/ActiveJobStrip.test.js`:
```js
import _ActiveJobStrip from './ActiveJobStrip.jsx';
const ActiveJobStrip = _ActiveJobStrip.default || _ActiveJobStrip;

// Mock stores
jest.mock('../stores/index.js', () => ({
  currentJob: { value: null },
  queueDepth: { value: 0 },
}));
jest.mock('../utils/time.js', () => ({ formatDuration: ms => `${ms}s` }));

const stores = require('../stores/index.js');

test('returns null when no active job', () => {
  stores.currentJob.value = null;
  expect(ActiveJobStrip()).toBeNull();
});

test('renders job model name when active', () => {
  stores.currentJob.value = { model: 'deepseek-r1:8b', status: 'running', started_at: null };
  const vnode = ActiveJobStrip();
  expect(vnode).toBeTruthy();
});
```

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=ActiveJobStrip
git add src/components/ActiveJobStrip.jsx src/components/ActiveJobStrip.test.js src/index.css
git commit -m "feat(spa): add ActiveJobStrip — persistent running job indicator across all tabs"
```

---

### Task 2.4: Create ActiveEvalStrip component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/ActiveEvalStrip.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/ActiveEvalStrip.test.js`

Create `src/components/ActiveEvalStrip.jsx`:
```jsx
/**
 * What it shows: A thin bar showing eval test progress from any page.
 *   Phase label in plain English, a progress bar, and the current best
 *   F1 score. Disappears when no eval is running.
 * Decision it drives: "Is the eval test still running? What phase is it in?
 *   Should I wait for results before changing anything?"
 */
import { evalActiveRun } from '../stores/eval.js';
import { currentTab } from '../stores/index.js';
import F1Score from './F1Score.jsx';
import VariantChip from './VariantChip.jsx';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import { cancelEvalRun } from '../stores/eval.js';

const PHASE_LABELS = {
  generating: 'Generating outputs',
  judging:    'Scoring with judge',
  analyzing:  'Analyzing results',
  promoting:  'Deciding winner',
};

export default function ActiveEvalStrip() {
  const run = evalActiveRun.value;
  if (!run || ['complete', 'failed', 'cancelled'].includes(run.status)) return null;

  const [fb, act] = useActionFeedback();
  const phaseLabel = PHASE_LABELS[run.phase] || run.phase || 'Running';
  const progress = run.progress_pct ?? 0;

  function handleClick() { currentTab.value = 'eval'; }

  return (
    <div class="active-eval-strip" role="status" aria-live="polite">
      <span class="active-eval-strip__label" onClick={handleClick} style="cursor:pointer">
        Eval: {phaseLabel}
      </span>
      <div class="active-eval-strip__bar">
        <div class="active-eval-strip__fill" style={`width:${progress}%`} />
      </div>
      {run.best_f1_so_far != null && <F1Score value={run.best_f1_so_far} showTooltip={false} />}
      <button
        class="active-eval-strip__cancel"
        disabled={fb.phase === 'loading'}
        onClick={() => act('Cancelling…', () => cancelEvalRun(run.run_id), () => 'Cancelled')}
      >
        {fb.phase === 'loading' ? '…' : '✕'}
      </button>
      {fb.msg && <span class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</span>}
    </div>
  );
}
```

Add CSS to `src/index.css`:
```css
.active-eval-strip         { display: flex; align-items: center; gap: 8px; padding: 4px 12px; background: color-mix(in srgb, var(--accent, #6366f1) 10%, var(--surface-2, #1f2937)); border-bottom: 1px solid var(--accent, #6366f1); font-size: 0.8rem; }
.active-eval-strip__bar    { flex: 1; height: 4px; background: var(--surface-3, #374151); border-radius: 2px; min-width: 60px; }
.active-eval-strip__fill   { height: 100%; background: var(--accent, #6366f1); border-radius: 2px; transition: width 0.3s ease; }
.active-eval-strip__cancel { padding: 0 6px; border: none; background: none; color: var(--text-muted, #9ca3af); cursor: pointer; }
@media (max-width: 768px)  { .active-eval-strip { display: none; } }
```

Write minimal test:
```js
// src/components/ActiveEvalStrip.test.js
import _ActiveEvalStrip from './ActiveEvalStrip.jsx';
const ActiveEvalStrip = _ActiveEvalStrip.default || _ActiveEvalStrip;

jest.mock('../stores/eval.js', () => ({
  evalActiveRun: { value: null },
  cancelEvalRun: jest.fn(),
}));
jest.mock('../stores/index.js', () => ({ currentTab: { value: 'now' } }));
jest.mock('../hooks/useActionFeedback.js', () => ({ useActionFeedback: () => [{ phase: 'idle', msg: '' }, jest.fn()] }));

const evalStore = require('../stores/eval.js');

test('returns null when no active eval', () => {
  evalStore.evalActiveRun.value = null;
  expect(ActiveEvalStrip()).toBeNull();
});

test('renders when eval is running', () => {
  evalStore.evalActiveRun.value = { run_id: 1, phase: 'judging', status: 'judging', progress_pct: 60 };
  expect(ActiveEvalStrip()).toBeTruthy();
});
```

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=ActiveEvalStrip
git add src/components/ActiveEvalStrip.jsx src/components/ActiveEvalStrip.test.js src/index.css
git commit -m "feat(spa): add ActiveEvalStrip — persistent eval progress strip across all tabs"
```

---

### Task 2.5: Create EvalWinnerChip + SystemSummaryLine + CohesionHeader

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/EvalWinnerChip.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/SystemSummaryLine.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/CohesionHeader.jsx`

**EvalWinnerChip** (`src/components/EvalWinnerChip.jsx`):
```jsx
/**
 * What it shows: Always-visible chip in the sidebar showing which prompt variant
 *   is currently winning. Gold star = it's in production. Silver = recommended only.
 * Decision it drives: "Has the winner changed? Do I need to promote it?"
 *   Click to jump to Eval Trends.
 */
import { evalWinner } from '../stores/eval.js';
import { currentTab, evalSubTab } from '../stores/index.js';
import F1Score from './F1Score.jsx';

export default function EvalWinnerChip() {
  const winner = evalWinner.value;
  if (!winner) return null;

  function handleClick() {
    if (typeof evalSubTab !== 'undefined') evalSubTab.value = 'timeline';
    currentTab.value = 'eval';
  }

  const star = winner.is_production ? '★' : '☆';

  return (
    <button class="eval-winner-chip" onClick={handleClick} title="View eval trends">
      <span class={winner.is_production ? 'eval-winner-chip__star--gold' : 'eval-winner-chip__star'}>{star}</span>
      <span class="eval-winner-chip__label">{winner.label || winner.id}</span>
      {winner.latest_f1 != null && <F1Score value={winner.latest_f1} showTooltip={false} />}
    </button>
  );
}
```

**SystemSummaryLine** (`src/components/SystemSummaryLine.jsx`):
```jsx
/**
 * What it shows: One sentence describing the whole system: how many jobs are
 *   waiting, what's running, and which eval variant is winning.
 * Decision it drives: "Do I need to take action anywhere, or is everything fine?"
 */
import { currentJob, queueDepth } from '../stores/index.js';
import { evalWinner } from '../stores/eval.js';
import ModelChip from './ModelChip.jsx';
import VariantChip from './VariantChip.jsx';

export default function SystemSummaryLine() {
  const job = currentJob.value;
  const depth = queueDepth.value || 0;
  const winner = evalWinner.value;

  return (
    <div class="system-summary-line">
      <span>{depth} queued</span>
      {job
        ? <><span>·</span><ModelChip name={job.model} isLive /></>
        : <span>· idle</span>
      }
      {winner && (
        <><span>·</span>
        <VariantChip id={winner.id} label={winner.label || winner.id} f1={winner.latest_f1} isProduction={winner.is_production} isRecommended={winner.is_recommended} /></>
      )}
    </div>
  );
}
```

**CohesionHeader** (`src/components/CohesionHeader.jsx`):
```jsx
/**
 * What it shows: A thin sticky strip at the top of every page summarizing the
 *   system state in one line: jobs waiting, what's running, eval winner.
 * Decision it drives: "Do I need to switch tabs to take action, or can I keep
 *   focused on what I'm doing?"
 */
import SystemSummaryLine from './SystemSummaryLine.jsx';
import { dlqCount } from '../stores/index.js';

export default function CohesionHeader() {
  return (
    <header class="cohesion-header">
      <SystemSummaryLine />
      {dlqCount.value > 0 && (
        <span class="cohesion-header__dlq-badge">{dlqCount.value} DLQ</span>
      )}
    </header>
  );
}
```

Add CSS to `src/index.css`:
```css
.cohesion-header          { display: flex; align-items: center; justify-content: space-between; padding: 4px 16px; background: var(--surface-1, #111827); border-bottom: 1px solid var(--border, #374151); font-size: 0.75rem; color: var(--text-muted, #9ca3af); position: sticky; top: 0; z-index: 10; }
.cohesion-header__dlq-badge { padding: 2px 8px; border-radius: 10px; background: var(--color-error, #ef4444); color: white; font-weight: 600; }
.system-summary-line      { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.eval-winner-chip         { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--border, #374151); background: none; color: var(--text, #f3f4f6); font-size: 0.75rem; cursor: pointer; }
.eval-winner-chip__star--gold { color: var(--color-warning, #f59e0b); }
@media (max-width: 768px)  { .cohesion-header { display: none; } }
```

Write minimal tests for all three:
```js
// src/components/EvalWinnerChip.test.js
jest.mock('../stores/eval.js', () => ({ evalWinner: { value: null } }));
jest.mock('../stores/index.js', () => ({ currentTab: { value: 'now' } }));
import _EvalWinnerChip from './EvalWinnerChip.jsx';
const EvalWinnerChip = _EvalWinnerChip.default || _EvalWinnerChip;
test('returns null when no winner', () => { expect(EvalWinnerChip()).toBeNull(); });
test('renders when winner exists', () => {
  require('../stores/eval.js').evalWinner.value = { id: 'C', label: 'variant-C', latest_f1: 0.87, is_production: true };
  expect(EvalWinnerChip()).toBeTruthy();
});
```

```bash
cd ollama_queue/dashboard/spa
npm test -- --testPathPattern="EvalWinnerChip|SystemSummaryLine|CohesionHeader"
git add src/components/EvalWinnerChip.jsx src/components/SystemSummaryLine.jsx src/components/CohesionHeader.jsx src/components/EvalWinnerChip.test.js src/index.css
git commit -m "feat(spa): add EvalWinnerChip, SystemSummaryLine, CohesionHeader"
```

---

### Task 2.6: Wire global strips into app.jsx and Sidebar.jsx

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/app.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/Sidebar.jsx`

**Step 1: Edit app.jsx**

Find the section that renders the main layout. Add the strips between the header and the main content div:

```jsx
// Add imports at top of app.jsx:
import CohesionHeader from './components/CohesionHeader.jsx';
import ActiveJobStrip from './components/ActiveJobStrip.jsx';
import ActiveEvalStrip from './components/ActiveEvalStrip.jsx';

// In the render return, before the page content:
// Find the <main> or main content wrapper and add before it:
<CohesionHeader />
<ActiveJobStrip />
<ActiveEvalStrip />
```

Hide strips on Now tab (redundant) and eval tab (already shown inline) via conditional:
```jsx
{currentTab.value !== 'now' && <ActiveJobStrip />}
{currentTab.value !== 'eval' && <ActiveEvalStrip />}
```

**Step 2: Edit Sidebar.jsx**

Find the health chip section. Add SystemSummaryLine and EvalWinnerChip below it:
```jsx
import SystemSummaryLine from './SystemSummaryLine.jsx';
import EvalWinnerChip from './EvalWinnerChip.jsx';

// In sidebar render, below <SystemHealthChip ...>:
<div class="sidebar-summary">
  <SystemSummaryLine />
  <EvalWinnerChip />
</div>
```

Hide in icon-only sidebar mode:
```css
/* In src/index.css */
@media (min-width: 768px) and (max-width: 1023px) { .sidebar-summary { display: none; } }
```

**Critical:** Check Sidebar.jsx for any `.map(h => ...)` patterns — rename callback to avoid JSX factory shadowing.

**Step 3: Build and verify**
```bash
cd ollama_queue/dashboard/spa && npm run build
cd ollama_queue/dashboard/spa && npm test
```

**Step 4: Commit**
```bash
git add ollama_queue/dashboard/spa/src/app.jsx ollama_queue/dashboard/spa/src/components/Sidebar.jsx ollama_queue/dashboard/spa/src/index.css
git commit -m "feat(spa): wire CohesionHeader, ActiveJobStrip, ActiveEvalStrip into layout; extend Sidebar with summary line"
```

---

## Batch 3 — Cross-Page Data Propagation

> **What this does:** Wires eval data into non-Eval pages — eval runs on Plan Gantt, eval winner badge on Models, eval events in History heatmap, deep-links from History to Now, live model indicators, eval annotations on Performance, EVAL badge on Plan nav, variant lineage tooltip.

---

### Task 3.1: Wire modelFilter signal into ModelsTab

**File:** `ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx`

Add at top:
```jsx
import { modelFilter } from '../stores/index.js';
```

Inside the component, add filter logic:
```jsx
// Filter models list when modelFilter is set
const filter = modelFilter.value;
const displayedModels = filter
  ? models.filter(m => m.name === filter || m.name.includes(filter))
  : models;

// Clear button shown when filter active
{filter && (
  <button class="model-filter-clear" onClick={() => { modelFilter.value = null; }}>
    Showing: {filter} ✕
  </button>
)}
```

Use `displayedModels` in the render instead of `models`. Build and commit:
```bash
git add ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx
git commit -m "feat(spa): wire modelFilter signal into ModelsTab for ModelChip deep-links"
```

---

### Task 3.2: Implement job deep-link from History → Now

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/History.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/Now.jsx`

**In History.jsx** — add to DLQ entry rows:
```jsx
import { highlightJobId, currentTab } from '../stores/index.js';

// In each DLQ row render:
<button
  class="dlq-view-context"
  onClick={() => { highlightJobId.value = entry.job_id; currentTab.value = 'now'; }}
>
  → View context
</button>
```

**In Now.jsx** — add pulse effect for highlighted job:
```jsx
import { highlightJobId } from '../stores/index.js';

// In job row render, add class conditionally:
class={`job-row${highlightJobId.value === job.id ? ' job-row--highlight' : ''}`}

// After 3 seconds, clear the highlight:
// Add this in a useEffect or via a setTimeout in the click handler:
if (highlightJobId.value === job.id) {
  setTimeout(() => { if (highlightJobId.value === job.id) highlightJobId.value = null; }, 3000);
}
```

Add CSS: `.job-row--highlight { animation: highlight-pulse 3s ease-out; }`

```bash
git add ollama_queue/dashboard/spa/src/pages/History.jsx ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat(spa): add job deep-link from History DLQ to Now tab with pulse highlight"
```

---

### Task 3.3: Create EvalRoleBadge and add to ModelsTab

**File:** Create `ollama_queue/dashboard/spa/src/components/EvalRoleBadge.jsx`

```jsx
/**
 * What it shows: Whether this AI model is currently the best "judge" (the one
 *   that scores test outputs) or "generator" (the one being tested), according
 *   to the most recent eval results. Shows the F1 score it achieved.
 * Decision it drives: "Should I keep using this model as my judge? Is a better
 *   option available?"
 */
import F1Score from './F1Score.jsx';
import { currentTab } from '../stores/index.js';

export default function EvalRoleBadge({ role, f1, runId }) {
  const label = role === 'judge' ? 'judge' : 'generator';

  function handleClick(e) {
    e.stopPropagation();
    currentTab.value = 'eval';
  }

  return (
    <button class={`eval-role-badge eval-role-badge--${role}`} onClick={handleClick} title="View eval results">
      <span class="eval-role-badge__label">{label}</span>
      {f1 != null && <F1Score value={f1} showTooltip={false} />}
    </button>
  );
}
```

**In ModelsTab.jsx** — add badge after the model name chip:
```jsx
import { evalVariants } from '../stores/eval.js';
import EvalRoleBadge from '../components/EvalRoleBadge.jsx';

// Derive production variant info
const productionVariant = evalVariants.value.find(v => v.is_production);
const judgeModel = productionVariant?.judge_model;
const generatorModel = productionVariant?.model;

// In model row render:
{model.name === judgeModel && <EvalRoleBadge role="judge" f1={productionVariant?.latest_f1} />}
{model.name === generatorModel && <EvalRoleBadge role="generator" f1={productionVariant?.latest_f1} />}
```

```bash
git add ollama_queue/dashboard/spa/src/components/EvalRoleBadge.jsx ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx
git commit -m "feat(spa): add EvalRoleBadge — shows eval winner role on Models tab"
```

---

### Task 3.4: Add scheduled eval runs to Plan Gantt

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/stores/eval.js`
- Modify: `ollama_queue/dashboard/spa/src/pages/Plan/index.jsx`

**In stores/eval.js** — add scheduled runs signal:
```js
export const scheduledEvalRuns = signal([]);

// Add fetch in fetchEvalRuns() or a new fetchScheduledEvalRuns():
export async function fetchScheduledEvalRuns() {
  try {
    const res = await fetch(`${API}/eval/runs?status=scheduled`);
    if (!res.ok) return;
    const data = await res.json();
    scheduledEvalRuns.value = Array.isArray(data) ? data : (data.items || []);
    scheduledEvalCount.value = scheduledEvalRuns.value.filter(r => {
      const inFourHours = Date.now() / 1000 + 4 * 3600;
      return r.scheduled_for && r.scheduled_for < inFourHours;
    }).length;
  } catch { /* silent — eval scheduled runs are optional */ }
}
```

**In Plan/index.jsx** — inject eval run blocks:
```jsx
import { scheduledEvalRuns } from '../stores/eval.js';
import { currentTab } from '../stores/index.js';

// Build eval gantt blocks alongside regular job blocks:
const evalBlocks = scheduledEvalRuns.value.map(run => ({
  id: `eval-${run.run_id}`,
  type: 'eval',
  label: `Eval: ${(run.variant_ids || []).join(',')}`,
  start: run.scheduled_for,
  end: run.scheduled_for + (run.estimated_duration || 600),
  color: 'var(--accent, #6366f1)',
  onClick: () => { currentTab.value = 'eval'; },
}));

// Pass to GanttChart: blocks={[...regularBlocks, ...evalBlocks]}
```

```bash
git add ollama_queue/dashboard/spa/src/stores/eval.js ollama_queue/dashboard/spa/src/pages/Plan/index.jsx
git commit -m "feat(spa): show scheduled eval runs on Plan Gantt"
```

---

### Task 3.5: Add EVAL badge to Plan tab nav + live indicators to ModelsTab

**Sidebar.jsx and BottomNav.jsx** — add EVAL badge:
```jsx
import { scheduledEvalCount } from '../stores/eval.js';

// In Plan nav item:
<span class="nav-item__label">Plan</span>
{scheduledEvalCount.value > 0 && (
  <span class="nav-badge nav-badge--eval" title={`${scheduledEvalCount.value} eval run(s) in next 4h`}>EVAL</span>
)}
```

**ModelsTab.jsx** — add live indicators:
```jsx
import { currentJob } from '../stores/index.js';
import { evalActiveRun } from '../stores/eval.js';
import LiveIndicator from '../components/LiveIndicator.jsx';

const liveModel = currentJob.value?.model;
const evalModels = evalActiveRun.value ? [evalActiveRun.value.judge_model, evalActiveRun.value.generator_model].filter(Boolean) : [];

// In model row:
{model.name === liveModel && <LiveIndicator state="running" />}
{evalModels.includes(model.name) && <LiveIndicator state="in-eval" />}
```

```bash
git add ollama_queue/dashboard/spa/src/components/Sidebar.jsx ollama_queue/dashboard/spa/src/components/BottomNav.jsx ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx
git commit -m "feat(spa): EVAL badge on Plan nav + live model indicators on Models tab"
```

---

### Task 3.6: Inject eval events into History heatmap + Performance annotations

**History.jsx** — fetch eval runs and inject events:
```jsx
import { useEffect } from 'preact/hooks';
import { signal } from '@preact/signals';

const evalEvents = signal([]);

// In History component or store fetch:
useEffect(() => {
  fetch('/api/eval/runs?limit=50')
    .then(r => r.ok ? r.json() : { items: [] })
    .then(data => {
      const runs = Array.isArray(data) ? data : (data.items || []);
      evalEvents.value = runs.flatMap(run => {
        const events = [];
        if (run.started_at) events.push({ type: 'eval_started', timestamp: run.started_at, label: `Eval started` });
        if (run.completed_at && run.status === 'complete') events.push({ type: 'eval_completed', timestamp: run.completed_at, label: `Eval complete (F1 ${run.winner_f1?.toFixed(2) || '?'})` });
        return events;
      });
    })
    .catch(() => {});
}, []);

// Pass to ActivityHeatmap:
<ActivityHeatmap data={activityData} events={evalEvents.value} />
```

**ActivityHeatmap.jsx** — accept and render events prop:
Add `events = []` to props, render event markers in the tooltip overlay as distinct icons.

**Performance.jsx** — eval annotation:
```jsx
import { evalVariants } from '../stores/eval.js';

const productionVariant = evalVariants.value.find(v => v.is_production);
const judgeModel = productionVariant?.judge_model;

// In perf curve chart section, after the chart:
{judgeModel && (
  <div class="perf-eval-annotation">
    ★ <strong>{judgeModel}</strong> is the current eval judge
    {productionVariant.latest_f1 && <> · <F1Score value={productionVariant.latest_f1} /></>}
  </div>
)}
```

```bash
git add ollama_queue/dashboard/spa/src/pages/History.jsx ollama_queue/dashboard/spa/src/components/ActivityHeatmap.jsx ollama_queue/dashboard/spa/src/pages/Performance.jsx
git commit -m "feat(spa): inject eval events into History heatmap + eval annotation on Performance"
```

---

### Task 3.7: Add variant data lineage tooltip

**File:** `ollama_queue/dashboard/spa/src/components/eval/VariantRow.jsx`

```jsx
// Add lineage fetch on hover for production/recommended variants
import { useState } from 'preact/hooks';

function LineageTip({ variantId }) {
  const [lineage, setLineage] = useState(null);

  useEffect(() => {
    fetch(`/api/eval/variants/${variantId}/lineage`)
      .then(r => r.ok ? r.json() : null)
      .then(data => setLineage(data))
      .catch(() => {});
  }, [variantId]);

  if (!lineage) return <span class="lineage-tip__loading">Loading lineage…</span>;

  return (
    <div class="lineage-tip">
      Promoted from run #{lineage.run_id}
      {lineage.f1_delta != null && ` · +${lineage.f1_delta.toFixed(2)} F1`}
      {lineage.comparison_variant_id && ` over variant-${lineage.comparison_variant_id}`}
      {lineage.lessons_tested && ` · ${lineage.lessons_tested} lessons tested`}
      {lineage.run_date && ` · ${new Date(lineage.run_date * 1000).toLocaleDateString()}`}
    </div>
  );
}

// In VariantRow, for is_production or is_recommended variants, add ⓘ button:
{(variant.is_production || variant.is_recommended) && (
  <span class="variant-lineage-trigger" title="Where did this promotion come from?">
    ⓘ <LineageTip variantId={variant.id} />
  </span>
)}
```

```bash
git add ollama_queue/dashboard/spa/src/components/eval/VariantRow.jsx
git commit -m "feat(spa): add variant data lineage tooltip for production/recommended variants"
```

---

### Task 3.8: Batch 3 quality gate

```bash
cd ollama_queue/dashboard/spa && npm run build && echo "BUILD OK"
cd ollama_queue/dashboard/spa && npm test && echo "TESTS OK"
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q && echo "PYTHON OK"
grep -rn "map(h =>" ollama_queue/dashboard/spa/src/ && echo "H-SHADOW VIOLATIONS" || echo "H-SHADOW CLEAN"
```

---

## Batch 4 — Eval Phase 6 Control Room

> **What this does:** Rebuilds the Eval tab from 4 old sub-tabs (Runs/Configurations/Trends/Settings) into 4 new sub-tabs (Campaign/Variants/Timeline/Config) with card grid, compare mode, sweep generator, optimization timeline with escalation bands, and provider config section.

---

### Task 4.1: Rename sub-tabs and update routing

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/stores/eval.js`
- Modify: `ollama_queue/dashboard/spa/src/pages/Eval.jsx`

**In stores/eval.js** — change default sub-tab:
```js
// Change:
export const evalSubTab = signal('runs');
// To:
export const evalSubTab = signal('campaign');
```

**In Eval.jsx** — update tab definitions:
```jsx
const TABS = [
  { id: 'campaign',  label: 'Campaign' },
  { id: 'variants',  label: 'Variants' },
  { id: 'timeline',  label: 'Timeline' },
  { id: 'config',    label: 'Config' },
];

// Update renderView() switch:
// 'campaign'  → <EvalRuns />     (rebuilt in 4.3)
// 'variants'  → <EvalVariants /> (rebuilt in 4.5)
// 'timeline'  → <EvalTrends />   (rebuilt in 4.6)
// 'config'    → <EvalSettings /> (rebuilt in 4.7)
```

Also update all other files that reference old sub-tab names:
```bash
grep -rn "evalSubTab.*'runs'\|evalSubTab.*'configurations'\|evalSubTab.*'trends'\|evalSubTab.*'settings'" ollama_queue/dashboard/spa/src/ --include="*.jsx" --include="*.js"
```
Replace all occurrences with new names.

```bash
git add ollama_queue/dashboard/spa/src/stores/eval.js ollama_queue/dashboard/spa/src/pages/Eval.jsx
git commit -m "feat(spa): rename eval sub-tabs to campaign/variants/timeline/config"
```

---

### Task 4.2: Add suggestions_json + oracle_json signals to eval store

**File:** `ollama_queue/dashboard/spa/src/stores/eval.js`

```js
import { computed } from '@preact/signals';

// Parsed suggestions from the active run's suggestions_json field
export const evalActiveSuggestions = computed(() => {
  const run = evalActiveRun.value;
  if (!run?.suggestions_json) return [];
  try {
    const parsed = typeof run.suggestions_json === 'string'
      ? JSON.parse(run.suggestions_json) : run.suggestions_json;
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
});

// Parsed oracle report from the active run's oracle_json field
export const evalActiveOracle = computed(() => {
  const run = evalActiveRun.value;
  if (!run?.oracle_json) return null;
  try {
    return typeof run.oracle_json === 'string'
      ? JSON.parse(run.oracle_json) : run.oracle_json;
  } catch { return null; }
});
```

```bash
git add ollama_queue/dashboard/spa/src/stores/eval.js
git commit -m "feat(spa): add evalActiveSuggestions + evalActiveOracle computed signals"
```

---

### Task 4.3: Create EvalNextStepsCard component

**File:** Create `ollama_queue/dashboard/spa/src/components/eval/EvalNextStepsCard.jsx`

```jsx
/**
 * What it shows: Up to 3 recommended next steps after an eval run completes.
 *   Each suggestion is a concrete action (clone a variant, run oracle calibration,
 *   expand the test set) with a one-click button to act on it.
 * Decision it drives: "What should I try next to improve the F1 score?"
 *   Removes guesswork — the system suggests the most likely improvement.
 */
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import { currentTab } from '../../stores/index.js';
import { evalSubTab, focusVariantId } from '../../stores/eval.js';

const ACTION_HANDLERS = {
  clone_variant: (suggestion) => {
    if (suggestion.base_variant_id && focusVariantId) focusVariantId.value = suggestion.base_variant_id;
    evalSubTab.value = 'variants';
    currentTab.value = 'eval';
  },
  run_oracle: () => {
    evalSubTab.value = 'config';
    currentTab.value = 'eval';
  },
  expand_eval_set: () => {
    evalSubTab.value = 'config';
    currentTab.value = 'eval';
  },
};

function SuggestionCard({ suggestion }) {
  const [fb, act] = useActionFeedback();
  const handler = ACTION_HANDLERS[suggestion.action_type] || (() => {});

  return (
    <div class="suggestion-card">
      <div class="suggestion-card__title">{suggestion.title}</div>
      {suggestion.description && <div class="suggestion-card__desc">{suggestion.description}</div>}
      <button
        class="suggestion-card__action"
        disabled={fb.phase === 'loading'}
        onClick={() => act('Opening…', async () => { handler(suggestion); }, () => 'Done')}
      >
        {fb.phase === 'loading' ? '…' : (suggestion.action_label || 'Try this')}
      </button>
      {fb.msg && <span class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</span>}
    </div>
  );
}

export default function EvalNextStepsCard({ suggestions = [] }) {
  if (!suggestions.length) return null;
  const top3 = suggestions.slice(0, 3);

  return (
    <div class="eval-next-steps">
      <h3 class="eval-next-steps__heading">Next Steps</h3>
      <div class="eval-next-steps__cards">
        {top3.map((s, idx) => <SuggestionCard key={idx} suggestion={s} />)}
      </div>
    </div>
  );
}
```

Write test `src/components/eval/EvalNextStepsCard.test.js`:
```js
jest.mock('../../hooks/useActionFeedback.js', () => ({ useActionFeedback: () => [{ phase: 'idle', msg: '' }, jest.fn()] }));
jest.mock('../../stores/index.js', () => ({ currentTab: { value: 'eval' } }));
jest.mock('../../stores/eval.js', () => ({ evalSubTab: { value: 'campaign' }, focusVariantId: { value: null } }));

import _EvalNextStepsCard from './EvalNextStepsCard.jsx';
const EvalNextStepsCard = _EvalNextStepsCard.default || _EvalNextStepsCard;

function findText(vnode) {
  if (!vnode) return '';
  if (typeof vnode === 'string') return vnode;
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode.props) { const c = vnode.props.children; return Array.isArray(c) ? c.map(findText).join('') : findText(c); }
  return '';
}

test('returns null when no suggestions', () => { expect(EvalNextStepsCard({ suggestions: [] })).toBeNull(); });
test('renders suggestion titles', () => {
  const vnode = EvalNextStepsCard({ suggestions: [{ title: 'Clone variant-C', action_type: 'clone_variant', action_label: 'Clone' }] });
  expect(findText(vnode)).toMatch(/Clone variant-C/);
});
test('shows max 3 suggestions', () => {
  const suggestions = [1,2,3,4,5].map(i => ({ title: `Step ${i}`, action_type: 'run_oracle' }));
  const vnode = EvalNextStepsCard({ suggestions });
  const text = findText(vnode);
  expect(text).toMatch(/Step 1/);
  expect(text).not.toMatch(/Step 4/);
});
```

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=EvalNextStepsCard
git add src/components/eval/EvalNextStepsCard.jsx src/components/eval/EvalNextStepsCard.test.js
git commit -m "feat(spa): add EvalNextStepsCard — one-click post-run action suggestions"
```

---

### Task 4.4: Create EvalOracleReport component

**File:** Create `ollama_queue/dashboard/spa/src/components/eval/EvalOracleReport.jsx`

```jsx
/**
 * What it shows: A report on how reliable the judge was during the eval run.
 *   Kappa score = how much the AI judge agreed with reference answers.
 *   High Kappa (green, ≥0.8) means the results are trustworthy.
 *   Low Kappa (red, <0.6) means the judge was unreliable — results may be noisy.
 * Decision it drives: "Can I trust these F1 scores? Should I run oracle calibration
 *   before promoting a variant?"
 */
import { useState } from 'preact/hooks';

const KAPPA_TOOLTIP = 'Agreement between judge and reference answers. 1.0 = perfect, 0.0 = random. Below 0.6 means the judge is unreliable.';

function kappaClass(kappa) {
  if (kappa >= 0.8) return 'oracle-kappa oracle-kappa--green';
  if (kappa >= 0.6) return 'oracle-kappa oracle-kappa--amber';
  return 'oracle-kappa oracle-kappa--red';
}

export default function EvalOracleReport({ oracle }) {
  const [open, setOpen] = useState(false);
  if (!oracle) return null;

  return (
    <div class="eval-oracle-report">
      <button class="eval-oracle-report__toggle" onClick={() => setOpen(o => !o)}>
        How reliable was the judge? {open ? '▲' : '▼'}
      </button>
      {open && (
        <div class="eval-oracle-report__body">
          <div class="oracle-row">
            <span class="oracle-label" title={KAPPA_TOOLTIP}>Kappa score</span>
            <span class={kappaClass(oracle.kappa)}>{oracle.kappa?.toFixed(3) ?? '—'}</span>
          </div>
          <div class="oracle-row">
            <span class="oracle-label">Agreement</span>
            <span>{oracle.agreement_pct != null ? `${Math.round(oracle.agreement_pct)}%` : '—'}</span>
          </div>
          <div class="oracle-row">
            <span class="oracle-label">Disagreements</span>
            <span>{oracle.disagreement_count ?? '—'}</span>
          </div>
          {oracle.opro_suggestions?.length > 0 && (
            <div class="oracle-suggestions">
              <div class="oracle-label">Suggested prompt improvements:</div>
              {oracle.opro_suggestions.map((s, i) => <div key={i} class="oracle-suggestion-item">{s}</div>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

Write test:
```js
jest.mock('preact/hooks', () => ({ useState: (init) => [init, jest.fn()] }));
import _EvalOracleReport from './EvalOracleReport.jsx';
const EvalOracleReport = _EvalOracleReport.default || _EvalOracleReport;
function findText(vnode) {
  if (!vnode) return ''; if (typeof vnode === 'string') return vnode;
  if (Array.isArray(vnode)) return vnode.map(findText).join('');
  if (vnode.props) { const c = vnode.props.children; return Array.isArray(c) ? c.map(findText).join('') : findText(c); }
  return '';
}
test('returns null when no oracle data', () => { expect(EvalOracleReport({ oracle: null })).toBeNull(); });
test('renders toggle button', () => { expect(findText(EvalOracleReport({ oracle: { kappa: 0.85 } }))).toMatch(/reliable/i); });
```

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern=EvalOracleReport
git add src/components/eval/EvalOracleReport.jsx src/components/eval/EvalOracleReport.test.js
git commit -m "feat(spa): add EvalOracleReport — judge reliability panel with Kappa score"
```

---

### Task 4.5: Rebuild Campaign tab (EvalRuns view)

**File:** `ollama_queue/dashboard/spa/src/views/EvalRuns.jsx`

Rebuild with the Campaign layout:
```jsx
/**
 * What it shows: The command center for your prompt optimization campaign.
 *   — Who's winning right now (variant + F1 score)
 *   — What the eval system is doing right now (if a test is running)
 *   — What you should try next (3 suggested actions)
 *   — A table of past test runs
 * Decision it drives: "Is this campaign converging toward a winner?
 *   Should I run another test, or promote the current leader?"
 */
import { evalWinner, evalActiveRun, evalActiveSuggestions, evalActiveOracle } from '../stores/eval.js';
import F1Score from '../components/F1Score.jsx';
import VariantChip from '../components/VariantChip.jsx';
import ActiveRunProgress from '../components/eval/ActiveRunProgress.jsx';
import EvalNextStepsCard from '../components/eval/EvalNextStepsCard.jsx';
import EvalOracleReport from '../components/eval/EvalOracleReport.jsx';
import RunTriggerPanel from '../components/eval/RunTriggerPanel.jsx';
import RunHistoryTable from '../components/eval/RunHistoryTable.jsx';

const PHASE_LABELS = {
  generating: 'Generating outputs',
  judging:    'Scoring with judge',
  analyzing:  'Analyzing results',
  promoting:  'Deciding winner',
};

export default function EvalRuns() {
  const winner = evalWinner.value;
  const activeRun = evalActiveRun.value;
  const suggestions = evalActiveSuggestions.value;
  const oracle = evalActiveOracle.value;

  return (
    <div class="eval-campaign">
      {/* F1 leader chip */}
      {winner && (
        <div class="eval-campaign__leader">
          <VariantChip
            id={winner.id} label={winner.label || winner.id}
            f1={winner.latest_f1} provider={winner.provider}
            isProduction={winner.is_production} isRecommended={winner.is_recommended}
          />
          {winner.latest_f1 != null && <F1Score value={winner.latest_f1} />}
          <span class="eval-campaign__leader-label">current leader</span>
        </div>
      )}

      {/* Active run progress */}
      {activeRun && !['complete', 'failed', 'cancelled'].includes(activeRun.status) && (
        <ActiveRunProgress run={activeRun} phaseLabels={PHASE_LABELS} />
      )}

      {/* Next steps card */}
      <EvalNextStepsCard suggestions={suggestions} />

      {/* Oracle report */}
      <EvalOracleReport oracle={oracle} />

      {/* Run trigger (collapsed when active run) */}
      <RunTriggerPanel collapsed={!!activeRun} />

      {/* Run history */}
      <RunHistoryTable />
    </div>
  );
}
```

```bash
git add ollama_queue/dashboard/spa/src/views/EvalRuns.jsx
git commit -m "feat(spa): rebuild Campaign tab — F1 leader, next steps, oracle, history"
```

---

### Task 4.6: Create VariantCard + rebuild Variants tab

**File:** Create `ollama_queue/dashboard/spa/src/components/eval/VariantCard.jsx`

```jsx
/**
 * What it shows: A summary card for one prompt variant — like a profile card
 *   for a prompt. Shows its name, which AI service it uses, how well it scored,
 *   how consistent it is, and a preview of the prompt text.
 *   Gold star = this is in production. Silver = recommended. Checkbox = select for compare.
 * Decision it drives: "Should I promote this variant? Is it stable enough to trust?
 *   Should I clone it and try a variation?"
 */
import F1Score from '../F1Score.jsx';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';

export default function VariantCard({ variant, selected = false, onSelect, onClone, onEdit, onDelete }) {
  const [fb, act] = useActionFeedback();

  // Top 3 non-default params as pills
  const paramPills = Object.entries(variant.params || {}).slice(0, 3);
  const promptPreview = variant.system_prompt
    ? variant.system_prompt.slice(0, 60) + (variant.system_prompt.length > 60 ? '…' : '')
    : null;

  const stabilityBadge = variant.f1_stdev != null
    ? (variant.f1_stdev < 0.03 ? { label: 'stable', cls: 'badge-stable' }
      : variant.f1_stdev < 0.07 ? { label: 'variable', cls: 'badge-variable' }
      : { label: 'unstable', cls: 'badge-unstable' })
    : null;

  return (
    <div class={`variant-card${selected ? ' variant-card--selected' : ''}`}>
      <div class="variant-card__header">
        <label class="variant-card__checkbox">
          <input type="checkbox" checked={selected} onChange={e => onSelect?.(e.target.checked)} />
        </label>
        <span class="variant-card__label">{variant.label || variant.id}</span>
        <span class={`variant-card__provider variant-card__provider--${variant.provider || 'ollama'}`}>
          {variant.provider || 'ollama'}
        </span>
        {variant.is_production && <span class="variant-card__badge variant-card__badge--gold">★ Production</span>}
        {variant.is_recommended && !variant.is_production && <span class="variant-card__badge variant-card__badge--silver">☆ Recommended</span>}
      </div>

      <div class="variant-card__scores">
        {variant.latest_f1 != null && <F1Score value={variant.latest_f1} />}
        {stabilityBadge && <span class={`stability-badge ${stabilityBadge.cls}`}>{stabilityBadge.label}</span>}
      </div>

      {paramPills.length > 0 && (
        <div class="variant-card__params">
          {paramPills.map(([k, v]) => (
            <span key={k} class="param-pill">{k} {v}</span>
          ))}
        </div>
      )}

      {promptPreview && <div class="variant-card__prompt-preview">{promptPreview}</div>}

      <div class="variant-card__actions">
        <button onClick={() => onClone?.()}>Clone</button>
        <button onClick={() => onEdit?.()}>Edit</button>
        <button
          class="variant-card__delete"
          disabled={fb.phase === 'loading'}
          onClick={() => act('Deleting…', () => onDelete?.(), () => 'Deleted')}
        >
          {fb.phase === 'loading' ? '…' : 'Delete'}
        </button>
        {fb.msg && <span class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</span>}
      </div>
    </div>
  );
}
```

**Rebuild EvalVariants.jsx** as card grid:
```jsx
/**
 * What it shows: Your library of prompt variants — each displayed as a card
 *   showing its score, stability, provider, and key settings. Like a deck of
 *   recipe cards, each for a different way to ask the AI to find lessons.
 * Decision it drives: "Which variant should I promote? Which one to clone for
 *   the next round of testing? Which ones to compare side-by-side?"
 */
import { useState } from 'preact/hooks';
import { evalVariants, fetchEvalVariants, focusVariantId } from '../stores/eval.js';
import VariantCard from '../components/eval/VariantCard.jsx';
import ConfigDiffPanel from '../components/eval/ConfigDiffPanel.jsx';

export default function EvalVariants() {
  const [selected, setSelected] = useState([]);
  const [showSweep, setShowSweep] = useState(false);

  const variants = [...evalVariants.value].sort((a, b) =>
    (b.latest_f1 ?? -1) - (a.latest_f1 ?? -1)
  );

  function toggleSelect(id, checked) {
    setSelected(prev => checked ? [...prev, id] : prev.filter(x => x !== id));
  }

  return (
    <div class="eval-variants">
      <div class="eval-variants__toolbar">
        <button onClick={() => { /* open create form */ }}>+ Create</button>
        <button onClick={() => setShowSweep(true)}>Sweep</button>
        {selected.length >= 2 && <span>{selected.length} selected for compare</span>}
      </div>

      {selected.length >= 2 && (
        <ConfigDiffPanel variantIds={selected} onClose={() => setSelected([])} />
      )}

      <div class="variant-grid">
        {variants.map(v => (
          <VariantCard
            key={v.id}
            variant={v}
            selected={selected.includes(v.id)}
            onSelect={checked => toggleSelect(v.id, checked)}
            onClone={() => { /* clone logic */ }}
            onEdit={() => { focusVariantId.value = v.id; /* open edit form */ }}
            onDelete={() => { /* delete + refetch */ fetchEvalVariants(); }}
          />
        ))}
      </div>
    </div>
  );
}
```

Add CSS: `.variant-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; padding: 16px 0; }`

```bash
git add src/components/eval/VariantCard.jsx src/views/EvalVariants.jsx src/index.css
git commit -m "feat(spa): rebuild Variants tab with card grid + VariantCard component"
```

---

### Task 4.7: Rebuild Timeline tab with escalation bands

**File:** `ollama_queue/dashboard/spa/src/views/EvalTrends.jsx`

```jsx
/**
 * What it shows: The full history of your optimization campaign as a timeline.
 *   Each dot is a test run; each star is when a winner was promoted.
 *   Background bands show what "level" the campaign is at:
 *     Level 0 (grey) = trying different prompt wordings
 *     Level 1 (blue) = fine-tuning parameters like temperature
 *     Level 2 (purple) = training a custom AI model
 * Decision it drives: "Is the system still improving or has it plateaued?
 *   Are we ready to try Level 1 (params tuning) or Level 2 (fine-tuning)?"
 */
import { evalTrends, evalRuns, evalVariants } from '../stores/eval.js';
import F1LineChart from '../components/eval/F1LineChart.jsx';
import VariantStabilityTable from '../components/eval/VariantStabilityTable.jsx';
import SignalQualityPanel from '../components/eval/SignalQualityPanel.jsx';

function getCurrentLevel(variants) {
  // Level 2 if any promoted variant has training_config set
  if (variants.some(v => v.is_production && v.training_config)) return 2;
  // Level 1 if any promoted variant has non-empty params
  if (variants.some(v => v.is_production && v.params && Object.keys(v.params).length > 0)) return 1;
  return 0;
}

const LEVEL_LABELS = {
  0: 'Level 0 — Prompt Engineering',
  1: 'Level 1 — Parameter Tuning',
  2: 'Level 2 — Model Fine-Tuning',
};

export default function EvalTrends() {
  const variants = evalVariants.value;
  const runs = evalRuns.value;
  const currentLevel = getCurrentLevel(variants);

  // Build event markers from run history
  const events = runs.flatMap(run => {
    const evts = [];
    if (run.completed_at && run.status === 'complete') {
      evts.push({ type: 'run_completed', timestamp: run.completed_at, label: `Run #${run.run_id} complete` });
    }
    if (run.promoted_at) {
      evts.push({ type: 'variant_promoted', timestamp: run.promoted_at, label: `${run.winner_variant} promoted` });
    }
    return evts;
  });

  return (
    <div class="eval-timeline">
      <div class="eval-timeline__level-indicator">
        <span class={`level-badge level-badge--${currentLevel}`}>{LEVEL_LABELS[currentLevel]}</span>
      </div>

      <F1LineChart
        trends={evalTrends.value}
        events={events}
        levelBands={[
          { level: 0, color: 'rgba(107,114,128,0.08)', label: 'Level 0' },
          { level: 1, color: 'rgba(59,130,246,0.08)',  label: 'Level 1' },
          { level: 2, color: 'rgba(139,92,246,0.08)',  label: 'Level 2' },
        ]}
        currentLevel={currentLevel}
      />

      <VariantStabilityTable />
      <SignalQualityPanel />
    </div>
  );
}
```

```bash
git add src/views/EvalTrends.jsx
git commit -m "feat(spa): rebuild Timeline tab with escalation level bands and event markers"
```

---

### Task 4.8: Create ProviderRoleSection + rebuild Config tab

**File:** Create `ollama_queue/dashboard/spa/src/components/eval/ProviderRoleSection.jsx`

```jsx
/**
 * What it shows: Configuration for one AI provider role — which service to use
 *   (Ollama for local, Claude, or OpenAI), which model to use, and the API key.
 *   A "Test connection" button verifies it's working before you run a real eval.
 * Decision it drives: "Is this role correctly connected? Is the model I want available?
 *   Am I staying within budget?"
 *
 * Roles: Generator (creates test outputs), Judge (scores them), Optimizer (suggests
 *   better prompts), Oracle (reference checker for judge calibration).
 */
import { useState } from 'preact/hooks';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';

const ROLE_DESCRIPTIONS = {
  generator: 'Creates the test outputs that the judge will score.',
  judge:     'Scores the test outputs against reference answers.',
  optimizer: 'Suggests better prompt variants based on results.',
  oracle:    'Reference AI used to check the judge\'s accuracy.',
};

const PROVIDERS = ['ollama', 'claude', 'openai'];

export default function ProviderRoleSection({ role, settings, onSave }) {
  const [provider, setProvider] = useState(settings?.provider || 'ollama');
  const [model, setModel] = useState(settings?.model || '');
  const [apiKey, setApiKey] = useState('');
  const [models, setModels] = useState([]);
  const [fb, act] = useActionFeedback();

  async function loadModels(prov) {
    try {
      const res = await fetch(`/api/eval/providers/models?provider=${prov}`);
      if (!res.ok) return;
      const data = await res.json();
      setModels(data.models || []);
    } catch { setModels([]); }
  }

  function handleProviderChange(e) {
    const prov = e.target.value;
    setProvider(prov);
    setModel('');
    loadModels(prov);
  }

  function handleTest() {
    act('Testing…', async () => {
      const res = await fetch('/api/eval/providers/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, model, api_key: apiKey || undefined }),
      });
      if (!res.ok) throw new Error(await res.text());
      return await res.json();
    }, () => 'Connected ✓');
  }

  return (
    <div class="provider-role-section">
      <div class="provider-role-section__header">
        <strong class="provider-role-section__title">{role.charAt(0).toUpperCase() + role.slice(1)}</strong>
        <span class="provider-role-section__desc">{ROLE_DESCRIPTIONS[role]}</span>
      </div>

      <div class="provider-role-section__fields">
        <label>
          Service
          <select value={provider} onChange={handleProviderChange}>
            {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>

        <label>
          Model
          <select value={model} onChange={e => setModel(e.target.value)}>
            <option value="">Select model…</option>
            {models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>

        {provider !== 'ollama' && (
          <label>
            API Key
            <input
              type="password"
              placeholder="sk-…"
              value={apiKey}
              onInput={e => setApiKey(e.target.value)}
            />
          </label>
        )}

        {provider !== 'ollama' && (
          <label>
            Max cost per run (USD)
            <input type="number" step="0.01" min="0" defaultValue={settings?.max_cost_per_run || ''} />
          </label>
        )}

        <div class="provider-role-section__actions">
          <button
            disabled={!model || fb.phase === 'loading'}
            onClick={handleTest}
          >
            {fb.phase === 'loading' ? 'Testing…' : 'Test connection'}
          </button>
          {fb.msg && <span class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</span>}
        </div>
      </div>
    </div>
  );
}
```

**Rebuild EvalSettings.jsx** as Config tab:
```jsx
/**
 * What it shows: The full configuration for the eval system. Set up which AI
 *   services to use for each role, where lesson data comes from, and when to
 *   automatically promote a winning variant.
 * Decision it drives: "Is the eval system correctly wired up and ready to run?
 *   What rules control automatic promotion?"
 */
import { useState } from 'preact/hooks';
import SetupChecklist from '../components/eval/SetupChecklist.jsx';
import DataSourcePanel from '../components/eval/DataSourcePanel.jsx';
import JudgeDefaultsForm from '../components/eval/JudgeDefaultsForm.jsx';
import GeneralSettings from '../components/eval/GeneralSettings.jsx';
import ProviderRoleSection from '../components/eval/ProviderRoleSection.jsx';
import { evalSettings } from '../stores/eval.js';

const ROLES = ['generator', 'judge', 'optimizer', 'oracle'];

export default function EvalSettings() {
  const [activeRole, setActiveRole] = useState('judge');
  const settings = evalSettings.value;

  return (
    <div class="eval-config">
      <SetupChecklist />

      <section class="eval-config__section">
        <h3>Provider Configuration</h3>
        <p class="eval-config__hint">
          Generator = which AI creates the test outputs.
          Judge = which AI scores them.
          Optimizer = which AI suggests better prompts.
          Oracle = reference AI to check the judge's accuracy.
        </p>
        <div class="provider-role-tabs">
          {ROLES.map(role => (
            <button
              key={role}
              class={`provider-role-tab${activeRole === role ? ' provider-role-tab--active' : ''}`}
              onClick={() => setActiveRole(role)}
            >
              {role.charAt(0).toUpperCase() + role.slice(1)}
            </button>
          ))}
        </div>
        <ProviderRoleSection
          role={activeRole}
          settings={settings[`${activeRole}_provider`] ? {
            provider: settings[`${activeRole}_provider`],
            model: settings[`${activeRole}_model`],
          } : {}}
        />
      </section>

      <section class="eval-config__section">
        <h3>Data Source</h3>
        <DataSourcePanel />
      </section>

      <section class="eval-config__section">
        <h3>Auto-Promote Rules</h3>
        <p class="eval-config__hint">
          These rules control when the system automatically promotes a winning variant
          to production without you having to manually approve it.
        </p>
        <JudgeDefaultsForm
          fieldHints={{
            f1_threshold: 'Minimum score to promote (0.0–1.0)',
            stability_window: 'How many tests before we trust the result',
            auto_promote_min_improvement: 'How much better it must be than the current winner',
            error_budget: 'Maximum allowed error rate (0 = zero tolerance)',
          }}
        />
      </section>

      <section class="eval-config__section">
        <h3>General</h3>
        <GeneralSettings />
      </section>
    </div>
  );
}
```

```bash
git add src/components/eval/ProviderRoleSection.jsx src/views/EvalSettings.jsx src/index.css
git commit -m "feat(spa): rebuild Config tab with provider role sections and field hints"
```

---

### Task 4.9: Verify layman comment blocks on all 4 rebuilt views

Run:
```bash
for f in ollama_queue/dashboard/spa/src/views/EvalRuns.jsx \
          ollama_queue/dashboard/spa/src/views/EvalVariants.jsx \
          ollama_queue/dashboard/spa/src/views/EvalTrends.jsx \
          ollama_queue/dashboard/spa/src/views/EvalSettings.jsx; do
  grep -l "What it shows" $f && echo "OK: $f" || echo "MISSING: $f"
done
```

For any file showing MISSING, add the comment block at the top following the format:
```jsx
/**
 * What it shows: [plain English, 15-year-old level]
 * Decision it drives: [what action does the user take after seeing this]
 */
```

```bash
git add ollama_queue/dashboard/spa/src/views/
git commit -m "docs(spa): ensure all rebuilt eval views have layman comment blocks"
```

---

### Task 4.10: Batch 4 quality gate

```bash
# Build
cd ollama_queue/dashboard/spa && npm run build && echo "BUILD OK"

# Frontend tests
cd ollama_queue/dashboard/spa && npm test && echo "TESTS OK"

# Python tests (regression)
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q && echo "PYTHON OK"

# h-shadowing check
grep -rn "map(h =>" ollama_queue/dashboard/spa/src/ && echo "H-SHADOW VIOLATIONS" || echo "H-SHADOW CLEAN"

# Verify new eval components exist
for f in \
  ollama_queue/dashboard/spa/src/components/eval/EvalNextStepsCard.jsx \
  ollama_queue/dashboard/spa/src/components/eval/EvalOracleReport.jsx \
  ollama_queue/dashboard/spa/src/components/eval/VariantCard.jsx \
  ollama_queue/dashboard/spa/src/components/eval/ProviderRoleSection.jsx; do
  test -f $f && echo "OK: $f" || echo "MISSING: $f"
done

# Verify tab rename
grep -q "campaign" ollama_queue/dashboard/spa/src/pages/Eval.jsx && echo "TABS RENAMED OK"

# Verify layman comments
for f in EvalRuns EvalVariants EvalTrends EvalSettings; do
  grep -q "What it shows" ollama_queue/dashboard/spa/src/views/${f}.jsx && echo "LAYMAN OK: $f" || echo "LAYMAN MISSING: $f"
done
```

---

## Final commit after all 4 batches

```bash
cd ~/Documents/projects/ollama-queue
git log --oneline -20  # Review commit history
git push origin HEAD   # Push to remote
```

---

## Execution Options

**Plan complete and saved to `docs/plans/2026-03-12-cohesion-eval-phase6-plan.md`.**

**Three execution options:**

**1. Subagent-Driven (this session)** — I dispatch a fresh subagent per task with two-stage review, fast iteration, you watch progress

**2. Parallel Session (separate)** — Open a new session in a worktree with executing-plans, batch execution with human review checkpoints

**3. Headless (walk away)** — Run this in the background. Fresh `claude -p` per batch, quality gates between batches, resume on interruption:
```bash
cd ~/Documents/projects/ollama-queue
scripts/run-plan.sh docs/plans/2026-03-12-cohesion-eval-phase6-plan.md \
  --quality-gate "cd ollama_queue/dashboard/spa && npm run build && npm test && cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q" \
  --on-failure retry --max-retries 2
```

**Which approach?**
