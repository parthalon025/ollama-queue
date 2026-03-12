import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

// What it shows: 7-day × 24-hour GPU activity heatmap. Hovering any cell shows a tooltip
//   with the exact day/hour label and GPU utilization in minutes.
// Decision it drives: Identify the busiest hours of the day and whether there's a weekly
//   pattern to GPU load — used to pick optimal scheduling windows.

function formatCellLabel(dayIdx, hourIdx) {
  return `${DAYS[dayIdx]} ${String(hourIdx).padStart(2, '0')}:00`;
}

/**
 * @param {{ data: Array<{ dow: string, hour: string, gpu_minutes: number }> }} props
 *   dow: strftime('%w') → '0'=Sun through '6'=Sat
 *   hour: strftime('%H') → '00' through '23'
 */
export default function ActivityHeatmap({ data }) {
  const [tooltip, setTooltip] = useState(null); // { x, y, label, value }
  const items = data || [];

  useEffect(() => {
    if (!tooltip) return;
    const clear = () => setTooltip(null);
    window.addEventListener('scroll', clear, { passive: true, capture: true });
    window.addEventListener('resize', clear, { passive: true });
    return () => {
      window.removeEventListener('scroll', clear, { capture: true });
      window.removeEventListener('resize', clear);
    };
  }, [tooltip]);

  if (items.length === 0) {
    return (
      <div class="t-frame" data-label="GPU Activity by Time of Day">
        <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center;">
          No activity yet — run some jobs and this chart will fill in over time
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
    <div class="t-frame" data-label="GPU Activity by Time of Day">
      <p style="font-size: var(--type-micro); color: var(--text-tertiary); margin: 0 0 6px; font-family: var(--font-mono);">
        Each cell shows how many minutes the GPU was busy in that hour. Brighter = more work.
      </p>
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
            const val = lookup[`${dow}-${hr}`];  // undefined if no data row exists
            const isEmpty = val === undefined || val === null;
            const minutes = isEmpty ? 0 : val;
            const opacity = maxMinutes > 0 ? Math.max(0.05, minutes / maxMinutes) : 0.05;
            return (
              <div
                key={hr}
                style={{
                  height: '14px',
                  background: `var(--accent)`,
                  opacity: minutes > 0 ? opacity : 0.05,
                  borderRadius: '1px',
                }}
                onMouseEnter={e => {
                  const TOOLTIP_W = 160;
                  const x = e.clientX + 12 + TOOLTIP_W > window.innerWidth
                    ? e.clientX - TOOLTIP_W - 4
                    : e.clientX + 12;
                  const y = Math.max(8, e.clientY - 40);
                  setTooltip({
                    x,
                    y,
                    label: formatCellLabel(dow, hr),
                    value: isEmpty
                      ? 'No data'
                      : minutes === 0
                        ? '0 GPU-minutes (no active work)'
                        : `${minutes.toFixed(1)} GPU-minutes`,
                  });
                }}
                onMouseLeave={() => setTooltip(null)}
              />
            );
          })}
        </div>
      ))}
      <div style="display: flex; align-items: center; gap: 8px; margin-top: 8px; justify-content: flex-end;">
        <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary);">less active</span>
        {[0.1, 0.3, 0.5, 0.7, 1.0].map(opVal => (
          <div key={opVal} style={{
            width: 12, height: 12, borderRadius: 2,
            background: 'var(--accent)', opacity: opVal,
          }} />
        ))}
        <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary);">more active</span>
        <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); margin-left: 8px;">
          busiest: {maxMinutes.toFixed(0)} GPU-minutes/hour
        </span>
      </div>
      {tooltip && (
        <div style={{
          position: 'fixed',
          left: `${tooltip.x}px`,
          top: `${tooltip.y}px`,
          zIndex: 200,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-primary)',
          borderRadius: 'var(--radius)',
          padding: '6px 10px',
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--type-micro)',
          color: 'var(--text-secondary)',
          pointerEvents: 'none',
          boxShadow: 'var(--card-shadow-hover)',
          whiteSpace: 'nowrap',
        }}>
          <div style={{ color: 'var(--text-primary)' }}>{tooltip.label}</div>
          <div>{tooltip.value}</div>
        </div>
      )}
    </div>
  );
}
