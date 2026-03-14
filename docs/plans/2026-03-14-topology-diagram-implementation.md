# Topology Diagram Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a live SVG system topology diagram to the Backends tab that shows every layer of ollama-queue with animated paths reflecting real-time state.

**Architecture:** Pure SVG embedded in JSX with hardcoded node positions. Two exported pure helper functions (`nodeState`, `edgeState`) compute visual style from props — fully testable in isolation. The component reads from existing signals (no new API calls). Replaces the static ASCII tree in BackendsTab section 6.4.

**Tech Stack:** Preact JSX, @preact/signals (read-only), SVG filters + markers + CSS keyframe animations, jest for unit tests. Design doc: `docs/plans/2026-03-14-backends-topology-diagram-design.md`.

---

### Task 1: Scaffold TopologyDiagram.jsx + export pure helpers

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/TopologyDiagram.test.js`

**Step 1: Write the failing tests for `nodeState`**

Create `src/components/TopologyDiagram.test.js`:

```js
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
  expect(es.animation).toBeNull(); // slow pulse, not marching
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
```

**Step 2: Run tests to verify they all fail**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npx jest TopologyDiagram --no-coverage 2>&1 | head -30
```

Expected: All tests FAIL with `Cannot find module './TopologyDiagram.jsx'`.

**Step 3: Create TopologyDiagram.jsx with the two exported helpers**

Create `src/components/TopologyDiagram.jsx`:

```jsx
// What it shows: The full ollama-queue system as a live directed-graph topology.
//   Four columns: Inputs → Queue/Scheduler → Daemon/DLQ/Sensing → Router/Backends.
//   Active paths animate with marching-ant arrows; nodes reflect live health state.
// Decision it drives: At a glance — is the system healthy? Which GPU is active?
//   Is the DLQ accumulating? Is sensing throttling the daemon? Are both GPUs busy?

// ── Colour tokens (CSS vars) ─────────────────────────────────────────────────
const C = {
  PHOSPHOR: 'var(--sh-phosphor, var(--accent))',
  THREAT:   'var(--sh-threat, var(--status-error))',
  AMBER:    'var(--status-warning, #f59e0b)',
  DIM:      'var(--border)',
  TEXT_DIM: 'var(--text-tertiary)',
};

// ── Pure helpers (exported for tests) ────────────────────────────────────────

/**
 * Returns visual style descriptor for a named node based on live props.
 * @param {string} name  Node identifier (see switch cases below)
 * @param {object} props { daemonStatus, currentJob, backends, dlqCount, activeEval }
 * @returns {{ stroke, filter, opacity, sublabel, sublabelColor, pulse }}
 */
export function nodeState(name, {
  daemonStatus = null,
  currentJob = null,
  backends = [],
  dlqCount = 0,
  activeEval = null,
} = {}) {
  const daemonSt     = daemonStatus?.state ?? 'offline';
  const isPaused     = daemonSt.startsWith('paused');
  const isProxy      = daemonStatus?.current_job_id === -1;
  const isDaemonJob  = typeof daemonStatus?.current_job_id === 'number' && daemonStatus.current_job_id > 0;
  const burst        = daemonStatus?.burst_regime ?? 'unknown';

  // Infer which backends are serving the current daemon job
  const activeModel = currentJob?.model ?? null;
  function isServing(b) {
    if (!b || !b.healthy || !activeModel) return false;
    return (b.loaded_models || []).some(
      m => m === activeModel || m.startsWith(activeModel.split(':')[0] + ':')
    );
  }
  const gtx = backends.find(b => { try { const h = new URL(b.url).hostname; return h === '127.0.0.1' || h === 'localhost'; } catch (_) { return false; } });
  const rtx = backends.find(b => { try { const h = new URL(b.url).hostname; return h !== '127.0.0.1' && h !== 'localhost'; } catch (_) { return false; } });

  const dim   = (sublabel = null) => ({ stroke: C.DIM, filter: null, opacity: 0.7, sublabel, sublabelColor: null, pulse: false });
  const glow  = (col, filter, sublabel = null) => ({ stroke: col, filter, opacity: 1, sublabel, sublabelColor: null, pulse: false });
  const threat = (sublabel = null) => ({ stroke: C.THREAT, filter: 'url(#glow-threat)', opacity: 1, sublabel, sublabelColor: C.THREAT, pulse: true });

  switch (name) {
    case 'daemon':
      if (isPaused)           return { stroke: C.TEXT_DIM, filter: null, opacity: 0.35, sublabel: 'PAUSED', sublabelColor: C.THREAT, pulse: false };
      if (daemonSt === 'offline') return threat('OFFLINE');
      if (isDaemonJob)        return glow(C.PHOSPHOR, 'url(#glow-phosphor)', 'poller · executor');
      return { ...dim('poller · executor'), opacity: 0.7 };

    case 'dlq':
      if (dlqCount > 0) return { stroke: C.AMBER, filter: 'url(#glow-amber)', opacity: 1, sublabel: `${dlqCount} entries`, sublabelColor: C.AMBER, pulse: false };
      return { ...dim('dead letter'), opacity: 0.6 };

    case 'eval':
      if (activeEval) return glow(C.PHOSPHOR, 'url(#glow-phosphor)', `run #${activeEval.id} · ${activeEval.status}`);
      return { ...dim('A/B eval · judge'), opacity: 0.6 };

    case 'proxy':
      if (isProxy) return glow(C.AMBER, 'url(#glow-amber)', '/generate · /embed');
      return { ...dim('/generate · /embed'), opacity: 0.6 };

    case 'gtx1650': {
      if (!gtx || !gtx.healthy) return threat('offline');
      const vram = `${gtx.vram_pct ?? 0}% VRAM`;
      return isServing(gtx) ? glow(C.PHOSPHOR, 'url(#glow-phosphor)', vram) : { ...dim(vram), opacity: 0.8 };
    }
    case 'rtx5080': {
      if (!rtx || !rtx.healthy) return threat('offline');
      const vram = `${rtx.vram_pct ?? 0}% VRAM`;
      return isServing(rtx) ? glow(C.PHOSPHOR, 'url(#glow-phosphor)', vram) : { ...dim(vram), opacity: 0.8 };
    }

    // Input nodes: reflect burst regime
    case 'recurring': case 'cli': case 'intercept':
      if (burst === 'storm') return { stroke: C.THREAT, filter: null, opacity: 1, sublabel: null, sublabelColor: null, pulse: true };
      if (burst === 'burst') return { stroke: C.AMBER,  filter: null, opacity: 1, sublabel: null, sublabelColor: null, pulse: false };
      return dim();

    default:
      return dim();
  }
}

