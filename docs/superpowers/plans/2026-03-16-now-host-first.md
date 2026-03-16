# Now Page — Host-First Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `CurrentJob.jsx` and `InfrastructurePanel.jsx` with a single `HostCard.jsx` that promotes GPU backend to the top-level organizing unit on the Now page.

**Architecture:** Pure helper functions extracted as named exports from `HostCard.jsx`, tested via TDD before any JSX. Render tests use the existing POJO vnode pattern (`h()` mock → traversable tree). `Now.jsx` takes over `fetchBackends` interval ownership and renders a HostCard list above the queue. Old components deleted after tests green.

**Tech Stack:** Preact 10, @preact/signals, superhot-ui (Sh* components + effects), Tailwind v4, Jest (node environment, POJO vnodes)

**Spec:** `docs/superpowers/specs/2026-03-16-now-page-host-first-design.md`

---

## File Map

| Action | Path |
|---|---|
| Create | `src/components/HostCard.jsx` |
| Create | `src/components/HostCard.test.js` |
| Create | `src/__mocks__/superhot-ui-preact.cjs` |
| Modify | `jest.config.cjs` |
| Modify | `src/pages/Now.jsx` |
| Delete | `src/components/CurrentJob.jsx` |
| Delete | `src/components/InfrastructurePanel.jsx` |

---

## Chunk 1: Test Infrastructure + Pure Helper Functions

### Task 1: Add superhot-ui/preact mock entry to jest.config.cjs

**Files:**
- Modify: `ollama_queue/dashboard/spa/jest.config.cjs`

- [ ] **Step 1: Read jest.config.cjs to confirm current state**

Run: `cat ollama_queue/dashboard/spa/jest.config.cjs`
Expected: 14 entry in moduleNameMapper for `'^superhot-ui$'` — no `superhot-ui/preact` entry.

- [ ] **Step 2: Add the new moduleNameMapper entry**

In `jest.config.cjs`, after the `'^superhot-ui$'` line, add:
```js
'^superhot-ui/preact$': '<rootDir>/src/__mocks__/superhot-ui-preact.cjs',
```

The `moduleNameMapper` block must now start:
```js
moduleNameMapper: {
    '^superhot-ui$': '<rootDir>/src/__mocks__/superhot-ui.cjs',
    '^superhot-ui/preact$': '<rootDir>/src/__mocks__/superhot-ui-preact.cjs',
    '^preact$': ...
```

- [ ] **Step 3: Verify no syntax errors**

Run: `node -e "require('./jest.config.cjs')"` in `ollama_queue/dashboard/spa/`
Expected: no output (clean parse).

---

### Task 2: Create superhot-ui-preact.cjs mock

**Files:**
- Create: `ollama_queue/dashboard/spa/src/__mocks__/superhot-ui-preact.cjs`

- [ ] **Step 1: Write the mock file**

```js
// Stubs for superhot-ui Preact components — each renders its children with
// a data-sh-effect attribute that tests can assert on via tree traversal.
// Uses the same h() from preact.cjs so returned values are traversable POJOs.
const { h } = require('./preact.cjs');

module.exports = {
    // Renders children with data-sh-status so tests can assert badge variant
    ShStatusBadge: ({ status, children }) =>
        h('span', { 'data-sh-effect': 'status-badge', 'data-sh-status': status }, children),

    // data-sh-active lets tests assert whether the pulse is triggered
    ShThreatPulse: ({ active, persistent, children }) =>
        h('div', {
            'data-sh-effect': 'threat-pulse',
            'data-sh-active': String(active),
            'data-sh-persistent': String(!!persistent),
        }, children),

    // data-sh-ts lets tests verify the timestamp multiplier (seconds → ms)
    ShFrozen: ({ timestamp, children }) =>
        h('span', { 'data-sh-effect': 'frozen', 'data-sh-ts': timestamp }, children),

    // data-sh-active lets tests verify edge-trigger logic is wired in
    ShGlitch: ({ active, intensity, children }) =>
        h('span', {
            'data-sh-effect': 'glitch',
            'data-sh-active': String(active),
            'data-sh-intensity': intensity || '',
        }, children),

    // onClick is forwarded from onDismiss so shatter cancel tests can call it
    ShShatter: ({ onDismiss, children }) =>
        h('div', { 'data-sh-effect': 'shatter', onClick: onDismiss }, children),
};
```

- [ ] **Step 2: Verify the mock parses**

Run: `node -e "require('./src/__mocks__/superhot-ui-preact.cjs')"` in `ollama_queue/dashboard/spa/`
Expected: no output.

---

### Task 3: Write failing pure function tests

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/HostCard.test.js`

- [ ] **Step 1: Write the test file (pure functions only — no render tests yet)**

All imports must appear at the top of the file. `import _HostCard` is included now even though the render tests are written in Chunk 2 — this avoids mid-file `import` errors when the test file is extended.

```js
// HostCard pure helper function tests.
// Render tests are added in Chunk 2 — they use the default export imported here.
// ALL imports are at the top (ES module rule; babel-jest enforces this).

import {
    deriveHostState,
    isLocalBackend,
    hostGauges,
    matchesBackend,
    computeAllUnhealthy,
} from './HostCard.jsx';

// Default export for render tests (Chunk 2). Declared here at the top
// so Chunk 2 can append tests without adding a mid-file import.
import _HostCard from './HostCard.jsx';
const HostCard = _HostCard.default || _HostCard;

// ── Shared fixtures ──────────────────────────────────────────────────────────

const healthyBackend = (overrides = {}) => ({
    url: 'http://127.0.0.1:11434',
    healthy: true,
    gpu_name: 'RTX 5080',
    vram_pct: 50,
    loaded_models: [],
    inference_mode: 'local',
    ...overrides,
});

// ── deriveHostState ──────────────────────────────────────────────────────────

