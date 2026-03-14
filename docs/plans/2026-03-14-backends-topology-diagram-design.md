# Backends Tab — Full System Topology Diagram

**Date:** 2026-03-14
**Status:** Approved for implementation

## Goal

Replace the static ASCII tree in section 6.4 of `BackendsTab.jsx` with a live SVG topology
diagram that shows the entire ollama-queue system as a directed graph. Active paths animate
based on real-time signal state — no new API calls required.

## What It Shows

A four-column left-to-right directed graph showing every layer of the system:

```
[Inputs] → [Queue/Scheduler] → [Daemon/DLQ/Sensing] → [Router/Backends]
```

Paths light up with animated marching-ant arrows when that route is actively in use.
Nodes reflect their live health state (healthy/paused/threat). Backward arcs show the
feedback loop (sensing → daemon pause) and the retry arc (DLQ → scheduler → queue).

## Layout

ViewBox: `0 0 860 480` — responsive via `width="100%"`, `overflow-x: auto` wrapper on mobile.

### Columns

| Column | x-offset | Nodes |
|--------|----------|-------|
| 1 — Inputs | 20 | Recurring Jobs, CLI/API Submit, Direct Proxy, Consumer Intercept, Eval Pipeline |
| 2 — Queue Layer | 215 | Scheduler, Queue |
| 3 — Engine | 410 | Daemon, Sensing, DLQ |
| 4 — Output | 605 | Backend Router, GTX 1650, RTX 5080 |

Node dimensions: `width=150 height=38 rx=4`

### Node Positions

| Node | cx | cy | Notes |
|------|----|----|-------|
| Recurring Jobs | 20 | 40 | |
| CLI / API Submit | 20 | 110 | |
| Direct Proxy | 20 | 180 | Bypasses queue → goes direct to Router |
| Consumer Intercept | 20 | 250 | iptables REDIRECT |
| Eval Pipeline | 20 | 320 | A/B eval sessions, judge runs |
| Scheduler | 215 | 40 | Recurring + DLQ + deferral |
| Queue | 215 | 150 | SQLite, priority-ordered |
| Daemon | 410 | 150 | Poller + executor — the heart |
| Sensing | 410 | 265 | HealthMonitor, StallDetector, BurstDetector |
| DLQ | 410 | 370 | Dead letter queue |
| Backend Router | 605 | 150 | 5-tier selection |
| GTX 1650 | 605 | 270 | Local GPU |
| RTX 5080 | 605 | 370 | Remote GPU |

## Edges

### Primary flow (solid arrows, left-to-right)

| ID | From | To | Label |
|----|------|----|-------|
| e1 | Recurring Jobs | Scheduler | promote |
| e2 | Scheduler | Queue | enqueue |
| e3 | CLI/API Submit | Queue | submit |
| e4 | Consumer Intercept | Queue | redirect |
| e5 | Eval Pipeline | Queue | eval jobs |
| e6 | Queue | Daemon | dequeue |
| e7 | Daemon | Backend Router | route |
| e8 | Direct Proxy | Backend Router | bypass |
| e9 | Backend Router | GTX 1650 | infer |
| e10 | Backend Router | RTX 5080 | infer |

### Feedback/retry arcs (dashed arrows)

| ID | From | To | Direction | Color |
|----|------|----|-----------|-------|
| e11 | Sensing | Daemon | right-to-left arc | red when throttling, dim otherwise |
| e12 | Daemon | DLQ | downward | amber when job fails |
| e13 | DLQ | Scheduler | left arc (right-to-left) | amber when dlqCount > 0 |

Edge routing: orthogonal paths (`M x1 y1 H midX V y2 H x2`). Arcs e11/e13 use a
bezier curve (`M ... C ...`) to visually distinguish feedback from forward flow.

## Live State Bindings

### Node states

