import { h } from 'preact';
import { useState } from 'preact/hooks';
import { API, fetchEvalTemplates, fetchEvalVariants } from '../../store.js';
import { EVAL_TRANSLATIONS } from './translations.js';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
// What it shows: A single prompt template with 3-level progressive disclosure.
//   L1: template ID, plain-language label.
//   L2: first 200 chars of instruction, edit/clone buttons.
//   L3: full instruction text in read-only textarea (or editable for user templates).
// Decision it drives: User understands how each template shapes principle extraction,
//   and can clone system templates to create customized variations.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function TemplateRow({ template }) {
  const [deleteFb, deleteAct] = useActionFeedback();

  const [level, setLevel] = useState(1); // 1 | 2 | 3
  const [cloning, setCloning] = useState(false);
  const [cloneError, setCloneError] = useState(null);

  const { id, label, instruction, is_system } = template;

  const preview = instruction ? instruction.slice(0, 200) + (instruction.length > 200 ? '…' : '') : '(empty)';
  const translatedLabel = EVAL_TRANSLATIONS[id]?.label ?? label ?? id;

  function toggleLevel(next) {
    setLevel(level === next ? 1 : next);
  }

  async function handleClone() {
    setCloning(true);
    setCloneError(null);
    try {
      const res = await fetch(`${API}/eval/templates/${encodeURIComponent(id)}/clone`, { method: 'POST' });
      if (!res.ok) throw new Error(`Clone failed: ${res.status}`);
      await fetchEvalTemplates();
    } catch (err) {
      setCloneError(err.message);
    } finally {
      setCloning(false);
    }
  }

  async function handleDelete(evt) {
    evt.stopPropagation();
    if (!confirm(`Delete template "${label}"?`)) return;
    await deleteAct(
      'Deleting…',
      async () => {
        const res = await fetch(`${API}/eval/templates/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
        await fetchEvalVariants(); // refreshes variants list
      },
      `Template deleted`
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
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flex: 1 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)', fontWeight: 600 }}>
            {id}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
            — {translatedLabel}
          </span>
          {is_system ? (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginLeft: '0.5rem' }}>
              [system]
            </span>
          ) : null}
        </div>
        <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)', flexShrink: 0 }}>
          {level >= 2 ? '▲' : '▼'}
        </span>
      </div>

      {/* L2 */}
      {level >= 2 && (
        <div style={{ padding: '0.5rem 1rem 0.75rem 1rem', borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-base)' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', marginBottom: '0.5rem', lineHeight: '1.5' }}>
            {preview}
          </div>

          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
            {is_system ? (
              <button
                class="t-btn t-btn-secondary"
                style={{ fontSize: 'var(--type-label)', padding: '3px 10px', opacity: 0.5, cursor: 'not-allowed' }}
                disabled
                title="System templates can't be edited. Clone to customize."
              >
                Edit (clone to customize)
              </button>
            ) : (
              <button
                class="t-btn t-btn-secondary"
                style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
                onClick={() => toggleLevel(3)}
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
              {cloning ? 'Cloning…' : 'Clone'}
            </button>
            {cloneError && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-error)' }}>
                {cloneError}
              </span>
            )}
            {!is_system && (
              <button
                class="t-btn t-btn-secondary"
                style={{ fontSize: 'var(--type-label)', padding: '3px 10px', marginLeft: 'auto' }}
                onClick={() => toggleLevel(3)}
              >
                {level === 3 ? '▲ Collapse' : '▼ Full text'}
              </button>
            )}
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
        <div style={{ padding: '0.5rem 1rem 0.75rem 1rem', borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-base)' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>
            Full instruction
          </div>
          <textarea
            readOnly={!!is_system}
            class="t-input"
            style={{
              width: '100%',
              minHeight: '150px',
              padding: '0.5rem',
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--type-label)',
              color: is_system ? 'var(--text-secondary)' : 'var(--text-primary)',
              resize: 'vertical',
              lineHeight: '1.5',
            }}
            value={instruction ?? ''}
          />
          {is_system && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginTop: '0.25rem' }}>
              Read-only — clone to make changes
            </div>
          )}
        </div>
      )}
    </div>
  );
}
