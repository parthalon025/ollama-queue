# Atmosphere Integration Design — ollama-queue × superhot-ui

**Date:** 2026-03-16
**Status:** Approved
**Approach:** Signal-based atmosphere store (Approach B)
**Scope:** Max implementation — all 8 atmosphere gaps + tiered button shatter

## Context

ollama-queue already integrates superhot-ui as a `file:` dependency with partial atmosphere wiring: freshness on backend cards/history/queue, shatter on toast/backend removal, glitch on health chip/status badge, threat pulse on unhealthy backends/stalled jobs, mantra on RUNNING/PAUSED/OFFLINE/SYSTEM PAUSED, entry animation on all pages, CRT overlay on layout root.

This design closes every remaining atmosphere gap to bring the dashboard to full spec compliance with the superhot atmosphere guide (40 rules) and experience design documents.

**Constraint:** Anything reusable goes into superhot-ui first, then ollama-queue consumes it. App-specific wiring stays in ollama-queue.

---

## §1 Atmosphere Store (`stores/atmosphere.js`)

New signal store deriving health state from existing signals. No new API calls — pure reactive computation.

### Inputs (existing signals)

- `connectionStatus` ('ok' | 'disconnected') — from `stores/queue.js`
- `status.value?.daemon_state` — daemon state
- `dlqCount.value` — dead letter queue count
- `backendsData.value` — per-backend health array
- `backendsError.value` — backend fetch error string

### Derived Signals

```
healthMode:      signal<'operational' | 'degraded' | 'critical'>
escalationLevel: signal<0 | 1 | 2 | 3>
failedServices:  signal<string[]>
prevHealthMode:  signal<'operational' | 'degraded' | 'critical'>
```

### Health Mode Rules

- **Critical:** `connectionStatus === 'disconnected'` OR daemon down OR all backends unhealthy
- **Degraded:** any backend unhealthy OR `dlqCount > 0` OR daemon paused
- **Operational:** everything else

### Escalation Timeline

Driven by setTimeout chain, started when mode leaves operational:

| Level | Time | Effect |
|---|---|---|
| 0 | 0–5s | Individual component effects only (threat pulse, toast) |
| 1 | 5–15s | Sidebar indicator pulses red |
| 2 | 15–60s | Section-level mantra appears |
| 3 | 60s+ | Layout-root mantra (`SYSTEM DEGRADED` or `OFFLINE`) |

Escalation resets to 0 when mode returns to operational.

### State Transition Detection

`prevHealthMode` signal enables recovery detection. `degraded → operational` triggers catharsis (glitch burst on recovering element, `playSfx('complete')` if audio enabled). `operational → degraded/critical` triggers `playSfx('error')` if audio enabled.

### Audio on Transitions

- `playSfx('error')` when entering degraded/critical
- `playSfx('complete')` on recovery to operational
- Fires once per transition, not per poll cycle
- Tied to `ShAudio.enabled` (opt-in, off by default, stored in `localStorage` key `queue-audio`)

### Effect Density + Cooldown

`canFireEffect(id)` wraps superhot-ui's `trackEffect()` / `isOverBudget()`:
- Checks 3-effect budget
- Enforces rest-frame cooldown (300ms after shatter, 200ms after glitch, 500ms after state change)
- Returns cleanup function on success, `null` if rejected

### Exports

```js
healthMode          // signal
escalationLevel     // signal
failedServices      // signal
prevHealthMode      // signal
canFireEffect(id)   // budget + cooldown gate → cleanup fn | null
initAtmosphere()    // starts escalation timers, called once from app.jsx
disposeAtmosphere() // cleanup on unmount
```

---

## §2 Tiered Button Shatter

Every action button shatters on click. Fragment count signals importance.

### Tier Mapping

| Tier | Fragments | Duration | Actions |
|---|---|---|---|
| **Earned** (destruction/catharsis) | 6–8 | 600ms | Cancel job, clear DLQ, remove backend, dismiss alert, clear all DLQ, delete variant |
| **Complete** (mission accomplished) | 6 | 600ms | Submit job, promote eval, save settings, include consumer |
| **Routine** (micro-feedback) | 3–4 | 400ms | Retry, pause/resume, test backend, scan consumers, filter, refresh |

### Implementation

New hook in ollama-queue (`hooks/useShatter.js`):

```js
function useShatter(tier = 'routine') {
  const ref = useRef(null);
  const fire = useCallback(() => {
    if (!ref.current) return;
    // Routine tier skips budget (too fast/small to count)
    if (tier !== 'routine' && !canFireEffect('shatter')) return;
    shatterElement(ref.current, TIER_PRESETS[tier]);
  }, [tier]);
  return [ref, fire];
}
```

