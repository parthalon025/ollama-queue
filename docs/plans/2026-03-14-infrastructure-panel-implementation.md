# InfrastructurePanel Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the separate System Resources frame + BackendsPanel on the Now tab with a single `InfrastructurePanel` that shows host scheduler metrics (RAM/CPU/Swap) and per-backend GPU rows (VRAM, loaded model, serving status) in one cohesive view.

**Architecture:** New `InfrastructurePanel.jsx` with two exported pure helpers (`hostGauges`, `backendRowState`) for unit testing, plus the component itself which reads `backendsData` and `currentJob` from stores internally (same pattern as BackendsPanel). Now.jsx drops the System Resources t-frame and `<BackendsPanel />` and replaces them with `<InfrastructurePanel>`.

**Tech Stack:** Preact 10, @preact/signals, Jest (SPA test runner), esbuild.

---

### Task 1: Scaffold InfrastructurePanel with pure helper tests (TDD)

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/InfrastructurePanel.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/InfrastructurePanel.test.js`

---

**Step 1: Write the failing tests**

Create `ollama_queue/dashboard/spa/src/components/InfrastructurePanel.test.js`:

```js
import { hostGauges, backendRowState } from './InfrastructurePanel.jsx';

// ── hostGauges ────────────────────────────────────────────────────────────────

test('hostGauges: null health → empty array', () => {
    expect(hostGauges(null, {}, 1)).toEqual([]);
});

test('hostGauges: returns exactly 3 gauges (RAM, CPU, Swap)', () => {
    const health = { ram_pct: 45, load_avg: 0.5, swap_pct: 5 };
    const gauges = hostGauges(health, {}, 1);
    expect(gauges).toHaveLength(3);
    expect(gauges.map(g => g.label)).toEqual(['RAM', 'CPU', 'Swap']);
});

test('hostGauges: no VRAM gauge', () => {
    const health = { ram_pct: 0, load_avg: 0, swap_pct: 0, vram_pct: 90 };
    const gauges = hostGauges(health, {}, 1);
    expect(gauges.every(g => g.label !== 'GPU' && g.label !== 'VRAM')).toBe(true);
});

test('hostGauges: converts load_avg to % using cpuCount', () => {
    const health = { ram_pct: 0, load_avg: 1.0, swap_pct: 0 };
    const gauges = hostGauges(health, {}, 4);
    expect(gauges[1].value).toBeCloseTo(25); // 1.0/4 * 100
});

test('hostGauges: uses settings thresholds when provided', () => {
    const health = { ram_pct: 0, load_avg: 0, swap_pct: 0 };
    const gauges = hostGauges(health, { ram_pause_pct: 70, ram_resume_pct: 60 }, 1);
    expect(gauges[0].pause).toBe(70);
    expect(gauges[0].resume).toBe(60);
});

test('hostGauges: defaults to sensible thresholds when settings empty', () => {
    const health = { ram_pct: 0, load_avg: 0, swap_pct: 0 };
    const gauges = hostGauges(health, {}, 1);
    expect(gauges[0].pause).toBe(85);  // ram default
    expect(gauges[2].pause).toBe(50);  // swap default
});

// ── backendRowState ────────────────────────────────────────────────────────────

const BASE = {
    url: 'http://127.0.0.1:11434',
    healthy: true,
    vram_pct: 21,
    loaded_models: ['nomic-embed-text:latest'],
    model_count: 12,
    gpu_name: null,
};

test('backendRowState: abbreviates NVIDIA GeForce prefix', () => {
    const b = { ...BASE, gpu_name: 'NVIDIA GeForce GTX 1650' };
    expect(backendRowState(b, null).label).toBe('GTX 1650');
});

test('backendRowState: abbreviates NVIDIA prefix without GeForce', () => {
    const b = { ...BASE, gpu_name: 'NVIDIA RTX A6000' };
    expect(backendRowState(b, null).label).toBe('RTX A6000');
});

test('backendRowState: falls back to hostname when gpu_name is null', () => {
    expect(backendRowState(BASE, null).label).toBe('127.0.0.1');
});

test('backendRowState: isServing true on exact model match', () => {
    const b = { ...BASE, loaded_models: ['qwen3.5:9b'] };
    expect(backendRowState(b, 'qwen3.5:9b').isServing).toBe(true);
});

test('backendRowState: isServing true on same family cross-tag match', () => {
    const b = { ...BASE, loaded_models: ['qwen3.5:latest'] };
    expect(backendRowState(b, 'qwen3.5:9b').isServing).toBe(true);
});

test('backendRowState: isServing false when different model family', () => {
    const b = { ...BASE, loaded_models: ['llama3:latest'] };
    expect(backendRowState(b, 'qwen3.5:9b').isServing).toBe(false);
});

