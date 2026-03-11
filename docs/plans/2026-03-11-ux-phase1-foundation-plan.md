# UX Phase 1: Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add plain-English tooltips, first-run onboarding overlay, contextual empty states, system health chip, submit button in nav, named priority selector, queue ETAs, keyboard shortcuts, and consumer tab explanation.

**Architecture:** Pure JSX/CSS changes. No new API endpoints. All changes additive — no existing component logic removed. New components: `OnboardingOverlay.jsx`, `EmptyState.jsx`, `SystemHealthChip.jsx`, `PrioritySelector.jsx`. Modified components: `HeroCard.jsx`, `ResourceGauges.jsx`, `QueueList.jsx`, `Sidebar.jsx`, `BottomNav.jsx`, `app.jsx`, `Consumers.jsx`, `SubmitJobModal.jsx`, `AddRecurringJobModal.jsx`.

**Tech Stack:** Preact 10, @preact/signals, Tailwind v4, esbuild. Run build: `cd ollama_queue/dashboard/spa && npm run build`. Tests: `cd ollama_queue/dashboard/spa && npm test`. Python suite: `python3 -m pytest tests/ -x -q`.

---

## Pre-flight

```bash
cd ~/Documents/projects/ollama-queue
git checkout -b feature/ux-foundation
# Verify build passes before touching anything
cd ollama_queue/dashboard/spa && npm run build && npm test
cd ../../..
```

---

## Task 1: HeroCard Tooltips + ResourceGauge Labels

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/HeroCard.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/ResourceGauges.jsx`
- Test: `ollama_queue/dashboard/spa/src/components/HeroCard.test.js` (create)

**Step 1: Read current HeroCard**

```bash
cat ollama_queue/dashboard/spa/src/components/HeroCard.jsx
```

**Step 2: Write failing test**

Create `ollama_queue/dashboard/spa/src/components/HeroCard.test.js`:

```js
import { h } from 'preact';
import { render } from '@testing-library/preact';
import HeroCard from './HeroCard.jsx';

test('renders tooltip on label when provided', () => {
  const { container } = render(
    h(HeroCard, { label: 'Jobs/24h', value: '42', tooltip: 'Total jobs completed in the last 24 hours.' })
  );
  const tooltipEl = container.querySelector('[title]');
  expect(tooltipEl).toBeTruthy();
  expect(tooltipEl.title).toContain('Total jobs completed');
});

test('renders without tooltip when not provided', () => {
  const { container } = render(h(HeroCard, { label: 'Jobs/24h', value: '42' }));
  const tooltipEl = container.querySelector('[data-tooltip]');
  expect(tooltipEl).toBeNull();
});
```

**Step 3: Run — expect FAIL**

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern HeroCard
```

**Step 4: Add `tooltip` prop to HeroCard**

In `HeroCard.jsx`, find the label rendering and wrap with `title`:
```jsx
<span
  style="font-size: var(--type-label); color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.08em;"
  title={tooltip || undefined}
>
  {label}{tooltip ? <span aria-label={tooltip} style="margin-left:4px;cursor:help;color:var(--text-tertiary)">?</span> : null}
</span>
```

Add `tooltip` to the destructured props: `export default function HeroCard({ label, value, delta, sparkData, tooltip, ... })`

