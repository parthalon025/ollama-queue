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

export function nodeState(name, {
  daemonStatus = null,
  currentJob = null,
  backends = [],
  dlqCount = 0,
  activeEval = null,
  queueDepth = 0,
} = {}) {
  const daemonSt    = daemonStatus?.state ?? 'offline';
  const isPaused    = daemonSt.startsWith('paused');
  const isProxy     = daemonStatus?.current_job_id === -1;
  const isDaemonJob = typeof daemonStatus?.current_job_id === 'number' && daemonStatus.current_job_id > 0;
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

  const dim    = (sublabel = null) => ({ stroke: C.DIM,    filter: null,                        opacity: 0.7, sublabel, sublabelColor: null,    pulse: false });
  const glow   = (col, flt, sublabel = null) => ({ stroke: col, filter: flt, opacity: 1, sublabel, sublabelColor: null, pulse: false });
  const threat = (sublabel = null) => ({ stroke: C.THREAT, filter: 'url(#topo-glow-threat)', opacity: 1, sublabel, sublabelColor: C.THREAT, pulse: true });

  switch (name) {
    case 'daemon':
      if (isPaused)              return { stroke: C.TEXT_DIM, filter: null, opacity: 0.35, sublabel: 'PAUSED', sublabelColor: C.THREAT, pulse: false };
      if (daemonSt === 'offline') return threat('OFFLINE');
      if (isDaemonJob)           return glow(C.PHOSPHOR, 'url(#topo-glow-phosphor)', 'poller · executor');
      return { ...dim('poller · executor'), opacity: 0.7 };

    case 'dlq':
      if (dlqCount > 0) return { stroke: C.AMBER, filter: 'url(#topo-glow-amber)', opacity: 1, sublabel: `${dlqCount} entries`, sublabelColor: C.AMBER, pulse: false };
      return { ...dim('dead letter'), opacity: 0.6 };

    case 'eval':
      if (activeEval) return glow(C.PHOSPHOR, 'url(#topo-glow-phosphor)', `run #${activeEval.id} · ${activeEval.status}`);
      return { ...dim('A/B eval · judge'), opacity: 0.6 };

    case 'proxy':
      if (isProxy) return glow(C.AMBER, 'url(#topo-glow-amber)', '/generate · /embed');
      return { ...dim('/generate · /embed'), opacity: 0.6 };

    case 'queue': {
      if (queueDepth > 0) return { stroke: C.AMBER, filter: null, opacity: 1, sublabel: `${queueDepth} pending`, sublabelColor: C.AMBER, pulse: false };
      return { ...dim('priority · sqlite'), opacity: 0.7 };
    }

    case 'gtx1650': {
      if (!gtx || !gtx.healthy) return threat('offline');
      const vram = `${gtx.vram_pct ?? 0}% VRAM`;
      return isServing(gtx) ? glow(C.PHOSPHOR, 'url(#topo-glow-phosphor)', vram) : { ...dim(vram), opacity: 0.8 };
    }
    case 'rtx5080': {
      if (!rtx || !rtx.healthy) return threat('offline');
      const vram = `${rtx.vram_pct ?? 0}% VRAM`;
      return isServing(rtx) ? glow(C.PHOSPHOR, 'url(#topo-glow-phosphor)', vram) : { ...dim(vram), opacity: 0.8 };
    }

    case 'recurring': case 'cli': case 'intercept':
      if (burst === 'storm') return { stroke: C.THREAT, filter: null, opacity: 1, sublabel: null, sublabelColor: null, pulse: true };
      if (burst === 'burst') return { stroke: C.AMBER,  filter: null, opacity: 1, sublabel: null, sublabelColor: null, pulse: false };
      return dim();

    default:
      return dim();
  }
}

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
  const neitherServing = isDaemonJob && !gtxServing && !rtxServing;

  const PH  = 'var(--sh-phosphor, var(--accent))';
  const THR = 'var(--sh-threat, var(--status-error))';
  const AMB = 'var(--status-warning, #f59e0b)';
  const DIM = 'var(--text-tertiary)';

  function active(col, anim, speed = '0.35s') {
    const key = col === PH ? 'phosphor' : col === AMB ? 'amber' : 'threat';
    return { stroke: col, strokeWidth: 2, dasharray: '6 3', animation: `${anim} ${speed} linear infinite`, opacity: 1, marker: `url(#arrow-${key})` };
  }
  function dim() {
    return { stroke: DIM, strokeWidth: 1, dasharray: null, animation: null, opacity: 0.3, marker: 'url(#arrow-dim)' };
  }

  switch (id) {
    case 'e6': case 'e7':
      if (isDaemonJob && !isPaused) return active(PH, 'march-phosphor');
      return dim();

    case 'e9':
      if (isDaemonJob && !isPaused && (gtxServing || neitherServing)) return active(PH, 'march-phosphor');
      if (isProxy && gtx?.healthy) return active(AMB, 'march-amber', '0.45s');
      return dim();

    case 'e10':
      if (isDaemonJob && !isPaused && (rtxServing || neitherServing)) return active(PH, 'march-phosphor');
      if (isProxy && rtx?.healthy) return active(AMB, 'march-amber', '0.45s');
      return dim();

    case 'e8':
      if (isProxy) return active(AMB, 'march-amber', '0.45s');
      return dim();

    case 'e11':
      if (isPaused) return active(THR, 'march-threat', '0.6s');
      return dim();

    case 'e12': case 'e13':
      if (dlqCount > 0) return { stroke: AMB, strokeWidth: 1.5, dasharray: '4 4', animation: null, opacity: 0.7, marker: 'url(#arrow-amber)' };
      return dim();

    case 'e3': case 'e4':
      if (burst === 'storm') return active(THR, 'march-threat', '0.4s');
      if (burst === 'burst') return active(AMB, 'march-amber', '0.5s');
      return dim();

    default:
      return dim();
  }
}

