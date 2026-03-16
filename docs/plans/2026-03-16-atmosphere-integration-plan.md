# Atmosphere Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bring ollama-queue dashboard to full superhot atmosphere spec compliance — all 5 effects fully wired, 3 health states, tiered button shatter, entry choreography, terminal voice, quiet world, and coordinated failure theater.

**Architecture:** New `stores/atmosphere.js` signal store derives health state from existing signals. New `hooks/useShatter.js` hook wraps superhot-ui's `shatterElement()` with tiered presets. All reusable pieces already exist in superhot-ui — this is pure wiring work in ollama-queue's SPA.

**Tech Stack:** Preact 10, @preact/signals, superhot-ui (file: dependency), Tailwind v4, esbuild

**Design doc:** `docs/plans/2026-03-16-atmosphere-integration-design.md`

**Key reference docs:**
- superhot-ui atmosphere guide: `~/Documents/projects/superhot-ui/docs/atmosphere-guide.md`
- superhot-ui experience design: `~/Documents/projects/superhot-ui/docs/experience-design.md`
- superhot-ui design philosophy: `~/Documents/projects/superhot-ui/docs/design-philosophy.md`
- LLM design system guide: `docs/llm-guide-design-system.md`

**Build & verify:** `cd ollama_queue/dashboard/spa && npm run build` after every batch. The SPA has no automated test suite for visual behavior — verify effects in-browser via `ollama-queue serve`.

**CRITICAL:** Never use `h` or `Fragment` as `.map()` callback parameter names — esbuild injects `h` as JSX factory.

---

## Batch 1: Foundation (Atmosphere Store + Shatter Hook)

### Task 1: Create atmosphere store

**Files:**
- Create: `ollama_queue/dashboard/spa/src/stores/atmosphere.js`

**Step 1: Write the atmosphere store**

```js
// What it does: Derives system health mode from existing signals and manages
//   escalation timeline, effect density budget, and state transition detection.
// Decision it drives: Components read healthMode/escalationLevel to coordinate
//   failure theater effects — glitch bursts, threat pulses, mantras — without
//   each component independently deciding "am I in trouble?"

import { signal, effect } from '@preact/signals';
import { trackEffect, isOverBudget, playSfx, ShAudio } from 'superhot-ui';
import { connectionStatus, status } from './queue.js';
import { dlqCount, backendsData, backendsError } from './health.js';

// ── Derived signals ──────────────────────────────────────────────────────────

export const healthMode = signal('operational');    // 'operational' | 'degraded' | 'critical'
export const prevHealthMode = signal('operational');
export const escalationLevel = signal(0);           // 0–3
export const failedServices = signal([]);           // human-readable list of what's wrong

// ── Audio preference ─────────────────────────────────────────────────────────

const AUDIO_KEY = 'queue-audio';

function _readAudioPref() {
  try { return localStorage.getItem(AUDIO_KEY) === 'true'; } catch (_) { return false; }
}

export function setAudioEnabled(enabled) {
  ShAudio.enabled = !!enabled;
  try { localStorage.setItem(AUDIO_KEY, String(!!enabled)); } catch (_) {}
}

// ── Effect density + cooldown ────────────────────────────────────────────────

let _lastEffectTime = 0;
const COOLDOWN_MS = 300; // rest frame after any significant effect

export function canFireEffect(id) {
  const now = Date.now();
  if (now - _lastEffectTime < COOLDOWN_MS) return null;
  if (isOverBudget()) return null;
  const cleanup = trackEffect(id);
  _lastEffectTime = now;
  return cleanup;
}

// ── Health computation ───────────────────────────────────────────────────────

function _computeHealth() {
  const failed = [];
  const conn = connectionStatus.value;
  const daemon = status.value?.daemon?.state;
  const backends = backendsData.value || [];
  const dlq = dlqCount.value;

  // Critical conditions
  if (conn === 'disconnected') failed.push('CONNECTION LOST');
  if (daemon === 'offline' || daemon === 'error') failed.push('DAEMON DOWN');
  const allBackendsDown = backends.length > 0 && backends.every(b => !b.healthy);
  if (allBackendsDown) failed.push('ALL BACKENDS UNREACHABLE');

  if (failed.length > 0) {
    return { mode: 'critical', failed };
  }

  // Degraded conditions
  if (backends.some(b => !b.healthy)) failed.push('BACKEND UNREACHABLE');
  if (dlq > 0) failed.push(`${dlq} DLQ ENTRIES`);
  if (daemon && daemon.startsWith('paused')) failed.push('DAEMON PAUSED');
  if (backendsError.value) failed.push('BACKEND FETCH ERROR');

  if (failed.length > 0) {
    return { mode: 'degraded', failed };
  }

  return { mode: 'operational', failed: [] };
}

// ── Escalation timeline ──────────────────────────────────────────────────────

let _escalationTimers = [];

function _clearEscalation() {
  _escalationTimers.forEach(clearTimeout);
  _escalationTimers = [];
  escalationLevel.value = 0;
}

function _startEscalation() {
  _clearEscalation();
  // Level 0 is immediate (individual component effects)
  _escalationTimers.push(setTimeout(() => { escalationLevel.value = 1; }, 5000));   // 5s: sidebar pulses
  _escalationTimers.push(setTimeout(() => { escalationLevel.value = 2; }, 15000));  // 15s: section mantra
  _escalationTimers.push(setTimeout(() => { escalationLevel.value = 3; }, 60000));  // 60s: layout-root mantra
}

// ── Lifecycle ────────────────────────────────────────────────────────────────

let _disposeEffect = null;

export function initAtmosphere() {
  // Restore audio preference
  ShAudio.enabled = _readAudioPref();

  _disposeEffect = effect(() => {
    const { mode, failed } = _computeHealth();
    const prev = healthMode.value;

    failedServices.value = failed;

    if (mode !== prev) {
      prevHealthMode.value = prev;
      healthMode.value = mode;

      if (mode === 'operational' && prev !== 'operational') {
        // Recovery catharsis
        _clearEscalation();
        if (ShAudio.enabled) playSfx('complete');
      } else if (mode !== 'operational' && prev === 'operational') {
        // Entering failure state
        _startEscalation();
        if (ShAudio.enabled) playSfx('error');
      } else if (mode === 'critical' && prev === 'degraded') {
        // Escalating — restart timers from current level
        _startEscalation();
        if (ShAudio.enabled) playSfx('error');
      }
    }
  });
}

export function disposeAtmosphere() {
  _clearEscalation();
  if (_disposeEffect) { _disposeEffect(); _disposeEffect = null; }
}
```

