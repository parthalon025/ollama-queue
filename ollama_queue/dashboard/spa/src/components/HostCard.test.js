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
