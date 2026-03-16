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
- `fetchBackends` 15s interval moves to `Now.jsx` — see Now.jsx Changes below
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
  RIGHT: 4 HeroCards (unchanged)
```

**Removed from page:** `CurrentJob`, `InfrastructurePanel`, Daemon KPI card, VRAM KPI card.

---

## HostCard Component

### File
`src/components/HostCard.jsx`

### Props
```js
{
  backend,        // from backendsData — { url, healthy, gpu_name, vram_pct, loaded_models, inference_mode }
  currentJob,     // from status.current_job — null if nothing running
  activeEval,     // from status.active_eval — null if no eval running
  evalActiveRun,  // plain object from stores/eval.js evalActiveRun signal (passed as .value) — for progress detail; HostCard must NOT import evalActiveRun from stores directly
  latestHealth,   // healthData[0] — RAM/CPU/Swap for local host only
  settings,       // for pause thresholds on gauge bars
  cpuCount,       // for CPU % conversion (load_avg / cpuCount * 100)
}
```

### Host State Derivation

A pure function `deriveHostState(backend, currentJob, activeEval)` determines each card's state:

| State | Condition | data-mood | ShStatusBadge status |
|---|---|---|---|
| `running` | `backend.healthy` AND `currentJob` exists AND `currentJob.model` matches backend's loaded_models | `dawn` | `active` |
| `eval` | `backend.healthy` AND `activeEval` exists AND (`activeEval.gen_backend_url` OR `activeEval.judge_backend_url`) matches backend.url (see note) | — | `waiting` |
| `warm` | `backend.healthy` AND `backend.loaded_models.length > 0` AND not running/eval | — | `ok` |
| `idle` | `backend.healthy` AND `backend.loaded_models.length === 0` | — | `ok` |
| `offline` | `!backend.healthy` | `dread` | `error` |

Priority: `running` wins over `eval` when both conditions are true.

**Eval backend URL note:** `gen_backend_url` and `judge_backend_url` can be `'auto'` (meaning the router chose the backend dynamically). `'auto'` never equals a real URL so it will never match — no explicit guard needed, but implementers must not add a fallback that treats `'auto'` as a match. When both fields are `'auto'` or null, no HostCard shows eval state (acceptable — routing was dynamic and backend affinity is unknown).

**`deriveHostState` return type:** `{ state, mood, statusBadgeStatus, gpuLabel, loadedLabel, modelsTooltip, isServing, vramPct, vramColor }`. `vramPct` is `backend.vram_pct ?? 0`. `vramColor` uses the **same thresholds and CSS var strings** as the existing `backendRowState`: `vramPct > 90` → `'var(--status-error)'`, `vramPct > 80` → `'var(--status-warning)'`, otherwise `'var(--sh-phosphor)'`. The `gpuLabel` field applies the same NVIDIA prefix abbreviation logic from `backendRowState` in `InfrastructurePanel.jsx`: strip `"NVIDIA GeForce "` and `"NVIDIA "` prefixes (case-insensitive), then extract the URL hostname as fallback if `gpu_name` is null — wrap `new URL(backend.url).hostname` in `try/catch` and fall back to `backend.url` raw string on parse failure (same defensive pattern as existing code).

Model matching uses the same prefix logic as existing `backendRowState`: `loaded.some(m => m === activeModel || m.startsWith(activeModel.split(':')[0] + ':'))`. **`deriveHostState` must call `matchesBackend(backend, currentJob.model)` internally** — do not duplicate the matching logic.

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
[stdout tail — last 5 lines, polled /api/jobs/{id}/log every 5s]   ← running state only; guard: if (!isRunning || !currentJob?.id) return early
[stall warning + "what should I do?" panel]                         ← if currentJob.stall_detected_at
[eval per-variant progress bars]                                    ← eval state only
```

### superhot-ui Effects

| Trigger | Effect |
|---|---|
| `state === 'running'` | `applyMantra(cardRef.current, 'RUNNING')` scoped to this card's ref |
| `state !== 'running'` | `removeMantra(cardRef.current)` |
| `state === 'offline'` | `<ShThreatPulse active={state === 'offline'} persistent={true}>` wraps entire card — `active` prop required or no effect renders |
| Job elapsed time display | `ShFrozen` with `timestamp={currentJob.started_at * 1000}` — `started_at` is Unix epoch **seconds** from DB; multiply by 1000 for the ms value ShFrozen expects |
| `backend.healthy` transitions false | `ShGlitch` wraps `ShStatusBadge`: `<ShGlitch active={glitchActive} intensity="medium"><ShStatusBadge status={statusBadgeStatus} /></ShGlitch>`. **Edge-triggered, not level-triggered**: track previous healthy value in a `useRef`; set `glitchActive` to `true` for one render cycle when `backend.healthy` transitions `true → false`, then clear it on the next render (do NOT keep `active={!backend.healthy}` — that glitches continuously while offline) |
| Cancel eval button | `ShShatter` wrapping the cancel button. `ShShatter`'s wrapper `<div>` intercepts the click — the inner `<button>` must NOT have its own `onClick` (double-fire). Use `onDismiss` for the API call: `<ShShatter onDismiss={() => act('Cancelling…', () => cancelEvalRun(activeEval.id), () => 'Cancelled')}><button class="t-btn">✕ cancel</button></ShShatter>` — use `useActionFeedback` per existing button patterns |

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
// Returns: { state, mood, statusBadgeStatus, gpuLabel, loadedLabel, modelsTooltip, isServing, vramPct, vramColor }
// gpuLabel: strips "NVIDIA GeForce " / "NVIDIA " prefixes; falls back to URL hostname (try/catch on URL parse)
// vramColor: 'var(--status-error)' (>90%), 'var(--status-warning)' (>80%), 'var(--sh-phosphor)' (otherwise)
// isServing: true when state === 'running' — used for phosphor outline on the compact row
// Calls matchesBackend() internally for the 'running' model-match check
// Includes all logic previously in backendRowState() from InfrastructurePanel.jsx