/**
 * Returns edge style descriptor for a named edge ID.
 * @param {string} id   Edge identifier e1–e13
 * @param {object} props { daemonStatus, currentJob, backends, dlqCount }
 * @returns {{ stroke, strokeWidth, dasharray, animation, opacity, marker }}
 */
export function edgeState(id, {
  daemonStatus = null,
  currentJob = null,
  backends = [],
  dlqCount = 0,
} = {}) {
  const isPaused    = daemonStatus?.state?.startsWith('paused') ?? false;
  const isDaemonJob = typeof daemonStatus?.current_job_id === 'number' && daemonStatus.current_job_id > 0;
  const isProxy     = daemonStatus?.current_job_id === -1;
  const burst       = daemonStatus?.burst_regime ?? 'unknown';

  const activeModel = currentJob?.model ?? null;
  function isServing(b) {
    if (!b || !b.healthy || !activeModel) return false;
    return (b.loaded_models || []).some(
      m => m === activeModel || m.startsWith(activeModel.split(':')[0] + ':')
    );
  }
  const gtx = backends.find(b => { try { const h = new URL(b.url).hostname; return h === '127.0.0.1' || h === 'localhost'; } catch (_) { return false; } });
  const rtx = backends.find(b => { try { const h = new URL(b.url).hostname; return h !== '127.0.0.1' && h !== 'localhost'; } catch (_) { return false; } });

  const gtxServing     = isServing(gtx);
  const rtxServing     = isServing(rtx);
  const neitherServing = isDaemonJob && !gtxServing && !rtxServing; // model loading — light both

  function active(col, anim, speed = '0.35s') {
    const key = col === C.PHOSPHOR ? 'phosphor' : col === C.AMBER ? 'amber' : 'threat';
    return { stroke: col, strokeWidth: 2, dasharray: '6 3', animation: `${anim} ${speed} linear infinite`, opacity: 1, marker: `url(#arrow-${key})` };
  }
  function dim() {
    return { stroke: C.TEXT_DIM, strokeWidth: 1, dasharray: null, animation: null, opacity: 0.3, marker: 'url(#arrow-dim)' };
  }
  function amberStatic() {
    return { stroke: C.AMBER, strokeWidth: 1.5, dasharray: '4 4', animation: null, opacity: 0.7, marker: 'url(#arrow-amber)' };
  }

  const C_PHOSPHOR = 'var(--sh-phosphor, var(--accent))';
  const C_THREAT   = 'var(--sh-threat, var(--status-error))';
  const C_AMBER    = 'var(--status-warning, #f59e0b)';

  // Re-alias so active() helper works (it uses C.PHOSPHOR etc.)
  const _C = { PHOSPHOR: C_PHOSPHOR, THREAT: C_THREAT, AMBER: C_AMBER };

  function _active(col, anim, speed = '0.35s') {
    const key = col === _C.PHOSPHOR ? 'phosphor' : col === _C.AMBER ? 'amber' : 'threat';
    return { stroke: col, strokeWidth: 2, dasharray: '6 3', animation: `${anim} ${speed} linear infinite`, opacity: 1, marker: `url(#arrow-${key})` };
  }

  switch (id) {
    case 'e6': // Queue → Daemon
    case 'e7': // Daemon → Router
      if (isDaemonJob && !isPaused) return _active(_C.PHOSPHOR, 'march-phosphor');
      return dim();

    case 'e9': // Router → GTX 1650
      if (isDaemonJob && !isPaused && (gtxServing || neitherServing)) return _active(_C.PHOSPHOR, 'march-phosphor');
      if (isProxy && gtx?.healthy) return _active(_C.AMBER, 'march-amber', '0.45s');
      return dim();

    case 'e10': // Router → RTX 5080
      if (isDaemonJob && !isPaused && (rtxServing || neitherServing)) return _active(_C.PHOSPHOR, 'march-phosphor');
      if (isProxy && rtx?.healthy) return _active(_C.AMBER, 'march-amber', '0.45s');
      return dim();

    case 'e8': // Direct Proxy → Router
      if (isProxy) return _active(_C.AMBER, 'march-amber', '0.45s');
      return dim();

    case 'e11': // Sensing → Daemon feedback arc
      if (isPaused) return _active(_C.THREAT, 'march-threat', '0.6s');
      return dim();

    case 'e12': // Daemon → DLQ
    case 'e13': // DLQ → Scheduler retry arc
      if (dlqCount > 0) return { stroke: _C.AMBER, strokeWidth: 1.5, dasharray: '4 4', animation: null, opacity: 0.7, marker: 'url(#arrow-amber)' };
      return dim();

    case 'e3': // CLI/API Submit → Queue
    case 'e4': // Consumer Intercept → Queue
      if (burst === 'storm') return _active(_C.THREAT, 'march-threat', '0.4s');
      if (burst === 'burst') return _active(_C.AMBER, 'march-amber', '0.5s');
      return dim();

    default:
      return dim();
  }
}

