import { nodeState, edgeState } from './TopologyDiagram.jsx';

// ── nodeState ──────────────────────────────────────────────────────────────

test('daemon: running job → phosphor stroke', () => {
  const ns = nodeState('daemon', { daemonStatus: { state: 'running', current_job_id: 42, burst_regime: 'calm' } });
  expect(ns.stroke).toContain('sh-phosphor');
  expect(ns.filter).toContain('glow-phosphor');
  expect(ns.opacity).toBe(1);
});

test('daemon: paused → dim + PAUSED sublabel', () => {
  const ns = nodeState('daemon', { daemonStatus: { state: 'paused_health', current_job_id: null, burst_regime: 'calm' } });
  expect(ns.opacity).toBe(0.35);
  expect(ns.sublabel).toBe('PAUSED');
  expect(ns.pulse).toBe(false);
});

test('daemon: offline → threat stroke + pulse', () => {
  const ns = nodeState('daemon', { daemonStatus: { state: 'offline', current_job_id: null, burst_regime: 'calm' } });
  expect(ns.stroke).toContain('sh-threat');
  expect(ns.pulse).toBe(true);
});

test('dlq: count > 0 → amber stroke with count sublabel', () => {
  const ns = nodeState('dlq', { dlqCount: 3 });
  expect(ns.stroke).toContain('status-warning');
  expect(ns.sublabel).toBe('3 entries');
});

test('dlq: count 0 → dim', () => {
  const ns = nodeState('dlq', { dlqCount: 0 });
  expect(ns.stroke).toContain('border');
});

test('eval: active eval → phosphor stroke with run info', () => {
  const ns = nodeState('eval', { activeEval: { id: 7, status: 'judging' } });
  expect(ns.stroke).toContain('sh-phosphor');
  expect(ns.sublabel).toContain('run #7');
});

test('proxy: proxy in flight (current_job_id=-1) → amber stroke', () => {
  const ns = nodeState('proxy', { daemonStatus: { state: 'running', current_job_id: -1, burst_regime: 'calm' } });
  expect(ns.stroke).toContain('status-warning');
});

test('input nodes: burst regime storm → threat stroke + pulse', () => {
  const ns = nodeState('cli', { daemonStatus: { state: 'running', current_job_id: null, burst_regime: 'storm' } });
  expect(ns.stroke).toContain('sh-threat');
  expect(ns.pulse).toBe(true);
});

test('input nodes: burst regime burst → amber stroke', () => {
  const ns = nodeState('recurring', { daemonStatus: { state: 'running', current_job_id: null, burst_regime: 'burst' } });
  expect(ns.stroke).toContain('status-warning');
  expect(ns.pulse).toBe(false);
});

test('gtx1650: unhealthy backend → threat stroke + pulse', () => {
  const backends = [{ url: 'http://127.0.0.1:11434', healthy: false, vram_pct: 0, loaded_models: [] }];
  const ns = nodeState('gtx1650', { backends });
  expect(ns.stroke).toContain('sh-threat');
  expect(ns.pulse).toBe(true);
});

test('gtx1650: serving current job → phosphor', () => {
  const backends = [{ url: 'http://127.0.0.1:11434', healthy: true, vram_pct: 55, loaded_models: ['llama3:8b'] }];
  const ns = nodeState('gtx1650', { backends, currentJob: { model: 'llama3:8b' }, daemonStatus: { state: 'running', current_job_id: 1, burst_regime: 'calm' } });
  expect(ns.stroke).toContain('sh-phosphor');
});

// ── edgeState ──────────────────────────────────────────────────────────────

test('e6 (Queue→Daemon): active daemon job → phosphor marching ants', () => {
  const es = edgeState('e6', { daemonStatus: { state: 'running', current_job_id: 5, burst_regime: 'calm' } });
  expect(es.stroke).toContain('sh-phosphor');
  expect(es.animation).toContain('march-phosphor');
  expect(es.dasharray).toBe('6 3');
});

test('e6: daemon paused → dim (no animation)', () => {
  const es = edgeState('e6', { daemonStatus: { state: 'paused_health', current_job_id: null, burst_regime: 'calm' } });
  expect(es.stroke).toContain('text-tertiary');
  expect(es.animation).toBeNull();
});

test('e8 (Proxy→Router): proxy active → amber marching ants', () => {
  const es = edgeState('e8', { daemonStatus: { state: 'running', current_job_id: -1, burst_regime: 'calm' } });
  expect(es.stroke).toContain('status-warning');
  expect(es.animation).toContain('march-amber');
});

test('e11 (Sensing→Daemon feedback): daemon paused → threat animation', () => {
  const es = edgeState('e11', { daemonStatus: { state: 'paused_health', current_job_id: null, burst_regime: 'calm' } });
  expect(es.stroke).toContain('sh-threat');
  expect(es.animation).toContain('march-threat');
});

test('e13 (DLQ→Scheduler retry): dlqCount > 0 → amber static', () => {
  const es = edgeState('e13', { daemonStatus: { state: 'running', current_job_id: null, burst_regime: 'calm' }, dlqCount: 2 });
  expect(es.stroke).toContain('status-warning');
  expect(es.animation).toBeNull();
});

test('e3 (CLI→Queue): storm burst → threat marching ants', () => {
  const es = edgeState('e3', { daemonStatus: { state: 'running', current_job_id: null, burst_regime: 'storm' } });
  expect(es.stroke).toContain('sh-threat');
  expect(es.animation).toContain('march-threat');
});

test('inactive edge → dim opacity 0.3', () => {
  const es = edgeState('e1', {});
  expect(es.opacity).toBe(0.3);
  expect(es.animation).toBeNull();
});
