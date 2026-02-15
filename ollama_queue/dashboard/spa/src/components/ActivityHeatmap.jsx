import { h } from 'preact';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

/**
 * 7-row × 24-column CSS grid heatmap of GPU activity.
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
      <p class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); margin-top: 8px; text-align: center;">
        Darker = more GPU time in that hour
      </p>
    </div>
  );
}