describe('deriveHostState', () => {
    test('returns running when backend healthy and currentJob model matches loaded_models', () => {
        const backend = healthyBackend({ loaded_models: ['qwen2.5:7b'] });
        const job = { model: 'qwen2.5:7b', source: 'aria', started_at: 1700000000 };
        const result = deriveHostState(backend, job, null);
        expect(result.state).toBe('running');
        expect(result.mood).toBe('dawn');
        expect(result.statusBadgeStatus).toBe('active');
        expect(result.isServing).toBe(true);
    });

    test('returns eval when activeEval.gen_backend_url matches backend.url', () => {
        const backend = healthyBackend({ url: 'http://100.1.2.3:11434', loaded_models: [] });
        const activeEval = { gen_backend_url: 'http://100.1.2.3:11434', judge_backend_url: null };
        const result = deriveHostState(backend, null, activeEval);
        expect(result.state).toBe('eval');
        expect(result.statusBadgeStatus).toBe('waiting');
    });

    test('returns eval when activeEval.judge_backend_url matches backend.url', () => {
        const backend = healthyBackend({ url: 'http://100.1.2.3:11434', loaded_models: [] });
        const activeEval = { gen_backend_url: null, judge_backend_url: 'http://100.1.2.3:11434' };
        const result = deriveHostState(backend, null, activeEval);
        expect(result.state).toBe('eval');
    });

    test('returns warm when backend healthy, models loaded, no job/eval', () => {
        const backend = healthyBackend({ loaded_models: ['qwen2.5:7b'] });
        const result = deriveHostState(backend, null, null);
        expect(result.state).toBe('warm');
        expect(result.statusBadgeStatus).toBe('ok');
    });

    test('returns idle when backend healthy, no models loaded', () => {
        const backend = healthyBackend({ loaded_models: [] });
        const result = deriveHostState(backend, null, null);
        expect(result.state).toBe('idle');
        expect(result.statusBadgeStatus).toBe('ok');
    });

    test('returns offline when backend.healthy is false', () => {
        const backend = healthyBackend({ healthy: false });
        const result = deriveHostState(backend, null, null);
        expect(result.state).toBe('offline');
        expect(result.mood).toBe('dread');
        expect(result.statusBadgeStatus).toBe('error');
    });

    test('prefers running over eval when both conditions are true', () => {
        const backend = healthyBackend({
            url: 'http://100.1.2.3:11434',
            loaded_models: ['qwen2.5:7b'],
        });
        const job = { model: 'qwen2.5:7b', source: 'aria', started_at: 1700000000 };
        const activeEval = { gen_backend_url: 'http://100.1.2.3:11434', judge_backend_url: null };
        const result = deriveHostState(backend, job, activeEval);
        expect(result.state).toBe('running');
    });

    test('gpuLabel strips NVIDIA GeForce prefix', () => {
        const backend = healthyBackend({ gpu_name: 'NVIDIA GeForce RTX 5080' });
        const result = deriveHostState(backend, null, null);
        expect(result.gpuLabel).toBe('RTX 5080');
    });

    test('gpuLabel strips NVIDIA prefix', () => {
        const backend = healthyBackend({ gpu_name: 'NVIDIA A100' });
        const result = deriveHostState(backend, null, null);
        expect(result.gpuLabel).toBe('A100');
    });

    test('gpuLabel falls back to URL hostname when gpu_name is null', () => {
        const backend = healthyBackend({ url: 'http://desktop-fbl9e0c.tail828051.ts.net:11434', gpu_name: null });
        const result = deriveHostState(backend, null, null);
        expect(result.gpuLabel).toBe('desktop-fbl9e0c.tail828051.ts.net');
    });

    test('vramColor is status-error when vram_pct > 90', () => {
        const backend = healthyBackend({ vram_pct: 95 });
        const result = deriveHostState(backend, null, null);
        expect(result.vramColor).toBe('var(--status-error)');
    });

    test('vramColor is status-warning when vram_pct > 80 and <= 90', () => {
        const backend = healthyBackend({ vram_pct: 85 });
        const result = deriveHostState(backend, null, null);
        expect(result.vramColor).toBe('var(--status-warning)');
    });

    test('vramColor is sh-phosphor when vram_pct <= 80', () => {
        const backend = healthyBackend({ vram_pct: 50 });
        const result = deriveHostState(backend, null, null);
        expect(result.vramColor).toBe('var(--sh-phosphor)');
    });

    test('eval does not match when gen_backend_url is "auto"', () => {
        const backend = healthyBackend({ url: 'http://127.0.0.1:11434', loaded_models: [] });
        const activeEval = { gen_backend_url: 'auto', judge_backend_url: 'auto' };
        const result = deriveHostState(backend, null, activeEval);
        // 'auto' never equals a real URL — should fall through to idle/warm
        expect(result.state).not.toBe('eval');
    });
});

// ── isLocalBackend ────────────────────────────────────────────────────────────

describe('isLocalBackend', () => {
    test('returns true for 127.0.0.1', () => {
        expect(isLocalBackend('127.0.0.1')).toBe(true);
    });

    test('returns true for localhost', () => {
        expect(isLocalBackend('localhost')).toBe(true);
    });

    test('returns true for http://127.0.0.1:11434 with port', () => {
        expect(isLocalBackend('http://127.0.0.1:11434')).toBe(true);
    });

    test('returns true for http://localhost:11434 with port', () => {
        expect(isLocalBackend('http://localhost:11434')).toBe(true);
    });

    test('returns false for remote IP', () => {
        expect(isLocalBackend('http://100.114.197.57:11434')).toBe(false);
    });

    test('returns false for Tailscale hostname', () => {
        expect(isLocalBackend('http://desktop-fbl9e0c.tail828051.ts.net:11434')).toBe(false);
    });
});

// ── hostGauges ────────────────────────────────────────────────────────────────

describe('hostGauges', () => {
    test('returns RAM, CPU, Swap gauges with correct pause/resume thresholds', () => {
        const health = { ram_pct: 60, load_avg: 3, swap_pct: 10 };
        const settings = {
            ram_pause_pct: 85, ram_resume_pct: 75,
            swap_pause_pct: 50, swap_resume_pct: 40,
            load_pause_multiplier: 2, load_resume_multiplier: 1.5,
        };
        const gauges = hostGauges(health, settings, 4);
        expect(gauges).toHaveLength(3);
        expect(gauges[0].label).toBe('RAM');
        expect(gauges[0].value).toBe(60);
        expect(gauges[0].pause).toBe(85);
        expect(gauges[0].resume).toBe(75);
        expect(gauges[1].label).toBe('CPU');
        expect(gauges[2].label).toBe('Swap');
    });

    test('normalises CPU from load_avg using cpuCount', () => {
        const health = { ram_pct: 0, load_avg: 2, swap_pct: 0 };
        const gauges = hostGauges(health, {}, 4);
        // 2 / 4 * 100 = 50
        expect(gauges[1].value).toBe(50);
    });

    test('returns [] when latestHealth is null', () => {
        expect(hostGauges(null, {}, 4)).toEqual([]);
    });

    test('uses default pause/resume when settings is null', () => {
        const health = { ram_pct: 50, load_avg: 1, swap_pct: 5 };
        const gauges = hostGauges(health, null, 4);
        expect(gauges[0].pause).toBe(85);   // default ram_pause_pct
        expect(gauges[2].pause).toBe(50);   // default swap_pause_pct
    });
});

// ── matchesBackend ────────────────────────────────────────────────────────────

