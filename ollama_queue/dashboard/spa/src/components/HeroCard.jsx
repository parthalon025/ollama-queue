import { h } from 'preact';
import TimeChart from './TimeChart.jsx';

/**
 * What it shows: A single KPI number in large type — jobs in 24h, average wait time, pause
 *   minutes, or 7-day success rate. Includes a sparkline trend and a plain-English delta line
 *   explaining what the number means (e.g. "3 jobs failed today").
 * Decision it drives: Is this metric healthy or does it need action? Orange border + text
 *   flags when a threshold has been crossed — e.g. success rate below 90% or long wait times.
 *   The delta line suggests what to do next (e.g. "lower thresholds in Settings").
 *
 * @param {Object} props
 * @param {*} props.value - Primary metric value
 * @param {string} props.label - Card label (shown via data-label)
 * @param {string} [props.unit] - Unit suffix
 * @param {string} [props.delta] - Delta/change text
 * @param {boolean} [props.warning] - Warning state (orange border + text)
 * @param {boolean} [props.loading] - Loading state (cursor-working)
 * @param {Array} [props.sparkData] - uPlot data array for sparkline [timestamps[], values[]]
 * @param {string} [props.sparkColor] - CSS color for sparkline (default: var(--accent))
 * @param {string} [props.tooltip] - Plain-English explanation shown on hover (ARIA "Explain like I'm 5")
 */
export default function HeroCard({ value, label, unit, delta, warning, loading, sparkData, sparkColor, tooltip }) {
  const cursorClass = loading ? 'cursor-working' : 'cursor-active';

  return (
    <div
      class={`t-frame ${cursorClass}`}
      data-label={label}
      style={warning ? 'border-left: 3px solid var(--status-warning);' : ''}
    >
      {/* Label row: metric name + optional plain-English tooltip icon */}
      <div class="flex items-center gap-1" style="margin-bottom: 4px;">
        <span
          style="font-size: var(--type-micro); color: var(--text-tertiary); font-family: var(--font-mono); text-transform: uppercase; letter-spacing: 0.05em;"
        >
          {label}
        </span>
        {tooltip && (
          <span
            aria-label={tooltip}
            title={tooltip}
            style="font-size: var(--type-micro); color: var(--text-tertiary); cursor: help; opacity: 0.6; line-height: 1;"
          >
            ?
          </span>
        )}
      </div>
      <div class="flex items-baseline gap-2" style="justify-content: space-between;">
        <div class="flex items-baseline gap-2">
          <span
            class="data-mono"
            style={`font-size: var(--type-hero); font-weight: 600; color: ${warning ? 'var(--status-warning)' : 'var(--accent)'}; line-height: 1;`}
          >
            {value ?? '\u2014'}
          </span>
          {unit && (
            <span
              class="data-mono"
              style="font-size: var(--type-headline); color: var(--text-tertiary);"
            >
              {unit}
            </span>
          )}
        </div>
        {sparkData && sparkData.length > 1 && sparkData[0].length > 1 && (
          <div style="width: 80px; height: 32px; flex-shrink: 0;">
            <TimeChart
              data={sparkData}
              series={[{ label: label || 'trend', color: sparkColor || 'var(--accent)', width: 1.5 }]}
              compact
            />
          </div>
        )}
      </div>
      {delta && (
        <div
          style="font-size: var(--type-label); color: var(--text-secondary); margin-top: 8px; font-family: var(--font-mono);"
        >
          {delta}
        </div>
      )}
    </div>
  );
}
