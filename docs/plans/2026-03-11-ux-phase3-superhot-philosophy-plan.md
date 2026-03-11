# UX Phase 3: Superhot Philosophy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **PREREQUISITE:** Phase 1 (`feature/ux-foundation`) must be merged to main before starting this phase.
> **PARALLEL:** This phase can run concurrently with Phase 4 in separate worktrees — they touch different files.

**Goal:** Extend superhot-ui effects throughout the dashboard — freshness on queue/DLQ rows, PAUSED/OFFLINE mantra, glitch on disconnection/killed state, shatter on DLQ dismiss and history clear, three-state ThreatPulse, degradation pulse, uniform PageBanners, CRT on modals.

**Architecture:** Pure SPA changes. All effects use `superhot-ui` imports already in the project. No new API endpoints. All changes additive over existing effect wiring from PR #103.

**Tech Stack:** Preact 10, superhot-ui (already installed), esbuild. Build: `cd ollama_queue/dashboard/spa && npm run build`. Tests: `npm test`.

---

## Pre-flight

```bash
cd ~/Documents/projects/ollama-queue
git checkout main && git pull origin main  # Must include Phase 1
git checkout -b feature/superhot-philosophy
# Verify superhot-ui is available
grep "superhot-ui" ollama_queue/dashboard/spa/package.json
cd ollama_queue/dashboard/spa && npm run build && npm test
```

---

## Task 1: ShFrozen on Queue Rows

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/QueueList.jsx`

**Step 1: Check ShFrozen API**

```bash
grep -rn "ShFrozen\|ShFrozen" ~/Documents/projects/superhot-ui/preact/ | head -10
cat ~/Documents/projects/superhot-ui/preact/index.js 2>/dev/null | head -30
```

**Step 2: Read QueueList.jsx**

```bash
cat ollama_queue/dashboard/spa/src/components/QueueList.jsx
```

**Step 3: Import ShFrozen and wrap rows**

In `QueueList.jsx`, add import:
```jsx
import { ShFrozen } from 'superhot-ui/preact';
```

Wrap each job row's root element with `<ShFrozen>`:
```jsx
<ShFrozen
  key={job.id}
  timestamp={job.enqueued_at}
  thresholds={{ cooling: 300, frozen: 1800, stale: 3600 }}
>
  <div class="queue-row ...">
    {/* existing row content */}
  </div>
</ShFrozen>
```

Note: `enqueued_at` is a Unix timestamp (seconds). Verify the field name:
```bash
grep -n "enqueued_at\|created_at\|queued_at" ollama_queue/dashboard/spa/src/store.js | head -5
```

**Step 4: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/QueueList.jsx
git commit -m "feat(spa): apply ShFrozen to queue rows — time-in-queue visible as data state"
```

---

## Task 2: ShFrozen on DLQ Entries

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/HistoryList.jsx`

**Step 1: Read HistoryList.jsx DLQ section**

```bash
grep -n "dlq\|DLQ\|dead.letter\|failed_at" ollama_queue/dashboard/spa/src/components/HistoryList.jsx | head -20
```

**Step 2: Wrap DLQ rows with ShFrozen**

DLQ entries are staler than queue entries — use longer thresholds:
```jsx
import { ShFrozen } from 'superhot-ui/preact';

// DLQ thresholds: fresh=<1h, cooling=1-6h, frozen=6-24h, stale=>24h
const DLQ_FRESHNESS = { cooling: 3600, frozen: 21600, stale: 86400 };

// Wrap each DLQ row:
<ShFrozen timestamp={entry.failed_at} thresholds={DLQ_FRESHNESS}>
  <div class="dlq-row ...">
    {/* existing DLQ row content */}
  </div>
</ShFrozen>
```

**Step 3: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/HistoryList.jsx
git commit -m "feat(spa): apply ShFrozen to DLQ entries with longer staleness thresholds"
```

---

## Task 3: Time-Aware Gantt — Past Bars Desaturate

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`

**Step 1: Read GanttChart.jsx**

```bash
cat ollama_queue/dashboard/spa/src/components/GanttChart.jsx | head -80
```

**Step 2: Add isPast computation to each Gantt bar**

Find where Gantt bars are rendered. For each job bar, compute:
```jsx
const nowSec = Date.now() / 1000;
const isPast = job.end_time && job.end_time < nowSec;
const isOverrun = job.start_time && !job.end_time && job.start_time < nowSec && job.estimated_end && job.estimated_end < nowSec;
```

Apply to bar style:
```jsx
style={{
  // ... existing bar styles ...
  filter: isPast ? 'saturate(0.2) opacity(0.6)' : 'none',
  ...(isOverrun ? { 'data-sh-effect': 'threat-pulse' } : {}),
}}
```

**Step 3: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/GanttChart.jsx
git commit -m "feat(spa): time-aware Gantt — past bars desaturate, overrun bars get ThreatPulse"
```