describe('matchesBackend', () => {
    test('exact model name match', () => {
        const backend = healthyBackend({ loaded_models: ['qwen2.5:7b'] });
        expect(matchesBackend(backend, 'qwen2.5:7b')).toBe(true);
    });

    test('prefix match: qwen2.5:latest matches when qwen2.5:7b loaded', () => {
        // Simulates: job model = qwen2.5:latest, loaded = qwen2.5:7b
        // Logic: m.startsWith('qwen2.5:') when split(':')[0] = 'qwen2.5'
        const backend = healthyBackend({ loaded_models: ['qwen2.5:7b'] });
        expect(matchesBackend(backend, 'qwen2.5:latest')).toBe(true);
    });

    test('returns false for no match', () => {
        const backend = healthyBackend({ loaded_models: ['llama3:8b'] });
        expect(matchesBackend(backend, 'qwen2.5:7b')).toBe(false);
    });

    test('returns false for empty loaded_models', () => {
        const backend = healthyBackend({ loaded_models: [] });
        expect(matchesBackend(backend, 'qwen2.5:7b')).toBe(false);
    });

    test('returns false when loaded_models is null/undefined', () => {
        const backend = healthyBackend({ loaded_models: undefined });
        expect(matchesBackend(backend, 'qwen2.5:7b')).toBe(false);
    });

    test('returns false when model is null', () => {
        const backend = healthyBackend({ loaded_models: ['qwen2.5:7b'] });
        expect(matchesBackend(backend, null)).toBe(false);
    });
});

// ── computeAllUnhealthy ───────────────────────────────────────────────────────

describe('computeAllUnhealthy', () => {
    test('returns true when all backends are unhealthy', () => {
        const backends = [
            { healthy: false },
            { healthy: false },
        ];
        expect(computeAllUnhealthy(backends)).toBe(true);
    });

    test('returns false when at least one backend is healthy', () => {
        const backends = [
            { healthy: false },
            { healthy: true },
        ];
        expect(computeAllUnhealthy(backends)).toBe(false);
    });

    test('returns false for empty array', () => {
        expect(computeAllUnhealthy([])).toBe(false);
    });
});
```

- [ ] **Step 2: Run the failing tests**

Run: `cd ollama_queue/dashboard/spa && npx jest src/components/HostCard.test.js 2>&1 | tail -30`
Expected: FAIL — `Cannot find module './HostCard.jsx'` or `named export not found`.

---

### Task 4: Implement pure functions in HostCard.jsx (no JSX yet)

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/HostCard.jsx`

- [ ] **Step 1: Write HostCard.jsx with pure exports only**

```jsx
// What it shows: One GPU backend — its state (running/eval/warm/idle/offline),
//   loaded model, VRAM pressure, and host resource gauges (RAM/CPU/Swap for local hosts).
// Decision it drives: "What is each host doing and is it healthy enough to take more work?"
//   Replaces the old CurrentJob + InfrastructurePanel split — backend is the top-level unit.

import { h } from 'preact';
import { useEffect, useRef } from 'preact/hooks';
import { useSignal } from '@preact/signals';
import { applyMantra, removeMantra } from 'superhot-ui';
import { ShStatusBadge, ShThreatPulse, ShFrozen, ShGlitch, ShShatter } from 'superhot-ui/preact';
import { cancelEvalRun } from '../stores/eval.js';
import { API } from '../stores';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import { formatDuration } from '../utils/time.js';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

// ── Pure helper functions (exported for unit testing) ─────────────────────────

/**
 * Derives the display state for one backend card.
 * Pure — no signals, no DOM, fully testable.
 * @returns {{ state, mood, statusBadgeStatus, gpuLabel, loadedLabel, modelsTooltip, isServing, vramPct, vramColor }}
 */
export function deriveHostState(backend, currentJob, activeEval) {
    // GPU label: strip NVIDIA prefixes, fall back to URL hostname
    let host = backend.url;
    try { host = new URL(backend.url).hostname; } catch (_) { /* keep full url */ }
    const gpuLabel = (backend.gpu_name || host)
        .replace(/^nvidia\s+geforce\s+/i, '')
        .replace(/^nvidia\s+/i, '');

    // VRAM pressure
    const vramPct = backend.vram_pct ?? 0;
    const vramColor = vramPct > 90
        ? 'var(--status-error)'
        : vramPct > 80
            ? 'var(--status-warning)'
            : 'var(--sh-phosphor)';

    // Loaded model display
    const loaded = backend.loaded_models || [];
    const loadedLabel = loaded.length > 0
        ? `${loaded[0].split(':')[0]}${loaded.length > 1 ? ` +${loaded.length - 1}` : ''}`
        : null;
    const modelsTooltip = loaded.length > 0 ? loaded.join(', ') : null;

    // State priority: offline > running > eval > warm > idle
    let state, mood, statusBadgeStatus;
    if (!backend.healthy) {
        state = 'offline';
        mood = 'dread';
        statusBadgeStatus = 'error';
    } else if (currentJob && matchesBackend(backend, currentJob.model)) {
        state = 'running';
        mood = 'dawn';
        statusBadgeStatus = 'active';
    } else if (
        activeEval &&
        (activeEval.gen_backend_url === backend.url ||
         activeEval.judge_backend_url === backend.url)
    ) {
        state = 'eval';
        mood = null;
        statusBadgeStatus = 'waiting';
    } else if (loaded.length > 0) {
        state = 'warm';
        mood = null;
        statusBadgeStatus = 'ok';
    } else {
        state = 'idle';
        mood = null;
        statusBadgeStatus = 'ok';
    }

    const isServing = state === 'running';
    return { state, mood, statusBadgeStatus, gpuLabel, loadedLabel, modelsTooltip, isServing, vramPct, vramColor };
}

/**
 * Returns true when the backend URL is local (127.0.0.1 or localhost).
 * Local backends show RAM/CPU/Swap from latestHealth; remote backends do not.
 * Pure — no side effects, fully testable.
 */
export function isLocalBackend(url) {
    return url.includes('127.0.0.1') || url.includes('localhost');
}

/**
 * Returns true when the given model matches any loaded model on the backend.
 * Uses prefix logic: qwen2.5:7b matches qwen2.5:latest and vice versa.
 * Guards against null/undefined loaded_models.
 * Pure — no signals, no DOM, fully testable.
 */
export function matchesBackend(backend, model) {
    if (!model) return false;
    const loaded = backend.loaded_models || [];
    return loaded.some(m => m === model || m.startsWith(model.split(':')[0] + ':'));
}

/**
 * Returns the three host gauge descriptors used by the daemon's job-admission gate.
 * Identical logic to the former InfrastructurePanel.hostGauges — moved here.
 * Pure — no signals, fully testable.
 * @returns {Array<{ label, value, pause, resume }>}
 */
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

/**
 * Returns true when there are configured backends but none are reachable.
 * Moved from InfrastructurePanel.jsx — same logic.
 * Pure — no signals, fully testable.
 */
export function computeAllUnhealthy(backends) {
    return backends.length > 0 && backends.every(b => !b.healthy);
}

// ── Component placeholder — JSX in next task ──────────────────────────────────
export default function HostCard() { return null; }
```

