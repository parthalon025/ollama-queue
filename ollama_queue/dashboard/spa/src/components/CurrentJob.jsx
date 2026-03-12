import { useEffect, useRef } from 'preact/hooks';
import { useSignal } from '@preact/signals';
import { applyMantra, removeMantra } from 'superhot-ui';
import StatusBadge from './StatusBadge.jsx';
import ResourceGauges from './ResourceGauges.jsx';
import EmptyState from './EmptyState.jsx';
import { formatDuration } from '../utils/time.js';
import { API } from '../stores';

/**
 * What it shows: What the daemon is doing RIGHT NOW — running job name/model/elapsed time
 *   with a progress bar against the estimated duration, paused state with reason, or idle.
 *   The orange "stalled" badge appears when the stall detector has flagged the job as frozen.
 * Decision it drives: Is the queue working? Is the current job taking too long or frozen?
 *   Should I cancel it and investigate? The progress bar turns orange when the job exceeds
 *   its estimated duration so you can decide whether to wait or kill it.
 *
 * @param {{ daemon: object, currentJob: object|null, latestHealth: object|null, settings: object, activeEval: object|null }} props
 *   daemon: daemon_state row (state, current_job_id, paused_reason, ...)
 *   currentJob: the running job row if any (source, model, started_at, estimated_duration, ...)
 *   latestHealth: most recent health_log row (ram_pct, vram_pct, load_avg, swap_pct)
 *   settings: threshold settings for resource gauges
 *   activeEval: active eval_run row if any (id, status, judge_model) — present for the full session,
 *     not just during individual proxy calls, so eval activity is always visible in the Now tab
 */
