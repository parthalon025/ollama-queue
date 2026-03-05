import { h } from 'preact';
import { useState } from 'preact/hooks';
import { signal } from '@preact/signals';
import { API, evalTemplates, fetchEvalVariants } from '../../store.js';
import { EVAL_TRANSLATIONS } from './translations.js';
// What it shows: Action toolbar for managing variant configs — create new,
//   bulk-generate from installed models, export, and import.
// Decision it drives: User quickly populates configs for all local models,
//   or imports a known-good config set from another machine.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function VariantToolbar() {
  const templates = evalTemplates.value;

  const [showNewForm, setShowNewForm] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [genPreview, setGenPreview] = useState(null);
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
      if (!res.ok) throw new Error(`Save failed: ${res.status}`);
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
    try {
      const res = await fetch(`${API}/eval/variants/generate/preview`);
      if (res.ok) setGenPreview(await res.json());
    } catch (e) {
      console.error('Generate preview failed:', e);
    }
  }

  async function handleGenerateConfirm() {
    setGenerating(true);
    try {
      await fetch(`${API}/eval/variants/generate`, { method: 'POST' });
      await fetchEvalVariants();
      setGenPreview(null);
    } catch (e) {
      console.error('Generate failed:', e);
    } finally {
      setGenerating(false);
    }
  }

  async function handleExport() {
    const res = await fetch(`${API}/eval/variants/export`);
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'eval-variants.json';
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleImportChange(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async ev => {
      try {
        const data = JSON.parse(ev.target.result);
        const res = await fetch(`${API}/eval/variants/import`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        });
        if (res.ok) await fetchEvalVariants();
      } catch (importErr) {
        console.error('Import failed:', importErr);
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
          disabled={generating}
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

      {/* Generate preview modal */}
      {genPreview && (
        <div class="t-callout" style={{ marginTop: '0.75rem', padding: '0.75rem 1rem' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)', marginBottom: '0.4rem' }}>
            Will create {genPreview.count ?? '?'} configurations from installed models.
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button class="t-btn t-btn-primary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }} onClick={handleGenerateConfirm} disabled={generating}>
              {generating ? 'Generating…' : 'Confirm'}
            </button>
            <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }} onClick={() => setGenPreview(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* New variant inline form */}
      {showNewForm && (
        <form onSubmit={handleSaveNew} class="t-callout" style={{ marginTop: '0.75rem', padding: '0.75rem 1rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            New configuration
          </div>

          {[
            { field: 'label', label: 'Name', placeholder: 'My custom config', type: 'text' },
            { field: 'model', label: 'Model', placeholder: 'qwen3:14b', type: 'text' },
          ].map(({ field, label, placeholder, type }) => (
            <div key={field} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '70px', flexShrink: 0 }}>
                {label}
              </label>
              <input
                type={type}
                class="t-input"
                style={{ padding: '4px 8px', fontSize: 'var(--type-label)', flex: 1 }}
                value={newVariant[field]}
                onInput={e => handleNewFieldChange(field, e.target.value)}
                placeholder={placeholder}
              />
            </div>
          ))}

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