**Step 5: Run — expect PASS**

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern HeroCard
```

**Step 6: Add ResourceGauge label tooltips**

In `ResourceGauges.jsx`, add `title` attributes to each gauge label:

```jsx
const GAUGE_TOOLTIPS = {
  ram:  'System RAM in use. Above the pause threshold, the daemon stops accepting new jobs.',
  vram: 'GPU memory in use by Ollama. Near 100% causes model loading failures — most common bottleneck.',
  load: '1-minute system load average. Values above CPU count indicate the system is overloaded.',
  swap: 'Swap (disk memory) in use. Non-zero swap on a machine with adequate RAM signals memory pressure.',
};
```

Wrap each gauge label: `<span title={GAUGE_TOOLTIPS.ram}>RAM</span>`

**Step 7: Update Now.jsx to pass tooltips to HeroCards**

In `Now.jsx`, locate the 4 `<HeroCard>` calls and add `tooltip` prop to each:

```jsx
<HeroCard label="Jobs/24h" value={...} tooltip="Total jobs completed in the last 24 hours. Rising = queue is healthy. Falling = daemon may be stalled." />
<HeroCard label="Avg Wait" value={...} tooltip="Average time a job spends in queue before the daemon starts it. Spikes mean the daemon was busy or paused." />
<HeroCard label="Pause Time" value={...} tooltip="Total minutes the daemon spent paused in the last 24 hours. High values mean frequent health-triggered pauses." />
<HeroCard label="Success Rate" value={...} tooltip="Percentage of completed jobs that succeeded. Below 90% warrants investigation in History." />
```

**Step 8: Build and verify**

```bash
cd ollama_queue/dashboard/spa && npm run build
```
Expected: build succeeds, no errors.

**Step 9: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/HeroCard.jsx \
        ollama_queue/dashboard/spa/src/components/HeroCard.test.js \
        ollama_queue/dashboard/spa/src/components/ResourceGauges.jsx \
        ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat(spa): add plain-English tooltips to KPI cards and resource gauges"
```

---

## Task 2: EmptyState Component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/EmptyState.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/EmptyState.test.js`

**Step 1: Write failing test**

```js
import { h } from 'preact';
import { render } from '@testing-library/preact';
import EmptyState from './EmptyState.jsx';

test('renders headline and body', () => {
  const { getByText } = render(
    h(EmptyState, { headline: 'Queue is empty', body: 'Jobs you submit will appear here.' })
  );
  expect(getByText('Queue is empty')).toBeTruthy();
  expect(getByText('Jobs you submit will appear here.')).toBeTruthy();
});

test('renders action button when action prop provided', () => {
  const onClick = jest.fn();
  const { getByRole } = render(
    h(EmptyState, { headline: 'Empty', body: 'Nothing here.', action: { label: '+ Submit a job', onClick } })
  );
  const btn = getByRole('button');
  expect(btn.textContent).toContain('Submit a job');
  btn.click();
  expect(onClick).toHaveBeenCalled();
});

test('renders without action button when action not provided', () => {
  const { queryByRole } = render(h(EmptyState, { headline: 'Empty', body: 'Nothing.' }));
  expect(queryByRole('button')).toBeNull();
});
```

**Step 2: Run — expect FAIL**

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern EmptyState
```

**Step 3: Implement EmptyState.jsx**

```jsx
import { h } from 'preact';

/**
 * EmptyState — shown when a list or section has no data.
 * Props:
 *   headline (string) — short title
 *   body (string) — explanation
 *   action ({ label, onClick }) — optional CTA button
 */
export default function EmptyState({ headline, body, action }) {
  return (
    <div style="display:flex;flex-direction:column;align-items:center;gap:8px;padding:24px 16px;color:var(--text-tertiary);text-align:center;">
      <span style="font-size:var(--type-body);color:var(--text-secondary);">{headline}</span>
      <span style="font-size:var(--type-label);">{body}</span>
      {action && (
        <button class="t-btn" onClick={action.onClick} style="margin-top:8px;font-size:var(--type-label);">
          {action.label}
        </button>
      )}
    </div>
  );
}
```

**Step 4: Run — expect PASS**

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern EmptyState
```

**Step 5: Wire EmptyState into CurrentJob, QueueList, HistoryList**

In `CurrentJob.jsx`, replace the idle `<div class="flex items-center gap-3">...Ready...</div>` with:
```jsx
import EmptyState from './EmptyState.jsx';
// In idle branch:
<EmptyState
  headline="Ready — nothing in queue"
  body="Jobs you submit will appear here."
  action={{ label: '+ Submit a job', onClick: onSubmitRequest }}
/>
```
Note: `onSubmitRequest` is a new prop passed from `Now.jsx` that opens `SubmitJobModal`.

In `QueueList.jsx`, add at top of render when `jobs.length === 0`:
```jsx
<EmptyState headline="Queue is empty" body="Jobs you submit will appear here." />
```

