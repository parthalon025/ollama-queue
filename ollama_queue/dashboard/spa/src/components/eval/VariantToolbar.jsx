import { useState } from 'preact/hooks';
import { API, evalTemplates, fetchEvalVariants } from '../../stores';
import { EVAL_TRANSLATIONS } from './translations.js';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import ModelSelect from '../ModelSelect.jsx';
// What it shows: Action toolbar for managing variant configs — create new,
//   bulk-generate from installed models, export, and import.
// Decision it drives: User quickly populates configs for all local models,
//   or imports a known-good config set from another machine.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function VariantToolbar() {
  const templates = evalTemplates.value;

  const [genFb, genAct] = useActionFeedback();

  const [showNewForm, setShowNewForm] = useState(false);
  const [genPreview, setGenPreview] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [exportError, setExportError] = useState(null);
  const [importError, setImportError] = useState(null);
  const [newVariant, setNewVariant] = useState({
    label: '',
    model: '',
    prompt_template_id: 'fewshot',
    temperature: 0.6,
    num_ctx: 8192,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  function handleNewFieldChange(field, value) {
    setNewVariant(prev => ({ ...prev, [field]: value }));
  }

  async function handleSaveNew(e) {
    e.preventDefault();
    if (!newVariant.label || !newVariant.model) {
      setError('Label and model are required.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${API}/eval/variants`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newVariant),
      });
      if (!res.ok) {
        let detail = `Save failed: ${res.status}`;
        try { const body = await res.json(); if (body.detail) detail = body.detail; } catch { /* non-JSON */ }
        throw new Error(detail);
      }
      await fetchEvalVariants();
      setShowNewForm(false);
      setNewVariant({ label: '', model: '', prompt_template_id: 'fewshot', temperature: 0.6, num_ctx: 8192 });
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleGeneratePreview() {
    setPreviewError(null);
    try {
      const res = await fetch(`${API}/eval/variants/generate/preview`);
      if (!res.ok) { setPreviewError(`Preview failed: HTTP ${res.status}`); return; }
      setGenPreview(await res.json());
    } catch (e) {
      setPreviewError(`Preview failed: ${e.message}`);
    }
  }

  async function handleGenerateConfirm() {
    await genAct(
      'Generating variants…',
      async () => {
        const res = await fetch(`${API}/eval/variants/generate`, { method: 'POST' });
        if (!res.ok) {
          let detail = `Generate failed: ${res.status}`;
          try { const body = await res.json(); if (body.detail) detail = body.detail; } catch { /* non-JSON */ }
          throw new Error(detail);
        }
        const data = await res.json();
        await fetchEvalVariants();
        setGenPreview(null);
        return data;
      },
      data => `${data.created ?? data.count ?? 'Variants'} generated`
    );
  }

  async function handleExport() {
    setExportError(null);
    try {
      const res = await fetch(`${API}/eval/variants/export`);
      if (!res.ok) { setExportError(`Export failed: HTTP ${res.status}`); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'eval-variants.json';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setExportError(`Export failed: ${e.message}`);
    }
  }

  function handleImportChange(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async ev => {
      setImportError(null);
      try {
        const data = JSON.parse(ev.target.result);
        const res = await fetch(`${API}/eval/variants/import`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        });
        if (!res.ok) { setImportError(`Import failed: HTTP ${res.status}`); return; }
        await fetchEvalVariants();
      } catch (importErr) {
        setImportError(`Import failed: ${importErr.message}`);
      }
    };
    reader.readAsText(file);
  }

  return (
    <div style={{ marginBottom: '0.75rem' }}>
      {/* Toolbar buttons */}
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <button
          class="t-btn t-btn-primary"
          style={{ fontSize: 'var(--type-label)', padding: '4px 12px' }}
          onClick={() => setShowNewForm(f => !f)}
        >
          + New configuration
        </button>
        <button
          class="t-btn t-btn-secondary"
          style={{ fontSize: 'var(--type-label)', padding: '4px 12px' }}
          onClick={handleGeneratePreview}
          disabled={genFb.phase === 'loading'}
        >
          ⚡ Generate from models
        </button>
        <button
          class="t-btn t-btn-secondary"
          style={{ fontSize: 'var(--type-label)', padding: '4px 12px' }}
          onClick={handleExport}
        >
          Export
        </button>
        <label class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '4px 12px', cursor: 'pointer' }}>
          Import
          <input type="file" accept=".json" style={{ display: 'none' }} onChange={handleImportChange} />
        </label>
      </div>
      {(previewError || exportError || importError) && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-error)', marginTop: '0.4rem' }}>
          {previewError || exportError || importError}
        </div>
      )}

      {/* Generate preview modal */}
      {genPreview && (
        <div class="t-callout" style={{ marginTop: '0.75rem', padding: '0.75rem 1rem' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)', marginBottom: '0.4rem' }}>
            Will create {genPreview.count ?? '?'} configurations from installed models.
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <button
              class="t-btn t-btn-primary"
              style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
              disabled={genFb.phase === 'loading'}
              onClick={handleGenerateConfirm}
            >
              {genFb.phase === 'loading' ? 'Generating…' : 'Confirm'}
            </button>
            <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }} onClick={() => setGenPreview(null)}>
              Cancel
            </button>
            {genFb.msg && <div class={`action-fb action-fb--${genFb.phase}`}>{genFb.msg}</div>}
          </div>
        </div>
      )}

      {/* New variant inline form */}
      {showNewForm && (
        <form onSubmit={handleSaveNew} class="t-callout" style={{ marginTop: '0.75rem', padding: '0.75rem 1rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            New configuration
          </div>

          {/* Name field */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '70px', flexShrink: 0 }}>
              Name
            </label>
            <input
              type="text"
              class="t-input"
              style={{ padding: '4px 8px', fontSize: 'var(--type-label)', flex: 1 }}
              value={newVariant.label}
              onInput={e => handleNewFieldChange('label', e.target.value)}
              placeholder="My custom config"
            />
          </div>

          {/* Model field */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '70px', flexShrink: 0 }}>
              Model
            </label>
            <ModelSelect
              value={newVariant.model}
              onChange={val => handleNewFieldChange('model', val)}
              backend="ollama"
              placeholder="qwen3:14b"
              class="t-input"
            />
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '70px', flexShrink: 0 }}>
              Template
            </label>
            <select
              class="t-input"
              style={{ padding: '4px 8px', fontSize: 'var(--type-label)', flex: 1 }}
              value={newVariant.prompt_template_id}
              onChange={e => handleNewFieldChange('prompt_template_id', e.target.value)}
            >
              {(templates || []).map(tmpl => (
                <option key={tmpl.id} value={tmpl.id}>
                  {EVAL_TRANSLATIONS[tmpl.id]?.label ?? tmpl.label ?? tmpl.id}
                </option>
              ))}
            </select>
          </div>

          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                {EVAL_TRANSLATIONS.temperature.label}
              </label>
              <input
                type="number"
                step="0.1"
                min="0"
                max="2"
                class="t-input eval-num-input"
                value={newVariant.temperature}
                onInput={e => handleNewFieldChange('temperature', parseFloat(e.target.value) || 0.6)}
              />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                {EVAL_TRANSLATIONS.num_ctx.label}
              </label>
              <input
                type="number"
                min="512"
                step="1024"
                class="t-input eval-num-input"
                style={{ width: '80px' }}
                value={newVariant.num_ctx}
                onInput={e => handleNewFieldChange('num_ctx', parseInt(e.target.value) || 8192)}
              />
            </div>
          </div>

          {error && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-error)' }}>{error}</div>
          )}

          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button type="submit" class="t-btn t-btn-primary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button type="button" class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }} onClick={() => setShowNewForm(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
