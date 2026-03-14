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

// ── Main component (placeholder — built up in subsequent tasks) ───────────────

export default function TopologyDiagram({ daemonStatus, currentJob, backends, dlqCount, activeEval, queueDepth }) {
  return <svg viewBox="0 0 860 480" width="100%" style={{ display: 'block' }} />;
}