// ── SVG animation styles ───────────────────────────────────────────────────────
const ANIM_CSS = `
  @keyframes march-phosphor { to { stroke-dashoffset: -18; } }
  @keyframes march-amber    { to { stroke-dashoffset: -18; } }
  @keyframes march-threat   { to { stroke-dashoffset: -9;  } }
  @keyframes threat-pulse   { 0%,100% { opacity:1; } 50% { opacity:0.35; } }
  .topo-threat-pulse { animation: threat-pulse 1.2s ease-in-out infinite; }
`;

// ── Defs component: SVG filter definitions and arrowhead markers ────────────────
// What it shows: SVG filter definitions for CRT phosphor glow effects and arrowhead markers.
// Decision it drives: All topology edges and nodes reference these shared filter/marker IDs.
function Defs() {
  return (
    <defs>
      <style>{ANIM_CSS}</style>

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

      {[
        { id: 'arrow-phosphor', fill: 'var(--sh-phosphor, var(--accent))' },
        { id: 'arrow-amber',    fill: 'var(--status-warning, #f59e0b)' },
        { id: 'arrow-threat',   fill: 'var(--sh-threat, var(--status-error))' },
        { id: 'arrow-dim',      fill: 'var(--text-tertiary)' },
      ].map(item => (
        <marker key={item.id} id={item.id} markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill={item.fill} />
        </marker>
      ))}
    </defs>
  );
}

// ── Node layout constants ─────────────────────────────────────────────────────
// All positions are top-left (x, y). Node size: 150 × 38.
const NW = 150, NH = 38;

