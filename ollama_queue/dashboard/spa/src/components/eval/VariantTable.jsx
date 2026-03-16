import { h } from 'preact';
import { useState } from 'preact/hooks';
import { evalVariants, fetchEvalVariants } from '../../stores';
import { API } from '../../stores/_shared.js';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import VariantRow from './VariantRow.jsx';
import { ShEmptyState } from 'superhot-ui/preact';
// What it shows: The full list of eval variant configs — system defaults first,
//   then user-created. Each row is expandable to model/template details and
//   run history. Non-system variants have an Edit button that opens an inline
//   form to update system_prompt, provider, and params.
// Decision it drives: User sees all available configs at a glance, identifies
//   the recommended one, and can clone, edit, or delete configs from here.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

const PROVIDER_OPTIONS = ['ollama', 'claude', 'openai'];

// What it shows: Inline edit panel for a single variant's advanced fields —
//   system_prompt, provider, and params (JSON). Visible only when the row's
//   Edit button is clicked.
// Decision it drives: Lets the user tune LLM provider and prompt prefix without
//   cloning a new variant for every small tweak.
function VariantEditPanel({ variant, onClose }) {
  const [saveFb, saveAct] = useActionFeedback();
  const [system_prompt, setSystemPrompt] = useState(variant.system_prompt || '');
  const [provider, setProvider] = useState(variant.provider || 'ollama');
  const [params, setParams] = useState(
    variant.params ? JSON.stringify(variant.params, null, 2) : '{}'
  );
  const [paramsError, setParamsError] = useState('');

  function validateParams(raw) {
    try { JSON.parse(raw); setParamsError(''); return true; }
    catch (e) { setParamsError(`Invalid JSON: ${e.message}`); return false; }
  }

  async function handleSave(evt) {
    evt.preventDefault();
    if (!validateParams(params)) return;
    await saveAct(
      'SAVING',
      async () => {
        const res = await fetch(`${API}/eval/variants/${encodeURIComponent(variant.id)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ system_prompt, provider, params: JSON.parse(params) }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${res.status}`);
        }
        await fetchEvalVariants();
      },
      'SAVED'
    );
  }

  return (
    <form
      class="variant-edit-panel"
      onSubmit={handleSave}
      style={{ padding: '0.75rem', background: 'var(--surface-raised)', borderTop: '1px solid var(--border-subtle)' }}
    >
      <div style={{ marginBottom: '0.5rem' }}>
        <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', display: 'block', marginBottom: '2px' }}>
          System prompt
        </label>
        <textarea
          value={system_prompt}
          onInput={evt => setSystemPrompt(evt.target.value)}
          rows={3}
          style={{ width: '100%', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', resize: 'vertical' }}
        />
      </div>
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
        <div>
          <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', display: 'block', marginBottom: '2px' }}>
            Provider
          </label>
          <select value={provider} onChange={evt => setProvider(evt.target.value)} style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)' }}>
            {PROVIDER_OPTIONS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div style={{ flex: '1 1 200px' }}>
          <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', display: 'block', marginBottom: '2px' }}>
            Params <span style={{ color: 'var(--text-tertiary)' }}>(JSON)</span>
          </label>
          <input
            type="text"
            value={params}
            onInput={evt => { setParams(evt.target.value); validateParams(evt.target.value); }}
            style={{ width: '100%', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)' }}
          />
          {paramsError && <div style={{ color: 'var(--status-error)', fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)' }}>{paramsError}</div>}
        </div>
      </div>
      <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
        <button type="submit" class="t-btn t-btn-primary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }} disabled={saveFb.phase === 'loading' || !!paramsError}>
          {saveFb.phase === 'loading' ? 'Saving…' : 'Save'}
        </button>
        <button type="button" class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }} onClick={onClose}>
          Cancel
        </button>
        {saveFb.msg && <span class={`action-fb action-fb--${saveFb.phase}`}>{saveFb.msg}</span>}
      </div>
    </form>
  );
}

export default function VariantTable() {
  // Read .value at top so Preact subscribes to the signal
  const variants = evalVariants.value;
  const [editId, setEditId] = useState(null);

  if (!variants || variants.length === 0) {
    return (
      <div class="t-frame" data-label="Configurations">
        <ShEmptyState mantra="UNCONFIGURED" />
      </div>
    );
  }

  // Sort: system first (A-E order), then user-created by label
  const systemVariants = variants.filter(v => v.is_system).sort((a, b) => a.id.localeCompare(b.id));
  const userVariants = variants.filter(v => !v.is_system).sort((a, b) => a.label.localeCompare(b.label));
  const sorted = [...systemVariants, ...userVariants];

  return (
    <div class="t-frame" data-label="Configurations">
      {sorted.map(variant => (
        <div key={variant.id}>
          <div style={{ display: 'flex', alignItems: 'stretch' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <VariantRow variant={variant} />
            </div>
            {!variant.is_system && (
              <button
                class="t-btn t-btn-secondary"
                style={{ fontSize: 'var(--type-label)', padding: '3px 8px', margin: '4px 4px 4px 0', alignSelf: 'flex-start', flexShrink: 0 }}
                title="Edit system_prompt, provider, and params"
                onClick={() => setEditId(editId === variant.id ? null : variant.id)}
              >
                {editId === variant.id ? '✕' : '✎ Edit'}
              </button>
            )}
          </div>
          {editId === variant.id && (
            <VariantEditPanel
              variant={variant}
              onClose={() => setEditId(null)}
            />
          )}
        </div>
      ))}
    </div>
  );
}