In `HistoryList.jsx`, add when history is empty:
```jsx
<EmptyState headline="No history yet" body="Run your first job to see results here." />
```

**Step 6: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 7: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/EmptyState.jsx \
        ollama_queue/dashboard/spa/src/components/EmptyState.test.js \
        ollama_queue/dashboard/spa/src/components/CurrentJob.jsx \
        ollama_queue/dashboard/spa/src/components/QueueList.jsx \
        ollama_queue/dashboard/spa/src/components/HistoryList.jsx
git commit -m "feat(spa): add EmptyState component with CTAs for idle/empty views"
```

---

## Task 3: SystemHealthChip

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/SystemHealthChip.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/SystemHealthChip.test.js`
- Modify: `ollama_queue/dashboard/spa/src/components/Sidebar.jsx`

**Step 1: Write failing test**

```js
import { h } from 'preact';
import { render } from '@testing-library/preact';
import SystemHealthChip from './SystemHealthChip.jsx';

const baseProps = {
  daemonState: 'idle',
  dlqCount: 0,
  ram: 30, vram: 40, load: 1.2, swap: 0,
  settings: { pause_ram_pct: 85, pause_vram_pct: 90, pause_load_avg: 8 },
};

test('shows Healthy when no issues', () => {
  const { getByText } = render(h(SystemHealthChip, baseProps));
  expect(getByText(/healthy/i)).toBeTruthy();
});

test('shows Warning when DLQ has entries', () => {
  const { getByText } = render(h(SystemHealthChip, { ...baseProps, dlqCount: 2 }));
  expect(getByText(/warning/i)).toBeTruthy();
});

test('shows Issues when daemon is paused', () => {
  const { getByText } = render(h(SystemHealthChip, { ...baseProps, daemonState: 'paused_health' }));
  expect(getByText(/issue/i)).toBeTruthy();
});

test('shows Issues when resource exceeds pause threshold', () => {
  const { getByText } = render(h(SystemHealthChip, { ...baseProps, ram: 90 }));
  expect(getByText(/issue/i)).toBeTruthy();
});
```

**Step 2: Run — expect FAIL**

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern SystemHealthChip
```

**Step 3: Implement SystemHealthChip.jsx**

```jsx
import { h } from 'preact';

/**
 * SystemHealthChip — single-line aggregate health indicator.
 * Combines daemon state + DLQ count + resource pressure.
 */
export default function SystemHealthChip({ daemonState, dlqCount, ram, vram, load, swap, settings }) {
  const s = settings || {};
  const isPaused = (daemonState || '').startsWith('paused');
  const isError = daemonState === 'error';
  const resourceCritical = ram >= (s.pause_ram_pct || 85) ||
                           vram >= (s.pause_vram_pct || 90) ||
                           load >= (s.pause_load_avg || 8);
  const resourceWarning = !resourceCritical && (ram >= 70 || vram >= 75);

  let level, label, color;
  const issueCount = (isPaused || isError || resourceCritical ? 1 : 0) + (dlqCount > 3 ? 1 : 0);
  const warnCount = (resourceWarning ? 1 : 0) + (dlqCount > 0 && dlqCount <= 3 ? 1 : 0);

  if (isError || isPaused || resourceCritical || dlqCount > 3) {
    level = 'error';
    label = issueCount === 1 ? '1 Issue' : `${issueCount} Issues`;
    color = 'var(--status-error)';
  } else if (resourceWarning || dlqCount > 0) {
    level = 'warning';
    label = warnCount === 1 ? '1 Warning' : `${warnCount} Warnings`;
    color = 'var(--status-warning)';
  } else {
    level = 'healthy';
    label = 'Healthy';
    color = 'var(--status-healthy)';
  }

  return (
    <div style={`display:flex;align-items:center;gap:6px;font-family:var(--font-mono);font-size:var(--type-micro);color:${color};`}>
      <span style={`width:6px;height:6px;border-radius:50%;background:${color};flex-shrink:0;`} />
      {label}
    </div>
  );
}
```

**Step 4: Run — expect PASS**

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern SystemHealthChip
```