const NODES = {
  // Column 1 — Inputs (x=20)
  recurring:  { x: 20,  y: 40,  label: 'Recurring Jobs',     sub: 'scheduler · promote' },
  cli:        { x: 20,  y: 110, label: 'CLI / API Submit',   sub: 'ollama-queue submit' },
  proxy:      { x: 20,  y: 180, label: 'Direct Proxy',       sub: '/generate · /embed' },
  intercept:  { x: 20,  y: 250, label: 'Consumer Intercept', sub: 'iptables REDIRECT' },
  eval:       { x: 20,  y: 320, label: 'Eval Pipeline',      sub: 'A/B eval · judge' },
  // Column 2 — Queue layer (x=215)
  scheduler:  { x: 215, y: 40,  label: 'Scheduler',          sub: 'recurring · dlq · defer' },
  queue:      { x: 215, y: 150, label: 'Queue',               sub: 'priority · sqlite' },
  // Column 3 — Engine (x=410)
  daemon:     { x: 410, y: 150, label: 'Daemon',              sub: 'poller · executor' },
  sensing:    { x: 410, y: 265, label: 'Sensing',             sub: 'health · stall · burst' },
  dlq:        { x: 410, y: 370, label: 'DLQ',                 sub: 'dead letter' },
  // Column 4 — Output (x=605)
  router:     { x: 605, y: 150, label: 'Backend Router',      sub: '5-tier selection' },
  gtx1650:    { x: 605, y: 270, label: 'GTX 1650',            sub: 'local GPU' },
  rtx5080:    { x: 605, y: 370, label: 'RTX 5080',            sub: 'remote GPU' },
};

// Connection point helpers
function rc(n) { return { x: n.x + NW,     y: n.y + NH / 2 }; } // right-center
function lc(n) { return { x: n.x,           y: n.y + NH / 2 }; } // left-center
function tc(n) { return { x: n.x + NW / 2, y: n.y };           } // top-center
function bc(n) { return { x: n.x + NW / 2, y: n.y + NH };      } // bottom-center

// What it shows: A single topology node — rect + label + sublabel.
// Decision it drives: Node colour/glow/opacity reflects live system state for that subsystem.
function Node({ name, ns, tprops }) {
  const n = NODES[name];
  const sub = ns.sublabel ?? n.sub;
  const subColor = ns.sublabelColor ?? 'var(--text-tertiary)';
  const cls = ns.pulse ? 'topo-threat-pulse' : '';

  // VRAM bar — only for GPU backend nodes with live vram_pct data
  let vramBar = null;
  if (name === 'gtx1650' || name === 'rtx5080') {
    const isLocal = name === 'gtx1650';
    const backends = tprops?.backends || [];
    const b = backends.find(bk => {
      try {
        const host = new URL(bk.url).hostname;
        return isLocal ? (host === '127.0.0.1' || host === 'localhost') : (host !== '127.0.0.1' && host !== 'localhost');
      } catch (_) { return false; }
    });
    if (b && b.healthy) {
      const pct = Math.min(100, Math.max(0, b.vram_pct ?? 0));
      const barFill = pct > 90 ? 'var(--sh-threat, var(--status-error))'
                    : pct > 80 ? 'var(--status-warning, #f59e0b)'
                    : 'var(--sh-phosphor, var(--accent))';
      vramBar = (
        <rect
          x={n.x} y={n.y + NH - 3}
          width={Math.round(pct / 100 * NW)} height={3}
          fill={barFill}
          opacity={ns.opacity}
        />
      );
    }
  }

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
      {vramBar}
    </g>
  );
}

// ── Main component ──────────────────────────────────────────────────────────────
export default function TopologyDiagram({ daemonStatus, currentJob, backends, dlqCount, activeEval, queueDepth }) {
  const tprops = { daemonStatus, currentJob, backends: backends || [], dlqCount: dlqCount || 0, activeEval, queueDepth: queueDepth || 0 };

  // Static dim state — live wiring comes in Task 5
  const dimNs = { stroke: 'var(--border)', filter: null, opacity: 0.7, sublabel: null, sublabelColor: null, pulse: false };

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
          <Node key={name} name={name} ns={dimNs} tprops={tprops} />
        ))}
      </svg>
    </div>
  );
}