- [ ] **Step 2: Run the pure function tests**

Run: `cd ollama_queue/dashboard/spa && npx jest src/components/HostCard.test.js 2>&1 | tail -30`
Expected: All pure function tests PASS. Component render tests not yet written.

- [ ] **Step 3: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/jest.config.cjs
git add ollama_queue/dashboard/spa/src/__mocks__/superhot-ui-preact.cjs
git add ollama_queue/dashboard/spa/src/components/HostCard.jsx
git add ollama_queue/dashboard/spa/src/components/HostCard.test.js
git commit -m "feat: HostCard pure helpers TDD + test infra (superhot-ui/preact mock)"
```

---

## Chunk 2: HostCard Render Implementation

### Task 5: Add render tests to HostCard.test.js

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/HostCard.test.js`

- [ ] **Step 0: Verify useSignal is exported from preact-signals mock**

Run: `node -e "const m = require('./src/__mocks__/preact-signals.cjs'); console.log(typeof m.useSignal)"` in `ollama_queue/dashboard/spa/`
Expected: `function`

If it prints `undefined`, add `useSignal: jest.fn((init) => { let v = init; return { get value() { return v; }, set value(x) { v = x; } }; })` to `preact-signals.cjs`. (The existing mock already exports `useSignal` as of this writing — this step confirms it.)

Also confirm `API` is in the stores mock: `node -e "const m = require('./src/__mocks__/stores.cjs'); console.log(m.API)"` → expected `http://localhost:7683/api`. (Already present — confirming so the log-fetch URL in HostCard builds correctly.)

- [ ] **Step 1: Append render test helpers and tests to HostCard.test.js**

Note: `HostCard` was already imported at the top of the test file in Task 3. Do NOT add another `import` statement — just append the following after the last `describe` block:

```js
// ── Render test infrastructure ────────────────────────────────────────────────

// NOTE: HostCard (default export) is already imported at top of this file.
// Do not re-import here.

// Recursively find first node matching predicate in the POJO vnode tree.
function findNode(v, pred) {
    if (!v) return null;
    if (pred(v)) return v;
    if (Array.isArray(v)) {
        for (const child of v) { const r = findNode(child, pred); if (r) return r; }
    }
    if (v && typeof v === 'object' && v.props?.children) {
        return findNode(v.props.children, pred);
    }
    return null;
}

// Concatenate all string/number leaves in the tree.
function findText(v) {
    if (!v) return '';
    if (typeof v === 'string' || typeof v === 'number') return String(v);
    if (Array.isArray(v)) return v.map(findText).join('');
    if (v && typeof v === 'object' && v.props?.children) return findText(v.props.children);
    return '';
}

const baseProps = () => ({
    backend: {
        url: 'http://127.0.0.1:11434',
        healthy: true,
        gpu_name: 'RTX 5080',
        vram_pct: 50,
        loaded_models: [],
        inference_mode: 'local',
    },
    currentJob: null,
    activeEval: null,
    evalActiveRun: null,
    latestHealth: { ram_pct: 60, load_avg: 2, swap_pct: 5 },
    settings: {},
    cpuCount: 4,
});

// ── Render tests ──────────────────────────────────────────────────────────────

describe('HostCard render — running state', () => {
    test('renders ShStatusBadge with status="active"', () => {
        const props = baseProps();
        props.backend.loaded_models = ['qwen2.5:7b'];
        props.currentJob = { model: 'qwen2.5:7b', source: 'aria-engine', started_at: 1700000000, id: 42 };
        const vnode = HostCard(props);
        const badge = findNode(vnode, n => n.props?.['data-sh-effect'] === 'status-badge');
        expect(badge).toBeTruthy();
        expect(badge.props['data-sh-status']).toBe('active');
    });

    test('renders source name text', () => {
        const props = baseProps();
        props.backend.loaded_models = ['qwen2.5:7b'];
        props.currentJob = { model: 'qwen2.5:7b', source: 'aria-engine', started_at: 1700000000, id: 42 };
        const vnode = HostCard(props);
        expect(findText(vnode)).toContain('aria-engine');
    });

    test('renders ShFrozen with timestamp in ms (started_at * 1000)', () => {
        const props = baseProps();
        props.backend.loaded_models = ['qwen2.5:7b'];
        props.currentJob = { model: 'qwen2.5:7b', source: 'aria-engine', started_at: 1700000000, id: 42 };
        const vnode = HostCard(props);
        const frozen = findNode(vnode, n => n.props?.['data-sh-effect'] === 'frozen');
        expect(frozen).toBeTruthy();
        // started_at is seconds; ShFrozen expects ms — verify the * 1000 conversion
        expect(frozen.props['data-sh-ts']).toBe(1700000000 * 1000);
    });
});

describe('HostCard render — offline state', () => {
    test('renders ShThreatPulse with data-sh-active="true"', () => {
        const props = baseProps();
        props.backend.healthy = false;
        const vnode = HostCard(props);
        const pulse = findNode(vnode, n => n.props?.['data-sh-effect'] === 'threat-pulse');
        expect(pulse).toBeTruthy();
        expect(pulse.props['data-sh-active']).toBe('true');
    });

    test('renders unreachable text', () => {
        const props = baseProps();
        props.backend.healthy = false;
        const vnode = HostCard(props);
        expect(findText(vnode)).toContain('unreachable');
    });

    test('gpuLabel falls back to URL hostname when gpu_name is null', () => {
        const props = baseProps();
        props.backend.healthy = false;
        props.backend.gpu_name = null;
        props.backend.url = 'http://desktop-fbl9e0c:11434';
        const vnode = HostCard(props);
        // The t-frame should have data-label set to the hostname
        const frame = findNode(vnode, n => n.props?.['data-label']);
        expect(frame).toBeTruthy();
        expect(frame.props['data-label']).toBe('desktop-fbl9e0c');
    });
});

describe('HostCard render — eval state', () => {
    test('renders ShStatusBadge with status="waiting"', () => {
        const props = baseProps();
        props.backend.url = 'http://100.1.2.3:11434';
        props.activeEval = { id: 5, gen_backend_url: 'http://100.1.2.3:11434', status: 'generating' };
        const vnode = HostCard(props);
        const badge = findNode(vnode, n => n.props?.['data-sh-effect'] === 'status-badge');
        expect(badge).toBeTruthy();
        expect(badge.props['data-sh-status']).toBe('waiting');
    });
});

describe('HostCard render — warm state', () => {
    test('renders loaded model name', () => {
        const props = baseProps();
        props.backend.loaded_models = ['qwen2.5:7b'];
        const vnode = HostCard(props);
        expect(findText(vnode)).toContain('qwen2.5');
    });
});

describe('HostCard render — idle state', () => {
    test('renders ShStatusBadge with status="ok"', () => {
        const props = baseProps();
        props.backend.loaded_models = [];
        const vnode = HostCard(props);
        const badge = findNode(vnode, n => n.props?.['data-sh-effect'] === 'status-badge');
        expect(badge).toBeTruthy();
        expect(badge.props['data-sh-status']).toBe('ok');
    });
});

describe('HostCard render — local vs remote backend', () => {
    test('local backend: renders gauge labels (RAM, CPU, Swap)', () => {
        const props = baseProps();
        // 127.0.0.1 is local — gauges should render
        const vnode = HostCard(props);
        const text = findText(vnode);
        expect(text).toContain('RAM');
        expect(text).toContain('CPU');
        expect(text).toContain('Swap');
    });

    test('remote backend: renders "remote host" note, no RAM/CPU/Swap labels', () => {
        const props = baseProps();
        props.backend.url = 'http://100.114.197.57:11434';
        props.latestHealth = null; // remote — no health data
        const vnode = HostCard(props);
        const text = findText(vnode);
        expect(text).toContain('remote host');
        expect(text).not.toContain('RAM');
    });
});

describe('HostCard render — expand toggle', () => {
    test('renders expand toggle button in all states', () => {
        for (const state of ['idle', 'warm', 'offline']) {
            const props = baseProps();
            if (state === 'offline') props.backend.healthy = false;
            if (state === 'warm') props.backend.loaded_models = ['llama3:8b'];
            const vnode = HostCard(props);
            const btn = findNode(vnode, n => n.type === 'button' && findText(n).includes('details'));
            expect(btn).toBeTruthy();
        }
    });

    test('stall warning renders when expanded and currentJob.stall_detected_at is set', () => {
        const props = baseProps();
        props.backend.loaded_models = ['qwen2.5:7b'];
        props.currentJob = {
            model: 'qwen2.5:7b', source: 'test', started_at: 1700000000, id: 1,
            stall_detected_at: 1700001000,
        };
        // The stall panel is gated behind `expanded.value && isRunning`.
        // useSignal is a jest.fn() — override it to return expanded=true for this render.
        const { useSignal } = require('../__mocks__/preact-signals.cjs');
        useSignal
            .mockReturnValueOnce({ value: true })   // expanded = true
            .mockReturnValueOnce({ value: [] });     // logLines = []
        const vnode = HostCard(props);
        const text = findText(vnode);
        expect(text).toContain('frozen');
        useSignal.mockReset(); // restore default behavior for subsequent tests
    });
});

describe('HostCard render — VRAM thresholds', () => {
    test('VRAM bar renders with error color when vram_pct > 90', () => {
        const props = baseProps();
        props.backend.vram_pct = 95;
        const vnode = HostCard(props);
        // The VRAM fill div has background set to vramColor
        const vramFill = findNode(vnode, n =>
            n.props?.style && (
                (typeof n.props.style === 'object' && n.props.style.background === 'var(--status-error)') ||
                (typeof n.props.style === 'string' && n.props.style.includes('--status-error'))
            )
        );
        expect(vramFill).toBeTruthy();
    });
});
```

