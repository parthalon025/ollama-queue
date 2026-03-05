import { h } from 'preact';
import { useState } from 'preact/hooks';
// What it shows: Per-variant stability and trend data in an expandable table.
// Decision it drives: Which variant to promote or investigate based on trend
//   direction, quality delta, and historical run-by-run scores.

import { evalTrends } from '../../store.js';

// Direction indicator: dot colour + text
const DIRECTION_META = {
  improving:  { symbol: '●', label: 'Getting better', color: 'var(--status-healthy)' },
  stable:     { symbol: '●', label: 'Stable',         color: 'var(--status-warning)' },
  regressing: { symbol: '●', label: 'Regressing',     color: 'var(--status-error)'   },
};

// Single variant row with expand/collapse
function StabilityRow({ vari }) {
  const [open, setOpen] = useState(false);

  const runs      = vari.runs || [];
  const lastRun   = runs[runs.length - 1];
  const prevRun   = runs[runs.length - 2];
  const f1Pct     = lastRun ? Math.round((lastRun.f1 || 0) * 100) : null;
  const delta     = (lastRun && prevRun)
    ? Math.round(((lastRun.f1 || 0) - (prevRun.f1 || 0)) * 100)
    : null;
  const direction = vari.trend_direction || 'stable';
  const meta      = DIRECTION_META[direction] || DIRECTION_META.stable;

  // L1 summary line: "E  Quality: 79%  +11% from last  ● Getting better  [▼]"
  const deltaStr = delta === null ? '' : (delta >= 0 ? `+${delta}%` : `${delta}%`) + ' from last';

  return (
    <div class={`eval-stability-row${open ? ' eval-stability-row--open' : ''}`}>
      {/* L1 */}
      <button
        type="button"
        class="eval-stability-row__header"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-label={`${open ? 'Collapse' : 'Expand'} variant ${vari.id} stability details`}
      >
        <span class="eval-stability-row__id data-mono">{vari.id}</span>
        {f1Pct !== null && (
          <span class="eval-stability-row__quality">Quality: {f1Pct}%</span>
        )}
        {deltaStr && (
          <span class="eval-stability-row__delta data-mono">{deltaStr}</span>
        )}
        <span class="eval-stability-row__direction" style={`color: ${meta.color}`}>
          {meta.symbol} {meta.label}
        </span>
        <span class="eval-stability-row__toggle" aria-hidden="true">{open ? '▲' : '▼'}</span>
      </button>

      {/* L2 — run-by-run history */}
      {open && (
        <div class="eval-stability-row__detail">
          {runs.length > 0 ? (
            <>
              <p class="eval-stability-row__run-history">
                <span class="t-bracket">Run scores</span>{' '}
                <span class="data-mono">
                  {runs.map(r => Math.round((r.f1 || 0) * 100) + '%').join(' → ')}
                </span>
              </p>
              <p class="eval-stability-row__items">
                <span class="t-bracket">Lessons tested</span>{' '}
                <span class="data-mono">
                  {runs.map(r => r.item_count ?? '—').join(' → ')}
                </span>
              </p>
              {vari.judge_reliability != null && (
                <p class="eval-stability-row__judge">
                  <span class="t-bracket">Scorer reliability</span>{' '}
                  <span class="data-mono">{Math.round(vari.judge_reliability * 100)}%</span>
                </p>
              )}
            </>
          ) : (
            <p class="eval-stability-row__empty">No completed runs for this variant.</p>
          )}
          <p class="eval-stability-row__l3-hint" style="color: var(--text-tertiary); font-size: var(--type-label); margin-top: 8px;">
            Per-cluster performance coming in a future update.
          </p>
        </div>
      )}
    </div>
  );
}

export default function VariantStabilityTable() {
  // Read .value at top to subscribe to signal changes
  const trends = evalTrends.value;

  if (!trends || !trends.variants || trends.variants.length === 0) {
    return null;
  }

  return (
    <div class="t-frame eval-stability-table" data-label="Variant stability">
      {trends.variants.map(vari => (
        <StabilityRow key={vari.id} vari={vari} />
      ))}
    </div>
  );
}