// ── Main component ────────────────────────────────────────────────────────────

export default function TopologyDiagram({ daemonStatus, currentJob, backends, dlqCount, activeEval }) {
  // Placeholder — full SVG built in Tasks 3–8
  return <svg viewBox="0 0 860 480" width="100%" style={{ display: 'block' }} />;
}
```

**Step 4: Run tests to verify they pass**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npx jest TopologyDiagram --no-coverage 2>&1 | tail -20
```

Expected: All 18 tests PASS.

**Step 5: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx \
        ollama_queue/dashboard/spa/src/components/TopologyDiagram.test.js
git commit -m "feat: add TopologyDiagram helpers + tests (scaffold)"
```

---

### Task 2: Add SVG `<defs>` — filters and arrowhead markers

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx` — replace placeholder SVG body

**Step 1: Replace the component body with full defs block**

In `TopologyDiagram.jsx`, replace the `export default function TopologyDiagram` at the bottom with:

```jsx
// ── CSS keyframe animations (injected as <style> inside SVG) ─────────────────
const ANIM_CSS = `
  @keyframes march-phosphor { to { stroke-dashoffset: -18; } }
  @keyframes march-amber    { to { stroke-dashoffset: -18; } }
  @keyframes march-threat   { to { stroke-dashoffset: -9;  } }
  @keyframes threat-pulse   { 0%,100% { opacity:1; } 50% { opacity:0.35; } }
  .topo-threat-pulse { animation: threat-pulse 1.2s ease-in-out infinite; }
