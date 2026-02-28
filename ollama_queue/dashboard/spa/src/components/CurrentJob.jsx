import { h } from 'preact';
import StatusBadge from './StatusBadge.jsx';
import ResourceGauges from './ResourceGauges.jsx';

/**
 * Shows current running job or daemon idle/paused status.
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
      progressPct = Math.min(100, (elapsed / estimated) * 100);
    }
  }

  const hp = latestHealth || {};

  return (
    <div class="t-frame" data-label="Current">
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
            </div>
            <div class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary);">
              {formatDuration(elapsed)}
              {estimated ? ` / ~${formatDuration(estimated)}` : ''}
            </div>
          </div>
          {/* Progress bar */}
          <div style="height: 4px; background: var(--bg-inset); border-radius: 2px; overflow: hidden;">
            <div style={{
              width: `${progressPct}%`,
              height: '100%',
              background: 'var(--accent)',
              borderRadius: '2px',
              transition: 'width 1s linear',
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
