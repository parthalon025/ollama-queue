# Now Page — Host-First Unification

**Date:** 2026-03-16
**Status:** Approved for implementation
**Branch:** `feature/now-host-first`

---

## Problem

The Now page is job-centric: it shows what job is running, then asks "which host is it on?" For a multi-GPU setup, this is the wrong mental model. VRAM is the scarce resource. The right question is "what are my hosts doing?" — with job, eval, and model state as attributes of each host.

Additionally, three subsystems (daemon job, eval progress, loaded models) are currently spread across separate components (`CurrentJob`, `InfrastructurePanel`, `ActiveEvalStrip`) with no unified view. The eval strip exists as a component but is not wired into Now.jsx. Model state is only visible on the Models tab or buried in backend rows.

---

## Goals

1. Promote GPU host to top-level organizing unit on the Now page
2. Surface daemon job + eval + loaded model state as attributes of the host running them
3. Remove `CurrentJob` and `InfrastructurePanel` — replace with a single `HostCard` component
4. Use only `superhot-ui` components — no expedition33-ui dependencies
5. Full test coverage for all host states via new `HostCard.test.js`

---

## Non-Goals

- No changes to the Models tab, History tab, or any other page
- No new API endpoints or backend changes
- No changes to polling intervals (backendsData stays at 15s, status stays at 5s)
- No expedition33-ui components (`BattlePanel`, `HUDFrame`, `StatBar`, `GlyphBadge`, etc.)

---

## Page Structure (After)

```
[ShPageBanner]
[ShStatsGrid — trimmed: Queue Depth, Jobs 24h, RAM only]
[HostCard × N  — one per backend from backendsData, 15s refresh]
[Alert strip — conditional, above bottom grid]
[2-column grid]
  LEFT:  QueueList (unchanged)
  RIGHT: 4 ShHeroCards (unchanged)
```

**Removed from page:** `CurrentJob`, `InfrastructurePanel`, Daemon KPI card, VRAM KPI card.

---

## HostCard Component

### File
`src/components/HostCard.jsx`

### Props
```js
{
  backend,        // from backendsData — { url, healthy, gpu_name, vram_pct, loaded_models, weight }
  currentJob,     // from status.current_job — null if nothing running
  activeEval,     // from status.active_eval — null if no eval running
  evalActiveRun,  // from stores/eval.js evalActiveRun signal — for progress detail
  latestHealth,   // healthData[0] — RAM/CPU/Swap for local host only
  settings,       // for pause thresholds on gauge bars
  cpuCount,       // for CPU % conversion (load_avg / cpuCount * 100)
}
```

### Host State Derivation

A pure function `deriveHostState(backend, currentJob, activeEval)` determines each card's state:

| State | Condition | Chroma | ShStatusBadge status |
|---|---|---|---|
| `running` | `backend.healthy` AND `currentJob` exists AND `currentJob.model` matches backend's loaded_models | `dawn` mood | `active` |
| `eval` | `backend.healthy` AND `activeEval` exists AND (`activeEval.gen_backend_url` OR `activeEval.judge_backend_url`) matches backend.url | — | `waiting` |
| `warm` | `backend.healthy` AND `backend.loaded_models.length > 0` AND not running/eval | — | `ok` |
| `idle` | `backend.healthy` AND `backend.loaded_models.length === 0` | — | `ok` |
| `offline` | `!backend.healthy` | `dread` mood | `error` |

Model matching uses the same prefix logic as existing `backendRowState`: `loaded.some(m => m === activeModel || m.startsWith(activeModel.split(':')[0] + ':'))`.

### Local vs Remote Host

A backend is "local" when its URL contains `127.0.0.1` or `localhost`. Local hosts show RAM/CPU/Swap from `latestHealth`. Remote hosts show: `"remote host — system metrics not available"`.

### Compact View (default)