`;

// ── SVG defs: filters + arrowhead markers ────────────────────────────────────
function Defs() {
  return (
    <defs>
      <style>{ANIM_CSS}</style>

      {/* Glow filters — CRT phosphor bloom effect */}
      <filter id="topo-glow-phosphor" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur" />
        <feComposite in="SourceGraphic" in2="blur" operator="over" />
      </filter>
      <filter id="topo-glow-amber" x="-30%" y="-30%" width="160%" height="160%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="3" result="blur" />
        <feComposite in="SourceGraphic" in2="blur" operator="over" />
      </filter>
      <filter id="topo-glow-threat" x="-40%" y="-40%" width="180%" height="180%">
        <feGaussianBlur in="SourceGraphic" stdDeviation="4" result="blur" />
        <feComposite in="SourceGraphic" in2="blur" operator="over" />
      </filter>

      {/* Arrowhead markers — one per colour state */}
      {[
        { id: 'arrow-phosphor', fill: 'var(--sh-phosphor, var(--accent))' },
        { id: 'arrow-amber',    fill: 'var(--status-warning, #f59e0b)' },
        { id: 'arrow-threat',   fill: 'var(--sh-threat, var(--status-error))' },
        { id: 'arrow-dim',      fill: 'var(--text-tertiary)' },
      ].map(({ id, fill }) => (
        <marker key={id} id={id} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill={fill} />
        </marker>
      ))}
    </defs>
  );
}

export default function TopologyDiagram({ daemonStatus, currentJob, backends, dlqCount, activeEval }) {
  return (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <svg
        viewBox="0 0 860 480"
        width="100%"
        style={{ display: 'block', minWidth: 480 }}
        aria-label="ollama-queue system topology"
      >
        <Defs />
        {/* nodes + edges added in subsequent tasks */}
      </svg>
    </div>
  );
}
```

**IMPORTANT:** Update filter references throughout the file — replace `url(#glow-phosphor)` with `url(#topo-glow-phosphor)`, `url(#glow-amber)` with `url(#topo-glow-amber)`, `url(#glow-threat)` with `url(#topo-glow-threat)` in both `nodeState` and `edgeState`. (The `topo-` prefix avoids collisions with any filters already defined in superhot-ui CSS.)

**Step 2: Run tests to confirm no regressions**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npx jest TopologyDiagram --no-coverage 2>&1 | tail -5
```

Expected: 18 tests PASS (filter string references updated in helpers too).

**Step 3: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx
git commit -m "feat: add SVG defs (filters + arrowhead markers)"
```

---

### Task 3: Render all 13 nodes (static, no live state yet)

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx`

**Node layout constants and helper:**

Add above the component:

```jsx
// ── Node layout ───────────────────────────────────────────────────────────────
// All positions are top-left (x, y). Node size: 150 × 38.
const NW = 150, NH = 38;  // node width, height

const NODES = {
  // Column 1 — Inputs (x=20)
  recurring:  { x: 20,  y: 40,  label: 'Recurring Jobs',      sub: 'scheduler · promote' },
  cli:        { x: 20,  y: 110, label: 'CLI / API Submit',    sub: 'ollama-queue submit' },
  proxy:      { x: 20,  y: 180, label: 'Direct Proxy',        sub: '/generate · /embed' },
  intercept:  { x: 20,  y: 250, label: 'Consumer Intercept',  sub: 'iptables REDIRECT' },
  eval:       { x: 20,  y: 320, label: 'Eval Pipeline',       sub: 'A/B eval · judge' },
  // Column 2 — Queue layer (x=215)
  scheduler:  { x: 215, y: 40,  label: 'Scheduler',           sub: 'recurring · dlq · defer' },
  queue:      { x: 215, y: 150, label: 'Queue',                sub: 'priority · sqlite' },
  // Column 3 — Engine (x=410)
  daemon:     { x: 410, y: 150, label: 'Daemon',               sub: 'poller · executor' },
  sensing:    { x: 410, y: 265, label: 'Sensing',              sub: 'health · stall · burst' },
  dlq:        { x: 410, y: 370, label: 'DLQ',                  sub: 'dead letter' },
  // Column 4 — Output (x=605)
  router:     { x: 605, y: 150, label: 'Backend Router',       sub: '5-tier selection' },
  gtx1650:    { x: 605, y: 270, label: 'GTX 1650',             sub: 'local GPU' },
  rtx5080:    { x: 605, y: 370, label: 'RTX 5080',             sub: 'remote GPU' },
};