- [ ] **Step 2: Run the render tests — they should fail**

Run: `cd ollama_queue/dashboard/spa && npx jest src/components/HostCard.test.js --testNamePattern="render" 2>&1 | tail -30`
Expected: FAIL — HostCard returns null (placeholder implementation).

---

### Task 6: Implement full HostCard JSX

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/HostCard.jsx`

- [ ] **Step 1: Replace the placeholder export default with the full component**

Replace `export default function HostCard() { return null; }` with:

```jsx
/**
 * What it shows: One GPU backend — running job, eval session, loaded model, VRAM pressure,
 *   and (for local hosts) RAM/CPU/Swap gauges. Each card represents a distinct Ollama backend.
 * Decision it drives: "Which host is doing what and can it take more work?"
 *   Running state = phosphor glow. Offline = threat pulse. Expanding reveals log/eval detail.
 */
export default function HostCard({
    backend,
    currentJob,
    activeEval,
    evalActiveRun,
    latestHealth,
    settings,
    cpuCount,
}) {
    // Hooks before any conditional return (Rules of Hooks)
    const cardRef = useRef(null);
    const logLines = useSignal([]);
    const expanded = useSignal(false);
    const prevHealthy = useRef(backend.healthy);
    const glitchActive = useSignal(false);
    const [cancelFb, cancelAct] = useActionFeedback();

    const derived = deriveHostState(backend, currentJob, activeEval);
    const { state, mood, statusBadgeStatus, gpuLabel, loadedLabel, modelsTooltip, isServing, vramPct, vramColor } = derived;
    const isLocal = isLocalBackend(backend.url);
    const gauges = isLocal ? hostGauges(latestHealth, settings, cpuCount) : [];
    const isRunning = state === 'running';
    const isStalled = isRunning && !!currentJob?.stall_detected_at;

    // Elapsed time and progress for running state
    let elapsed = null;
    let estimated = null;
    let progressPct = 0;
    if (isRunning && currentJob?.started_at) {
        const now = Date.now() / 1000;
        elapsed = now - currentJob.started_at;
        estimated = currentJob.estimated_duration || null;
        if (estimated && estimated > 0) progressPct = (elapsed / estimated) * 100;
    }
    const isOverrun = estimated && progressPct > 100;

    // Mantra: stamp "RUNNING" watermark on card while this backend is active
    useEffect(() => {
        if (!cardRef.current) return;
        if (isRunning) {
            applyMantra(cardRef.current, 'RUNNING');
        } else {
            removeMantra(cardRef.current);
        }
        return () => { if (cardRef.current) removeMantra(cardRef.current); };
    }, [isRunning]);

    // ShGlitch edge trigger: glitch once when healthy transitions true → false
    // NOT level-triggered — do not use active={!backend.healthy} (continuous glitch)
    useEffect(() => {
        if (prevHealthy.current === true && !backend.healthy) {
            glitchActive.value = true;
        } else {
            glitchActive.value = false;
        }
        prevHealthy.current = backend.healthy;
    }, [backend.healthy]);

    // Live log tail: polls /api/jobs/{id}/log only when expanded + running
    useEffect(() => {
        if (!expanded.value || !isRunning || !currentJob?.id) {
            logLines.value = [];
            return;
        }
        let cancelled = false;
        async function fetchLog() {
            try {
                const r = await fetch(`${API}/jobs/${currentJob.id}/log?tail=5`);
                if (!cancelled && r.ok) {
                    const data = await r.json();
                    logLines.value = data.lines || [];
                }
            } catch (_) { /* best-effort */ }
        }
        fetchLog();
        const iv = setInterval(fetchLog, 5000);
        return () => { cancelled = true; clearInterval(iv); };
    }, [expanded.value, isRunning, currentJob?.id]);

    const vramBarPct = Math.min(vramPct, 100);

    const card = (
        <div ref={cardRef} class="t-frame" data-label={gpuLabel}>
            {/* What it shows: Compact summary row — status badge + GPU name + VRAM + loaded model */}
            <div class="flex items-center gap-2 flex-wrap">
                <ShGlitch active={glitchActive.value} intensity="medium">
                    <ShStatusBadge status={statusBadgeStatus} />
                </ShGlitch>
                <span class="data-mono" style="color: var(--text-primary); font-size: var(--type-body);">
                    {gpuLabel}
                </span>

                {/* VRAM bar + percentage */}
                {backend.healthy && (
                    <div class="flex items-center gap-1" style="flex: 1; min-width: 80px;">
                        <div style="flex: 1; height: 4px; background: var(--bg-surface); border-radius: 2px; overflow: hidden;">
                            <div style={{
                                width: `${vramBarPct}%`,
                                height: '100%',
                                background: vramColor,
                                transition: 'width 0.4s',
                            }} />
                        </div>
                        <span class="data-mono" style={{ color: vramColor, fontSize: 'var(--type-micro)', flexShrink: 0, minWidth: '3rem', textAlign: 'right' }}>
                            {vramPct}%
                        </span>
                    </div>
                )}

                {/* Loaded model chip — title shows full list when truncated by "+N" */}
                {loadedLabel && (
                    <span
                        class="data-mono"
                        title={modelsTooltip}
                        style={{ color: isServing ? 'var(--sh-phosphor)' : 'var(--status-healthy)', fontSize: 'var(--type-micro)', flexShrink: 0 }}
                    >
                        {loadedLabel}
                    </span>
                )}

                {/* State pill */}
                {isServing && (
                    <span class="data-mono" style="color: var(--sh-phosphor); font-size: var(--type-micro); letter-spacing: 0.04em;">
                        ▶
                    </span>
                )}
            </div>

            {/* State-specific detail row */}
            {isRunning && currentJob && (
                // What it shows: Job source, elapsed time (ShFrozen freezes on stall), progress bar
                <div class="flex flex-col gap-1" style="margin-top: 0.375rem;">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="data-mono" style="font-size: var(--type-body); color: var(--text-primary);">
                            {currentJob.source}
                        </span>
                        <ShFrozen timestamp={currentJob.started_at * 1000} />
                        {estimated && (
                            <span class="data-mono" style="font-size: var(--type-label); color: var(--text-tertiary);">
                                / ~{formatDuration(estimated)}
                            </span>
                        )}
                        {isOverrun && (
                            <span style="font-size: var(--type-micro); color: var(--status-warning); background: var(--status-warning-subtle); padding: 1px 5px; border-radius: 3px;">
                                over estimate
                            </span>
                        )}
                    </div>
                    {estimated && (
                        <div style="height: 3px; background: var(--bg-inset); border-radius: 2px; overflow: hidden;">
                            <div style={{
                                width: isOverrun ? '100%' : `${progressPct}%`,
                                height: '100%',
                                background: isOverrun ? 'var(--status-warning)' : 'var(--accent)',
                                transition: 'width 1s linear',
                            }} />
                        </div>
                    )}
                </div>
            )}

            {state === 'eval' && activeEval && (
                // What it shows: Eval session in progress — phase and progress
                <div class="flex items-center gap-2 flex-wrap" style="margin-top: 0.25rem;">
                    <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary);">
                        eval #{activeEval.id} · {activeEval.status}
                    </span>
                </div>
            )}

            {state === 'warm' && (
                <div class="data-mono" style="font-size: var(--type-label); color: var(--text-tertiary); margin-top: 0.25rem;">
                    model loaded · idle
                </div>
            )}

            {state === 'offline' && (
                <span style="color: var(--status-error); font-size: var(--type-label); font-family: var(--font-mono);">
                    unreachable
                </span>
            )}

            {/* Gauges — local host only */}
            {gauges.length > 0 ? (
                // What it shows: RAM/CPU/Swap — the three daemon job-admission gate metrics
                <div class="flex gap-3 flex-wrap" style="margin-top: 0.375rem;">
                    {gauges.map(gauge => <HostGaugeBar key={gauge.label} gauge={gauge} />)}
                </div>
            ) : (
                !isLocal && (
                    <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); display: block; margin-top: 0.25rem;">
                        remote host — system metrics not available
                    </span>
                )
            )}

            {/* Expand toggle — always visible so users can drill into any state */}
            <button
                class="t-btn"
                style="margin-top: 0.375rem; font-size: var(--type-micro);"
                onClick={() => { expanded.value = !expanded.value; }}
            >
                {expanded.value ? '▴ details' : '▾ details'}
            </button>

            {/* Expanded: log tail + stall panel (running only) */}
            {expanded.value && isRunning && (
                // What it shows: Last 5 stdout lines — confirms job is producing output
                <div style="margin-top: 0.5rem;">
                    <div style="font-family: var(--font-mono); font-size: var(--type-micro); background: var(--bg-terminal, var(--bg-inset)); padding: 8px; border-radius: var(--radius); max-height: 120px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; color: var(--text-secondary);">
                        {logLines.value.length > 0
                            ? logLines.value.map((line, i) => <div key={i}>{line}</div>)
                            : <span style="color: var(--text-tertiary);">No output yet</span>
                        }
                    </div>
                    {isStalled && (
                        <details style="margin-top: 0.5rem;">
                            <summary style="cursor: pointer; font-size: var(--type-label); color: var(--status-warning); list-style: none;">
                                ⚠ frozen — what should I do?
                            </summary>
                            <div style="padding: 8px; background: var(--bg-surface); border: 1px solid var(--border-primary); border-radius: var(--radius); font-size: var(--type-label); color: var(--text-secondary);">
                                <ol style="margin: 0; padding-left: 16px; display: flex; flex-direction: column; gap: 4px;">
                                    <li>Wait 2 more minutes — some models are slow to start</li>
                                    <li>Cancel and retry — click × in the queue below</li>
                                    <li>Check Ollama: run <code style="font-family: var(--font-mono);">ollama ps</code></li>
                                    <li>Restart daemon from Settings if Ollama itself is stuck</li>
                                </ol>
                            </div>
                        </details>
                    )}
                </div>
            )}

            {/* Expanded: eval progress + cancel (eval state only) */}
            {expanded.value && state === 'eval' && evalActiveRun && (
                // What it shows: Per-variant progress bars + cancel button for the active eval run
                <div style="margin-top: 0.5rem; display: flex; flex-direction: column; gap: 0.375rem;">
                    {evalActiveRun.progress_pct != null && (
                        <div>
                            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary);">
                                {evalActiveRun.phase} · {Math.round(evalActiveRun.progress_pct)}%
                            </span>
                            <div style="height: 3px; background: var(--bg-inset); border-radius: 2px; overflow: hidden; margin-top: 2px;">
                                <div style={{ width: `${Math.min(evalActiveRun.progress_pct, 100)}%`, height: '100%', background: 'var(--accent)' }} />
                            </div>
                        </div>
                    )}
                    {/* Cancel eval — ShShatter animates the button out on dismiss */}
                    {activeEval?.id && (
                        <div>
                            <ShShatter
                                onDismiss={() => cancelAct(
                                    'Cancelling…',
                                    () => cancelEvalRun(activeEval.id),
                                    () => 'Cancelled'
                                )}
                            >
                                <button class="t-btn" style="font-size: var(--type-micro);">✕ cancel eval</button>
                            </ShShatter>
                            {cancelFb.msg && (
                                <span class={`action-fb action-fb--${cancelFb.phase}`}>{cancelFb.msg}</span>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );

    // Offline: wrap in ShThreatPulse + dread mood wrapper
    if (state === 'offline') {
        return (
            <div data-mood="dread">
                <ShThreatPulse active={true} persistent={true}>
                    {card}
                </ShThreatPulse>
            </div>
        );
    }

    // Running (or any other mood): wrap in mood div for CSS cascade
    if (mood) {
        return <div data-mood={mood}>{card}</div>;
    }

    return card;
}

// ── Sub-components ─────────────────────────────────────────────────────────────

// Renders a single host metric bar with gradient fill + pause threshold marker.
// Same gradient + mask technique as the former InfrastructurePanel.HostGaugeBar.
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
                <div style={{ position: 'absolute', left: `${pauseNorm}%`, top: 0, bottom: 0, width: '1px', borderLeft: '1px dashed var(--text-tertiary)', opacity: 0.5 }} />
            </div>
            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-secondary); width: 28px;">
                {Math.round(pct)}%
            </span>
        </div>
    );
}
```

- [ ] **Step 2: Run all HostCard tests**

Run: `cd ollama_queue/dashboard/spa && npx jest src/components/HostCard.test.js 2>&1 | tail -30`
Expected: All tests PASS.

If any test fails, read the error carefully. Common issues:
- `data-sh-effect` not found → check the Sh* mock stubs return the right attributes
- `findNode` returns null → add a console.log of the vnode structure to trace

- [ ] **Step 3: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/HostCard.jsx
git add ollama_queue/dashboard/spa/src/components/HostCard.test.js
git commit -m "feat: HostCard full implementation + render tests"
```