```
[ShStatusBadge status={state}]  [GPU label]  [VRAM bar + %]  [loaded model chip]  [state pill]
  [if running]  source · elapsed · ~remaining   [progress bar]
  [if eval]     phase label · N% · ~remaining   [progress bar]
  [if warm]     model name · idle
  [if offline]  unreachable
[RAM  ░░░░░░░░  N%]
[CPU  ░░░░░░░░  N%]
[Swap ░░░░░░░░  N%]     ← local host only; "remote host" note for remote backends
[▾ details]             ← expand toggle, always present
```

### Expanded View (on toggle)

Compact view stays visible. Adds below:
```
[stdout tail — last 5 lines, polled /api/jobs/{id}/log every 5s]   ← running state only
[stall warning + "what should I do?" panel]                         ← if currentJob.stall_detected_at
[eval per-variant progress bars]                                    ← eval state only
```

### superhot-ui Effects

| Trigger | Effect |
|---|---|
| `state === 'running'` | `applyMantra(cardRef.current)` scoped to this card's ref |
| `state !== 'running'` | `removeMantra(cardRef.current)` |
| `state === 'offline'` | `ShThreatPulse` wraps entire card with `persistent={true}` |
| Job elapsed time display | `ShFrozen` with `timestamp={currentJob.started_at * 1000}` — ages if job stalls |
| `backend.healthy` transitions false | `ShGlitch` on `ShStatusBadge` (`active` prop flips) |
| Cancel eval button | `ShShatter` wrapping the cancel button |

### CSS Classes

- Outer: `.t-frame[data-label={gpuLabel}]` — existing frame pattern, mood-aware
- Running state: `data-mood="dawn"` on the card
- Offline state: `data-mood="dread"` on the card
- Bars: same gradient pattern as current `InfrastructurePanel` `HostGaugeBar` (reuse inline style logic)
- Expand toggle: `.t-btn` for consistency with existing button styles

---

## Pure Helper Functions (exported from HostCard.jsx)

These are extracted as named exports so they can be unit-tested without a render cycle:

```js
export function deriveHostState(backend, currentJob, activeEval)
// Returns: { state, mood, statusBadgeStatus, gpuLabel, loadedLabel, modelsTooltip, isServing }

export function isLocalBackend(url)
// Returns true if url contains 127.0.0.1 or localhost

export function hostGauges(latestHealth, settings, cpuCount)
// Same logic as current InfrastructurePanel.hostGauges — moved here
// Returns: [{ label, value, pause, resume }]

export function matchesBackend(backend, model)
// Returns true if model matches any entry in backend.loaded_models
// Same prefix logic as backendRowState.isServing
```

---

## Now.jsx Changes

### Imports removed
- `CurrentJob` from `../components/CurrentJob.jsx`
- `InfrastructurePanel` from `../components/InfrastructurePanel.jsx`

### Imports added
- `HostCard` from `../components/HostCard.jsx`
- `backendsData` from `../stores` (already exported via health.js re-export)
- `evalActiveRun` from `../stores`

### kpiStats trimmed
Remove from the array:
- `{ label: 'Daemon', ... }` — state now on host card
- `{ label: 'VRAM', ... }` — VRAM now on host card

Keep: Queue Depth, Jobs 24h, RAM.

### Layout change
Replace the 2-column `now-grid` with:
```jsx
{/* Host cards — one per backend */}
<div class="flex flex-col gap-3">
  {(backendsData.value || []).map(backend => (
    <HostCard
      key={backend.url}
      backend={backend}
      currentJob={currentJob}
      activeEval={activeEval}
      evalActiveRun={evalActiveRun.value}
      latestHealth={latestHealth}
      settings={sett}
      cpuCount={cpuCount.value}
    />
  ))}
</div>

{/* Alert strip — unchanged */}
{showAlerts && (...)}

{/* Bottom 2-column: queue + hero cards */}
<div class="now-grid">
  <QueueList jobs={q} currentJob={currentJob} />
  <div class="flex flex-col gap-3">
    {/* 4 HeroCards — unchanged */}
    {/* Proxy mini-stat — unchanged */}
  </div>
</div>
```

---

## Files Deleted