| Signal | Node affected | Visual change |
|--------|--------------|---------------|
| `status.value?.daemon?.state === 'running'` | Daemon | phosphor stroke + glow |
| `status.value?.daemon?.state?.startsWith('paused')` | Daemon | dim (opacity 0.35), stroke `--text-tertiary` |
| `status.value?.daemon?.state === 'offline'` | Daemon | threat stroke + pulse |
| `status.value?.active_eval` | Eval Pipeline | phosphor stroke + glow |
| `dlqCount.value > 0` | DLQ | amber stroke + count badge |
| `backend.healthy === false` | GTX/RTX nodes | threat stroke + pulse |
| `backend.vram_pct > 90` | GTX/RTX nodes | VRAM label turns threat-red |
| `burst_regime === 'burst'` | Inputs group | ambient amber on input nodes |
| `burst_regime === 'storm'` | Inputs group | ambient threat on input nodes + e3/e4 arrows pulse red |

### Path animations (marching ants)

| Condition | Edges lit | Color |
|-----------|-----------|-------|
| `currentJob.value && daemon not proxy` | e6 → e7 → e9 or e10 (inferred from loaded_models) | phosphor green |
| `current_job_id === -1` (proxy in flight) | e8 → e9 or e10 | amber |
| Both simultaneously | both paths active | green + amber overlap |
| `Sensing` actively throttling (`evaluate().should_pause`) | e11 (Sensing→Daemon arc) | threat red |
| `dlqCount.value > 0` | e12, e13 | amber (slow pulse, not marching) |

**Backend serving inference:** cross-ref `currentJob.value?.model` against each
`backend.loaded_models`. If match found → light e9 or e10. If no match, light both
(model loading on whichever backend answers).

**SYSTEM PAUSED state:** When `isDaemonPaused`:
- Daemon node dims to opacity 0.35
- Edges e6 (Queue→Daemon), e7 (Daemon→Router) dim — no marching animation
- e11 (Sensing→Daemon) stays active — sensing still reports even while paused
- e13 (DLQ→Scheduler) stays active — retry scheduling continues while paused

## Visual Specification

### SVG filters (defined in `<defs>`)

```svg
<filter id="glow-phosphor" x="-30%" y="-30%" width="160%" height="160%">
  <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur" />
  <feComposite in="SourceGraphic" in2="blur" operator="over" />
</filter>

<filter id="glow-amber" x="-30%" y="-30%" width="160%" height="160%">
  <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur" />
  <feComposite in="SourceGraphic" in2="blur" operator="over" />
</filter>

<filter id="glow-threat" x="-40%" y="-40%" width="180%" height="180%">
  <feGaussianBlur in="SourceGraphic" stdDeviation="4" result="blur" />
  <feComposite in="SourceGraphic" in2="blur" operator="over" />
</filter>
```

### Arrowhead markers

Four markers: `arrow-dim`, `arrow-phosphor`, `arrow-amber`, `arrow-threat`.
Each is a `<marker>` with a `<path d="M0,0 L6,3 L0,6 Z">` in the appropriate color.
`markerWidth=6 markerHeight=6 refX=5 refY=3 orient="auto"`.

### Node rendering

Each node:
```
<rect x cy w=150 h=38 rx=4 fill="var(--bg-elevated)" stroke=[state-color] strokeWidth=[1|2] filter=[glow|none] />
<text x+75 cy+14 textAnchor="middle" fontFamily="var(--font-mono)" fontSize=11 fill=[label-color]>[Name]</text>
<text x+75 cy+26 textAnchor="middle" fontFamily="var(--font-mono)" fontSize=9 fill="var(--text-tertiary)">[sublabel]</text>
```

Active node: `strokeWidth=2`, `filter=url(#glow-phosphor)`, `stroke=var(--sh-phosphor)`.
Idle node: `strokeWidth=1`, no filter, `stroke=var(--border)`.
Threat node: `strokeWidth=2`, `filter=url(#glow-threat)`, `stroke=var(--sh-threat)`, CSS class `threat-pulse`.