test('backendRowState: isServing false when activeModel is null', () => {
    expect(backendRowState(BASE, null).isServing).toBe(false);
});

test('backendRowState: isServing false when backend is unhealthy', () => {
    const b = { ...BASE, healthy: false, loaded_models: ['nomic-embed-text:latest'] };
    expect(backendRowState(b, 'nomic-embed-text:latest').isServing).toBe(false);
});

test('backendRowState: loadedLabel strips tag from single model', () => {
    expect(backendRowState(BASE, null).loadedLabel).toBe('nomic-embed-text');
});

test('backendRowState: loadedLabel shows overflow count for multiple models', () => {
    const b = { ...BASE, loaded_models: ['model-a:latest', 'model-b:latest', 'model-c:latest'] };
    expect(backendRowState(b, null).loadedLabel).toBe('model-a +2');
});

test('backendRowState: loadedLabel is null when no models loaded', () => {
    const b = { ...BASE, loaded_models: [] };
    expect(backendRowState(b, null).loadedLabel).toBeNull();
});

test('backendRowState: vramColor error when vram_pct > 90', () => {
    const b = { ...BASE, vram_pct: 95 };
    expect(backendRowState(b, null).vramColor).toBe('var(--status-error)');
});

test('backendRowState: vramColor warning when vram_pct 81-90', () => {
    const b = { ...BASE, vram_pct: 85 };
    expect(backendRowState(b, null).vramColor).toBe('var(--status-warning)');
});

test('backendRowState: vramColor phosphor when vram_pct <= 80', () => {
    const b = { ...BASE, vram_pct: 21 };
    expect(backendRowState(b, null).vramColor).toBe('var(--sh-phosphor)');
});

test('backendRowState: isHealthy reflects backend.healthy', () => {
    expect(backendRowState(BASE, null).isHealthy).toBe(true);
    expect(backendRowState({ ...BASE, healthy: false }, null).isHealthy).toBe(false);
});
```

**Step 2: Run tests to verify they fail**

```bash
cd ollama_queue/dashboard/spa
node --experimental-vm-modules node_modules/.bin/jest InfrastructurePanel.test.js --no-coverage 2>&1 | tail -15
```

Expected: FAIL — `Cannot find module './InfrastructurePanel.jsx'`

---

**Step 3: Write the InfrastructurePanel component**

Create `ollama_queue/dashboard/spa/src/components/InfrastructurePanel.jsx`:

```jsx
// What it shows: Unified host + GPU infrastructure view — the three scheduler-gate
//   metrics (RAM/CPU/Swap) that determine if a new job can start, plus one row per
//   configured backend showing VRAM pressure, the loaded model, and whether that
//   backend is currently serving the active job.
// Decision it drives: "Where is the work happening and can the system sustain it?"
//   Replaces the separate System Resources frame and BackendsPanel on the Now tab.

import { useEffect } from 'preact/hooks';
import { backendsData, fetchBackends, currentJob } from '../stores';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

// Returns the three host gauge descriptors the daemon uses to gate job admission.
// VRAM is intentionally excluded — it belongs to the backend rows, not the host row.
// Pure — no signals, fully testable.
export function hostGauges(latestHealth, settings, cpuCount) {
    if (!latestHealth) return [];
    const s = settings || {};
    const cpu = (latestHealth.load_avg / (cpuCount || 1)) * 100;
    return [
        { label: 'RAM',  value: latestHealth.ram_pct  ?? 0, pause: s.ram_pause_pct  || 85, resume: s.ram_resume_pct  || 75 },
        { label: 'CPU',  value: cpu,                         pause: (s.load_pause_multiplier || 2) * 100, resume: (s.load_resume_multiplier || 1.5) * 100 },
        { label: 'Swap', value: latestHealth.swap_pct ?? 0,  pause: s.swap_pause_pct || 50, resume: s.swap_resume_pct || 40 },
    ];
}

// Returns display state for one backend row.
// Pure — no signals, no DOM, fully testable.
export function backendRowState(backend, activeModel) {
    let host = backend.url;
    try { host = new URL(backend.url).hostname; } catch (_) { /* keep full url */ }
    const label = (backend.gpu_name || host)
        .replace(/^nvidia\s+geforce\s+/i, '')
        .replace(/^nvidia\s+/i, '');

    const vramPct = backend.vram_pct ?? 0;
    const vramColor = vramPct > 90
        ? 'var(--status-error)'
        : vramPct > 80
            ? 'var(--status-warning)'
            : 'var(--sh-phosphor)';

    const loaded = backend.loaded_models || [];
    const loadedLabel = loaded.length > 0
        ? `${loaded[0].split(':')[0]}${loaded.length > 1 ? ` +${loaded.length - 1}` : ''}`
        : null;

    const isServing = !!(activeModel && backend.healthy &&
        loaded.some(m => m === activeModel || m.startsWith(activeModel.split(':')[0] + ':')));

    return { label, vramPct, vramColor, loadedLabel, isServing, isHealthy: !!backend.healthy };
}

