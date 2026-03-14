# Now Tab — InfrastructurePanel Design

**Date:** 2026-03-14
**Status:** Approved for implementation

## Problem

The Now tab right column has two disconnected panels:

- **ResourceGauges** (System Resources frame) — shows local machine RAM/VRAM/CPU/Swap. VRAM displayed here is the idle local GTX 1650 even when inference is running on the remote RTX 5080, making it actively misleading. Single-backend users get no per-GPU context.
- **BackendsPanel** — shows per-backend health + VRAM + loaded model, but knows nothing about host CPU/RAM/Swap that govern whether the daemon will admit the next job. Hidden entirely on single-backend setups.

Neither panel answers the actual question: *"Where is the work happening, and can the system sustain it?"*

## Solution

Replace both with a single `InfrastructurePanel` component.

## Layout

```
┌─ Infrastructure ─────────────────────────────────────────────────┐
│ host   RAM ■■□□□ 45%  CPU ■□□□□ 12%  Swap 0%                     │
│ ──────────────────────────────────────────────────────────────── │
│ ● GTX 1650   [VRAM ■■□□□] 21%   · nomic-embed-text               │
│ ◉ RTX 5080   [VRAM ■■□□□] 23%   · qwen3.5:7b    ▶ serving        │
└──────────────────────────────────────────────────────────────────┘
```

### Host row

Shows **RAM, CPU, Swap** — the three metrics the health monitor uses to gate job admission. Each bar uses the existing gradient + pause-threshold marker from ResourceGauges (behavior unchanged). VRAM is removed from this row — it is now per-backend.

### Backend rows

One row per configured backend:
- Health dot (green/red)
- GPU name (`backend.gpu_name` || hostname)
- VRAM bar (gradient: accent < 60%, warning 60–80%, error > 80%) + percentage
- Loaded model label (`· model-name` or `· model +N` for multiple)
- `▶ serving` badge + phosphor-green outline when this backend has the active job's model loaded

**Single-backend users** — always show one backend row. BackendsPanel previously hid the entire panel for single-backend; this design removes that gate, giving single-backend users their GPU row.

**All backends unreachable** — replace panel body with `All backends unreachable — routing unavailable` in threat color.

## Visual States

| Condition | Backend row appearance |
|-----------|----------------------|
| Healthy, idle | Normal — green dot, VRAM bar, loaded model if any |
| Healthy, serving active job | Phosphor outline (`1px solid var(--sh-phosphor)`), `▶ serving` label in phosphor color |
| Healthy, VRAM > 80% | VRAM bar fills with `--status-warning`, % label in warning color |
| Healthy, VRAM > 90% | VRAM bar fills with `--status-error`, % label in threat color |
| Unhealthy | Red dot, row dimmed (opacity 0.5), `unreachable` label |

Host row pause-threshold markers: retain existing dashed marker from ResourceGauges for RAM, CPU, Swap.

## Props

```jsx
InfrastructurePanel({
  latestHealth,   // most recent health_log row: { ram_pct, load_avg, swap_pct }
  settings,       // threshold settings: { ram_pause_pct, load_pause_multiplier, swap_pause_pct, ... }
  backends,       // backendsData.value: Array<{ url, healthy, gpu_name, vram_pct, loaded_models, model_count }>
  currentJob,     // currentJob signal value: { model, ... } | null
  cpuCount,       // cpu_count from health signal — for load_avg → % conversion
})
```

`backends` refresh: component self-manages a 15s `fetchBackends()` interval, same as BackendsPanel does today.

## What Does NOT Change

- `ResourceGauges` component — kept. Still used inside `CurrentJob` as a compact 4-metric snapshot (RAM, VRAM, CPU, Swap) while a job runs. VRAM there is local and still useful (shows local GPU pressure as a baseline).
- `BackendsPanel` component — kept on the Backends tab. No change to that tab.
- `QueueList`, `CurrentJob`, KPI cards, alert strip — untouched.

## Files Changed

| File | Change |
|------|--------|
| `src/components/InfrastructurePanel.jsx` | New component |
| `src/pages/Now.jsx` | Remove System Resources t-frame + BackendsPanel; add InfrastructurePanel |
