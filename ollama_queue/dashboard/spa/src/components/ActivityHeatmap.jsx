import { h } from 'preact';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

/**
 * What it shows: 7 days × 24 hours of GPU activity. Each cell's brightness represents
 *   how many minutes the GPU was busy in that slot. Hover for the exact GPU-minutes.
 *   Brighter = heavier use. Max value shown bottom-right for scale reference.
 * Decision it drives: When does the queue run heaviest? Are there recurring spikes that
 *   suggest overloading at certain times? Useful for deciding where NOT to schedule new
 *   heavy jobs, and for spotting runaway overnight jobs.
 *
 * @param {{ data: Array<{ dow: string, hour: string, gpu_minutes: number }> }} props
 *   dow: strftime('%w') → '0'=Sun through '6'=Sat
 *   hour: strftime('%H') → '00' through '23'
 */
export default function ActivityHeatmap({ data }) {
  const items = data || [];

  if (items.length === 0) {
    return (
      <div class="t-frame" data-label="Activity">
        <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center;">
          No activity data yet
        </p>
      </div>
    );
  }

  // Build lookup and find max for normalization
  const lookup = {};
  let maxMinutes = 0;
  for (const row of items) {
    const key = `${row.dow}-${parseInt(row.hour, 10)}`;
    const val = row.gpu_minutes || 0;
    lookup[key] = val;
    if (val > maxMinutes) maxMinutes = val;
  }

  return (
    <div class="t-frame" data-label="Activity">
      {/* Hour labels */}
      <div style="display: grid; grid-template-columns: 32px repeat(24, 1fr); gap: 1px; margin-bottom: 2px;">
        <div />
        {HOURS.map((hr) => (
          <div key={hr} class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); text-align: center;">
            {hr % 6 === 0 ? hr : ''}
          </div>
        ))}
      </div>
      {/* Grid rows: reorder to Mon-Sun (indices 1,2,3,4,5,6,0) */}
      {[1, 2, 3, 4, 5, 6, 0].map((dow) => (
        <div key={dow} style="display: grid; grid-template-columns: 32px repeat(24, 1fr); gap: 1px;">
          <div class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); line-height: 14px;">
            {DAYS[dow]}
          </div>
          {HOURS.map((hr) => {
            const val = lookup[`${dow}-${hr}`] || 0;
            const opacity = maxMinutes > 0 ? Math.max(0.05, val / maxMinutes) : 0.05;
            return (
              <div
                key={hr}
                title={`${DAYS[dow]} ${hr}:00 — ${val.toFixed(1)} GPU min`}
                style={{
                  height: '14px',
                  background: `var(--accent)`,
                  opacity: val > 0 ? opacity : 0.05,
                  borderRadius: '1px',
                }}
              />
            );
          })}
        </div>
      ))}
      <div style="display: flex; align-items: center; gap: 8px; margin-top: 8px; justify-content: flex-end;">
        <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary);">less</span>
        {[0.1, 0.3, 0.5, 0.7, 1.0].map(opVal => (
          <div key={opVal} style={{
            width: 12, height: 12, borderRadius: 2,
            background: 'var(--accent)', opacity: opVal,
          }} />
        ))}
        <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary);">more</span>
        <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); margin-left: 8px;">
          max: {maxMinutes.toFixed(0)} GPU min
        </span>
      </div>
    </div>
  );
}