// Helper: right-center and left-center connection points
function rc(n) { return { x: n.x + NW,       y: n.y + NH / 2 }; } // right-center
function lc(n) { return { x: n.x,             y: n.y + NH / 2 }; } // left-center
function tc(n) { return { x: n.x + NW / 2,   y: n.y };           } // top-center
function bc(n) { return { x: n.x + NW / 2,   y: n.y + NH };      } // bottom-center

// Renders a single node rect + two text rows
function Node({ name, ns }) {
  const n = NODES[name];
  const sub = ns.sublabel ?? n.sub;
  const subColor = ns.sublabelColor ?? 'var(--text-tertiary)';
  const cls = ns.pulse ? 'topo-threat-pulse' : '';
  return (
    <g class={cls}>
      <rect
        x={n.x} y={n.y} width={NW} height={NH} rx="4"
        fill="var(--bg-elevated)"
        stroke={ns.stroke}
        stroke-width={ns.filter ? 2 : 1}
        filter={ns.filter ?? undefined}
        opacity={ns.opacity}
      />
      <text
        x={n.x + NW / 2} y={n.y + 14}
        text-anchor="middle"
        font-family="var(--font-mono)"
        font-size="11"
        fill={ns.filter ? ns.stroke : 'var(--text-primary)'}
        opacity={ns.opacity}
      >{n.label}</text>
      <text
        x={n.x + NW / 2} y={n.y + 27}
        text-anchor="middle"
        font-family="var(--font-mono)"
        font-size="9"
        fill={subColor}
        opacity={ns.opacity}
      >{sub}</text>
    </g>
  );
}
```

**Update `TopologyDiagram` component to render all nodes (still with static dim state):**

```jsx
export default function TopologyDiagram({ daemonStatus, currentJob, backends, dlqCount, activeEval }) {
  const props = { daemonStatus, currentJob, backends: backends || [], dlqCount: dlqCount || 0, activeEval };

  // Static dim state for all nodes until live wiring in Task 5
  const dimState = { stroke: 'var(--border)', filter: null, opacity: 0.7, sublabel: null, sublabelColor: null, pulse: false };

  return (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <svg
        viewBox="0 0 860 480"
        width="100%"
        style={{ display: 'block', minWidth: 480 }}
        aria-label="ollama-queue system topology"
      >
        <Defs />
        {Object.keys(NODES).map(name => (
          <Node key={name} name={name} ns={dimState} />
        ))}
      </svg>
    </div>
  );
}
```

**Step 2: Build and visually verify nodes appear**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

Expected: Build succeeds. Open `/queue/ui/` → Backends tab. Should see 13 dim node rectangles arranged in 4 columns. No edges yet.

**Step 3: Run tests**

```bash
npx jest TopologyDiagram --no-coverage 2>&1 | tail -5
```

Expected: 18 PASS.

**Step 4: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx
git commit -m "feat: render all 13 topology nodes (static layout)"
```

---