---

## Task 4: PAUSED Mantra on CurrentJob

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/CurrentJob.jsx`

**Step 1: Read existing mantra useEffect in CurrentJob.jsx**

```bash
grep -n "mantra\|applyMantra\|removeMantra" ollama_queue/dashboard/spa/src/components/CurrentJob.jsx
```

**Step 2: Add PAUSED mantra effect**

The `RUNNING` mantra already exists. Add a parallel effect for `PAUSED`.
In `CurrentJob.jsx`, after the existing mantra `useEffect`:

```jsx
// Mantra: stamp "PAUSED" watermark when daemon is paused.
useEffect(() => {
  if (!cardRef.current) return;
  if (isPaused) {
    applyMantra(cardRef.current, 'PAUSED');
  } else {
    removeMantra(cardRef.current);
  }
  return () => { if (cardRef.current) removeMantra(cardRef.current); };
}, [isPaused]);
```

Note: `isRunning` and `isPaused` are mutually exclusive — both mantras won't fire simultaneously.

**Step 3: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/CurrentJob.jsx
git commit -m "feat(spa): add PAUSED mantra watermark to CurrentJob on daemon pause"
```

---

## Task 5: OFFLINE Mantra on Disconnection

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Now.jsx`

**Step 1: Read Now.jsx**

```bash
grep -n "connectionStatus\|disconnected\|useRef\|mantra" ollama_queue/dashboard/spa/src/pages/Now.jsx | head -20
```

**Step 2: Add OFFLINE mantra effect**

In `Now.jsx`:
```jsx
import { applyMantra, removeMantra } from 'superhot-ui';
import { useRef, useEffect } from 'preact/hooks';

// In component body:
const nowRef = useRef(null);

useEffect(() => {
  if (!nowRef.current) return;
  if (connectionStatus.value === 'disconnected') {
    applyMantra(nowRef.current, 'OFFLINE');
  } else {
    removeMantra(nowRef.current);
  }
  return () => { if (nowRef.current) removeMantra(nowRef.current); };
}, [connectionStatus.value]);
```

Add `ref={nowRef}` to the Now page root div.

**Step 3: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat(spa): add OFFLINE mantra watermark to Now page when disconnected"
```

---

## Task 6: Glitch on Disconnection + Killed State

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/SystemHealthChip.jsx` (or Sidebar.jsx)
- Modify: `ollama_queue/dashboard/spa/src/components/StatusBadge.jsx`

**Step 1: Check StatusBadge glitch implementation from PR #103**

```bash
grep -n "glitch\|data-sh" ollama_queue/dashboard/spa/src/components/StatusBadge.jsx
```

**Step 2: Add `killed` to glitch-trigger states in StatusBadge.jsx**

Find where `failed` triggers a glitch burst. Add `killed` alongside it:
```jsx
const shouldGlitch = state === 'failed' || state === 'killed' || state === 'error';
```

Ensure `data-sh-intensity="high"` is applied for both `failed` and `killed`.

**Step 3: Add glitch to SystemHealthChip on disconnection**

In `SystemHealthChip.jsx`, add a ref and glitch effect:
```jsx
import { useRef, useEffect } from 'preact/hooks';

const chipRef = useRef(null);
const lastDisconnected = useRef(false);

useEffect(() => {
  if (!chipRef.current) return;
  const isDisconnected = connectionStatus === 'disconnected';

  if (isDisconnected && !lastDisconnected.current) {
    // Transition to disconnected — fire glitch
    chipRef.current.setAttribute('data-sh-effect', 'glitch');
    setTimeout(() => {
      if (chipRef.current) chipRef.current.removeAttribute('data-sh-effect');
    }, 600);
  }
  lastDisconnected.current = isDisconnected;
}, [connectionStatus]);
```

Add `ref={chipRef}` to the chip root element. Add `connectionStatus` as a prop from Sidebar.

**Step 4: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/StatusBadge.jsx \
        ollama_queue/dashboard/spa/src/components/SystemHealthChip.jsx \
        ollama_queue/dashboard/spa/src/components/Sidebar.jsx
git commit -m "feat(spa): glitch on disconnection and killed state transitions"
```