**Step 2: Re-export from stores barrel**

Modify: `ollama_queue/dashboard/spa/src/stores/index.js`

Add after the existing re-exports (after `export * from './health.js';`):

```js
export * from './atmosphere.js';
```

**Step 3: Build to verify no syntax errors**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Build succeeds with no errors.

**Step 4: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/stores/atmosphere.js ollama_queue/dashboard/spa/src/stores/index.js
git commit -m "feat(spa): add atmosphere store — health mode, escalation, effect density"
```

---

### Task 2: Create useShatter hook

**Files:**
- Create: `ollama_queue/dashboard/spa/src/hooks/useShatter.js`

**Step 1: Write the hook**

```js
// What it does: Returns a ref + fire function for tiered shatter effects on buttons.
// Decision it drives: Every action button in the SPA shatters on click — fragment
//   count communicates intent (earned > complete > routine).

import { useRef, useCallback } from 'preact/hooks';
import { shatterElement } from 'superhot-ui';
import { canFireEffect } from '../stores/atmosphere.js';

const TIER_PRESETS = {
  earned:   { fragments: 7 },
  complete: { fragments: 6 },
  routine:  { fragments: 3 },
};

export function useShatter(tier = 'routine') {
  const ref = useRef(null);

  const fire = useCallback(() => {
    if (!ref.current) return;
    // Routine tier skips effect budget — too fast and small to count
    if (tier !== 'routine') {
      const cleanup = canFireEffect('shatter-' + tier);
      if (!cleanup) return;
      // cleanup is called automatically when fragment animation ends
    }
    shatterElement(ref.current, TIER_PRESETS[tier] || TIER_PRESETS.routine);
  }, [tier]);

  return [ref, fire];
}
```

**Step 2: Build to verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Build succeeds.

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/hooks/useShatter.js
git commit -m "feat(spa): add useShatter hook — tiered button shatter (earned/complete/routine)"
```

---

### Task 3: Wire atmosphere into app.jsx

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/app.jsx`

**Step 1: Add atmosphere lifecycle**

At the top imports, add:

```js
import { initAtmosphere, disposeAtmosphere, healthMode, escalationLevel, failedServices } from './stores';
```

In the `App` component, add a `useEffect` for lifecycle:

```js
useEffect(() => {
    initAtmosphere();
    return () => disposeAtmosphere();
}, []);
```

**Step 2: Wire layout-root health attribute**

On the `<div class="layout-root sh-crt">`, add:

```js
data-sh-health={healthMode.value}
```

**Step 3: Replace hardcoded ShMantra with escalation-driven mantra**

Replace the existing `<ShMantra text="SYSTEM PAUSED" active={isDaemonPaused} />` with:

```jsx
<ShMantra
    text={healthMode.value === 'critical'
        ? (failedServices.value[0] || 'SYSTEM DOWN')
        : (isDaemonPaused ? 'SYSTEM PAUSED' : '')}
    active={isDaemonPaused || escalationLevel.value >= 3}
/>
```

**Step 4: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Build succeeds.

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/app.jsx
git commit -m "feat(spa): wire atmosphere store into app lifecycle + escalation-driven mantra"
```

---

### Task 4: Wire sidebar escalation indicator

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/Sidebar.jsx`

**Step 1: Import atmosphere signals**

Add to imports:

```js
import { escalationLevel, healthMode } from '../stores';
import { ShThreatPulse } from 'superhot-ui/preact';
```

**Step 2: Add escalation pulse to health chip area**

Wrap the `SystemHealthChip` container div with `ShThreatPulse`:

```jsx
<div style={{ borderBottom: '1px solid var(--border-subtle)', flexShrink: 0 }}>
    <ShThreatPulse active={escalationLevel.value >= 1} persistent>
        <SystemHealthChip ... />
    </ShThreatPulse>
