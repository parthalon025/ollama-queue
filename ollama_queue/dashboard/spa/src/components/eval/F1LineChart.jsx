import { h } from 'preact';
import { useRef, useEffect } from 'preact/hooks';
import uPlot from 'uplot';
// What it shows: Quality score over time for each eval variant as a line chart.
//   Detects judge_mode from trend data: plots AUC for bayesian/tournament runs,
//   F1 for legacy (rubric/binary) runs.
// Decision it drives: User sees which variant is improving and at what rate,
//   helping them decide which config to promote or investigate.

import { evalTrends } from '../../store.js';

// Stable palette for variant lines — falls back to accent for any beyond 5
const VARIANT_COLORS = [
  'var(--accent)',
  'var(--status-healthy)',
  'var(--status-warning)',
  'var(--status-error)',
  'var(--text-secondary)',
];

function resolveColor(varStr) {
  if (!varStr.startsWith('var(')) return varStr;
  return getComputedStyle(document.documentElement)
    .getPropertyValue(varStr.slice(4, -1))
    .trim();
}

// Inner chart component — only rendered when we have data
function ChartCanvas({ variants, itemSetsDiffer, metricKey, yAxisLabel }) {
  const containerRef = useRef(null);
  const chartRef     = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !variants || variants.length === 0) return;

    const styles     = getComputedStyle(document.documentElement);
    const textColor  = styles.getPropertyValue('--text-tertiary').trim();
    const gridColor  = styles.getPropertyValue('--border-subtle').trim();
    const fontMono   = styles.getPropertyValue('--font-mono').trim() || 'monospace';

    // Build uPlot data arrays: [xValues, ...ySeriesArrays]
    // X-axis: run dates (unix timestamps) or item_count if item sets differ
    const allTimestamps = new Set();
    variants.forEach(vari => {
      (vari.runs || []).forEach(run => allTimestamps.add(run.timestamp));
    });
    const xValues = Array.from(allTimestamps).sort((a, b) => a - b);

    const seriesData = variants.map(vari => {
      const runMap = {};
      (vari.runs || []).forEach(run => { runMap[run.timestamp] = run[metricKey]; });
      return xValues.map(ts => runMap[ts] ?? null);
    });

    const data = [xValues, ...seriesData];

    const seriesOpts = variants.map((vari, idx) => {
      const raw = VARIANT_COLORS[idx % VARIANT_COLORS.length];
      const resolved = resolveColor(raw);
      return {
        label: vari.id,
        stroke: resolved,
        width: 2,
        fill: resolved + '15',
        spanGaps: false,
      };
    });

    if (chartRef.current) {
      chartRef.current.destroy();
    }

    const opts = {
      width:  containerRef.current.clientWidth,
      height: 200,
      cursor: { show: true, drag: { x: false, y: false } },
      legend: { show: true },
      scales: {
        x: { time: !itemSetsDiffer },
        y: {
          range: (_u, dataMin, dataMax) => {
            if (dataMin === null || dataMax === null) return [0, 1];
            const pad = (dataMax - dataMin) * 0.1 || 0.05;
            return [Math.max(0, dataMin - pad), Math.min(1, dataMax + pad)];
          },
        },
      },
      axes: [
        {
          stroke: textColor,
          grid:   { stroke: gridColor, width: 1 },
          font:   `10px ${fontMono}`,
          ticks:  { stroke: gridColor, width: 1 },
          label:  itemSetsDiffer ? 'Lessons Tested' : 'Run date',
        },
        {
          stroke: textColor,
          grid:   { stroke: gridColor, width: 1 },
          font:   `10px ${fontMono}`,
          ticks:  { stroke: gridColor, width: 1 },
          size:   50,
          label:  yAxisLabel,
          values: (_u, vals) => vals.map(v => v == null ? '' : (v * 100).toFixed(0) + '%'),
        },
      ],
      series: [
        {}, // x axis placeholder
        ...seriesOpts,
      ],
    };

    chartRef.current = new uPlot(opts, data, containerRef.current);

    return () => {
      if (chartRef.current) { chartRef.current.destroy(); chartRef.current = null; }
    };
  }, [variants, itemSetsDiffer, metricKey, yAxisLabel]);

  // Resize observer keeps the chart filling its container
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(() => {
      if (chartRef.current && containerRef.current) {
        chartRef.current.setSize({ width: containerRef.current.clientWidth, height: 200 });
      }
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  return <div ref={containerRef} role="img" aria-label={`${yAxisLabel} over time per variant`} />;
}

export default function F1LineChart() {
  // Read .value at top of body to subscribe to signal changes
  const trends = evalTrends.value;

  if (!trends || !trends.variants || trends.variants.length === 0) {
    return (
      <div class="t-frame eval-f1-chart-empty" data-label="Quality Score Over Time">
        Complete at least 2 evaluation runs to see whether quality is improving, staying the same, or declining over time.
      </div>
    );
  }

  const itemSetsDiffer = !!trends.item_sets_differ;

  // Detect whether any variant's runs use bayesian/tournament judge_mode.
  // If so, plot AUC instead of F1.
  const hasBayesian = (trends.variants || []).some(vari =>
    (vari.runs || []).some(entry => entry.judge_mode === 'bayesian' || entry.judge_mode === 'tournament')
  );
  const metricKey = hasBayesian ? 'auc' : 'f1';
  const yAxisLabel = hasBayesian
    ? 'Discrimination Score (AUC, 0–100%)'
    : 'Quality Score (0–100%, higher is better)';

  return (
    <div class="t-frame eval-f1-chart" data-label="Quality Score Over Time">
      {itemSetsDiffer && (
        <div class="eval-f1-chart__warning t-callout" style="margin-bottom: 12px; padding: 8px 12px;">
          ⚠ The set of lessons tested changed between runs — score differences may reflect different data, not a real improvement in quality.
        </div>
      )}
      <ChartCanvas variants={trends.variants} itemSetsDiffer={itemSetsDiffer} metricKey={metricKey} yAxisLabel={yAxisLabel} />
      <div class="eval-f1-chart__legend" aria-hidden="true">
        {/* uPlot renders its own legend above the chart */}
      </div>
    </div>
  );
}
