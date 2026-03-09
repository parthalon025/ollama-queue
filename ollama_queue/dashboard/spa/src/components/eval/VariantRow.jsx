import { h } from 'preact';
import { useState } from 'preact/hooks';
import { API, fetchEvalVariants } from '../../store.js';
import { EVAL_TRANSLATIONS } from './translations.js';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
// What it shows: A single eval variant config with 3-level progressive disclosure.
//   L1: ★ badge, variant ID/label, model, recommended/production badges, latest quality.
//   L2: model, creativity, memory window, template, quality sparkline, edit/clone buttons.
//   L3: Run history table for this variant (past F1 scores).
// Decision it drives: User sees which config is recommended, can clone system
//   configs to customize them, and tracks per-variant quality over time.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

function getTemplateLabelFromId(templateId) {
  return EVAL_TRANSLATIONS[templateId]?.label ?? templateId;
}

export default function VariantRow({ variant }) {
  const [deleteFb, deleteAct] = useActionFeedback();

  const [level, setLevel] = useState(1); // 1 | 2 | 3
  const [cloning, setCloning] = useState(false);
  const [cloneError, setCloneError] = useState(null);
  const [history, setHistory] = useState(null);

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
    try {
      const res = await fetch(`${API}/eval/variants/${encodeURIComponent(id)}/history`);
      if (res.ok) setHistory(await res.json());
    } catch (e) {
      console.error('loadHistory failed:', e);
    }
  }

  async function handleClone() {
    setCloning(true);
    setCloneError(null);
    try {
      const res = await fetch(`${API}/eval/variants/${encodeURIComponent(id)}/clone`, { method: 'POST' });
      if (!res.ok) throw new Error(`Clone failed: ${res.status}`);
      await fetchEvalVariants();
    } catch (err) {
      setCloneError(err.message);
    } finally {
      setCloning(false);
    }
  }

  async function handleDelete(evt) {
    evt.stopPropagation();
    if (!confirm(`Delete variant "${label}"?`)) return;
    await deleteAct(
      'Deleting…',
      async () => {
        const res = await fetch(`${API}/eval/variants/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
        await fetchEvalVariants();
      },
      `Variant deleted`
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
          {is_recommended ? <span class="eval-badge eval-badge-recommended">★</span> : null}
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)', fontWeight: 600 }}>
            {id}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
            {label}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
            · {model}
          </span>
          {is_recommended ? <span class="eval-badge eval-badge-recommended">★ Recommended</span> : null}
          {is_production ? <span class="eval-badge eval-badge-production">Production</span> : null}
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
            <div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                Model:
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)', marginLeft: '0.4rem' }}>
                {model}
              </span>
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
            {is_system ? (
              <button
                class="t-btn t-btn-secondary"
                style={{ fontSize: 'var(--type-label)', padding: '3px 10px', opacity: 0.5, cursor: 'not-allowed' }}
                disabled
                title="System configs can't be edited directly. Clone to customize."
              >
                Edit (clone to customize)
              </button>
            ) : (
              <button
                class="t-btn t-btn-secondary"
                style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
              >
                Edit
              </button>
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
              <div>
                <button
                  class="t-btn t-btn-secondary"
                  style={{ fontSize: 'var(--type-label)', padding: '3px 10px', color: 'var(--status-error)' }}
                  disabled={deleteFb.phase === 'loading'}
                  onClick={handleDelete}
                >
                  {deleteFb.phase === 'loading' ? 'Deleting…' : 'Delete'}
                </button>
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
          {!history && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>Loading…</div>
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