**Step 5: Wire into Sidebar.jsx**

Read `Sidebar.jsx`. Find the existing daemon status chip at the top. Replace it with `<SystemHealthChip>` receiving props from the global signals. Pass: `daemonState={daemonState.value}`, `dlqCount={dlqCount.value}`, `ram={latestHealth.value?.ram_pct}`, etc.

Import `SystemHealthChip` at the top of Sidebar.jsx.

**Step 6: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 7: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/SystemHealthChip.jsx \
        ollama_queue/dashboard/spa/src/components/SystemHealthChip.test.js \
        ollama_queue/dashboard/spa/src/components/Sidebar.jsx
git commit -m "feat(spa): add SystemHealthChip aggregating daemon + DLQ + resource health"
```

---

## Task 4: Submit Button in Sidebar + FAB on Mobile

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/Sidebar.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/BottomNav.jsx`
- Modify: `ollama_queue/dashboard/spa/src/app.jsx`

**Step 1: Read current Sidebar.jsx and BottomNav.jsx**

```bash
cat ollama_queue/dashboard/spa/src/components/Sidebar.jsx
cat ollama_queue/dashboard/spa/src/components/BottomNav.jsx
```

**Step 2: Add `onSubmitRequest` signal to app.jsx**

In `app.jsx`, add a signal: `const showSubmitModal = signal(false);`
Pass `onSubmitRequest={() => (showSubmitModal.value = true)}` to Sidebar and BottomNav.
Pass `open={showSubmitModal}` to `SubmitJobModal`.

**Step 3: Add Submit button to Sidebar**

At the bottom of the nav list in `Sidebar.jsx`, add:
```jsx
<button
  class="t-btn"
  onClick={onSubmitRequest}
  style="width:100%;margin-top:auto;font-size:var(--type-label);padding:8px;"
  disabled={daemonState === 'error'}
>
  + Submit
</button>
```

**Step 4: Add FAB to BottomNav**

In `BottomNav.jsx`, add after the nav bar:
```jsx
<button
  class="t-btn"
  onClick={onSubmitRequest}
  aria-label="Submit job"
  style="position:fixed;bottom:72px;right:16px;z-index:50;width:48px;height:48px;border-radius:50%;font-size:1.25rem;display:flex;align-items:center;justify-content:center;"
>
  +
</button>
```

**Step 5: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 6: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/Sidebar.jsx \
        ollama_queue/dashboard/spa/src/components/BottomNav.jsx \
        ollama_queue/dashboard/spa/src/app.jsx
git commit -m "feat(spa): add Submit button to sidebar and mobile FAB"
```

---

## Task 5: PrioritySelector Component

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/PrioritySelector.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/PrioritySelector.test.js`
- Modify: `ollama_queue/dashboard/spa/src/components/SubmitJobModal.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/AddRecurringJobModal.jsx`

**Step 1: Write failing test**

```js
import { h } from 'preact';
import { render, fireEvent } from '@testing-library/preact';
import PrioritySelector from './PrioritySelector.jsx';

test('renders all 5 priority labels', () => {
  const { getByText } = render(h(PrioritySelector, { value: 5, onChange: () => {} }));
  expect(getByText('Normal')).toBeTruthy();
  expect(getByText('Critical')).toBeTruthy();
  expect(getByText('Background')).toBeTruthy();
});

test('calls onChange with numeric value on selection', () => {
  const onChange = jest.fn();
  const { getByText } = render(h(PrioritySelector, { value: 5, onChange }));
  fireEvent.click(getByText('Critical'));
  expect(onChange).toHaveBeenCalledWith(1);
});

test('highlights selected option', () => {
  const { getByText } = render(h(PrioritySelector, { value: 5, onChange: () => {} }));
  const normal = getByText('Normal').closest('button');
  expect(normal.className).toMatch(/selected|active/);
});
```

**Step 2: Run — expect FAIL**

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern PrioritySelector
```

**Step 3: Implement PrioritySelector.jsx**

```jsx
import { h } from 'preact';

