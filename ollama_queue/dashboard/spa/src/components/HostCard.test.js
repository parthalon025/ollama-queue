// HostCard pure helper function tests.
// Render tests are added in a subsequent task — they use the default export imported here.
// ALL imports are at the top (ES module rule; babel-jest enforces this).

import {
    deriveHostState,
    isLocalBackend,
    hostGauges,
    matchesBackend,
    computeAllUnhealthy,
} from './HostCard.jsx';

// Default export for render tests (added in next task). Declared here at the top
// so the next task can append tests without adding a mid-file import.
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
        expect(gauges[1].pause).toBe(200);   // 2 × 100 (not × 50 — see CLAUDE.md gotcha)
        expect(gauges[1].resume).toBe(150);  // 1.5 × 100
        expect(gauges[2].label).toBe('Swap');
    });

    test('normalises CPU from load_avg using cpuCount', () => {
        const health = { ram_pct: 0, load_avg: 2, swap_pct: 0 };
        const gauges = hostGauges(health, {}, 4);
        expect(gauges[1].value).toBe(50);
    });

    test('returns [] when latestHealth is null', () => {
        expect(hostGauges(null, {}, 4)).toEqual([]);
    });

    test('uses default pause/resume when settings is null', () => {
        const health = { ram_pct: 50, load_avg: 1, swap_pct: 5 };
        const gauges = hostGauges(health, null, 4);
        expect(gauges[0].pause).toBe(85);
        expect(gauges[2].pause).toBe(50);
    });
});

// ── matchesBackend ────────────────────────────────────────────────────────────

describe('matchesBackend', () => {
    test('exact model name match', () => {
        const backend = healthyBackend({ loaded_models: ['qwen2.5:7b'] });
        expect(matchesBackend(backend, 'qwen2.5:7b')).toBe(true);
    });

    test('prefix match: qwen2.5:latest matches when qwen2.5:7b loaded', () => {
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
        const backends = [{ healthy: false }, { healthy: false }];
        expect(computeAllUnhealthy(backends)).toBe(true);
    });

    test('returns false when at least one backend is healthy', () => {
        const backends = [{ healthy: false }, { healthy: true }];
        expect(computeAllUnhealthy(backends)).toBe(false);
    });

    test('returns false for empty array', () => {
        expect(computeAllUnhealthy([])).toBe(false);
    });
});

// ── Render test infrastructure ────────────────────────────────────────────────

// NOTE: HostCard (default export) is already imported at top of this file.
// Do not re-import here.

// Recursively find first node matching predicate in the POJO vnode tree.
// When a node's type is a function (unresolved component), it calls the function
// with the node's props so the mock's rendered output is traversed too.
function findNode(v, pred) {
    if (!v || v === true || v === false) return null;
    // Resolve function components (e.g. ShGlitch, ShStatusBadge) by calling them
    if (v && typeof v === 'object' && typeof v.type === 'function') {
        const resolved = v.type(v.props || {});
        // Check the resolved result first, then continue traversal on it
        const r = findNode(resolved, pred);
        if (r) return r;
        return null;
    }
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
// Also resolves function components for text extraction.
function findText(v) {
    if (!v || v === true || v === false) return '';
    if (typeof v === 'string' || typeof v === 'number') return String(v);
    if (Array.isArray(v)) return v.map(findText).join('');
    // Resolve function components
    if (v && typeof v === 'object' && typeof v.type === 'function') {
        const resolved = v.type(v.props || {});
        return findText(resolved);
    }
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
        // useSignal call order in HostCard: logLines (1st), expanded (2nd), glitchActive (3rd).
        // mockReturnValueOnce queues in FIFO — first queue entry goes to logLines, second to expanded.
        const { useSignal } = require('../__mocks__/preact-signals.cjs');
        useSignal
            .mockReturnValueOnce({ value: [] })     // logLines = []
            .mockReturnValueOnce({ value: true });  // expanded = true
        const vnode = HostCard(props);
        const text = findText(vnode);
        expect(text).toContain('frozen');
        useSignal.mockClear(); // clear call history; default implementation is preserved (mockReset would strip it)
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