Presets:
- `earned: { fragments: 7, duration: 600 }`
- `complete: { fragments: 6, duration: 600 }`
- `routine: { fragments: 3, duration: 400 }`

Buttons get: `<button ref={ref} onClick={() => { fire(); doAction(); }}>`.

---

## §3 Entry Choreography (Stagger)

### 5-Tier Stagger

| Order | Delay | What |
|---|---|---|
| Structure (T0) | 0ms | Nav, headers, page banner, layout grid |
| Skeletons (T1) | 50ms | Loading placeholders while data fetches |
| Primary data (T2) | 150ms | Hero card, current job, topology diagram |
| Secondary data (T3) | 250ms | Stat cards, queue list, table rows, gauges |
| Ambient effects (T4) | 400ms | CRT layers, glows, mantra watermarks |

### Implementation

Uses existing superhot-ui CSS classes:
- `.sh-stagger-children` on page content wrappers (auto-staggers children)
- `.sh-delay-100` through `.sh-delay-800` for explicit tier grouping

Applied to all 9 page components: Now, Plan, History, Models, Perf, Settings, Eval, Consumers, Backends.

Skeleton integration: existing `<LoadingState>` renders at T1, real content replaces at T2/T3 via conditional render.

---

## §4 Quiet World (Empty States)

### EmptyState → ShEmptyState Migration (~20 states)

| Location | Mantra | Hint |
|---|---|---|
| Now (no job) | `STANDBY` | submit a job to begin |
| Queue list (empty) | `CLEAR` | all jobs processed |
| History (no results) | `NO MATCH` | adjust filters |
| History (no jobs at all) | `NO DATA` | — |
| DLQ (empty) | `ALL CLEAR` | no failures recorded |
| Deferred (empty) | `CLEAR` | — |
| Models (loading) | `SCANNING` | — |
| Models (empty) | `NO MODELS` | check ollama connection |
| Perf (no metrics) | `NO DATA` | — |
| Perf (empty heatmap) | `NO SIGNAL` | — |
| Plan (no recurring jobs) | `UNSCHEDULED` | — |
| Plan (empty load map) | `NO ACTIVITY` | — |
| Eval Runs (none) | `AWAITING ORDERS` | create a variant and run |
| Eval Variants (none) | `UNCONFIGURED` | — |
| Eval Trends (no data) | `INSUFFICIENT DATA` | — |
| Backends (none) | `NO SIGNAL` | add a backend to begin |
| Consumers (none) | `DARK` | scan to detect services |
| CurrentJob log tail | `NO OUTPUT` | — |

### ErrorState → ShErrorState Migration

Conversational "Something went wrong" → terminal voice + `.sh-frame` + retry button.

### LoadingState → ShSkeleton Migration

Basic spinner → phosphor shimmer skeleton with proper T1 stagger slot.

---

## §5 Terminal Voice Audit (Max Effort — ~100+ strings)

All user-facing rendered text follows piOS voice: uppercase, terse, first-person from system, present tense, never apologetic.

### Scope

- **Toast messages:** All `addToast()` call sites across all pages/stores
- **Page banner subtitles:** TAB_CONFIG subtitle strings
- **Tooltips:** Sidebar nav, buttons, badges
- **Inline help text:** Stall checklist, settings hints, eval setup gates, onboarding overlay, filter placeholders
- **Feedback messages:** All `useActionFeedback` success/error labels
- **Modal descriptions:** SubmitJobModal, etc.
- **Labels/headers:** `<details>` summaries, collapsible headers, table column headers
- **Data formatting:** Inline prose ("No output yet" → `NO OUTPUT`)

### Excluded

- Status badge values (lowercase per spec typography hierarchy)
- Code comments, console.log, API responses
- Numeric/data values

---

## §6 ShFrozen Expansion (Freshness Everywhere)

### New Wrappings (14 components)

| Component | Timestamp Source |
|---|---|
| CurrentJob | `currentJob.started_at` (freezes on completion) |
| HeroCard / ShStatCard (Now KPIs) | `status.value?.timestamp` |
| DLQ entries | `entry.failed_at` |
| Deferred jobs | `deferral.deferred_at` |
| Eval run rows | `run.completed_at` or `run.started_at` |
| Consumer cards | `consumer.last_seen` |
| Model performance stats | `stat.last_recorded_at` |
| System health gauges | `latestHealth.timestamp` |
| GanttChart bars | `job.last_run_at` |
| LoadMapStrip buckets | Bucket timestamp |

### Custom Thresholds

| Context | Cooling | Frozen | Stale |
|---|---|---|---|
| Default | 5m | 30m | 60m |
| Health gauges | 30s | 2m | 5m |
| Backend last_checked | 30s | 2m | 5m |
| DLQ entries | 1h | 6h | 24h |
| Eval runs | 1h | 12h | 48h |