</div>
```

**Step 3: Add DLQ badge threat pulse**

Wrap the DLQ badge span with `ShThreatPulse`:

```jsx
{badge && (
    <ShThreatPulse active={badge > 0} persistent>
        <span key={badgeAnimKey.current} ...>{badge}</span>
    </ShThreatPulse>
)}
```

**Step 4: Terminal voice on tooltips**

Update NAV_ITEMS tooltips to terminal voice:

```js
{ id: 'now',      ..., tooltip: 'LIVE — CURRENT OPERATIONS' },
{ id: 'plan',     ..., tooltip: 'SCHEDULE — RECURRING JOBS' },
{ id: 'history',  ..., tooltip: 'HISTORY — COMPLETED AND FAILED' },
{ id: 'models',   ..., tooltip: 'MODELS — INSTALLED AI MODELS' },
{ id: 'settings', ..., tooltip: 'CONFIG — THRESHOLDS AND DEFAULTS' },
{ id: 'eval',     ..., tooltip: 'EVAL — MODEL COMPARISON' },
{ id: 'consumers',..., tooltip: 'CONSUMERS — OLLAMA SERVICE DETECTION' },
{ id: 'performance',..., tooltip: 'PERF — THROUGHPUT AND HEALTH' },
{ id: 'backends', ..., tooltip: 'BACKENDS — GPU FLEET MANAGEMENT' },
```

**Step 5: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 6: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/Sidebar.jsx
git commit -m "feat(spa): sidebar escalation pulse + DLQ threat pulse + terminal tooltips"
```

---

## Batch 2: Entry Choreography + Quiet World

### Task 5: Add stagger classes to all pages

**Files:**
- Modify: All 9 page files

**Step 1: Apply stagger to each page**

For each page component, replace the outer content `<div>` class. Pattern:

**Before:** `<div class="flex flex-col gap-4 animate-page-enter">`
**After:** `<div class="flex flex-col gap-4 sh-stagger-children animate-page-enter">`

Apply to:
- `src/pages/Now.jsx:131` — `<div ref={pageRef} class="flex flex-col gap-4 animate-page-enter" ...>`
- `src/pages/Plan/index.jsx` — outer div
- `src/pages/History.jsx` — outer div
- `src/pages/ModelsTab.jsx` — outer div
- `src/pages/Settings.jsx:129` — `<div class="flex flex-col gap-4 animate-page-enter" ...>`
- `src/pages/Eval.jsx` — outer div
- `src/pages/Consumers.jsx` — outer div
- `src/pages/Performance.jsx` — outer div
- `src/pages/BackendsTab.jsx` — outer div

**Step 2: Add tier delay classes to primary/secondary content groups**

Within each page, add `sh-delay-100` to primary data sections and `sh-delay-200` to secondary sections. Example for Now.jsx:

- `<ShPageBanner ...>` — T0 (no delay, first child of stagger)
- `<ShStatsGrid ...>` — T1 (auto-staggered as 2nd child)
- `<div class="now-grid">` — add `sh-delay-100` for T2
- HeroCard grid — add `sh-delay-200` for T3

**Step 3: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 4: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/
git commit -m "feat(spa): add sh-stagger-children entry choreography to all 9 pages"
```

---

### Task 6: Migrate EmptyState → ShEmptyState

**Files:**
- Modify: `src/components/CurrentJob.jsx` (lines 253-257)
- Modify: `src/components/QueueList.jsx` (line 104)
- Modify: `src/components/HistoryList.jsx` (line 37)
- Delete: `src/components/EmptyState.jsx` (after all references removed)

**Step 1: Update CurrentJob empty state**

Replace the `EmptyState` import with `ShEmptyState`:

```js
import { ShEmptyState } from 'superhot-ui/preact';
```

Replace (around line 253):

```jsx
<EmptyState
    headline="Ready — nothing in queue"
    body="Jobs you submit will appear here."
    action={onSubmitRequest ? { label: '+ Submit a job', onClick: onSubmitRequest } : undefined}
/>
```

With:

```jsx
<ShEmptyState mantra="STANDBY" hint="submit a job to begin">
    {onSubmitRequest && (
        <button class="t-btn" onClick={onSubmitRequest} style="margin-top:8px;font-size:var(--type-label);">
            + SUBMIT
        </button>
    )}
</ShEmptyState>
```

**Step 2: Update QueueList empty state**

In `src/components/QueueList.jsx`, replace `EmptyState` import with `ShEmptyState` from `superhot-ui/preact`:

```jsx
<ShEmptyState mantra="CLEAR" hint="all jobs processed" />
```

**Step 3: Update HistoryList empty state**

In `src/components/HistoryList.jsx`, replace:

```jsx
<ShEmptyState mantra="NO DATA" hint="run a job to see results" />
```

**Step 4: Remove EmptyState.jsx**

Delete `src/components/EmptyState.jsx`. Verify no remaining imports:

Run: `grep -r "EmptyState" ollama_queue/dashboard/spa/src/ --include="*.jsx" --include="*.js"`
Expected: No matches.

**Step 5: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 6: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add -u ollama_queue/dashboard/spa/src/
git commit -m "feat(spa): migrate EmptyState → ShEmptyState with terminal mantras"
```

---

### Task 7: Migrate ErrorState → ShErrorState and additional empty states

**Files:**
- Modify: Any files importing ErrorState (search for `ErrorState` usage)
- Modify: Pages with inline empty states not using EmptyState component
- Delete: `src/components/ErrorState.jsx` (after migration)

