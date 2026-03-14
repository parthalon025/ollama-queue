import LiveIndicator from './LiveIndicator.jsx';

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

// ── Backend detection helpers (shared by nodeState + edgeState) ──────────────
function _findLocalBackend(backends) {
  return backends.find(b => {
    try { const host = new URL(b.url).hostname; return host === '127.0.0.1' || host === 'localhost'; }
    catch (_) { return false; }
  });
}
function _findRemoteBackend(backends) {
  return backends.find(b => {
    try { const host = new URL(b.url).hostname; return host !== '127.0.0.1' && host !== 'localhost'; }
    catch (_) { return false; }
  });
}
function _isServing(backend, activeModel) {
  if (!backend || !backend.healthy || !activeModel) return false;
  return (backend.loaded_models || []).some(
    m => m === activeModel || m.startsWith(activeModel.split(':')[0] + ':')
  );
}

// Builds the sublabel for a GPU node: task source + VRAM% when active, model + VRAM% when warm
function _gpuSubLabel(backend, activeModel, activeSource) {
  const pct = `${backend.vram_pct ?? 0}%`;
  const models = backend.loaded_models || [];
  const serving = _isServing(backend, activeModel);
  if (models.length === 0) return `idle · ${pct}`;
  const modelTag = models.length === 1 ? models[0] : `${models[0]} +${models.length - 1}`;
  if (serving) return `▶ ${activeSource ?? modelTag} · ${pct}`;
  return `${modelTag} · ${pct}`;
}

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

  const activeModel  = currentJob?.model  ?? null;
  const activeSource = currentJob?.source ?? null;
  const gtx = _findLocalBackend(backends);
  const rtx = _findRemoteBackend(backends);

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
      const gtxSub = _gpuSubLabel(gtx, activeModel, activeSource);
      return _isServing(gtx, activeModel) ? glow(C.PHOSPHOR, 'url(#topo-glow-phosphor)', gtxSub) : { ...dim(gtxSub), opacity: 0.8 };
    }
    case 'rtx5080': {
      if (!rtx || !rtx.healthy) return threat('offline');
      const rtxSub = _gpuSubLabel(rtx, activeModel, activeSource);
      return _isServing(rtx, activeModel) ? glow(C.PHOSPHOR, 'url(#topo-glow-phosphor)', rtxSub) : { ...dim(rtxSub), opacity: 0.8 };
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
  const gtx = _findLocalBackend(backends);
  const rtx = _findRemoteBackend(backends);

  const gtxServing     = _isServing(gtx, activeModel);
  const rtxServing     = _isServing(rtx, activeModel);
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

// ── Edge path definitions ─────────────────────────────────────────────────────
// What it shows: SVG path strings for all 13 directed edges in the topology graph.
// Decision it drives: Edges connect nodes visually; active edges animate to show live data flow.
function buildEdgePaths() {
  const N = NODES;
  const R = name => rc(N[name]), L = name => lc(N[name]),
        T = name => tc(N[name]), B = name => bc(N[name]);

  return {
    // Primary flow (left-to-right, orthogonal routing)
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
    // Feedback arcs — bezier curves (visually distinct from forward flow)
    e11: `M ${L('sensing').x} ${L('sensing').y} C ${N.sensing.x - 60} ${L('sensing').y} ${N.daemon.x - 60} ${L('daemon').y} ${L('daemon').x} ${L('daemon').y}`,
    e12: `M ${R('daemon').x} ${R('daemon').y + 10} H ${R('daemon').x + 30} V ${T('dlq').y - 10} H ${R('dlq').x} V ${T('dlq').y}`,
    e13: `M ${L('dlq').x} ${L('dlq').y} C ${N.dlq.x - 120} ${L('dlq').y} ${N.scheduler.x - 80} ${R('scheduler').y} ${R('scheduler').x} ${R('scheduler').y}`,
  };
}
const EDGE_PATHS = buildEdgePaths();

// Renders a single directed edge with its computed style
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
// What it shows: Section header (live indicator dot + "SYSTEM TOPOLOGY" label + optional burst
//   regime badge) above the full directed-graph SVG.
// Decision it drives: At a glance — is the system live? Is a burst regime active?
export default function TopologyDiagram({ daemonStatus, currentJob, backends, dlqCount, activeEval, queueDepth }) {
  const tprops = { daemonStatus, currentJob, backends: backends || [], dlqCount: dlqCount || 0, activeEval, queueDepth: queueDepth || 0 };
  const burst = daemonStatus?.burst_regime;
  const burstActive = burst && burst !== 'calm' && burst !== 'unknown';

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
        {burstActive && (
          <span class="data-mono" style={{
            fontSize: 'var(--type-micro)',
            color: burst === 'storm' ? 'var(--sh-threat, var(--status-error))' : 'var(--status-warning, #f59e0b)',
            marginLeft: 'auto',
          }}>
            {burst.toUpperCase()}
          </span>
        )}
      </div>
      <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
        <svg
          viewBox="0 0 860 480"
          width="100%"
          style={{ display: 'block', minWidth: 480 }}
          aria-label="ollama-queue system topology"
        >
          <Defs />
          {/* Edges drawn first — nodes layer on top */}
          {Object.keys(EDGE_PATHS).map(id => (
            <Edge key={id} id={id} es={edgeState(id, tprops)} />
          ))}
          {Object.keys(NODES).map(name => (
            <Node key={name} name={name} ns={nodeState(name, tprops)} tprops={tprops} />
          ))}
        </svg>
      </div>
    </>
  );
}