### Task 4: Render all 13 edges (static, dim)

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx`

**Step 1: Add edge path definitions and Edge renderer**

Add after the `Node` component:

```jsx
// ── Edge path definitions ─────────────────────────────────────────────────────
// Each edge is defined as an SVG path string. Orthogonal routing (H + V turns).
// Feedback arcs (e11, e13) use cubic bezier to visually distinguish from flow edges.
function buildEdgePaths() {
  const N = NODES;
  // Shorthand: right-center, left-center, top-center, bottom-center
  const R = name => rc(N[name]), L = name => lc(N[name]),
        T = name => tc(N[name]), B = name => bc(N[name]);

  return {
    e1:  `M ${R('recurring').x} ${R('recurring').y} H ${L('scheduler').x}`,
    e2:  `M ${B('scheduler').x} ${B('scheduler').y} V ${T('queue').y}`,
    e3:  `M ${R('cli').x} ${R('cli').y} H ${R('cli').x + 20} V ${L('queue').y} H ${L('queue').x}`,
    e4:  `M ${R('intercept').x} ${R('intercept').y} H ${R('intercept').x + 20} V ${L('queue').y} H ${L('queue').x}`,
    e5:  `M ${R('eval').x} ${R('eval').y} H ${R('eval').x + 20} V ${L('queue').y} H ${L('queue').x}`,
    e6:  `M ${R('queue').x} ${R('queue').y} H ${L('daemon').x}`,
    e7:  `M ${R('daemon').x} ${R('daemon').y} H ${L('router').x}`,
    e8:  `M ${R('proxy').x} ${R('proxy').y} H ${(R('proxy').x + L('router').x) / 2} V ${L('router').y} H ${L('router').x}`,
    e9:  `M ${N.router.x + 60} ${B('router').y} V ${T('gtx1650').y}`,
    e10: `M ${N.router.x + 110} ${B('router').y} V ${N.router.y + NH + 30} H ${N.rtx5080.x + NW - 20} V ${T('rtx5080').y} H ${N.rtx5080.x + 110}`,
    // Feedback arcs — bezier curves for visual distinction
    e11: `M ${L('sensing').x} ${L('sensing').y} C ${N.sensing.x - 60} ${L('sensing').y} ${N.daemon.x - 60} ${L('daemon').y} ${L('daemon').x} ${L('daemon').y}`,
    e12: `M ${R('daemon').x} ${R('daemon').y + 10} H ${R('daemon').x + 30} V ${T('dlq').y - 10} H ${R('dlq').x} V ${T('dlq').y}`,
    e13: `M ${L('dlq').x} ${L('dlq').y} C ${N.dlq.x - 120} ${L('dlq').y} ${N.scheduler.x - 80} ${R('scheduler').y} ${R('scheduler').x} ${R('scheduler').y}`,
  };
}
const EDGE_PATHS = buildEdgePaths();

// Renders a single edge path with computed style
function Edge({ id, es }) {
  return (
    <path
      d={EDGE_PATHS[id]}
      stroke={es.stroke}
      stroke-width={es.strokeWidth}
      stroke-dasharray={es.dasharray ?? undefined}
      stroke-linecap="round"
      fill="none"
      opacity={es.opacity}
      marker-end={es.marker}
      style={es.animation ? { animation: es.animation } : undefined}
    />
  );
}
```

**Update component to render edges:**

In the `TopologyDiagram` return, add after `{Object.keys(NODES).map(...)}`:

```jsx
{/* Edges rendered BEFORE nodes so nodes appear on top */}
```

Actually, reorder the render — edges first, nodes on top:

```jsx
<svg ...>
  <Defs />
  {/* Edges drawn first — nodes layer on top */}
  {Object.keys(EDGE_PATHS).map(id => (
    <Edge key={id} id={id} es={dimEdgeState} />
  ))}
  {Object.keys(NODES).map(name => (
    <Node key={name} name={name} ns={dimState} />
  ))}
</svg>
```

Where `dimEdgeState = { stroke: 'var(--text-tertiary)', strokeWidth: 1, dasharray: null, animation: null, opacity: 0.3, marker: 'url(#arrow-dim)' }`.

**Step 2: Build and visually verify**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

Open Backends tab. Should see all nodes connected by dim lines with arrowheads. Adjust any paths that look obviously wrong (overlapping, wrong direction) by editing the `buildEdgePaths()` values — the y-offset tweaks may need ±10 adjustment visually.

**Step 3: Run tests**

```bash
npx jest TopologyDiagram --no-coverage 2>&1 | tail -5
```

Expected: 18 PASS.

**Step 4: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx
git commit -m "feat: render all topology edges (static dim)"
```

---

### Task 5: Wire live node and edge state

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx`

**Step 1: Replace static dim states with live `nodeState`/`edgeState` calls**

In `TopologyDiagram`, replace the static `dimState`/`dimEdgeState` objects and map calls:

```jsx
export default function TopologyDiagram({ daemonStatus, currentJob, backends, dlqCount, activeEval }) {
  const props = { daemonStatus, currentJob, backends: backends || [], dlqCount: dlqCount || 0, activeEval };

  return (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      <svg
        viewBox="0 0 860 480"
        width="100%"
        style={{ display: 'block', minWidth: 480 }}
        aria-label="ollama-queue system topology"
      >
        <Defs />
        {Object.keys(EDGE_PATHS).map(id => (
          <Edge key={id} id={id} es={edgeState(id, props)} />
        ))}
        {Object.keys(NODES).map(name => (
          <Node key={name} name={name} ns={nodeState(name, props)} />
        ))}
      </svg>
    </div>
  );
}
```

**Step 2: Build + verify live state visually**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

Test scenarios to verify manually (use `ollama-queue submit` to trigger live state):
- Submit a job → e6, e7, and serving-backend edge should glow phosphor green and animate
- Check DLQ has entries → DLQ node turns amber, e12/e13 edges show amber dashes
- If daemon is paused (via Settings tab) → Daemon node dims to 0.35, e11 should glow red

**Step 3: Run tests**

```bash
npx jest TopologyDiagram --no-coverage 2>&1 | tail -5
```

Expected: 18 PASS.

**Step 4: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx
git commit -m "feat: wire live nodeState + edgeState into topology SVG"
```

