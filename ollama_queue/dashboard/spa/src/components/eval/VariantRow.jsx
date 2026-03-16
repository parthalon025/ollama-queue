import { useState, useEffect } from 'preact/hooks';
import { API, fetchEvalVariants } from '../../stores';
import { EVAL_TRANSLATIONS } from './translations.js';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import ModelChip from '../ModelChip.jsx';
// What it shows: A single eval variant config with 3-level progressive disclosure.
//   L1: ★ badge, variant ID/label, model, recommended/production badges, latest quality.
//   L2: model, creativity, memory window, template, quality sparkline, edit/clone buttons.
//   L3: Run history table for this variant (past F1 scores).
// Decision it drives: User sees which config is recommended, can clone system
//   configs to customize them, and tracks per-variant quality over time.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

// What it shows: Promotion lineage for production/recommended variants — which run promoted
//   this variant, the F1 improvement over the prior variant, and how many lessons were tested.
// Decision it drives: Lets the user understand why a variant is production without drilling
//   into run history. Gracefully shows nothing if the /lineage endpoint doesn't exist yet.
function LineageTip({ variantId }) {
  const [lineage, setLineage] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/eval/variants/${variantId}/lineage`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { setLineage(data); setLoading(false); })
      .catch(() => { setLoading(false); });
  }, [variantId]);

  if (loading) return <span class="lineage-tip__loading">…</span>;
  if (!lineage) return <span class="lineage-tip__empty">No lineage</span>;

  return (
    <div class="lineage-tip">
      {lineage.run_id && <span>Run #{lineage.run_id}</span>}
      {lineage.f1_delta != null && <span> · +{lineage.f1_delta.toFixed(2)} F1</span>}
      {lineage.comparison_variant_id && <span> over variant-{lineage.comparison_variant_id}</span>}
      {lineage.lessons_tested != null && <span> · {lineage.lessons_tested} lessons</span>}
      {lineage.run_date && <span> · {new Date(lineage.run_date * 1000).toLocaleDateString()}</span>}
    </div>
  );
}

function getTemplateLabelFromId(templateId) {
  return EVAL_TRANSLATIONS[templateId]?.label ?? templateId;
}

export default function VariantRow({ variant }) {
  const [deleteFb, deleteAct] = useActionFeedback();

  const [level, setLevel] = useState(1); // 1 | 2 | 3
  const [cloning, setCloning] = useState(false);
  const [cloneError, setCloneError] = useState(null);
  const [history, setHistory] = useState(null);
  const [historyError, setHistoryError] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(false);

  const {
    id,
    label,
    model,
    temperature,
    num_ctx,
    prompt_template_id,
    is_recommended,
    is_production,
    is_system,
    latest_f1,
  } = variant;

  function toggleLevel(next) {
    setLevel(level === next ? 1 : next);
    if (next === 3 && !history) loadHistory();
  }

  async function loadHistory() {
    setHistoryError(null);
    try {
      const res = await fetch(`${API}/eval/variants/${encodeURIComponent(id)}/history`);
      if (!res.ok) { setHistoryError(`Failed to load history (HTTP ${res.status})`); return; }
      setHistory(await res.json());
    } catch (e) {
      setHistoryError(`Failed to load history: ${e.message}`);
    }
  }

  async function handleClone() {
    setCloning(true);
    setCloneError(null);
    try {
      const res = await fetch(`${API}/eval/variants/${encodeURIComponent(id)}/clone`, { method: 'POST' });
      if (!res.ok) {
        let detail = `Clone failed: ${res.status}`;
        try { const body = await res.json(); if (body.detail) detail = body.detail; } catch { /* non-JSON */ }
        throw new Error(detail);
      }
      await fetchEvalVariants();
    } catch (err) {
      setCloneError(err.message);
    } finally {
      setCloning(false);
    }
  }

  async function handleDelete(evt) {
    evt.stopPropagation();
    await deleteAct(
      'DELETING',
      async () => {
        const res = await fetch(`${API}/eval/variants/${id}`, { method: 'DELETE' });
        if (!res.ok) {
          let detail = `Delete failed: ${res.status}`;
          try { const body = await res.json(); if (body.detail) detail = body.detail; } catch { /* non-JSON */ }
          throw new Error(detail);
        }
        await fetchEvalVariants();
      },
      'DELETED'
    );
  }

  return (
    <div style={{ borderBottom: '1px solid var(--border-subtle)' }}>
      {/* L1 */}
      <div
        class="eval-run-row"
        style={{ cursor: 'pointer', userSelect: 'none' }}
        onClick={() => toggleLevel(2)}
        role="button"
        aria-expanded={level >= 2}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flex: 1, flexWrap: 'wrap' }}>
          {(is_recommended || is_production) ? <span class="eval-badge eval-badge-recommended">★</span> : null}
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)', fontWeight: 600 }}>
            {id}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
            {label}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>·</span>
          <ModelChip model={model} />
          {is_production ? <span class="eval-badge eval-badge-production">★ Production</span> : null}
          {is_recommended && !is_production ? <span class="eval-badge eval-badge-recommended">★ Recommended</span> : null}
          {(is_production || is_recommended) && (
            <span class="variant-lineage-trigger" title="Promotion history">
              ⓘ <LineageTip variantId={id} />
            </span>
          )}
          {latest_f1 != null && (
            <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
              {EVAL_TRANSLATIONS.f1.label}: {Math.round(latest_f1 * 100)}%
            </span>
          )}
        </div>
        <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)', flexShrink: 0, marginLeft: '0.5rem' }}>
          {level >= 2 ? '▲' : '▼'}
        </span>
      </div>

      {/* L2 */}
      {level >= 2 && (
        <div class="eval-variant-row-l2">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem', marginBottom: '0.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                Model:
              </span>
              <ModelChip model={model} />
            </div>
            <div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                {EVAL_TRANSLATIONS.temperature.label}:
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)', marginLeft: '0.4rem' }}>
                {temperature}
              </span>
            </div>
            <div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                {EVAL_TRANSLATIONS.num_ctx.label}:
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)', marginLeft: '0.4rem' }}>
                {(num_ctx ?? 0).toLocaleString()} tokens
              </span>
            </div>
            <div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                How:
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)', marginLeft: '0.4rem' }}>
                {getTemplateLabelFromId(prompt_template_id)}
              </span>
            </div>
          </div>

          {/* Best quality score from latest_f1 prop */}
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
            {latest_f1 != null
              ? `Best quality score: ${Math.round(latest_f1 * 100)}% — expand below to see full run history`
              : 'No runs yet — include this config in a test run to see quality scores'}
          </div>

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
            {is_system && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                System config — clone to customize
              </span>
            )}
            <button
              class="t-btn t-btn-secondary"
              style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
              onClick={handleClone}
              disabled={cloning}
            >
              {cloning ? 'Cloning…' : 'Copy to customize'}
            </button>
            {cloneError && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-error)' }}>
                {cloneError}
              </span>
            )}
            <button
              class="t-btn t-btn-secondary"
              style={{ fontSize: 'var(--type-label)', padding: '3px 10px', marginLeft: 'auto' }}
              onClick={() => toggleLevel(3)}
            >
              {level === 3 ? '▲ Hide run history' : '▼ Run history'}
            </button>
            {!is_system && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                {!pendingDelete ? (
                  <button
                    class="t-btn t-btn-secondary"
                    style={{ fontSize: 'var(--type-label)', padding: '3px 10px', color: 'var(--status-error)' }}
                    disabled={deleteFb.phase === 'loading'}
                    onClick={evt => { evt.stopPropagation(); setPendingDelete(true); }}
                  >
                    Delete
                  </button>
                ) : (
                  <>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-error)' }}>
                      Delete "{label}"?
                    </span>
                    <button
                      class="t-btn t-btn-secondary"
                      style={{ fontSize: 'var(--type-label)', padding: '3px 8px', color: 'var(--status-error)', borderColor: 'var(--status-error)' }}
                      disabled={deleteFb.phase === 'loading'}
                      onClick={handleDelete}
                    >
                      {deleteFb.phase === 'loading' ? 'Deleting…' : 'Yes, delete'}
                    </button>
                    <button
                      class="t-btn t-btn-secondary"
                      style={{ fontSize: 'var(--type-label)', padding: '3px 8px' }}
                      onClick={evt => { evt.stopPropagation(); setPendingDelete(false); }}
                    >
                      Cancel
                    </button>
                  </>
                )}
                {deleteFb.msg && <div class={`action-fb action-fb--${deleteFb.phase}`}>{deleteFb.msg}</div>}
              </div>
            )}
          </div>
        </div>
      )}

      {/* L3 */}
      {level >= 3 && (
        <div class="eval-run-row-l3">
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Run history for Config {id}
          </div>
          {!history && !historyError && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>Loading…</div>
          )}
          {historyError && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-error)' }}>{historyError}</div>
          )}
          {history && history.length === 0 && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>No runs for this config yet.</div>
          )}
          {history && history.length > 0 && (
            <table class="eval-metrics-table" style={{ width: 'auto' }}>
              <thead>
                <tr>
                  <th style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'left' }}>Run</th>
                  <th style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'center' }}>{EVAL_TRANSLATIONS.f1.label}</th>
                  <th style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'center' }}>{EVAL_TRANSLATIONS.recall.label}</th>
                  <th style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'center' }}>{EVAL_TRANSLATIONS.precision.label}</th>
                </tr>
              </thead>
              <tbody>
                {history.map(row => (
                  <tr key={row.run_id}>
                    <td style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                      #{row.run_id}
                    </td>
                    <td style={{ padding: '4px 8px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)' }}>
                      {row.f1 != null ? `${Math.round(row.f1 * 100)}%` : '—'}
                    </td>
                    <td style={{ padding: '4px 8px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                      {row.recall != null ? `${Math.round(row.recall * 100)}%` : '—'}
                    </td>
                    <td style={{ padding: '4px 8px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                      {row.precision != null ? `${Math.round(row.precision * 100)}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