---

## Chunk 3: Now.jsx Changes + Cleanup + Verification

### Task 7: Update Now.jsx

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Now.jsx`

- [ ] **Step 1: Remove old imports, add new ones**

Remove these imports:
```js
import CurrentJob from '../components/CurrentJob.jsx';
import InfrastructurePanel from '../components/InfrastructurePanel.jsx';
```

Add these imports after the existing import block:
```js
import HostCard from '../components/HostCard.jsx';
```

In the stores import line, add `backendsData`, `fetchBackends` (they are already re-exported via `stores/index.js` — check first with `grep -n 'backendsData\|fetchBackends' ollama_queue/dashboard/spa/src/stores/index.js`). Also add `evalActiveRun` from `../stores/eval.js`:
```js
import { evalActiveRun } from '../stores/eval.js';
```

Add `backendsData, fetchBackends` to the existing `'../stores'` import. The current multi-line import block (lines 3–7) must become:

```js
import {
    status, queue, history, healthData, cpuCount, durationData, settings,
    dlqCount, connectionStatus, currentTab, clearDLQ,
    scheduleJobs, fetchSchedule,
    backendsData, fetchBackends,
} from '../stores';
```

- [ ] **Step 2: Add fetchBackends useEffect (takes over from InfrastructurePanel)**

After the existing `useEffect(() => { fetchSchedule(); }, []);` block, add:
```jsx
// Take over the 15s backend refresh — InfrastructurePanel owned this interval;
// when it is deleted, Now.jsx must own it so backendsData stays fresh.
useEffect(() => {
    fetchBackends();
    const id = setInterval(fetchBackends, 15000);
    return () => clearInterval(id);
}, []);
```

- [ ] **Step 3: Trim kpiStats — remove Daemon and VRAM entries**

Find the `kpiStats` array. Remove these two entries:
```js
// DELETE this entry:
{
    label: 'Daemon',
    value: daemonDisplayValue,
    status: daemonStatStatus,
    detail: warmModel ? warmModel.split(':')[0] : undefined,
},
```
```js
// DELETE this block at the bottom:
if (latestHealth?.vram_pct != null) {
    kpiStats.push({
        label: 'VRAM',
        value: `${Math.round(latestHealth.vram_pct)}%`,
        status: latestHealth.vram_pct > 85 ? 'error' : latestHealth.vram_pct > 70 ? 'warning' : 'ok',
    });
}
```

Keep: Queue Depth, Jobs 24h, RAM. `kpiStats` should be a 3-element array after this change.

Also remove the now-unused derived variables at the top of the function. The exact lines to delete (read Now.jsx to confirm line numbers):
```js
// Remove these 4 lines — no longer needed (daemon state now shown on HostCard)
const rawDaemonState = st?.daemon?.state ?? null;
const warmModel = rawDaemonState === 'idle' ? (latestHealth?.ollama_model ?? null) : null;
const daemonDisplayValue = warmModel ? 'warm' : (rawDaemonState ?? '—');
const daemonStatStatus =
    !st ? 'waiting' :
    rawDaemonState === 'running' ? 'active' :
    (rawDaemonState || '').startsWith('paused') ? 'warning' :
    rawDaemonState === 'offline' ? 'error' : 'ok';