export default function InfrastructurePanel({ latestHealth, settings, cpuCount }) {
    const backends = backendsData.value || [];
    const activeModel = currentJob.value?.model ?? null;

    // Self-managed 15s refresh — independent of the main 5s status poll so
    // remote backend latency doesn't slow the hot path.
    useEffect(() => {
        fetchBackends();
        const id = setInterval(fetchBackends, 15000);
        return () => clearInterval(id);
    }, []);

    const gauges = hostGauges(latestHealth, settings, cpuCount);
    const allUnhealthy = backends.length > 0 && backends.every(b => !b.healthy);

    return (
        <div class="t-frame" data-label="Infrastructure" data-chroma="lune">
            {/* Host row — RAM, CPU, Swap: the three metrics that gate job admission */}
            {gauges.length > 0 && (
                <div style={{ marginBottom: backends.length > 0 ? '0.5rem' : 0 }}>
                    <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); display: block; margin-bottom: 0.25rem;">
                        host
                    </span>
                    <div class="flex gap-3 flex-wrap">
                        {gauges.map(gauge => <HostGaugeBar key={gauge.label} gauge={gauge} />)}
                    </div>
                </div>
            )}

            {/* Divider between host metrics and backend rows */}
            {gauges.length > 0 && backends.length > 0 && (
                <div style={{ height: 1, background: 'var(--border-subtle)', margin: '0.5rem 0' }} />
            )}

            {/* Backend rows — one per configured Ollama backend */}
            {allUnhealthy ? (
                <span style={{ color: 'var(--status-error)', fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)' }}>
                    All backends unreachable — routing unavailable
                </span>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.375rem' }}>
                    {backends.map(backend => (
                        <BackendRow
                            key={backend.url}
                            row={backendRowState(backend, activeModel)}
                            url={backend.url}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

// Renders a single host metric bar with gradient fill + pause threshold marker.
// Replicates the gauge bar pattern from ResourceGauges (same gradient + mask technique).
function HostGaugeBar({ gauge }) {
    const { label, value, pause, resume } = gauge;
    const pct = Math.min(100, Math.max(0, value));
    const pauseNorm = Math.min(pause, 100);
    const resumeNorm = pause > 100 ? (resume / pause) * 100 : Math.min(resume, 100);
    const gradientBg = `linear-gradient(to right, var(--accent) 0%, var(--status-warning) ${resumeNorm.toFixed(1)}%, var(--status-error) ${pauseNorm.toFixed(1)}%)`;

    return (
        <div class="flex items-center gap-1" style="min-width: 80px; flex: 1;">
            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); width: 32px; text-align: right;">
                {label}
            </span>
            <div style="flex: 1; height: 6px; background: var(--bg-inset); border-radius: 3px; position: relative; overflow: hidden;">
                <div style={{ position: 'absolute', inset: '0', background: gradientBg }} />
                <div style={{ position: 'absolute', left: `${pct}%`, top: 0, bottom: 0, right: 0, background: 'var(--bg-inset)', transition: 'left 0.3s ease' }} />
                <div style={{ position: 'absolute', left: `${pauseNorm}%`, top: 0, bottom: 0, width: '1px', borderLeft: '1px dashed var(--text-tertiary)', opacity: 0.5, zIndex: 1 }} />
            </div>
            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-secondary); width: 28px;">
                {Math.round(pct)}%
            </span>
        </div>
    );
}

// Renders one backend row: health dot + GPU name + VRAM bar + loaded model + serving badge.
function BackendRow({ row, url }) {
    const { label, vramPct, vramColor, loadedLabel, isServing, isHealthy } = row;

    return (
        <div
            title={url}
            style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                padding: '0.375rem 0.5rem',
                background: 'var(--bg-elevated)',
                borderRadius: 'var(--radius-sm)',
                fontSize: 'var(--type-label)',
                fontFamily: 'var(--font-mono)',
                outline: isServing ? '1px solid var(--sh-phosphor)' : 'none',
                opacity: isHealthy ? 1 : 0.5,
            }}
        >
            {/* Health indicator dot */}
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: isHealthy ? 'var(--status-ok)' : 'var(--status-error)', flexShrink: 0 }} />

            {/* GPU label */}
            <span style={{ color: 'var(--text-primary)', flex: '0 0 auto', minWidth: '6rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {label}
            </span>

            {isHealthy ? (
                <>
                    {/* VRAM bar + percentage */}
                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                        <div style={{ flex: 1, height: 4, background: 'var(--bg-surface)', borderRadius: 2, overflow: 'hidden' }}>
                            <div style={{ width: `${Math.min(vramPct, 100)}%`, height: '100%', background: vramColor, transition: 'width 0.4s' }} />
                        </div>
                        <span style={{ color: vramColor, fontSize: 'var(--type-micro)', flexShrink: 0, minWidth: '3rem', textAlign: 'right' }}>
                            {vramPct}%
                        </span>
                    </div>

                    {/* Currently loaded model */}
                    {loadedLabel && (
                        <span style={{ color: isServing ? 'var(--sh-phosphor)' : 'var(--status-ok)', fontSize: 'var(--type-micro)', flexShrink: 0 }}>
                            · {loadedLabel}
                        </span>
                    )}

                    {/* Serving indicator — only when this backend has the active job's model */}
                    {isServing && (
                        <span style={{ color: 'var(--sh-phosphor)', fontSize: 'var(--type-micro)', flexShrink: 0, letterSpacing: '0.04em' }}>
                            ▶
                        </span>
                    )}
                </>
            ) : (
                <span style={{ color: 'var(--status-error)', flex: 1 }}>unreachable</span>
            )}
        </div>
    );
}
```

**Step 4: Run tests to verify they pass**

```bash
cd ollama_queue/dashboard/spa
node --experimental-vm-modules node_modules/.bin/jest InfrastructurePanel.test.js --no-coverage 2>&1 | tail -10
```

Expected: `20 passed` (all tests green)

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/InfrastructurePanel.jsx
git add ollama_queue/dashboard/spa/src/components/InfrastructurePanel.test.js
git commit -m "feat: add InfrastructurePanel component + tests (host gauges + per-backend GPU rows)"
```

---

### Task 2: Wire InfrastructurePanel into Now.jsx

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Now.jsx`

**Context:** Now.jsx currently renders two independent panels in the right column:
1. A `<div class="t-frame" data-label="System Resources" data-chroma="lune">` wrapping `<ResourceGauges>` at lines ~207–217
2. `<BackendsPanel />` at line ~220

Both get removed. `InfrastructurePanel` takes their place. `ResourceGauges` stays inside `CurrentJob` — no change there.

---

**Step 1: Add the import**

In `Now.jsx`, the existing imports look like:
```jsx
import BackendsPanel from '../components/BackendsPanel.jsx';
```

Replace that line with:
```jsx
import InfrastructurePanel from '../components/InfrastructurePanel.jsx';
```

**Step 2: Replace the two panels with InfrastructurePanel**

Find and replace the System Resources t-frame block (the `{latestHealth && (` guard + t-frame wrapping ResourceGauges) and the `<BackendsPanel />` line:

**Remove** this block (approximately lines 206–221):
```jsx
                    {/* Resource gauges */}
                    {latestHealth && (
                        <div class="t-frame" data-label="System Resources" data-chroma="lune">
                            <ResourceGauges
                                ram={latestHealth.ram_pct}
                                vram={latestHealth.vram_pct}
                                load={(latestHealth.load_avg / (cpuCount.value || 1)) * 100}
                                swap={latestHealth.swap_pct}
                                settings={sett}
                            />
                        </div>
                    )}

                    {/* Backend health panel — only visible when multi-backend is configured */}
                    <BackendsPanel />
```

**Insert** in its place:
```jsx
                    {/* Infrastructure panel — host scheduler metrics + per-backend GPU rows */}
                    <InfrastructurePanel
                        latestHealth={latestHealth}
                        settings={sett}
                        cpuCount={cpuCount.value}
                    />
```

**Step 3: Build and verify no errors**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -10
```

Expected: `Done in Xs` with no errors. The bundle size will decrease slightly (BackendsPanel removed from Now.jsx's dependency path, InfrastructurePanel added — net roughly neutral).

**Step 4: Run full JS test suite**

```bash
cd ollama_queue/dashboard/spa
node --experimental-vm-modules node_modules/.bin/jest --no-coverage 2>&1 | tail -10
```

Expected: All existing tests + 20 new InfrastructurePanel tests pass. No regressions.

**Step 5: Restart service and verify in browser**

```bash
systemctl --user restart ollama-queue.service
```

Open `http://127.0.0.1:7683/queue/ui/`. On the Now tab, verify:
- The right column shows an "Infrastructure" t-frame (not separate "System Resources" + "Backends")
- Host row shows `host  RAM ■■□ 45%  CPU ■□□ 12%  Swap 0%`
- Each configured backend has its own row with health dot, GPU name, VRAM bar, loaded model
- If a job is running, the serving backend row has a phosphor outline and `▶` badge

**Step 6: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat: replace System Resources + BackendsPanel with InfrastructurePanel on Now tab"
```