**Step 1: Find and replace all ErrorState usages**

Search all `.jsx` files for `ErrorState` usage. Replace import with `ShErrorState` from `superhot-ui/preact`.

Replace `<ErrorState error={err} onRetry={fn} />` with:
```jsx
<ShErrorState title="FAULT" message={err instanceof Error ? err.message : String(err)} onRetry={fn} />
```

**Step 2: Add ShEmptyState to pages with inline empty patterns**

Search for inline empty state patterns (e.g. `"No data"`, `"No results"`, `"Nothing to show"`) in all page components and replace with `<ShEmptyState mantra="..." hint="..." />` per the design doc §4 mapping table.

Key locations to check:
- `src/pages/History.jsx` — DLQ empty, deferred empty, filter no-match
- `src/pages/Eval.jsx` / `src/views/EvalRuns.jsx` — no runs, no variants, no trends
- `src/pages/Consumers.jsx` — no consumers detected
- `src/pages/Performance.jsx` — no metrics
- `src/pages/Plan/index.jsx` — no recurring jobs, empty load map
- `src/pages/ModelsTab.jsx` — no models
- `src/pages/BackendsTab.jsx` — no backends (unlikely but handle)

**Step 3: Delete ErrorState.jsx**

**Step 4: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add -u ollama_queue/dashboard/spa/src/
git commit -m "feat(spa): migrate ErrorState → ShErrorState + add ShEmptyState across all pages"
```

---

## Batch 3: Terminal Voice Audit

### Task 8: Terminal voice — TAB_CONFIG subtitles + toast messages

**Files:**
- Modify: `src/config/tabs.js`
- Modify: `src/stores/health.js` (addToast call sites)
- Modify: `src/pages/BackendsTab.jsx` (addToast call site)

**Step 1: Update TAB_CONFIG subtitles to terminal voice**

In `src/config/tabs.js`, update all subtitle strings:

```js
{ id: 'now',         subtitle: 'LIVE COMMAND CENTER' },
{ id: 'plan',        subtitle: 'RECURRING JOBS AND RUN TIMES' },
{ id: 'history',     subtitle: 'COMPLETED AND FAILED JOBS' },
{ id: 'models',      subtitle: 'INSTALLED MODELS AND DOWNLOADS' },
{ id: 'settings',    subtitle: 'THRESHOLDS, DEFAULTS, DAEMON CONTROLS' },
{ id: 'eval',        subtitle: 'TEST AND COMPARE MODEL CONFIGURATIONS' },
{ id: 'consumers',   subtitle: 'CONSUMER DETECTION AND ROUTING' },
{ id: 'performance', subtitle: 'MODEL THROUGHPUT AND SYSTEM HEALTH' },
{ id: 'backends',    subtitle: 'MULTI-GPU FLEET MANAGEMENT AND ROUTING' },
```

Also update tooltip strings to UPPERCASE terminal voice (matching §4 Sidebar changes).

**Step 2: Update toast strings**

In `src/pages/BackendsTab.jsx:262`:
- `'Remove failed: ${e.message}'` → `'REMOVE FAILED: ${e.message}'`

Search for any other `addToast(` calls and convert messages to UPPERCASE terminal voice.

**Step 3: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 4: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/config/tabs.js ollama_queue/dashboard/spa/src/stores/health.js ollama_queue/dashboard/spa/src/pages/BackendsTab.jsx
git commit -m "feat(spa): terminal voice — TAB_CONFIG subtitles + toast messages"
```

---

### Task 9: Terminal voice — useActionFeedback labels

**Files:**
- Modify: All files containing `useActionFeedback` call sites (see grep results)

**Step 1: Update all action feedback labels to terminal voice**

For every `act('Loading…', fn, 'Done')` or `act('Cancelling…', fn, result => ...)` pattern, convert to UPPERCASE:

Key files (with their action labels):

**`src/pages/Now.jsx:214`:**
- `'Clearing…'` → `'CLEARING'`
- `'Cleared'` → `'CLEARED'`

**`src/pages/Settings.jsx:100-141`:**
- `'Pausing daemon…'` → `'PAUSING'`
- `'Daemon paused'` → `'PAUSED'`
- `'Resuming daemon…'` → `'RESUMING'`
- `'Daemon resumed'` → `'RESUMED'`
- `'Restarting…'` → `'RESTARTING'`
- `'Restart signalled'` → `'RESTART SIGNALLED'`

**`src/pages/History.jsx`:**
- All retry/dismiss/reschedule feedback labels → UPPERCASE

**`src/pages/Plan/index.jsx`:**
- Delete/run-now/pin/rebalance/re-enable/save/generate/batch-toggle → UPPERCASE

**`src/pages/Consumers.jsx`:**
- Scan/include/ignore feedback → UPPERCASE

**`src/pages/BackendsTab.jsx`:**
- Test/add/remove/weight feedback → UPPERCASE

**All `src/components/eval/*.jsx`:**
- Repeat/analyze/promote/cancel/retry/save/delete/generate-description → UPPERCASE

**`src/components/SubmitJobModal.jsx`:**
- Submit feedback → UPPERCASE

**`src/components/DeferredPanel.jsx`:**
- Resume feedback → UPPERCASE

**`src/components/ActiveEvalStrip.jsx`:**
- Cancel feedback → UPPERCASE

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/
git commit -m "feat(spa): terminal voice — all useActionFeedback labels to UPPERCASE"
```

---

### Task 10: Terminal voice — inline copy audit

**Files:**
- Modify: Multiple component files

**Step 1: Audit and update all remaining inline copy**

Target strings (search and replace across all `.jsx` files):

**Now.jsx:**
- `'⚠ Lost connection to the queue server — trying to reconnect...'` → `'SIGNAL LOST — RECONNECTING'`
- `'⚠ Needs Attention'` → `'ATTENTION REQUIRED'`
- `'{dlqCnt} failed {dlqCnt === 1 ? 'job' : 'jobs'} need attention'` → `'{dlqCnt} FAILED — REVIEW REQUIRED'`
- `'View failed'` → `'VIEW'`
- `'Dismiss all'` → `'DISMISS ALL'`
- `'{recentFailures} job{...} failed in the last 24h'` → `'{recentFailures} FAILURES (24H)'`
- `'{disabledRecurring} scheduled job{...} auto-disabled'` → `'{disabledRecurring} JOBS AUTO-DISABLED'`
- HeroCard labels: keep as-is (data labels stay title-case per spec)
- Delta strings (buildJobsDelta etc.): convert to terminal voice

**CurrentJob.jsx:**
- `'⚠ frozen — what should I do? ▾'` → `'STALLED — RESOLUTION ▾'`
- `'Job is not producing output.'` → `'NO OUTPUT DETECTED'`
- Resolution checklist items: keep as-is (instructional text stays readable)
- `'Output'` summary → `'OUTPUT'`
- `'No output yet'` → `'NO OUTPUT'`

**CohesionHeader.jsx:**
- Connection status text → terminal voice

**OnboardingOverlay.jsx:**
- All onboarding copy → terminal voice

**SubmitJobModal.jsx:**
- Modal title and field labels → terminal voice

**Settings.jsx:**
- `'⚠ Daemon restart required...'` → `'RESTART REQUIRED'`
- Keyboard shortcuts line → terminal voice

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/
git commit -m "feat(spa): terminal voice — all inline copy converted to piOS voice"
```

---

## Batch 4: Freshness Expansion

### Task 11: ShFrozen on Now page KPIs and health gauges

**Files:**
- Modify: `src/pages/Now.jsx`
- Modify: `src/components/HeroCard.jsx`
- Modify: `src/components/ResourceGauges.jsx`

**Step 1: Wrap ShStatsGrid with ShFrozen using daemon timestamp**

In `src/pages/Now.jsx`, import `ShFrozen`:

```js
import { ShFrozen } from 'superhot-ui/preact';
```

Wrap the `<ShStatsGrid>` with:

```jsx
<ShFrozen timestamp={st?.daemon?.timestamp ? st.daemon.timestamp * 1000 : null}
          thresholds={{ cooling: 30, frozen: 120, stale: 300 }}>
    <ShStatsGrid stats={kpiStats} />
</ShFrozen>
```

**Step 2: Wrap HeroCards with ShFrozen**

Each HeroCard in the 2x2 grid already has a timestamp in its sparkData. Wrap each with:

```jsx
<ShFrozen timestamp={latestHealth?.timestamp ? latestHealth.timestamp * 1000 : null}
          thresholds={{ cooling: 30, frozen: 120, stale: 300 }}>
    <HeroCard ... />
</ShFrozen>
```

**Step 3: Wrap ResourceGauges with ShFrozen in CurrentJob.jsx**

In `src/components/CurrentJob.jsx`, add `ShFrozen` import and wrap `ResourceGauges`:

```jsx
<ShFrozen timestamp={latestHealth?.timestamp ? latestHealth.timestamp * 1000 : null}
          thresholds={{ cooling: 30, frozen: 120, stale: 300 }}>
    <ResourceGauges ... />
</ShFrozen>
```

**Step 4: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/Now.jsx ollama_queue/dashboard/spa/src/components/CurrentJob.jsx
git commit -m "feat(spa): ShFrozen on Now KPIs + health gauges (30s/2m/5m thresholds)"
```

---

### Task 12: ShFrozen on DLQ, deferred, eval, consumers, models, gantt

**Files:**
- Modify: `src/pages/History.jsx` — DLQ entry rows, deferred panel rows
- Modify: `src/views/EvalRuns.jsx` — eval run rows
- Modify: `src/pages/Consumers.jsx` or `src/components/consumers/ConsumerRow.jsx` — consumer cards
- Modify: `src/pages/Performance.jsx` — model performance stats
- Modify: `src/components/GanttChart.jsx` — gantt bars

**Step 1: Wrap DLQ entries in History with ShFrozen**

In the DLQ entry list rendering (inside `History.jsx`), wrap each entry's row/card with:

```jsx
<ShFrozen timestamp={entry.failed_at ? entry.failed_at * 1000 : null}
          thresholds={{ cooling: 3600, frozen: 21600, stale: 86400 }}>
    {/* existing DLQ row content */}
</ShFrozen>
```

**Step 2: Wrap eval run rows with ShFrozen**

In `src/views/EvalRuns.jsx` or `src/components/eval/RunRow/index.jsx`, wrap each run row:

```jsx
<ShFrozen timestamp={(run.completed_at || run.started_at) ? (run.completed_at || run.started_at) * 1000 : null}
          thresholds={{ cooling: 3600, frozen: 43200, stale: 172800 }}>
    {/* existing run row content */}
</ShFrozen>
```

**Step 3: Wrap consumer rows with ShFrozen**

In `src/components/consumers/ConsumerRow.jsx`, wrap with:

```jsx
<ShFrozen timestamp={consumer.last_seen ? consumer.last_seen * 1000 : null}>
    {/* existing consumer row content */}
</ShFrozen>
```

**Step 4: Wrap GanttChart bars**

In `src/components/GanttChart.jsx`, wrap each job bar with ShFrozen using `job.last_run_at`:

```jsx
<ShFrozen timestamp={job.last_run_at ? job.last_run_at * 1000 : null}>
    {/* existing bar content */}
</ShFrozen>
```

**Step 5: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 6: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/
git commit -m "feat(spa): ShFrozen on DLQ/deferred/eval/consumers/gantt (custom thresholds)"
```

---

## Batch 5: Effect Deepening + Transitions

### Task 13: Glitch expansion — connection, eval failures, backend test

**Files:**
- Modify: `src/components/CohesionHeader.jsx`
- Modify: `src/views/EvalRuns.jsx` or `src/components/eval/RunRow/index.jsx`
- Modify: `src/pages/BackendsTab.jsx`

**Step 1: Add glitch on connection state transitions in CohesionHeader**

In `src/components/CohesionHeader.jsx`, add:

```js
import { useRef, useEffect } from 'preact/hooks';
import { glitchText } from 'superhot-ui';
import { connectionStatus } from '../stores';
import { canFireEffect } from '../stores/atmosphere.js';
```

Add a `useEffect` that watches `connectionStatus` and fires glitch:

```js
const headerRef = useRef(null);
const prevConn = useRef(connectionStatus.value);

useEffect(() => {
    const curr = connectionStatus.value;
    if (curr !== prevConn.current && headerRef.current) {
        const intensity = curr === 'disconnected' ? 'high' : 'medium';
        const cleanup = canFireEffect('glitch-connection');
        if (cleanup) {
            glitchText(headerRef.current, { intensity });
        }
    }
    prevConn.current = curr;
}, [connectionStatus.value]);
```

**Step 2: Add glitch on eval run failure**

In eval run row component, watch for status transition to 'failed':

```js
useEffect(() => {
    if (run.status === 'failed' && rowRef.current) {
        const cleanup = canFireEffect('glitch-eval-' + run.id);
        if (cleanup) glitchText(rowRef.current, { intensity: 'high' });
    }
}, [run.status]);
```

**Step 3: Add glitch on backend test failure**

In `src/pages/BackendsTab.jsx`, in the `handleTest` catch block:

```js
import { glitchText } from 'superhot-ui';
import { canFireEffect } from '../stores/atmosphere.js';

// In the catch block of handleTest:
catch (e) {
    setTestResult({ ok: false, error: e.message });
    // Glitch burst on test failure
    if (cardRef.current) {
        const cleanup = canFireEffect('glitch-backend-test');
        if (cleanup) glitchText(cardRef.current, { intensity: 'medium' });
    }
}
```

**Step 4: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/CohesionHeader.jsx ollama_queue/dashboard/spa/src/pages/BackendsTab.jsx
git add ollama_queue/dashboard/spa/src/views/ ollama_queue/dashboard/spa/src/components/eval/
git commit -m "feat(spa): glitch expansion — connection transitions, eval failures, backend tests"
```

---

### Task 14: ThreatPulse expansion — VRAM, RAM, circuit breaker, stuck eval

**Files:**
- Modify: `src/components/ResourceGauges.jsx`
- Modify: `src/components/ActiveEvalStrip.jsx`
- Modify: `src/pages/Now.jsx` (circuit breaker on daemon card)

**Step 1: Add ThreatPulse to ResourceGauges on threshold breach**

In `src/components/ResourceGauges.jsx`, import `ShThreatPulse`:

```js
import { ShThreatPulse } from 'superhot-ui/preact';
```

Wrap VRAM bar with:

```jsx
<ShThreatPulse active={vram > 90} persistent>
    {/* existing VRAM gauge */}
</ShThreatPulse>
```

Wrap RAM bar with:

```jsx
<ShThreatPulse active={ram > (settings?.ram_pause_threshold || 85)} persistent>
    {/* existing RAM gauge */}
</ShThreatPulse>
```

**Step 2: Add ThreatPulse to ActiveEvalStrip on stuck eval**

In `src/components/ActiveEvalStrip.jsx`:

```js
import { ShThreatPulse } from 'superhot-ui/preact';
```

Check if eval has been generating for >10 minutes:

```jsx
const isStuck = activeRun?.status === 'generating' &&
    activeRun?.started_at && (Date.now() / 1000 - activeRun.started_at > 600);

<ShThreatPulse active={isStuck}>
    {/* existing strip content */}
</ShThreatPulse>
```

**Step 3: Add ThreatPulse on Now daemon card for circuit breaker**

In `src/pages/Now.jsx`, check if daemon state indicates circuit breaker:

```jsx
const isCircuitOpen = daemon?.state === 'error' || daemon?.circuit_breaker_open;
```

Wrap the CurrentJob component area with:

```jsx
<ShThreatPulse active={isCircuitOpen} persistent>
    <CurrentJob ... />
</ShThreatPulse>
```

**Step 4: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 5: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/ResourceGauges.jsx ollama_queue/dashboard/spa/src/components/ActiveEvalStrip.jsx ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat(spa): ThreatPulse on VRAM/RAM breach, stuck eval, circuit breaker"
```

---

### Task 15: Audio toggle in Settings + recovery catharsis

**Files:**
- Modify: `src/pages/Settings.jsx`

**Step 1: Add audio toggle next to CRT toggle**

In `src/pages/Settings.jsx`, import:

```js
import { setAudioEnabled } from '../stores/atmosphere.js';
import { ShAudio } from 'superhot-ui';
```

Add audio state:

```js
const [audioOn, setAudioOn] = useState(() => {
    try { return localStorage.getItem('queue-audio') === 'true'; } catch (_) { return false; }
});
```

After the CRT toggle `<div class="t-frame" data-label="Display">`, add:

```jsx
<div class="t-frame" data-label="Audio" style="margin-top:1rem;">
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-family:var(--font-mono);font-size:var(--type-label);color:var(--text-secondary);">
        <input
            type="checkbox"
            checked={audioOn}
            onChange={ev => {
                const val = ev.target.checked;
                setAudioOn(val);
                setAudioEnabled(val);
            }}
        />
        PROCEDURAL SFX
    </label>
    <span style="font-size:var(--type-micro);color:var(--text-tertiary);margin-top:4px;display:block;">
        AUDIO CUES ON STATE TRANSITIONS — ERROR, RECOVERY, DLQ
    </span>
</div>
```

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/Settings.jsx
git commit -m "feat(spa): audio toggle in Settings — opt-in procedural SFX"
```

---

## Batch 6: Button Shatter Wiring

### Task 16: Wire useShatter to Now page actions

**Files:**
- Modify: `src/pages/Now.jsx`

**Step 1: Add useShatter to the Dismiss All button**

Import:

```js
import { useShatter } from '../hooks/useShatter.js';
```

In the component, add:

```js
const [dismissRef, dismissShatter] = useShatter('earned');
```

Wire the "Dismiss all" button:

```jsx
<button
    ref={dismissRef}
    class="t-btn"
    style={{ fontSize: 'var(--type-micro)', padding: '2px 8px', color: 'var(--text-tertiary)' }}
    disabled={dismissFb.phase === 'loading'}
    onClick={() => { dismissShatter(); dismissAct('CLEARING', clearDLQ, () => 'CLEARED'); }}
>DISMISS ALL</button>
```

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat(spa): wire useShatter to Now page dismiss-all (earned tier)"
```

---

### Task 17: Wire useShatter to Settings page actions

**Files:**
- Modify: `src/pages/Settings.jsx`

**Step 1: Wire shatter to Pause/Resume/Restart buttons**

Import:

```js
import { useShatter } from '../hooks/useShatter.js';
```

Add hooks:

```js
const [pauseRef, pauseShatter] = useShatter('routine');
const [resumeRef, resumeShatter] = useShatter('routine');
const [restartRef, restartShatter] = useShatter('complete');
```

Wire ref and fire to each button's `ref` and `onClick`.

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/Settings.jsx
git commit -m "feat(spa): wire useShatter to Settings pause/resume/restart buttons"
```

---

### Task 18: Wire useShatter to History page actions

**Files:**
- Modify: `src/pages/History.jsx`

**Step 1: Wire shatter to DLQ retry/dismiss/reschedule/clear buttons**

For each DLQ entry row component (DLQEntryRow or inline):
- Retry button → `useShatter('routine')`
- Dismiss button → `useShatter('earned')`
- Reschedule button → `useShatter('routine')`
- Retry All button → `useShatter('routine')`
- Clear All button → `useShatter('earned')`

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/History.jsx
git commit -m "feat(spa): wire useShatter to History DLQ actions (earned/routine tiers)"
```

---

### Task 19: Wire useShatter to Plan page actions

**Files:**
- Modify: `src/pages/Plan/index.jsx`

**Step 1: Wire shatter to Plan actions**

- Delete button → `useShatter('earned')`
- Run Now button → `useShatter('complete')`
- Pin/Unpin button → `useShatter('routine')`
- Rebalance button → `useShatter('routine')`
- Re-enable button → `useShatter('routine')`
- Save button → `useShatter('complete')`
- Batch run → `useShatter('complete')`
- Batch toggle → `useShatter('routine')`

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/Plan/
git commit -m "feat(spa): wire useShatter to Plan page actions (delete=earned, run/save=complete)"
```

---

### Task 20: Wire useShatter to Eval components

**Files:**
- Modify: `src/components/eval/RunRow/index.jsx`
- Modify: `src/components/eval/ActiveRunProgress.jsx`
- Modify: `src/components/eval/VariantRow.jsx`
- Modify: `src/components/eval/VariantToolbar.jsx`
- Modify: `src/components/eval/RunTriggerPanel.jsx`
- Modify: `src/components/eval/TemplateRow.jsx`
- Modify: `src/components/eval/EvalNextStepsCard.jsx`
- Modify: `src/components/eval/ProviderRoleSection.jsx`
- Modify: `src/components/eval/VariantTable.jsx`

**Step 1: Wire shatter to eval actions**

- Cancel eval → `useShatter('earned')`
- Repeat run → `useShatter('complete')`
- Promote → `useShatter('complete')`
- Analyze → `useShatter('routine')`
- Delete variant → `useShatter('earned')`
- Save variant → `useShatter('complete')`
- Generate description → `useShatter('routine')`
- Delete template → `useShatter('earned')`
- Run trigger → `useShatter('complete')`
- Prime datasource → `useShatter('routine')`
- Resume eval → `useShatter('routine')`
- Retry eval → `useShatter('routine')`

**Step 2: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/eval/
git commit -m "feat(spa): wire useShatter to all Eval component actions"
```

---

### Task 21: Wire useShatter to Backends, Consumers, SubmitJobModal

**Files:**
- Modify: `src/pages/BackendsTab.jsx`
- Modify: `src/pages/Consumers.jsx`
- Modify: `src/components/consumers/ConsumerRow.jsx`
- Modify: `src/components/SubmitJobModal.jsx`
- Modify: `src/components/DeferredPanel.jsx`

**Step 1: Wire shatter to Backend actions**

- Add backend → `useShatter('complete')`
- Remove backend → `useShatter('earned')`
- Test backend → `useShatter('routine')`
- Update weight → `useShatter('routine')`

**Step 2: Wire shatter to Consumer actions**

- Scan → `useShatter('routine')`
- Include consumer → `useShatter('complete')`
- Ignore consumer → `useShatter('earned')`
- Revert consumer → `useShatter('earned')`

**Step 3: Wire shatter to SubmitJobModal**

- Submit button → `useShatter('complete')`

**Step 4: Wire shatter to DeferredPanel**

- Resume button → `useShatter('routine')`

**Step 5: Build and verify**

Run: `cd ollama_queue/dashboard/spa && npm run build`

**Step 6: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/BackendsTab.jsx ollama_queue/dashboard/spa/src/pages/Consumers.jsx
git add ollama_queue/dashboard/spa/src/components/consumers/ ollama_queue/dashboard/spa/src/components/SubmitJobModal.jsx
git add ollama_queue/dashboard/spa/src/components/DeferredPanel.jsx
git commit -m "feat(spa): wire useShatter to Backends/Consumers/Submit/Deferred actions"
```

---

## Batch 7: Final Integration + Mock Update

### Task 22: Update test mocks

**Files:**
- Modify: `src/__mocks__/superhot-ui.cjs`

**Step 1: Add new imports to the mock**

Add mocked versions of any newly-imported superhot-ui functions:

```js
module.exports = {
    // Existing mocks
    applyFreshness: () => {},
    shatterElement: () => () => {},
    glitchText: () => Promise.resolve(),
    applyMantra: () => {},
    removeMantra: () => {},
    playSfx: () => {},
    ShAudio: { enabled: false },
    trackEffect: () => () => {},
    isOverBudget: () => false,
    activeEffectCount: () => 0,
    MAX_EFFECTS: 3,
    SHATTER_PRESETS: { toast: 4, cancel: 6, alert: 8, purge: 12 },
    setCrtMode: () => {},
    CRT_PRESETS: {},
    setCrtPreset: () => {},
};
```

Also update `src/__mocks__/superhot-ui/preact.cjs` if it exists (or the preact mock path in jest.config.cjs) to include `ShEmptyState` and `ShErrorState`:

```js
ShEmptyState: ({ mantra, hint, children }) => h('div', { 'data-testid': 'sh-empty' }, mantra, children),
ShErrorState: ({ title, message, onRetry }) => h('div', { 'data-testid': 'sh-error' }, message),
```

**Step 2: Run existing tests**

Run: `cd ollama_queue/dashboard/spa && npm test`
Expected: All existing tests pass with updated mocks.

**Step 3: Build final production bundle**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Clean build, no errors.

**Step 4: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/__mocks__/
git commit -m "chore(spa): update test mocks for atmosphere integration"
```

---

### Task 23: Final verification

**Step 1: Run full test suite**

Run: `cd ~/Documents/projects/ollama-queue && source .venv/bin/activate && pytest --timeout=120 -x -q`
Expected: All 1,943+ tests pass (SPA changes don't affect Python tests).

**Step 2: Run SPA tests**

Run: `cd ollama_queue/dashboard/spa && npm test`
Expected: All tests pass.

**Step 3: Production build**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Clean build.

**Step 4: Visual verification**

Run: `ollama-queue serve --port 7683`
Open: `http://127.0.0.1:7683/ui/`

Verify:
- [ ] Pages stagger-enter (structure first, then data, then ambient)
- [ ] Empty states show terminal mantras (STANDBY, CLEAR, etc.)
- [ ] Buttons shatter on click (earned = more fragments than routine)
- [ ] KPI cards and health gauges show freshness aging
- [ ] DLQ badge on sidebar pulses red
- [ ] All copy is terminal voice (UPPERCASE system messages)
- [ ] CRT toggle still works in Settings
- [ ] Audio toggle appears in Settings
- [ ] Connection loss triggers glitch burst on header
- [ ] Recovery triggers glitch + SFX (if audio enabled)

**Step 5: Final commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/dist/
git commit -m "build(spa): production bundle with full atmosphere integration"
```