- `src/components/CurrentJob.jsx`
- `src/components/CurrentJob.test.js` (if exists)
- `src/components/InfrastructurePanel.jsx`
- `src/components/InfrastructurePanel.test.js` (if exists)

---

## Files Created

- `src/components/HostCard.jsx`
- `src/components/HostCard.test.js`

---

## Files Modified

- `src/pages/Now.jsx`

---

## Testing

### HostCard.test.js — pure function tests

```
deriveHostState()
  ✓ returns 'running' when backend is healthy and currentJob model matches loaded_models
  ✓ returns 'eval' when activeEval.gen_backend_url matches backend.url
  ✓ returns 'eval' when activeEval.judge_backend_url matches backend.url
  ✓ returns 'warm' when backend healthy, models loaded, no job/eval
  ✓ returns 'idle' when backend healthy, no models loaded
  ✓ returns 'offline' when backend.healthy is false
  ✓ prefers 'running' over 'eval' when both conditions are true (edge case)

isLocalBackend()
  ✓ returns true for 127.0.0.1
  ✓ returns true for localhost
  ✓ returns false for remote IP
  ✓ returns false for Tailscale hostname

hostGauges()
  ✓ returns RAM, CPU, Swap gauges with correct pause/resume thresholds
  ✓ normalises CPU from load_avg using cpuCount
  ✓ returns [] when latestHealth is null

matchesBackend()
  ✓ exact model name match
  ✓ prefix match (qwen2.5:7b matches qwen2.5:latest)
  ✓ returns false for no match
  ✓ returns false for empty loaded_models

HostCard render tests (JSDOM)
  ✓ running state: renders ShStatusBadge with status="active"
  ✓ running state: renders source, elapsed, progress bar
  ✓ eval state: renders phase label and progress bar
  ✓ warm state: renders loaded model name
  ✓ idle state: renders idle message
  ✓ offline state: renders unreachable message, ShThreatPulse active
  ✓ local backend: renders RAM/CPU/Swap gauges
  ✓ remote backend: renders "remote host" note, no gauges
  ✓ expand toggle: shows stdout section after click
  ✓ expand toggle: collapses on second click
  ✓ stall warning: renders when currentJob.stall_detected_at is set
  ✓ eval expanded: renders per-variant progress bars
```

### Now.jsx
Existing Now.jsx tests (if any) should be updated to reflect removed CurrentJob/InfrastructurePanel and added HostCard.

---

## Data Flow

```
backendsData (15s)  ──→  HostCard list  ──→  one card per backend
status.current_job (5s) ──→  passed to each HostCard as prop
status.active_eval (5s) ──→  passed to each HostCard as prop
evalActiveRun (5s when active) ──→  expanded eval detail in HostCard
healthData[0] (60s) ──→  RAM/CPU/Swap for local host card only
```

No new fetch calls. No polling interval changes. `models` signal from `stores/models.js` is NOT used by HostCard — loaded model comes from `backend.loaded_models` in `backendsData` (already fresh at 15s).

---

## Edge Cases

- **No backends configured:** Render nothing in the host section; existing alert strip covers connection issues.
- **All backends unreachable:** All HostCards show `offline` state with `ShThreatPulse`. No special "all unreachable" message needed — the cards communicate it directly.
- **currentJob model not in any backend's loaded_models yet (still loading):** `deriveHostState` returns `warm` or `idle` — the `running` state badge only shows when model is confirmed warm on that backend. This matches existing `ActiveGpuBadge` behavior.
- **eval active but no backend URL match:** Eval state falls back to Now.jsx's existing `activeEval` display in the first local backend card (same behavior as current `currentJob` on single-backend setups).
- **Single backend:** One HostCard renders. No layout change needed — flex-col stacks fine.

---

## Notes on UI Layman Comments

Per `ollama-queue/CLAUDE.md`, every JSX component must include a comment block:
- **What it shows**
- **What decision it drives**

`HostCard.jsx` must include this at file level and on any non-obvious sub-section (expand panel, gauge row, eval section).
