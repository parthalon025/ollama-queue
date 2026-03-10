import { h } from 'preact';
// What it shows: A single-line status bar summarising whether the eval system is getting
//   better, stable, or regressing across all variants.
// Decision it drives: Quick answer to "is the system improving?" — lets users decide
//   whether to investigate further or act immediately on a regression.

import { evalTrends } from '../../stores';

// Map API trend_direction values to plain-language labels and colours
const TREND_META = {
  improving:  { label: 'Getting better', color: 'var(--status-healthy)' },
  stable:     { label: 'Stable',         color: 'var(--status-warning)' },
  regressing: { label: 'Regressing',     color: 'var(--status-error)'   },
};

export default function TrendSummaryBar() {
  // Read .value at top of body so Preact tracks this signal subscription
  const trends = evalTrends.value;

  if (!trends) {
    return (
      <div class="eval-trend-summary-bar eval-trend-summary-bar--empty">
        No runs yet — run an eval to see trends.
      </div>
    );
  }

  const direction = trends.trend_direction || 'stable';
  const meta = TREND_META[direction] || TREND_META.stable;

  return (
    <div class="eval-trend-summary-bar" style={`--dot-color: ${meta.color}`}>
      <span class="eval-status-dot" aria-hidden="true" />
      <span class="eval-trend-summary-bar__label">{meta.label}</span>
    </div>
  );
}