---

### Task 6: Add VRAM bars + Queue depth sublabel + section header

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx`

**Step 1: Add VRAM bar to GPU backend nodes**

In the `Node` component, after the second `<text>` add:

```jsx
{/* VRAM bar — only on backend nodes that have vram_pct data */}
{(name === 'gtx1650' || name === 'rtx5080') && (() => {
  // Find matching backend
  const isLocal = name === 'gtx1650';
  const b = (props?.backends || []).find(bk => {
    try { const h = new URL(bk.url).hostname; return isLocal ? (h === '127.0.0.1' || h === 'localhost') : (h !== '127.0.0.1' && h !== 'localhost'); }
    catch (_) { return false; }
  });
  if (!b || !b.healthy) return null;
  const pct = Math.min(100, Math.max(0, b.vram_pct ?? 0));
  const fill = pct > 90 ? 'var(--sh-threat, var(--status-error))'
             : pct > 80 ? 'var(--status-warning, #f59e0b)'
             : 'var(--sh-phosphor, var(--accent))';
  const barW = Math.round(pct / 100 * NW);
  return (
    <rect
      x={n.x} y={n.y + NH - 3}
      width={barW} height={3}
      rx="0"
      fill={fill}
      opacity={ns.opacity}
    />
  );
})()}
```

**Note:** `Node` needs access to `props` — pass it as a prop: `<Node key={name} name={name} ns={...} props={props} />` and add `props` to the function signature: `function Node({ name, ns, props = {} })`.

**Step 2: Add Queue depth sublabel when queue has jobs**

In `nodeState` switch case `default` — add a `queue` case before default:

```js
case 'queue': {
  const depth = props?.queueDepth ?? 0;  // Note: pass queueDepth in props
  if (depth > 0) return { stroke: C.AMBER, filter: null, opacity: 1, sublabel: `${depth} pending`, sublabelColor: C.AMBER, pulse: false };
  return { ...dim('priority · sqlite'), opacity: 0.7 };
}
```

Also update `TopologyDiagram` to pass `queueDepth` from the `queue` signal. In `BackendsTab.jsx` (Task 7), import `queue` signal and pass `queue.value?.length ?? 0` as `queueDepth` prop.

For now, add `queueDepth = 0` to the `nodeState` props destructure.

**Step 3: Add section header inside the t-frame (above SVG)**

In `TopologyDiagram`, wrap the return in a fragment and add a header:

```jsx
return (
  <>
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
      <LiveIndicator
        state={daemonStatus?.state === 'running' ? 'running' : daemonStatus?.state?.startsWith('paused') ? 'queued' : 'running'}
        pulse={daemonStatus?.state === 'running'}
      />
      <span class="data-mono" style={{ fontSize: 'var(--type-label)', color: 'var(--text-secondary)', letterSpacing: '0.08em' }}>
        SYSTEM TOPOLOGY
      </span>
      {daemonStatus?.burst_regime && daemonStatus.burst_regime !== 'calm' && daemonStatus.burst_regime !== 'unknown' && (
        <span class="data-mono" style={{ fontSize: 'var(--type-micro)', color: daemonStatus.burst_regime === 'storm' ? 'var(--sh-threat, var(--status-error))' : 'var(--status-warning, #f59e0b)', marginLeft: 'auto' }}>
          {daemonStatus.burst_regime.toUpperCase()}
        </span>
      )}
    </div>
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
      ...svg...
    </div>
  </>
);
```

Import `LiveIndicator` at the top: `import LiveIndicator from './LiveIndicator.jsx';`

**Step 4: Build + verify**

```bash
npm run build 2>&1 | tail -5
```

Check: VRAM bars appear on GPU nodes, header shows "SYSTEM TOPOLOGY" with pulsing dot.

**Step 5: Run tests**

```bash
npx jest TopologyDiagram --no-coverage 2>&1 | tail -5
```

Expected: 18 PASS.

**Step 6: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx
git commit -m "feat: add VRAM bars, queue depth sublabel, section header"
```