export function isLocalBackend(url)
// Returns true if url contains 127.0.0.1 or localhost

export function hostGauges(latestHealth, settings, cpuCount)
// Same logic as current InfrastructurePanel.hostGauges — moved here
// Returns: [{ label, value, pause, resume }]

export function matchesBackend(backend, model)
// Returns true if model matches any entry in backend.loaded_models
// Guards against null/undefined backend.loaded_models — treats as empty array
// Same prefix logic as backendRowState.isServing in InfrastructurePanel.jsx

export function computeAllUnhealthy(backends)
// Returns true when backends.length > 0 and every backend is unhealthy
// Moved from InfrastructurePanel.jsx — keeps the same logic
// Exported for use by callers (e.g. test suites, future alert banners)
```

---

## Now.jsx Changes

### Imports removed
- `CurrentJob` from `../components/CurrentJob.jsx`
- `InfrastructurePanel` from `../components/InfrastructurePanel.jsx`

### Imports added
- `HostCard` from `../components/HostCard.jsx`
- `backendsData`, `fetchBackends` from `../stores` (already exported via health.js re-export)
- `evalActiveRun` from `../stores`

Note: `applyMantra` and `removeMantra` belong in `HostCard.jsx` only — do not import them into `Now.jsx`.

### fetchBackends polling (replaces InfrastructurePanel's interval)

`InfrastructurePanel.jsx` owned the 15s `setInterval(fetchBackends, 15000)`. When it is deleted, `Now.jsx` takes over ownership:

```jsx
useEffect(() => {
    fetchBackends();
    const id = setInterval(fetchBackends, 15000);
    return () => clearInterval(id);
}, []);
```

This must be added to `Now.jsx` to prevent `backendsData` from going stale after first load.

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
  ✓ returns true for http://127.0.0.1:11434 (with port)
  ✓ returns true for http://localhost:11434 (with port)
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

computeAllUnhealthy()
  ✓ returns true when all backends are unhealthy
  ✓ returns false when at least one backend is healthy
  ✓ returns false for empty array

HostCard render tests (JSDOM)
  ✓ running state: renders ShStatusBadge with status="active"
  ✓ running state: renders source, elapsed, progress bar
  ✓ eval state: renders phase label and progress bar
  ✓ warm state: renders loaded model name
  ✓ idle state: renders idle message
  ✓ offline state: renders unreachable message; container.querySelector('[data-sh-effect="threat-pulse"]') is non-null (ShThreatPulse active prop set)
  ✓ offline state: gpuLabel falls back to URL hostname when gpu_name is null
  ✓ local backend: renders RAM/CPU/Swap gauges
  ✓ remote backend: renders "remote host" note, no gauges
  ✓ expand toggle: renders in all states (offline/idle/warm — not just running)
  ✓ expand toggle: stdout poll does not fire when currentJob is null (non-running state)
  ✓ expand toggle: shows stdout section after click in running state
  ✓ expand toggle: collapses on second click
  ✓ stall warning: renders when currentJob.stall_detected_at is set
  ✓ eval expanded: renders per-variant progress bars
  ✓ deriveHostState returns vramColor='var(--status-error)' when vram_pct > 90
  ✓ deriveHostState returns vramColor='var(--status-warning)' when vram_pct > 80 and <= 90
  ✓ deriveHostState returns vramColor='var(--sh-phosphor)' when vram_pct <= 80
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
- **All backends unreachable:** All HostCards show `offline` state with `ShThreatPulse`. The "All backends unreachable — routing unavailable" string message from `InfrastructurePanel` is **intentionally dropped** — individual HostCard offline states communicate this directly without a redundant banner.
- **currentJob model not in any backend's loaded_models yet (still loading):** `deriveHostState` returns `warm` or `idle` — the `running` state badge only shows when model is confirmed warm on that backend. This matches existing `ActiveGpuBadge` behavior.
- **eval active but no backend URL match:** When `gen_backend_url` and `judge_backend_url` are both `'auto'` or null, no HostCard matches and no card shows `eval` state — the eval is running but backend affinity is unknown. This is acceptable. No fallback display is needed; the eval progress is visible on the Eval tab.
- **Single backend:** One HostCard renders. No layout change needed — flex-col stacks fine.
- **Empty-state submit affordance removed:** `CurrentJob.jsx` rendered an `EmptyState` component with an `onSubmitRequest` callback when the queue was idle — a shortcut to submit a job from the Now page. This affordance is intentionally dropped. Submitting jobs is the CLI's job (`ollama-queue submit`); the dashboard is read-only monitoring. No replacement needed.

---

## Notes on UI Layman Comments

Per `ollama-queue/CLAUDE.md`, every JSX component must include a comment block:
- **What it shows**
- **What decision it drives**

`HostCard.jsx` must include this at file level and on any non-obvious sub-section (expand panel, gauge row, eval section).
