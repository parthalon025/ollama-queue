import { h } from 'preact';
import { useState } from 'preact/hooks';
import { EVAL_TRANSLATIONS } from './translations.js';
import GenerationInspector from './GenerationInspector.jsx';
// What it shows: A single scored eval pair in the run results table.
//   L1: config ID, item IDs, quality score, pass/fail.
//   L2: principle text, target title, score grid, override button.
//   L3: GenerationInspector — full scorer reasoning and queue job link.
// Decision it drives: User can review individual scores and override them
//   when the scorer was wrong, improving future run accuracy.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function ResultRow({ result }) {
  const [level, setLevel] = useState(1); // 1 | 2 | 3

  const {
    variant,
    source_item_id,
    target_item_id,
    score_transfer,
    score_precision,
    score_action,
    override_score_transfer,
    override_score_precision,
    override_score_action,
    is_same_cluster,
  } = result;

  // Effective scores (overrides win)
  const t = override_score_transfer ?? score_transfer ?? 0;
  const p = override_score_precision ?? score_precision ?? 0;
  const a = override_score_action ?? score_action ?? 0;

  // Quality = average of the three 1-5 scores, normalised to 0–1
  const quality = (t + p + a) > 0 ? ((t + p + a) / 15).toFixed(2) : null;
  const passed = quality !== null && parseFloat(quality) >= 0.6;

  function toggleLevel(next) {
    setLevel(level === next ? 1 : next);
  }

  return (
    <div class="eval-result-row">
      {/* L1 */}
      <div
        class="eval-run-row"
        style={{ cursor: 'pointer', userSelect: 'none' }}
        onClick={() => toggleLevel(2)}
        role="button"
        aria-expanded={level >= 2}
      >
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
          Config {variant}
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginLeft: '0.5rem' }}>
          #{source_item_id}→#{target_item_id}
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', marginLeft: '0.5rem' }}>
          {EVAL_TRANSLATIONS.f1.label}: {quality ?? '—'}
        </span>
        <span style={{
          marginLeft: '0.5rem',
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--type-label)',
          color: passed ? 'var(--status-healthy)' : 'var(--status-error)',
        }}>
          {passed ? '✓ pass' : '✗ fail'}
        </span>
        {is_same_cluster ? (
          <span style={{ marginLeft: '0.5rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
            [same cluster]
          </span>
        ) : null}
        <span style={{ marginLeft: 'auto', color: 'var(--text-tertiary)', fontSize: 'var(--type-label)' }}>
          {level >= 2 ? '▲' : '▼'}
        </span>
      </div>

      {/* L2 */}
      {level >= 2 && (
        <div class="eval-run-row-l2">
          {/* Principle text */}
          {result.principle && (
            <div style={{ marginBottom: '0.5rem' }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '2px' }}>
                Principle
              </div>
              <div style={{ color: 'var(--text-primary)', fontSize: 'var(--type-body)', lineHeight: '1.5' }}>
                {result.principle}
              </div>
            </div>
          )}

          {/* Score grid */}
          <table class="eval-metrics-table" style={{ marginBottom: '0.5rem' }}>
            <thead>
              <tr>
                {['recall', 'precision', 'actionability'].map(key => (
                  <th key={key} style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'center' }}>
                    {EVAL_TRANSLATIONS[key].label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                {[
                  override_score_transfer ?? score_transfer,
                  override_score_precision ?? score_precision,
                  override_score_action ?? score_action,
                ].map((score, idx) => (
                  <td key={idx} style={{ padding: '4px 8px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)' }}>
                    <span style={{ color: score >= 4 ? 'var(--status-healthy)' : score >= 3 ? 'var(--text-primary)' : 'var(--status-error)' }}>
                      {score ?? '—'}/5
                    </span>
                  </td>
                ))}
              </tr>
            </tbody>
          </table>

          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <button
              class="t-btn t-btn-secondary"
              style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
              onClick={() => toggleLevel(3)}
            >
              {level === 3 ? '▲ Hide inspector' : '▼ Show inspector'}
            </button>
          </div>
        </div>
      )}

      {/* L3 */}
      {level >= 3 && <GenerationInspector result={result} />}
    </div>
  );
}