const LEVELS = [
  { label: 'Critical',   value: 1, color: 'var(--status-error)' },
  { label: 'High',       value: 3, color: 'var(--status-warning)' },
  { label: 'Normal',     value: 5, color: 'var(--accent)' },
  { label: 'Low',        value: 7, color: 'var(--text-tertiary)' },
  { label: 'Background', value: 9, color: 'var(--text-tertiary)', opacity: '0.6' },
];

/**
 * PrioritySelector — segmented control for job priority.
 * Props: value (number 1-10), onChange (fn(number))
 * Submits numeric value; displays named levels.
 */
export default function PrioritySelector({ value, onChange }) {
  // Map numeric value to nearest level
  const selected = LEVELS.reduce((prev, curr) =>
    Math.abs(curr.value - value) < Math.abs(prev.value - value) ? curr : prev
  );

  return (
    <div style="display:flex;gap:4px;flex-wrap:wrap;" role="group" aria-label="Priority">
      {LEVELS.map(level => (
        <button
          key={level.value}
          class={`t-btn${level.value === selected.value ? ' selected' : ''}`}
          onClick={() => onChange(level.value)}
          style={`
            font-size:var(--type-label);
            padding:4px 10px;
            color:${level.color};
            opacity:${level.opacity || 1};
            border-color:${level.value === selected.value ? level.color : 'var(--border-subtle)'};
            background:${level.value === selected.value ? `color-mix(in srgb, ${level.color} 12%, transparent)` : 'transparent'};
          `}
        >
          {level.label}
        </button>
      ))}
    </div>
  );
}
```

**Step 4: Run — expect PASS**

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern PrioritySelector
```

**Step 5: Replace priority inputs in SubmitJobModal and AddRecurringJobModal**

In each modal: find `<input type="number" ... priority ...>` and replace with:
```jsx
import PrioritySelector from './PrioritySelector.jsx';
// Replace input:
<PrioritySelector value={form.priority} onChange={v => setForm(f => ({ ...f, priority: v }))} />
```

**Step 6: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 7: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/PrioritySelector.jsx \
        ollama_queue/dashboard/spa/src/components/PrioritySelector.test.js \
        ollama_queue/dashboard/spa/src/components/SubmitJobModal.jsx \
        ollama_queue/dashboard/spa/src/components/AddRecurringJobModal.jsx
git commit -m "feat(spa): add named PrioritySelector replacing numeric priority input"
```

---

## Task 6: Queue Row ETAs

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/QueueList.jsx`

**Step 1: Read QueueList.jsx and store.js**

```bash
cat ollama_queue/dashboard/spa/src/components/QueueList.jsx
grep -n "queueEtas\|eta" ollama_queue/dashboard/spa/src/store.js | head -20
```

**Step 2: Wire queueEtas into QueueList**

`queueEtas` is already fetched in the store (used by Plan tab). In `QueueList.jsx`:
- Import `queueEtas` signal from store
- In each job row, look up `queueEtas.value?.[job.id]` to get `eta_seconds`
- Render:
```jsx
{eta && (
  <span class="data-mono" style="font-size:var(--type-micro);color:var(--text-tertiary);">
    ~{formatDuration(eta)}
  </span>
)}
```

Where `formatDuration` is the same helper already defined in `CurrentJob.jsx` — extract it to a shared utils file or import directly.

**Step 3: Extract formatDuration to shared utility**

Create `ollama_queue/dashboard/spa/src/utils/time.js`:
```js
export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || seconds < 0) return '--';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
```

Update `CurrentJob.jsx` to import from there instead of the local function. Update `QueueList.jsx` to import from there too.

**Step 4: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/QueueList.jsx \
        ollama_queue/dashboard/spa/src/utils/time.js \
        ollama_queue/dashboard/spa/src/components/CurrentJob.jsx
git commit -m "feat(spa): show estimated wait time on queue rows, extract formatDuration util"
```

---

## Task 7: Keyboard Shortcuts

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/app.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/Settings.jsx`

**Step 1: Add keyboard shortcut handler to app.jsx**

In `app.jsx`, add in the component body:

```jsx
useEffect(() => {
  const TABS = ['now', 'plan', 'history', 'models', 'settings'];
  function handler(e) {
    // Skip if focus is inside an input, textarea, or contenteditable
    const tag = document.activeElement?.tagName?.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || document.activeElement?.isContentEditable) return;
    const idx = parseInt(e.key, 10) - 1;
    if (idx >= 0 && idx < TABS.length) {
      currentTab.value = TABS[idx];
    }
  }
  window.addEventListener('keydown', handler);
  return () => window.removeEventListener('keydown', handler);
}, []);
```

**Step 2: Add shortcut legend to Settings tab**

In `Settings.jsx`, add at the bottom of the page:
```jsx
<div style="margin-top:24px;padding-top:16px;border-top:1px solid var(--border-subtle);">
  <p style="font-family:var(--font-mono);font-size:var(--type-micro);color:var(--text-tertiary);">
    Keyboard shortcuts: <kbd>1</kbd> Now · <kbd>2</kbd> Plan · <kbd>3</kbd> History · <kbd>4</kbd> Models · <kbd>5</kbd> Settings
  </p>
</div>
```

**Step 3: Add kbd styling to index.css**

```css
kbd {
  font-family: var(--font-mono);
  font-size: 0.7em;
  padding: 1px 4px;
  border: 1px solid var(--border-primary);
  border-radius: 3px;
  background: var(--bg-surface-raised);
}
```

**Step 4: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/app.jsx \
        ollama_queue/dashboard/spa/src/pages/Settings.jsx \
        ollama_queue/dashboard/spa/src/index.css
git commit -m "feat(spa): add keyboard shortcuts 1-5 for tab navigation"
```

---

## Task 8: Consumer Tab Explanation + OnboardingOverlay

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Consumers.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/OnboardingOverlay.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/OnboardingOverlay.test.js`
- Modify: `ollama_queue/dashboard/spa/src/app.jsx`

**Step 1: Add explanation to Consumers.jsx**

Read `Consumers.jsx`, then add at the top of the return:
```jsx
<p style="color:var(--text-secondary);font-size:var(--type-body);margin-bottom:1rem;">
  Consumers are services or scripts that submit jobs to the queue. Each consumer gets its own
  priority lane and is tracked separately for rate limiting and history.
</p>
```

**Step 2: Write OnboardingOverlay test**

```js
import { h } from 'preact';
import { render, fireEvent } from '@testing-library/preact';
import OnboardingOverlay from './OnboardingOverlay.jsx';

test('renders first step content', () => {
  const { getByText } = render(h(OnboardingOverlay, { onDismiss: () => {} }));
  expect(getByText(/Now/i)).toBeTruthy();
  expect(getByText(/real-time command center/i)).toBeTruthy();
});

test('advances to next step on Next click', () => {
  const { getByText } = render(h(OnboardingOverlay, { onDismiss: () => {} }));
  fireEvent.click(getByText('Next'));
  expect(getByText(/Plan/i)).toBeTruthy();
});

test('calls onDismiss when Got it clicked', () => {
  const onDismiss = jest.fn();
  const { getByText } = render(h(OnboardingOverlay, { onDismiss }));
  fireEvent.click(getByText(/got it/i));
  expect(onDismiss).toHaveBeenCalled();
});
```

**Step 3: Run — expect FAIL**

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern OnboardingOverlay
```

**Step 4: Implement OnboardingOverlay.jsx**

```jsx
import { h } from 'preact';
import { useState } from 'preact/hooks';

const STEPS = [
  { tab: 'Now', body: 'Your real-time command center. Shows what\'s running right now, what\'s waiting, and whether the system has the resources to keep working.' },
  { tab: 'Plan', body: 'Your 24-hour schedule. Recurring jobs, estimated run windows, and conflict detection. Use this to understand when your jobs will run.' },
  { tab: 'History', body: 'Everything that already ran. Failed jobs, duration trends, and GPU activity. Use this to investigate problems and measure performance.' },
  { tab: 'Models', body: 'What Ollama has installed and what\'s available to download. Use this when a job fails because a model is missing.' },
  { tab: 'Settings', body: 'Health thresholds, retry behavior, stall detection, and daemon controls. Change these when the system is pausing too aggressively or not enough.' },
];

