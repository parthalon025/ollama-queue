import { h } from 'preact';
import { useState } from 'preact/hooks';
import { EVAL_TRANSLATIONS } from './translations.js';
import ResultsTable from './ResultsTable.jsx';
// What it shows: A single eval run row with 3-level progressive disclosure.
//   L1: status dot, winner config, quality score, date, item count.
//   L2: per-variant metric table, scorer info, action buttons.
//   L3: paginated ResultsTable of scored pairs.
// Decision it drives: User sees run history at a glance, drills into
//   per-variant breakdown, and can re-run, compare, or export.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

function statusDot(status) {
  const colors = {
    complete: 'var(--status-healthy)',
    failed: 'var(--status-error)',
    cancelled: 'var(--status-waiting)',
    generating: 'var(--accent)',
    judging: 'var(--accent)',
    pending: 'var(--text-tertiary)',
  };
  return (
    <span style={{
      display: 'inline-block',
      width: '8px',
      height: '8px',
      borderRadius: '50%',
      background: colors[status] ?? 'var(--text-tertiary)',
      marginRight: '0.4rem',
      flexShrink: 0,
    }} />
  );
}

function formatDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch { return iso; }
}

function fmtPct(val) {
  if (val == null) return '—';
  return `${Math.round(val * 100)}%`;
}

export default function RunRow({ run }) {
  const [level, setLevel] = useState(1); // 1 | 2 | 3

  const {
    id,
    status,
    winner_variant,
    metrics,
    item_count,
    started_at,
    judge_model,
  } = run;

  // Parse metrics JSON if it came as string
  let parsedMetrics = {};
  try {
    parsedMetrics = typeof metrics === 'string' ? JSON.parse(metrics) : (metrics ?? {});
  } catch { parsedMetrics = {}; }

  // Winner quality (F1)
  const winnerMetrics = winner_variant ? parsedMetrics[winner_variant] : null;
  const winnerQuality = winnerMetrics?.f1 ?? null;

  const variantIds = Object.keys(parsedMetrics);

  // Count judge calls: sum of item_count across variants (approx)
  const judgeCallCount = item_count ?? 0;

  function toggleLevel(next) {
    setLevel(level === next ? 1 : next);
  }

  const [tooltip, setTooltip] = useState(null);

  function handleTooltip(key) {
    setTooltip(tooltip === key ? null : key);
  }

  return (
    <div class="eval-run-row-container" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
      {/* L1 */}
      <div
        class="eval-run-row"
        style={{ cursor: 'pointer', userSelect: 'none' }}
        onClick={() => toggleLevel(2)}
        role="button"
        aria-expanded={level >= 2}
      >
        <div style={{ display: 'flex', alignItems: 'center', flex: 1, gap: '0.5rem', flexWrap: 'wrap' }}>
          {statusDot(status)}
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', textTransform: 'lowercase' }}>
            {EVAL_TRANSLATIONS[status]?.label ?? status}
          </span>
          {winner_variant && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)' }}>
              Winner: Config {winner_variant}
            </span>
          )}
          {winnerQuality != null && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)' }}>
              {EVAL_TRANSLATIONS.f1.label}: {fmtPct(winnerQuality)}
            </span>
          )}
          {started_at && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
              {formatDate(started_at)}
            </span>
          )}
          {item_count != null && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
              · {item_count} items
            </span>
          )}
        </div>
        <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)', flexShrink: 0 }}>
          {level >= 2 ? '▲' : '▼'}
        </span>
      </div>

      {/* L2 */}
      {level >= 2 && (
        <div class="eval-run-row-l2">
          {/* Per-variant metric table */}
          {variantIds.length > 0 && (
            <div style={{ marginBottom: '0.75rem', overflowX: 'auto' }}>
              <table class="eval-metrics-table">
                <thead>
                  <tr>
                    <th style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'left' }}>
                      Config
                    </th>
                    {['f1', 'recall', 'precision', 'actionability'].map(metric => (
                      <th key={metric} style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'center', cursor: 'pointer' }}>
                        {EVAL_TRANSLATIONS[metric]?.label ?? metric}
                        {EVAL_TRANSLATIONS[metric]?.tooltip && (
                          <button
                            onClick={e => { e.stopPropagation(); handleTooltip(metric); }}
                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '0 2px', fontSize: '0.7rem' }}
                            aria-label={`Info about ${metric}`}
                          >
                            ?
                          </button>
                        )}
                      </th>
                    ))}
                  </tr>
                  {tooltip && EVAL_TRANSLATIONS[tooltip]?.tooltip && (
                    <tr>
                      <td colSpan="5" style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)', background: 'var(--bg-base)' }}>
                        {EVAL_TRANSLATIONS[tooltip].tooltip}
                      </td>
                    </tr>
                  )}
                </thead>
                <tbody>
                  {variantIds.map(vid => {
                    const vm = parsedMetrics[vid] ?? {};
                    const isWinner = vid === winner_variant;
                    return (
                      <tr key={vid} style={{ background: isWinner ? 'var(--accent-glow)' : 'transparent' }}>
                        <td style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: isWinner ? 'var(--accent)' : 'var(--text-primary)' }}>
                          {isWinner && '★ '}Config {vid}
                        </td>
                        {['f1', 'recall', 'precision', 'actionability'].map(metric => (
                          <td key={metric} style={{ padding: '4px 8px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                            {vm[metric] != null ? fmtPct(vm[metric]) : '—'}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Scorer info */}
          {judge_model && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
              Scorer: {judge_model} · {judgeCallCount} items
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
            {winner_variant && (
              <button class="t-btn t-btn-primary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}>
                Use this config
              </button>
            )}
            <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}>
              Score again
            </button>
            <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}>
              Export
            </button>
            <button
              class="t-btn t-btn-secondary"
              style={{ fontSize: 'var(--type-label)', padding: '3px 10px', marginLeft: 'auto' }}
              onClick={() => toggleLevel(3)}
            >
              {level === 3 ? '▲ Hide scored pairs' : `▼ Show scored pairs (${item_count ?? '?'})`}
            </button>
          </div>
        </div>
      )}

      {/* L3 */}
      {level >= 3 && <ResultsTable runId={id} />}
    </div>
  );
}
