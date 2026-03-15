/**
 * What it shows: Configuration for one AI provider role — which service to use
 *   (Ollama for local, Claude, or OpenAI), which model to use, and the API key.
 *   A "Test connection" button verifies it's working before you run a real eval.
 * Decision it drives: "Is this role correctly connected? Is the model I want available?
 *   Am I staying within budget?"
 *
 * Roles: Generator (creates test outputs), Judge (scores them), Optimizer (suggests
 *   better prompts), Oracle (reference checker for judge calibration).
 */
import { h } from 'preact';
import { useState } from 'preact/hooks';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import { backendsData } from '../../stores/health.js';
import { evalSettings, saveEvalSettings } from '../../stores/eval.js';

const ROLE_DESCRIPTIONS = {
  generator: 'Creates the test outputs that the judge will score.',
  judge:     'Scores the test outputs against reference answers.',
  optimizer: 'Suggests better prompt variants based on results.',
  oracle:    'Reference AI used to check the judge\'s accuracy.',
};

const PROVIDERS = ['ollama', 'claude', 'openai'];

export default function ProviderRoleSection({ role, settings, onSave }) {
  const [provider, setProvider] = useState(settings?.provider || 'ollama');
  const [model, setModel] = useState(settings?.model || '');
  const [apiKey, setApiKey] = useState('');
  const [models, setModels] = useState([]);
  const [backendUrl, setBackendUrl] = useState(
    evalSettings.value?.[`eval.${role}_backend_url`] || 'auto'
  );
  const [fb, act] = useActionFeedback();

  async function loadModels(prov) {
    try {
      const res = await fetch(`/api/eval/providers/models?provider=${prov}`);
      if (!res.ok) return;
      const data = await res.json();
      setModels(data.models || []);
    } catch { setModels([]); }
  }

  function handleProviderChange(e) {
    const prov = e.target.value;
    setProvider(prov);
    setModel('');
    loadModels(prov);
  }

  function handleTest() {
    act('Testing…', async () => {
      const res = await fetch('/api/eval/providers/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, model, api_key: apiKey || undefined }),
      });
      if (!res.ok) {
        let detail = `Test failed: ${res.status}`;
        try {
          const body = await res.json();
          if (body.detail) detail = body.detail;
        } catch { /* non-JSON response body */ }
        throw new Error(detail);
      }
      return await res.json();
    }, () => 'Connected ✓');
  }

  return (
    <div class="provider-role-section">
      <div class="provider-role-section__header">
        <strong class="provider-role-section__title">{role.charAt(0).toUpperCase() + role.slice(1)}</strong>
        <span class="provider-role-section__desc">{ROLE_DESCRIPTIONS[role]}</span>
      </div>

      <div class="provider-role-section__fields">
        <label>
          Service
          <select value={provider} onChange={handleProviderChange}>
            {PROVIDERS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>

        <label>
          Model
          <select value={model} onChange={e => setModel(e.target.value)}>
            <option value="">Select model…</option>
            {models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>

        {/* Backend host selector — only shown for Ollama provider when multiple backends
            are configured. Saves to eval.{role}_backend_url setting on change. */}
        {provider === 'ollama' && backendsData.value.length > 1 && (
          <label>
            Backend host
            <select value={backendUrl} onChange={e => {
              const val = e.target.value;
              setBackendUrl(val);
              saveEvalSettings({ [`eval.${role}_backend_url`]: val }).catch(err => {
                console.error('Failed to save backend_url:', err);
              });
            }}>
              <option value="auto">Auto (smart routing)</option>
              {backendsData.value
                .filter(b => b.healthy)
                .map(b => (
                  <option key={b.url} value={b.url}>
                    {b.gpu_name || b.url} — {Math.round(b.vram_pct)}% VRAM
                  </option>
                ))}
            </select>
          </label>
        )}

        {provider !== 'ollama' && (
          <label>
            API Key
            <input
              type="password"
              placeholder="sk-…"
              value={apiKey}
              onInput={e => setApiKey(e.target.value)}
            />
          </label>
        )}

        {provider !== 'ollama' && (
          <label>
            Max cost per run (USD)
            <input type="number" step="0.01" min="0" defaultValue={settings?.max_cost_per_run || ''} />
          </label>
        )}

        <div class="provider-role-section__actions">
          <button
            disabled={!model || fb.phase === 'loading'}
            onClick={handleTest}
          >
            {fb.phase === 'loading' ? 'Testing…' : 'Test connection'}
          </button>
          {fb.msg && <span class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</span>}
        </div>
      </div>
    </div>
  );
}
