import { hostGauges, backendRowState, computeAllUnhealthy } from './InfrastructurePanel.jsx';

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

test('backendRowState: modelsTooltip is full comma-joined list', () => {
    const b = { ...BASE, loaded_models: ['model-a:latest', 'model-b:7b', 'model-c:latest'] };
    expect(backendRowState(b, null).modelsTooltip).toBe('model-a:latest, model-b:7b, model-c:latest');
});

test('backendRowState: modelsTooltip is null when no models loaded', () => {
    const b = { ...BASE, loaded_models: [] };
    expect(backendRowState(b, null).modelsTooltip).toBeNull();
});

// ── computeAllUnhealthy ────────────────────────────────────────────────────────
// Controls the "All backends unreachable" message vs per-row rendering in InfrastructurePanel.

test('computeAllUnhealthy: empty backends → false (no backends configured)', () => {
    expect(computeAllUnhealthy([])).toBe(false);
});

test('computeAllUnhealthy: all unhealthy → true (shows error message)', () => {
    const backends = [
        { ...BASE, healthy: false },
        { ...BASE, url: 'http://10.0.0.2:11434', healthy: false },
    ];
    expect(computeAllUnhealthy(backends)).toBe(true);
});

test('computeAllUnhealthy: mixed health → false (rows render normally)', () => {
    const backends = [
        { ...BASE, healthy: true },
        { ...BASE, url: 'http://10.0.0.2:11434', healthy: false },
    ];
    expect(computeAllUnhealthy(backends)).toBe(false);
});

test('computeAllUnhealthy: all healthy → false', () => {
    const backends = [{ ...BASE, healthy: true }, { ...BASE, url: 'http://10.0.0.2:11434', healthy: true }];
    expect(computeAllUnhealthy(backends)).toBe(false);
});