---

### Task 7: Integrate into BackendsTab.jsx

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/BackendsTab.jsx`

**Step 1: Add imports**

At the top of `BackendsTab.jsx`, add:

```jsx
import TopologyDiagram from '../components/TopologyDiagram.jsx';
import { status, dlqCount, queue } from '../stores';
```

(`backendsData`, `currentJob` are already imported.)

**Step 2: Replace section 6.4 ASCII block**

Find and replace the entire section 6.4 block:

```jsx
{/* 6.4 Backend Topology (ASCII CSS diagram) */}
<div class="t-frame" data-label="Topology">
  <div class="data-mono" style={{ fontSize: 'var(--type-label)', lineHeight: 1.8, color: 'var(--text-secondary)' }}>
    ... (entire ASCII block) ...
  </div>
</div>
```

Replace with:

```jsx
{/* 6.4 System Topology — live directed-graph diagram */}
<div class="t-frame" data-label="System Topology">
  <TopologyDiagram
    daemonStatus={status.value?.daemon ?? null}
    currentJob={currentJob.value}
    backends={backendsData.value || []}
    dlqCount={dlqCount.value ?? 0}
    activeEval={status.value?.active_eval ?? null}
    queueDepth={queue.value?.length ?? 0}
  />
</div>
```

**Step 3: Build and verify full integration**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -5
```

Open Backends tab. Verify:
- Topology section appears below "Routing Logic"
- All nodes and edges render
- Live state updates when a job runs (submit one: `ollama-queue submit --source test --model qwen2.5:7b --priority 3 --timeout 30 -- echo hello`)
- Header shows pulsing dot when daemon is running

**Step 4: Run full SPA test suite**

```bash
npx jest --no-coverage 2>&1 | tail -10
```

Expected: All tests pass. No regressions.

**Step 5: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/BackendsTab.jsx \
        ollama_queue/dashboard/spa/src/components/TopologyDiagram.jsx
git commit -m "feat: integrate TopologyDiagram into Backends tab (replaces ASCII tree)"
```

---

### Task 8: Final build verification + visual QA pass

**Files:**
- No code changes — verification only

**Step 1: Run full test suite**

```bash
cd /home/justin/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q 2>&1 | tail -5
```

Expected: All Python tests pass (no changes to backend).

```bash
cd ollama_queue/dashboard/spa
npx jest --no-coverage 2>&1 | tail -5
```

Expected: All JS tests pass.

**Step 2: Production build**

```bash
npm run build 2>&1 | tail -5
```

Expected: Build succeeds, no warnings about undefined variables.

**Step 3: Visual QA checklist**

Open `/queue/ui/` → Backends tab → scroll to "System Topology":

- [ ] All 13 nodes visible in 4 columns
- [ ] Edges connect nodes with arrowheads
- [ ] Idle state: all edges dim (opacity 0.3), nodes dimmed
- [ ] Submit a job: e6→e7→e9 or e10 glow green with marching animation
- [ ] Daemon node glows phosphor when job running
- [ ] Backend node matching job model glows phosphor
- [ ] DLQ badge shows count if entries exist
- [ ] Section header shows "SYSTEM TOPOLOGY" + live indicator dot
- [ ] Responsive: shrinks on narrow viewport, scrolls horizontally < 480px

**Step 4: Final commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add -p  # stage only if there are any last visual fixes
git commit -m "feat: topology diagram — full system live SVG (backends tab)"
```

---

## Summary

| Task | Files | Tests |
|------|-------|-------|
| 1 — Helpers + tests | `TopologyDiagram.jsx`, `TopologyDiagram.test.js` | 18 new |
| 2 — SVG defs | `TopologyDiagram.jsx` | 18 pass |
| 3 — 13 nodes static | `TopologyDiagram.jsx` | 18 pass |
| 4 — 13 edges static | `TopologyDiagram.jsx` | 18 pass |
| 5 — Live state wiring | `TopologyDiagram.jsx` | 18 pass |
| 6 — VRAM bars + header | `TopologyDiagram.jsx` | 18 pass |
| 7 — BackendsTab integration | `BackendsTab.jsx` | full suite |
| 8 — Final QA | — | all pass |

**Branch:** `fix/bug-audit-fixes` (current) or create `feature/topology-diagram` for isolation.