export default function CurrentJob({ daemon, currentJob, latestHealth, settings, activeEval, onSubmitRequest }) {
  // Hooks must come before any conditional return (Rules of Hooks).
  const cardRef = useRef(null);

  // What it shows: The last 5 lines of stdout from the running job, polled every 5s.
  // Decision it drives: Lets the user see whether the job is producing output or stuck silent.
  const logLines = useSignal([]);
  const logExpanded = useSignal(false);

  const state = daemon ? (daemon.state || 'idle') : 'idle';
  const isPaused = state.startsWith('paused');
  const isRunning = state === 'running';
  const isStalled = isRunning && currentJob && !!currentJob.stall_detected_at;

  // Mantra: stamp "RUNNING" or "PAUSED" watermark on the card to reflect daemon state.
  // Removes itself when idle — the absence of the watermark signals the queue is at rest.
  useEffect(() => {
    if (!cardRef.current) return;
    if (isRunning) {
      applyMantra(cardRef.current, 'RUNNING');
    } else if (isPaused) {
      applyMantra(cardRef.current, 'PAUSED');
    } else {
      removeMantra(cardRef.current);
    }
    return () => { if (cardRef.current) removeMantra(cardRef.current); };
  }, [isRunning, isPaused]);

  // ThreatPulse: red glow pulse when stall detected.
  useEffect(() => {
    if (!cardRef.current || !isStalled) return;
    cardRef.current.setAttribute('data-sh-effect', 'threat-pulse');
    const timer = setTimeout(
      () => { if (cardRef.current) cardRef.current.removeAttribute('data-sh-effect'); },
      3000
    );
    return () => clearTimeout(timer);
  }, [isStalled]);

  // Live log tail: polls /api/jobs/{id}/log?tail=5 every 5s while a job is running.
  // Clears when the job stops so stale output doesn't persist into the next run.
  useEffect(() => {
    if (!isRunning || !currentJob?.id) {
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
  }, [isRunning, currentJob?.id]);

  if (!daemon) return null;

  // Elapsed time for running job
  let elapsed = null;
  let estimated = null;
  let progressPct = 0;
  if (isRunning && currentJob && currentJob.started_at) {
    const now = Date.now() / 1000;
    elapsed = now - currentJob.started_at;
    estimated = currentJob.estimated_duration || null;
    if (estimated && estimated > 0) {
      progressPct = (elapsed / estimated) * 100;
    }
  }
  const isOverrun = estimated && progressPct > 100;

  const hp = latestHealth || {};

  const burstRegime = daemon.burst_regime || 'unknown';

  const pausedReasonLabel = {
    paused_health:      'Paused — system resources are too high to start new jobs',
    paused_manual:      'Paused manually — resume in Settings when ready',
    paused_interactive: 'Paused — waiting for active computer use to stop',
  }[state] || (daemon.paused_reason || state.replace('paused_', ''));

  return (
    <div ref={cardRef} class="t-frame" data-label="Currently Running" data-chroma="gustave"
      style={isStalled ? 'border-left: 3px solid var(--status-warning);' : ''}>
      {isRunning ? (
        <div class="flex flex-col gap-2">
          <div class="flex items-center justify-between flex-wrap gap-2">
            <div class="flex items-center gap-2">
              <StatusBadge state="running" />
              {currentJob && currentJob.source && (
                <span class="data-mono" style="font-size: var(--type-body); color: var(--text-primary);">
                  {currentJob.source}
                </span>
              )}
              {currentJob && currentJob.model && (
                <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary);">
                  {currentJob.model}
                </span>
              )}
              {/* Proxy call in progress for an eval session (current_job_id=-1 → no job row) */}
              {!currentJob && activeEval && (
                <>
                  <span class="data-mono" style="font-size: var(--type-body); color: var(--text-primary);">
                    eval #{activeEval.id}
                  </span>
                  {activeEval.judge_model && (
                    <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary);">
                      {activeEval.judge_model}
                    </span>
                  )}
                  <span class="data-mono" style="font-size: var(--type-label); color: var(--text-tertiary);">
                    {activeEval.status}
                  </span>
                </>
              )}
              {/* What it shows: Expandable stall resolution panel when the stall detector flags a frozen job.
               *  Decision it drives: Gives the user a concrete 4-step checklist so they know exactly
               *    what to do next — wait, cancel, inspect, or restart — without leaving the dashboard. */}
              {isStalled && (
                <details style="display:inline-block;position:relative;">
                  <summary style="cursor:pointer;font-size:var(--type-label);color:var(--status-warning);background:var(--status-warning-subtle);padding:2px 8px;border-radius:3px;border:1px solid var(--status-warning);list-style:none;display:inline-flex;align-items:center;gap:4px;">
                    ⚠ frozen — what should I do? ▾
                  </summary>
                  <div style="position:absolute;z-index:10;background:var(--bg-surface);border:1px solid var(--border-primary);border-radius:var(--radius);padding:12px;max-width:300px;font-size:var(--type-label);color:var(--text-secondary);box-shadow:var(--card-shadow-hover);margin-top:4px;left:0;">
                    <p style="margin:0 0 8px;font-weight:600;color:var(--status-warning);">Job is not producing output.</p>
                    <ol style="margin:0;padding-left:16px;display:flex;flex-direction:column;gap:4px;">
                      <li>Wait 2 more minutes — some models are slow to start</li>
                      <li>Cancel and retry — click × in the queue below</li>
                      <li>Check Ollama: run <code style="font-family:var(--font-mono);">ollama ps</code> to verify model is loaded</li>
                      <li>Restart daemon from Settings if Ollama itself is stuck</li>
                    </ol>
                  </div>
                </details>
              )}
              {/* Burst regime badge — shows traffic pattern detected by burst detector.
               *  steady=normal, burst=high-activity surge, trough=quiet window, unknown=no data yet.
               *  Helps decide whether to hold off submitting more work (burst) or pile it on (trough). */}
              <BurstBadge regime={burstRegime} />
            </div>
            <div class="flex items-center gap-2 flex-wrap">
              <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary);">
                {formatDuration(elapsed)}
                {estimated ? ` / ~${formatDuration(estimated)}` : ''}
              </span>
              {isOverrun && (
                <span style="font-size: var(--type-micro); color: var(--status-warning); background: var(--status-warning-subtle);
                             padding: 1px 5px; border-radius: 3px; border: 1px solid var(--border-warning);">
                  +{formatDuration(elapsed - estimated)} over estimate
                </span>
              )}
            </div>
          </div>
          {/* Progress bar */}
          <div title={isOverrun ? 'Over estimated time' : undefined}
               style="height: 4px; background: var(--bg-inset); border-radius: 2px; overflow: hidden;">
            <div style={{
              width: '100%',
              maxWidth: '100%',
              height: '100%',
              background: isOverrun ? '#f97316' : 'var(--accent)',
              borderRadius: '2px',
              transition: 'background 0.3s ease',
              ...(isOverrun ? {} : { width: `${progressPct}%`, transition: 'width 1s linear' }),
            }} />
          </div>
          {/* Compact resource gauges */}
          <ResourceGauges
            ram={hp.ram_pct}
            vram={hp.vram_pct}
            load={hp.load_avg}
            swap={hp.swap_pct}
            settings={settings}
          />
          {/* Collapsible live log tail — last 5 lines of job stdout, polled every 5s */}
          <details
            style="margin-top:4px;"
            open={logExpanded.value}
            onToggle={e => { logExpanded.value = e.currentTarget.open; }}
          >
            <summary style="font-family:var(--font-mono);font-size:var(--type-micro);color:var(--text-tertiary);cursor:pointer;user-select:none;list-style:none;">
              Output {logLines.value.length > 0 ? `(${logLines.value.length} lines)` : ''}
            </summary>
            <div style="margin-top:6px;padding:8px;background:var(--bg-terminal,var(--bg-inset));border-radius:var(--radius);font-family:var(--font-mono);font-size:var(--type-micro);color:var(--text-secondary);white-space:pre-wrap;word-break:break-all;max-height:120px;overflow-y:auto;">
              {logLines.value.length > 0
                ? logLines.value.map((line, i) => <div key={i}>{line}</div>)
                : <span style="color:var(--text-tertiary);">No output yet</span>
              }
            </div>
          </details>
        </div>
      ) : isPaused ? (
        <div class="flex items-center gap-3">
          <StatusBadge state={state} />
          <span style="color: var(--text-secondary); font-size: var(--type-body);">
            {pausedReasonLabel}
          </span>
        </div>
      ) : activeEval ? (
        /* Eval session running between proxy calls — daemon shows idle but Ollama is active.
         * Driven by: user knows eval is consuming GPU even when no queue job is running. */
        <div class="flex items-center gap-3 flex-wrap">
          <StatusBadge state="running" />
          <span class="data-mono" style="font-size: var(--type-body); color: var(--text-primary);">
            eval #{activeEval.id}
          </span>
          {activeEval.judge_model && (
            <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary);">
              {activeEval.judge_model}
            </span>
          )}
          <span class="data-mono" style="font-size: var(--type-label); color: var(--text-tertiary);">
            {activeEval.status}
          </span>
        </div>
      ) : (
        <EmptyState
          headline="Ready — nothing in queue"
          body="Jobs you submit will appear here."
          action={onSubmitRequest ? { label: '+ Submit a job', onClick: onSubmitRequest } : undefined}
        />
      )}
    </div>
  );
}