```

- [ ] **Step 4: Replace the 2-column layout**

**Read `src/pages/Now.jsx` first** — you will need the exact existing JSX for the alert strip, HeroCards block, and proxy mini-stat. These must be copied verbatim.

Remove the existing `<div class="now-grid">` block (lines ~148–328 — the entire section from `{/* 2-column layout */}` to the closing `</div>` before `</div>` that closes the outer page div).

Replace it with the following structure, copying verbatim where noted:

```jsx
{/* Host cards — one per configured backend, shows what each GPU is doing */}
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
```

Then copy verbatim from current Now.jsx: the entire `{showAlerts && (<div style={{...}}>...</div>)}` block (the alert strip with DLQ count, recent failures, and disabled recurring badges).

Then add the bottom 2-column grid:

```jsx
{/* Bottom grid: queue list on left, hero cards on right */}
<div class="now-grid">
    {/* LEFT: queue */}
    <QueueList jobs={q} currentJob={currentJob} />

    {/* RIGHT: hero cards + proxy stat */}
    <div class="flex flex-col gap-4">
```

Then copy verbatim from current Now.jsx: the `<div class="grid grid-cols-2 gap-3">` block with all 4 HeroCards, followed by the `{showProxyStat && (...)}` block.

Close the divs:
```jsx
    </div>
