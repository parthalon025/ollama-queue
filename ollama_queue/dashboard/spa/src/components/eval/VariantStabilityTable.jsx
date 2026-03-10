import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
// What it shows: Per-variant stability and trend data in an expandable table.
//   L2 now includes cross-run F1 stdev and stable/unstable badge from the
//   /api/eval/variants/stability endpoint.
// Decision it drives: Which variant to promote or investigate based on trend
//   direction, quality delta, historical run-by-run scores, and cross-run consistency.

import { evalTrends, evalStability, fetchVariantStability } from '../../stores';

// Direction indicator: dot colour + text
const DIRECTION_META = {
  improving:  { symbol: '\u25CF', label: 'Getting better', color: 'var(--status-healthy)' },
  stable:     { symbol: '\u25CF', label: 'Stable',         color: 'var(--status-warning)' },
  regressing: { symbol: '\u25CF', label: 'Regressing',     color: 'var(--status-error)'   },
};

// Single variant row with expand/collapse
function StabilityRow({ vari }) {
  const [open, setOpen] = useState(false);

  const stability = evalStability.value?.[vari.id];

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
        aria-label={`${open ? 'Collapse' : 'Expand'} stability history for configuration ${vari.id}`}
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
        <span class="eval-stability-row__toggle" aria-hidden="true">{open ? '\u25B2' : '\u25BC'}</span>
      </button>

      {/* L2 — run-by-run history */}
      {open && (
        <div class="eval-stability-row__detail">
          {runs.length > 0 ? (
            <>
              <p class="eval-stability-row__run-history">
                <span class="t-bracket">Quality scores, run by run</span>{' '}
                <span class="data-mono">
                  {runs.map(runItem => Math.round((runItem.f1 || 0) * 100) + '%').join(' \u2192 ')}
                </span>
              </p>
              <p class="eval-stability-row__items">
                <span class="t-bracket">Lessons tested</span>{' '}
                <span class="data-mono">
                  {runs.map(runItem => runItem.item_count ?? '\u2014').join(' \u2192 ')}
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
          {/* Cross-run stability from /api/eval/variants/stability */}
          {stability && (
            <div style={{ display: 'flex', gap: '1rem', marginTop: '0.3rem', fontSize: 'var(--type-label)', flexWrap: 'wrap' }}>
              <span>Stdev: <strong style={{ fontFamily: 'var(--font-mono)' }}>{(stability.stdev * 100).toFixed(1)}%</strong></span>
              <span>Runs: <strong style={{ fontFamily: 'var(--font-mono)' }}>{stability.n_runs}</strong></span>
              <span style={{
                padding: '1px 6px',
                borderRadius: '3px',
                background: stability.stable ? 'rgba(34,197,94,0.15)' : 'rgba(239,68,68,0.15)',
                color: stability.stable ? 'var(--status-healthy)' : 'var(--status-error)',
              }}>
                {stability.stable ? '\u2713 Stable' : '\u2717 Unstable'}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function VariantStabilityTable() {
  // Read .value at top to subscribe to signal changes
  const trends = evalTrends.value;

  useEffect(() => {
    fetchVariantStability();
  }, []);

  if (!trends || !trends.variants || trends.variants.length === 0) {
    return null;
  }

  return (
    <div class="t-frame eval-stability-table" data-label="How Consistent Is Each Configuration?">
      {trends.variants.map(vari => (
        <StabilityRow key={vari.id} vari={vari} />
      ))}
    </div>
  );
}