// What it shows: A small colored label for the current traffic burst regime:
//   burst (orange), trough (blue), steady (muted green), unknown (grey).
// Decision it drives: Quick visual cue — burst means the queue is under pressure,
//   trough means it's a good time to submit batch work.
const REGIME_STYLE = {
  burst:   { color: '#f97316', border: '#f97316', bg: 'rgba(249,115,22,0.1)' },
  trough:  { color: '#60a5fa', border: '#60a5fa', bg: 'rgba(96,165,250,0.1)' },
  steady:  { color: 'var(--status-healthy)', border: 'var(--status-healthy)', bg: 'rgba(74,222,128,0.08)' },
  unknown: { color: 'var(--text-tertiary)', border: 'var(--border-subtle)', bg: 'transparent' },
};

const REGIME_LABELS = {
  burst:   'burst — high traffic',
  trough:  'quiet — good time for batch work',
  steady:  'steady',
  unknown: 'unknown',
};

function BurstBadge({ regime }) {
  if (!regime || regime === 'unknown') return null;
  const style = REGIME_STYLE[regime] || REGIME_STYLE.unknown;
  return (
    <span style={{
      fontSize: 'var(--type-label)',
      color: style.color,
      background: style.bg,
      padding: '1px 6px',
      borderRadius: '3px',
      border: `1px solid ${style.border}`,
    }}>
      {REGIME_LABELS[regime] || regime}
    </span>
  );
}

