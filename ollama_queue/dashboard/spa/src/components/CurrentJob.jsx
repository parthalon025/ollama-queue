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
 * @param {{ daemon: object, currentJob: object|null, latestHealth: object|null, settings: object }} props
 *   daemon: daemon_state row (state, current_job_id, paused_reason, ...)
 *   currentJob: the running job row if any (source, model, started_at, estimated_duration, ...)
 *   latestHealth: most recent health_log row (ram_pct, vram_pct, load_avg, swap_pct)
 *   settings: threshold settings for resource gauges
 */
export default function CurrentJob({ daemon, currentJob, latestHealth, settings }) {
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

  return (
    <div class="t-frame" data-label="Current"
      style={isStalled ? 'border-left: 3px solid #f97316;' : ''}>
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
              {isStalled && (
                <span style="font-size: var(--type-label); color: #f97316; background: rgba(249,115,22,0.1);
                             padding: 1px 6px; border-radius: 3px; border: 1px solid #f97316;">
                  ⚠ stalled
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
                <span style="font-size: var(--type-micro); color: #f97316; background: rgba(249,115,22,0.1);
                             padding: 1px 5px; border-radius: 3px; border: 1px solid rgba(249,115,22,0.3);">
                  +{formatDuration(elapsed - estimated)} over
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
            {daemon.paused_reason || state.replace('paused_', '')}
          </span>
        </div>
      ) : (
        <div class="flex items-center gap-3">
          <StatusBadge state="idle" />
          <span style="color: var(--text-secondary); font-size: var(--type-body);">Idle</span>
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
      {regime}
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