---

## Task 7: Shatter on DLQ Dismiss + History Clear-All

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/HistoryList.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/History.jsx`
- Modify: `ollama_queue/dashboard/spa/src/store.js`

**Step 1: Read HistoryList DLQ dismiss flow**

```bash
grep -n "dismiss\|acknowledge\|dlq" ollama_queue/dashboard/spa/src/components/HistoryList.jsx | head -10
```

**Step 2: Add shatter to DLQ row dismiss**

In `HistoryList.jsx`, for each DLQ row, add a `rowRef`:
```jsx
const rowRefs = useRef({});
// On each DLQ row element: ref={el => rowRefs.current[entry.id] = el}

// On dismiss button click:
async function dismissDlqEntry(entryId) {
  const el = rowRefs.current[entryId];
  const { shatterElement } = await import('superhot-ui');
  if (el) {
    shatterElement(el, {
      onComplete: async () => {
        await fetch(`/api/dlq/${entryId}`, { method: 'DELETE' });
        triggerRefresh();
      },
    });
  } else {
    await fetch(`/api/dlq/${entryId}`, { method: 'DELETE' });
    triggerRefresh();
  }
}
```

**Step 3: Add "Clear all completed" to History.jsx with cascade shatter**

In `History.jsx`, find the completed jobs section. Add a "Clear completed" button:

```jsx
async function clearCompleted() {
  const { shatterElement } = await import('superhot-ui');
  const refs = completedRowRefs.current; // array of row element refs
  refs.forEach((el, i) => {
    if (el) {
      setTimeout(() => {
        shatterElement(el, {
          onComplete: i === refs.length - 1
            ? () => fetch('/api/jobs/completed', { method: 'DELETE' }).then(() => triggerRefresh())
            : undefined,
        });
      }, i * 80);
    }
  });
}
```

Check if `DELETE /api/jobs/completed` exists:
```bash
grep -rn "completed" ollama_queue/api/routes/ | grep -i "delete\|clear" | head -5
```

If not, add it following the existing job route patterns.

**Step 4: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/HistoryList.jsx \
        ollama_queue/dashboard/spa/src/pages/History.jsx \
        ollama_queue/dashboard/spa/src/store.js
git commit -m "feat(spa): shatter on DLQ row dismiss and cascade shatter on clear-all completed"
```

---

## Task 8: Three-State ThreatPulse on ResourceGauges + KPI Degradation

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/ResourceGauges.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/Now.jsx`

**Step 1: Read existing ThreatPulse in ResourceGauges**

```bash
grep -n "threat\|pulse\|data-sh" ollama_queue/dashboard/spa/src/components/ResourceGauges.jsx
```

**Step 2: Add warning-tier ThreatPulse to ResourceGauges**

Find where the existing critical ThreatPulse fires. Add a warning tier at 70%:

```jsx
const WARNING_PCT = 70; // below pause threshold

// For each gauge, compute:
const isWarning = value >= WARNING_PCT && value < pauseThreshold;
const isCritical = value >= pauseThreshold;

// Apply effects:
if (isCritical) {
  el.setAttribute('data-sh-effect', 'threat-pulse');
  el.setAttribute('data-sh-color', 'error');
} else if (isWarning) {
  el.setAttribute('data-sh-effect', 'threat-pulse');
  el.setAttribute('data-sh-color', 'warning');
} else {
  el.removeAttribute('data-sh-effect');
  el.removeAttribute('data-sh-color');
}
```

Verify `data-sh-color` is a valid superhot-ui attribute:
```bash
grep -rn "data-sh-color\|sh-color" ~/Documents/projects/superhot-ui/css/ | head -5
```

If not supported, use amber pulse by adding a CSS class for warning color override instead.

**Step 3: Add ThreatPulse on Success Rate degradation in Now.jsx**

In `Now.jsx`, find the Success Rate HeroCard. Add a ref and effect:

```jsx
const successRateRef = useRef(null);
const lastPulsed = useRef(0);