export default function OnboardingOverlay({ onDismiss }) {
  const [step, setStep] = useState(0);
  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  return (
    <div style="position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;">
      <div class="t-frame" style="max-width:480px;width:90%;padding:24px;display:flex;flex-direction:column;gap:16px;">
        <div style="font-family:var(--font-mono);font-size:var(--type-label);color:var(--text-tertiary);">
          {step + 1} / {STEPS.length}
        </div>
        <h2 style="font-size:var(--type-headline);color:var(--text-primary);margin:0;">{current.tab}</h2>
        <p style="font-size:var(--type-body);color:var(--text-secondary);margin:0;">{current.body}</p>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;">
          <div style="display:flex;gap:4px;">
            {STEPS.map((_, i) => (
              <div key={i} style={`width:6px;height:6px;border-radius:50%;background:${i === step ? 'var(--accent)' : 'var(--border-subtle)'};`} />
            ))}
          </div>
          <div style="display:flex;gap:8px;">
            <button class="t-btn" onClick={onDismiss} style="font-size:var(--type-label);">Got it</button>
            {!isLast && (
              <button class="t-btn" onClick={() => setStep(s => s + 1)} style="font-size:var(--type-label);">Next</button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
```

**Step 5: Run — expect PASS**

```bash
cd ollama_queue/dashboard/spa && npm test -- --testPathPattern OnboardingOverlay
```

**Step 6: Wire OnboardingOverlay into app.jsx**

```jsx
import OnboardingOverlay from './components/OnboardingOverlay.jsx';

// In App component body:
const [showOnboarding, setShowOnboarding] = useState(
  () => !localStorage.getItem('oq_onboarded')
);
const dismissOnboarding = () => {
  localStorage.setItem('oq_onboarded', '1');
  setShowOnboarding(false);
};

// In JSX return:
{showOnboarding && <OnboardingOverlay onDismiss={dismissOnboarding} />}
```

**Step 7: Build + full test suite**

```bash
cd ollama_queue/dashboard/spa && npm run build && npm test
cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/ -x -q
```

**Step 8: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Consumers.jsx \
        ollama_queue/dashboard/spa/src/components/OnboardingOverlay.jsx \
        ollama_queue/dashboard/spa/src/components/OnboardingOverlay.test.js \
        ollama_queue/dashboard/spa/src/app.jsx
git commit -m "feat(spa): add first-run onboarding overlay and consumer tab explanation"
```

---

## Task 9: Final Build, Test, Push, PR

**Step 1: Full build and test**

```bash
cd ollama_queue/dashboard/spa && npm run build && npm test
cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/ -x -q
```

**Step 2: Push branch**

```bash
cd ~/Documents/projects/ollama-queue
git push -u origin feature/ux-foundation
```

**Step 3: Create PR**

```bash
gh pr create \
  --title "feat(spa): UX Phase 1 — foundation (tooltips, empty states, health chip, submit button, priority selector, ETAs, keyboard nav, onboarding)" \
  --body "## UX Phase 1: Foundation

Implements items 1, 2, 3, 4, 5, 6, 7, 16, 20 from the UX & design philosophy improvements design.

### Changes
- Plain-English tooltips on all KPI cards and resource gauges
- First-run onboarding overlay (5-step, localStorage-gated)
- Contextual empty states with CTAs on CurrentJob/QueueList/HistoryList
- SystemHealthChip: aggregates daemon + DLQ + resource pressure
- Submit button in sidebar + FAB on mobile
- PrioritySelector: named levels (Critical/High/Normal/Low/Background)
- Estimated wait time on queue rows from queueEtas signal
- Keyboard shortcuts: 1-5 to switch tabs
- Consumer tab explanation header
- formatDuration extracted to shared utility

### Design doc
\`docs/plans/2026-03-11-ux-design-philosophy-improvements-design.md\`" \
  --base main
```
