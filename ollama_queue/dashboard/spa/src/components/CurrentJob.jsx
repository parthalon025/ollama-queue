import { h } from 'preact';
import StatusBadge from './StatusBadge.jsx';
import ResourceGauges from './ResourceGauges.jsx';

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
export default function CurrentJob({ daemon, currentJob, latestHealth, settings, activeEval }) {
  if (!daemon) return null;

  const state = daemon.state || 'idle';
  const isPaused = state.startsWith('paused');
  const isRunning = state === 'running';

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
  const isStalled = isRunning && currentJob && !!currentJob.stall_detected_at;
  const burstRegime = daemon.burst_regime || 'unknown';

  const pausedReasonLabel = {
    paused_health:      'Paused — system resources are too high to start new jobs',
    paused_manual:      'Paused manually — resume in Settings when ready',
    paused_interactive: 'Paused — waiting for active computer use to stop',
  }[state] || (daemon.paused_reason || state.replace('paused_', ''));

  return (
    <div class="t-frame" data-label="Currently Running"
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
              {isStalled && (
                <span
                  title="This job appears to be frozen — not producing output or making progress"
                  style="font-size: var(--type-label); color: var(--status-warning); background: var(--status-warning-subtle);
                             padding: 1px 6px; border-radius: 3px; border: 1px solid var(--status-warning);">
                  ⚠ frozen
                </span>
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
        <div class="flex items-center gap-3">
          <StatusBadge state="idle" />
          <span style="color: var(--text-secondary); font-size: var(--type-body);">Ready — waiting for jobs to run</span>
        </div>
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

function formatDuration(seconds) {
  if (seconds === null || seconds < 0) return '--';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