useEffect(() => {
  if (!successRateRef.current) return;
  const rate = successRate.value || 1;
  const isDegraded = rate < 0.8;
  const now = Date.now();

  // Re-fire at most every 30s to prevent pulse fatigue
  if (isDegraded && now - lastPulsed.current > 30000) {
    successRateRef.current.setAttribute('data-sh-effect', 'threat-pulse');
    lastPulsed.current = now;
    setTimeout(() => {
      if (successRateRef.current) successRateRef.current.removeAttribute('data-sh-effect');
    }, 3000);
  }
}, [successRate.value]);
```

Add `ref={successRateRef}` to the Success Rate HeroCard wrapper.

**Step 4: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/ResourceGauges.jsx \
        ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat(spa): three-state ThreatPulse on gauges (warning/critical), degradation pulse on KPIs"
```

---

## Task 9: PageBanner Audit + CRT on Modals

**Files:**
- Modify: pages that are missing PageBanner: `Consumers.jsx`, `Eval.jsx`, `Performance.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/SubmitJobModal.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/AddRecurringJobModal.jsx`

**Step 1: Audit which pages have PageBanner**

```bash
grep -rn "PageBanner" ollama_queue/dashboard/spa/src/pages/ ollama_queue/dashboard/spa/src/components/
```

**Step 2: Add PageBanner to each missing page**

For each page missing it:
```jsx
import PageBanner from '../components/PageBanner.jsx';

// At top of return:
<PageBanner label="CONSUMERS" />  // or EVAL, PERFORMANCE
```

Verify PageBanner accepts a `label` prop:
```bash
cat ollama_queue/dashboard/spa/src/components/PageBanner.jsx
```

**Step 3: Add .sh-crt to SubmitJobModal**

```bash
cat ollama_queue/dashboard/spa/src/components/SubmitJobModal.jsx | head -30
```

Find the modal root element. Add `class="sh-crt"` or append to existing classes:
```jsx
<div class="modal-overlay">
  <div class="t-frame sh-crt" style="...">
    {/* modal content */}
  </div>
</div>
```

Verify `.sh-crt` is exported from superhot-ui CSS:
```bash
grep -n "sh-crt\|\.crt" ~/Documents/projects/superhot-ui/css/ -r | head -5
```

**Step 4: Add .sh-crt to AddRecurringJobModal**

Same pattern as Step 3.

**Step 5: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 6: Full test + commit**

```bash
cd ollama_queue/dashboard/spa && npm test
git add ollama_queue/dashboard/spa/src/pages/Consumers.jsx \
        ollama_queue/dashboard/spa/src/pages/Eval.jsx \
        ollama_queue/dashboard/spa/src/pages/Performance.jsx \
        ollama_queue/dashboard/spa/src/components/SubmitJobModal.jsx \
        ollama_queue/dashboard/spa/src/components/AddRecurringJobModal.jsx
git commit -m "feat(spa): uniform PageBanners on all tabs, CRT scanlines on modal dialogs"
```

---

## Task 10: Final Build, Test, Push, PR

**Step 1: Full build and test suite**

```bash
cd ollama_queue/dashboard/spa && npm run build && npm test
cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/ -x -q
```

**Step 2: Push**

```bash
cd ~/Documents/projects/ollama-queue
git push -u origin feature/superhot-philosophy
```

**Step 3: Create PR**

```bash
gh pr create \
  --title "feat(spa): UX Phase 3 — superhot philosophy (freshness, mantra, glitch, shatter, ThreatPulse extensions)" \
  --body "## UX Phase 3: Superhot Philosophy

Implements items 21–33 from the UX & design philosophy improvements design.

### Changes
- ShFrozen on all queue rows (enqueue timestamp → cooling/frozen/stale)
- ShFrozen on DLQ entries (failure timestamp, longer thresholds)
- Time-aware Gantt: past bars desaturate, overrun bars get ThreatPulse
- PAUSED mantra watermark on CurrentJob
- OFFLINE mantra watermark on Now page when disconnected
- Glitch on daemon status chip during disconnection
- Glitch burst on 'killed' state (alongside existing 'failed')
- Shatter on DLQ row dismiss
- Cascade shatter on clear-all completed jobs
- Three-state ThreatPulse on ResourceGauges (warning + critical)
- ThreatPulse on Success Rate KPI when < 80%
- PageBanner added to all missing tabs (Consumers, Eval, Performance)
- .sh-crt CRT scanlines on SubmitJobModal and AddRecurringJobModal

### Design doc
\`docs/plans/2026-03-11-ux-design-philosophy-improvements-design.md\`" \
  --base main
```