---

## §7 Effect Wiring Pass (Glitch + ThreatPulse Deepening)

### Glitch Expansion

| Trigger | Component | Intensity |
|---|---|---|
| Connection lost | CohesionHeader | high |
| Connection restored (catharsis) | CohesionHeader | medium |
| Data refresh timestamp changes | ShStatCard KPIs on Now page | low |
| Eval run fails | EvalRuns row | high |
| Backend test fails | BackendCard | medium |
| DLQ entry arrives | DLQ section header | medium |
| Job overruns estimate | CurrentJob progress bar | low |
| Consumer scan completes | Consumers page | low |

### ThreatPulse Expansion

| Trigger | Component | Persistent? |
|---|---|---|
| DLQ count > 0 | Sidebar DLQ badge | Yes (until cleared) |
| Any backend unreachable | TopologyDiagram edge | Yes |
| VRAM > 90% | ResourceGauges VRAM bar | Yes |
| RAM > threshold | ResourceGauges RAM bar | Yes |
| Eval run stuck (>10min generating) | ActiveEvalStrip | No (2-cycle) |
| Recurring job auto-disabled | Plan tab Gantt bar | No (2-cycle) |
| Circuit breaker open | Now page daemon card | Yes |

All gated by `canFireEffect()` — over budget or in cooldown = silently skipped.

---

## §8 Transition Effects (Recovery + Audio)

### Recovery Catharsis (degraded/critical → operational)

1. Glitch burst (medium) on the recovering element
2. Persistent threat pulses auto-clear (tied to `healthMode`)
3. Layout-root mantra fades (escalation resets to 0)
4. `playSfx('complete')` if audio enabled

### Degradation Entry (operational → degraded/critical)

1. `playSfx('error')` if audio enabled
2. Escalation timer starts (§1 timeline)

### Audio Integration

- Toggle added next to CRT toggle in Settings page
- `ShAudio.enabled` stored in `localStorage` key `queue-audio`
- Default: off (opt-in per spec)
- SFX triggers: error/degraded transition, recovery, DLQ entry, job complete, eval complete

---

## File Impact Summary

### New Files (ollama-queue)

- `src/stores/atmosphere.js` — atmosphere store (§1)
- `src/hooks/useShatter.js` — tiered shatter hook (§2)

### Modified Files (ollama-queue, estimated)

- `src/app.jsx` — initAtmosphere/disposeAtmosphere, layout-root health attrs
- `src/stores/index.js` — re-export atmosphere signals
- `src/components/Sidebar.jsx` — escalation level indicator (§1)
- `src/components/CohesionHeader.jsx` — glitch on connection transitions (§7)
- `src/components/CurrentJob.jsx` — ShFrozen wrap, deepened effects (§6, §7)
- `src/components/ResourceGauges.jsx` — ThreatPulse on threshold breach (§7)
- `src/components/HeroCard.jsx` — ShFrozen wrap (§6)
- `src/components/SystemHealthChip.jsx` — ShFrozen wrap (§6)
- `src/components/QueueList.jsx` — empty state migration (§4)
- `src/components/EmptyState.jsx` — may be deleted (replaced by ShEmptyState)
- `src/components/ErrorState.jsx` — may be deleted (replaced by ShErrorState)
- `src/components/LoadingState.jsx` — may be deleted (replaced by ShSkeleton)
- `src/components/TopologyDiagram.jsx` — ThreatPulse on unreachable edges (§7)
- `src/components/GanttChart.jsx` — ShFrozen + ThreatPulse on disabled jobs (§6, §7)
- `src/components/ShToastContainer.jsx` — terminal voice toasts (§5)
- `src/components/ActiveEvalStrip.jsx` — ThreatPulse on stuck eval (§7)
- All 9 `src/pages/*.jsx` — stagger classes (§3), empty states (§4), voice (§5), ShFrozen (§6), effects (§7)
- `src/config/tabs.js` — subtitle voice audit (§5)
- All `addToast()` call sites — voice audit (§5)
- All `useActionFeedback` call sites — voice + shatter (§2, §5)

### superhot-ui Changes

None expected. All capabilities already exported. If any gap is discovered during implementation, the reusable piece goes into superhot-ui first.

---

## Dependencies

- superhot-ui `file:` dependency already in package.json
- No new npm packages
- No API changes
- No backend changes

## Risks

- **Effect pile-up during rapid state changes** — mitigated by canFireEffect() budget + cooldown
- **Terminal voice feels too aggressive** — mantras and system messages are uppercase; data values and status badges stay lowercase for balance
- **Performance on mobile** — superhot-ui already has responsive degradation (shatter → fade, mantra hidden, T1 off on phone)
- **~30 file modification scope** — plan should batch by section for incremental testing