Node sublabels (shown at 9px in `--text-tertiary`):
- Daemon: `"poller · executor"` | paused → `"PAUSED"` in threat color
- Queue: `"priority · sqlite"` + live depth count when > 0 (e.g., `"3 pending"`)
- DLQ: `"dead letter"` + count badge if dlqCount > 0
- Sensing: `"health · stall · burst"`
- Scheduler: `"recurring · dlq · defer"`
- Backend Router: `"5-tier selection"`
- GTX 1650: VRAM % live (e.g., `"42% VRAM"`)
- RTX 5080: VRAM % live (e.g., `"67% VRAM"`)

### Edge rendering

Active (marching ants):
```css
@keyframes march-phosphor { to { stroke-dashoffset: -18; } }
@keyframes march-amber    { to { stroke-dashoffset: -18; } }
@keyframes march-threat   { to { stroke-dashoffset: -9;  } }
```
`strokeDasharray="6 3"`, `animation: march-phosphor 0.35s linear infinite`.

Inactive: `stroke="var(--text-tertiary)"`, `opacity=0.3`, `strokeWidth=1`, no animation.

Threat pulse (node CSS class):
```css
@keyframes threat-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.35; }
}
.threat-pulse { animation: threat-pulse 1.2s ease-in-out infinite; }
```

### Section header

Above the SVG, inside the `t-frame`:
```
● SYSTEM TOPOLOGY    [live indicator dot — pulses green when daemon running]
```
Uses existing `LiveIndicator` component + `data-mono` styling.

### VRAM bars on backend nodes

Inside each backend node rect, a thin bar (h=3) at the bottom:
- Full width = node width (150px in SVG units)
- Fill width = `vram_pct / 100 * 150`
- Fill color: `--sh-phosphor` when < 80%, `--status-warning` 80-90%, `--sh-threat` > 90%

## Component Structure

**New file:** `src/components/TopologyDiagram.jsx`

```
TopologyDiagram({ daemonStatus, currentJob, backends, dlqCount, activeEval })
  ├── <defs> — filters + arrowhead markers
  ├── nodeState(name) → { stroke, filter, labelColor, sublabel }
  ├── edgeState(id) → { stroke, strokeWidth, animation, marker }
  ├── renderNode(name, x, y) → <g> with rect + text
  └── renderEdge(id, pathD) → <path> with computed edge state
```

Props come directly from existing signals — no new store state needed.

**Integration in BackendsTab.jsx:**

Replace section 6.4 (the `<div class="t-frame" data-label="Topology">` ASCII block)
with:

```jsx
<div class="t-frame" data-label="System Topology">
  <TopologyDiagram
    daemonStatus={status.value?.daemon ?? null}
    currentJob={currentJob.value}
    backends={backendsData.value || []}
    dlqCount={dlqCount.value ?? 0}
    activeEval={status.value?.active_eval ?? null}
  />
</div>
```

Import `status` from `../stores` (already available in BackendsTab scope).

## Mobile Behavior

- SVG `width="100%"` with fixed `viewBox="0 0 860 480"` — scales proportionally
- Wrapper: `overflow-x: auto; -webkit-overflow-scrolling: touch`
- Minimum rendered width ~320px before horizontal scroll kicks in
- No reflow needed — viewBox scaling preserves all proportions and text legibility

## What Is NOT in Scope

- Clickable nodes navigating to other tabs (future enhancement)
- Animated job "particles" moving along edges (would require requestAnimationFrame loop)
- Dynamic layout engine (positions are hardcoded — intentional)
- Per-backend throughput sparklines (those live in the Performance tab)

## Files Changed

| File | Change |
|------|--------|
| `src/components/TopologyDiagram.jsx` | New — full SVG topology component |
| `src/pages/BackendsTab.jsx` | Replace section 6.4 ASCII block with `<TopologyDiagram>` |