</div>
```

**Important:** `now-grid` class is still used on the bottom section. Verify no dangling closing `</div>` tags after removing the old layout. Use `npm run build` in Step 6 to catch any JSX nesting errors.

- [ ] **Step 5: Clean up unused variables**

After the layout changes, `onSubmitRequest` prop is no longer needed (it was passed to CurrentJob for the EmptyState button, which is intentionally removed). Update the function signature:

```jsx
// Before:
export default function Now({ onSubmitRequest }) {

// After:
export default function Now() {
```

Also remove the `activeEval` destructure if it's now passed directly from `st?.active_eval` — verify it's still assigned: `const activeEval = st?.active_eval ?? null;` should remain since it's passed to HostCard.

- [ ] **Step 6: Verify build compiles without errors**

Run: `cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -30`
Expected: Build succeeds, no import errors, no undefined variables.

If build fails: read the error. Most likely causes — a variable was removed but still referenced, or an import path is wrong.

- [ ] **Step 7: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat: Now.jsx host-first layout — HostCard list, fetchBackends interval, trimmed kpiStats"
```

---

### Task 8: Delete old components

**Files:**
- Delete: `ollama_queue/dashboard/spa/src/components/CurrentJob.jsx`
- Delete: `ollama_queue/dashboard/spa/src/components/InfrastructurePanel.jsx`

- [ ] **Step 1: Verify no remaining imports of the deleted files**

Run: `grep -r "CurrentJob\|InfrastructurePanel" ollama_queue/dashboard/spa/src/ --include="*.jsx" --include="*.js" -l`
Expected: No files listed. If any appear, fix those imports first.

- [ ] **Step 2: Delete the files**

```bash
rm ollama_queue/dashboard/spa/src/components/CurrentJob.jsx
rm ollama_queue/dashboard/spa/src/components/InfrastructurePanel.jsx
```

- [ ] **Step 3: Check for associated test files**

Run: `ls ollama_queue/dashboard/spa/src/components/CurrentJob.test.js ollama_queue/dashboard/spa/src/components/InfrastructurePanel.test.js 2>/dev/null`
If either file exists, delete it too.

- [ ] **Step 4: Verify build still passes after deletion**

Run: `cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -20`
Expected: Clean build — no "Cannot resolve" or "missing module" errors.

- [ ] **Step 5: Run full Jest suite**

Run: `cd ollama_queue/dashboard/spa && npx jest 2>&1 | tail -20`
Expected: All existing tests pass. HostCard tests pass. No regressions.

- [ ] **Step 6: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add -u ollama_queue/dashboard/spa/src/components/CurrentJob.jsx
git add -u ollama_queue/dashboard/spa/src/components/InfrastructurePanel.jsx
git commit -m "chore: delete CurrentJob.jsx + InfrastructurePanel.jsx (replaced by HostCard)"
```

Note: `git add -u` stages deletions. Confirm with `git status` before committing.

---

### Task 9: Final verification

- [ ] **Step 1: Run full Python test suite (backend unchanged — should be green)**

Run: `cd /home/justin/Documents/projects/ollama-queue && source .venv/bin/activate && pytest --timeout=120 -x -q 2>&1 | tail -20`
Expected: All tests pass (1,943+). No regressions from frontend-only change.

- [ ] **Step 2: Run full SPA test suite**

Run: `cd ollama_queue/dashboard/spa && npx jest 2>&1 | tail -20`
Expected: All tests pass, including new HostCard tests.

- [ ] **Step 3: Rebuild production bundle**

Run: `cd ollama_queue/dashboard/spa && npm run build`
Expected: Clean build, no warnings about missing modules.

- [ ] **Step 4: Smoke test the live dashboard**

If `ollama-queue.service` is running, open: `https://<your-machine>.<your-tailnet>.ts.net/queue/ui/`

Verify:
- Now tab loads without blank screen
- At least one HostCard renders per configured backend
- HostCard shows GPU name, VRAM bar, loaded model (if any)
- KPI strip shows 3 cards (Queue Depth, Jobs 24h, RAM) — not 4 or 5
- Queue list and HeroCards appear below the host cards
- No JavaScript console errors

If the live dashboard is not accessible, rebuild the bundle and verify no esbuild errors instead.

- [ ] **Step 5: Final commit + run quality gate**

```bash
cd /home/justin/Documents/projects/ollama-queue
git status  # Confirm no stray files
git log --oneline -5  # Review the 3-4 commits made in this plan
```

Run quality gate:
```bash
lessons-db scan --target . --baseline HEAD 2>&1 | tail -20
```

Expected: No new lesson violations.

- [ ] **Step 6: Commit any cleanup and create PR**

```bash
git push origin feature/now-host-first
gh pr create --title "feat: Now page host-first redesign (HostCard replaces CurrentJob + InfrastructurePanel)" --body "Closes the multi-GPU mental model gap: GPU backend is now the top-level unit on the Now tab. One HostCard per backend shows running job, eval progress, loaded model, VRAM pressure, and host gauges.

Changes:
- New: HostCard.jsx with 5 exported pure helpers (TDD, 30+ tests)
- New: superhot-ui/preact mock for jest
- Modified: Now.jsx — HostCard list, fetchBackends ownership, 3-card kpiStats
- Deleted: CurrentJob.jsx, InfrastructurePanel.jsx

Spec: docs/superpowers/specs/2026-03-16-now-page-host-first-design.md"
```

---

## Key Invariants (check if something breaks)

| Invariant | How to verify |
|---|---|
| `data-mood` on wrapper div, not `.t-frame` | `grep 'data-mood' src/components/HostCard.jsx` — should appear on outer `<div>`, not on `.t-frame` |
| ShGlitch is edge-triggered | `useRef(backend.healthy)` is initial value; `glitchActive` set in useEffect on transition only |
| ShShatter inner button has NO onClick | `<button class="t-btn">` inside ShShatter has no onClick prop — onDismiss only |
| cancelEvalRun import from stores/eval.js | `import { cancelEvalRun } from '../stores/eval.js'` (not `../stores`) |
| evalActiveRun passed as .value from Now.jsx | `evalActiveRun={evalActiveRun.value}` in the HostCard map — HostCard never imports signal |
| fetchBackends interval in Now.jsx | `grep 'fetchBackends' src/pages/Now.jsx` — setInterval must appear there, not in HostCard |
| ShFrozen timestamp multiplied by 1000 | `timestamp={currentJob.started_at * 1000}` — DB stores seconds, ShFrozen expects ms |
| vramColor thresholds: > 90, > 80 (strict) | Match exact thresholds from backendRowState in deleted InfrastructurePanel.jsx |
