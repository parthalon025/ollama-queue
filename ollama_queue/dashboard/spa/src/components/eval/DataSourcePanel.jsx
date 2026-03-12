import { useState } from 'preact/hooks';
// What it shows: The configured data source connection status and last-test result.
//   Shows item count, cluster count, and response time so users can verify the
//   source is healthy.
// Decision it drives: User connects, verifies, or changes the data source. All
//   eval runs pull lesson data from this source.

import { evalSettings, testDataSource, saveEvalSettings } from '../../stores';

export default function DataSourcePanel() {
  // Read .value at top of body to subscribe to signal changes
  const settings = evalSettings.value;

  // Local form state — initialised from signal, edited locally until Save
  const [url,         setUrl]         = useState(settings['eval.data_source_url']   ?? 'http://127.0.0.1:7685');
  const [token,       setToken]       = useState(settings['eval.data_source_token'] ?? '');
  const [showToken,   setShowToken]   = useState(false);
  const [testResult,  setTestResult]  = useState(null); // {ok, item_count, cluster_count, response_ms} | null
  const [testing,     setTesting]     = useState(false);
  const [saving,      setSaving]      = useState(false);
  const [saveError,   setSaveError]   = useState('');
  const [open,        setOpen]        = useState(false);

  // Determine status dot colour
  let dotColor = 'var(--text-tertiary)'; // gray = untested
  if (testResult) {
    dotColor = testResult.ok ? 'var(--status-healthy)' : 'var(--status-error)';
  }

  // L1 summary text
  const l1Summary = testResult && testResult.ok
    ? `${testResult.item_count ?? '?'} items · ${testResult.cluster_count ?? '?'} clusters · tested ${testResult.response_ms != null ? testResult.response_ms + 'ms ago' : 'just now'}`
    : testResult
      ? 'Connection failed'
      : 'Not yet tested';

  async function handleTest() {
    setTesting(true);
    setSaveError('');
    try {
      const result = await testDataSource();
      setTestResult(result);
    } catch (err) {
      console.error('handleTest failed:', err);
      setTestResult({ ok: false });
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setSaveError('');
    try {
      await saveEvalSettings({
        'eval.data_source_url':   url,
        'eval.data_source_token': token,
      });
    } catch (err) {
      setSaveError(err.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div class="eval-datasource-panel t-frame" data-label="Data source">
      {/* L1 — status line */}
      <button
        type="button"
        class="eval-datasource-panel__header"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-label={`${open ? 'Collapse' : 'Expand'} data source details`}
      >
        <span class="eval-status-dot" style={`--dot-color: ${dotColor}`} aria-hidden="true" />
        <span class="eval-datasource-panel__name">lessons-db</span>
        <span class="eval-datasource-panel__summary" style="color: var(--text-secondary); font-size: var(--type-label);">
          {l1Summary}
        </span>
        <span aria-hidden="true" style="margin-left: auto;">{open ? '▲' : '▼'}</span>
      </button>

      {/* L2 — URL + token + test + save */}
      {open && (
        <div class="eval-datasource-panel__detail" style="margin-top: 12px; display: flex; flex-direction: column; gap: 10px;">
          <label class="eval-settings-label">
            <span>Source URL</span>
            <input
              class="t-input eval-settings-input"
              type="url"
              value={url}
              onInput={evt => setUrl(evt.currentTarget.value)}
              placeholder="http://127.0.0.1:7685"
            />
          </label>
          <label class="eval-settings-label">
            <span>Auth token</span>
            <div class="eval-token-row">
              <input
                class="t-input eval-settings-input"
                type={showToken ? 'text' : 'password'}
                value={token}
                onInput={evt => setToken(evt.currentTarget.value)}
                placeholder="Leave blank if none"
                aria-label="Bearer auth token (masked)"
              />
              <button
                type="button"
                class="t-btn t-btn-secondary"
                onClick={() => setShowToken(!showToken)}
                style="padding: 4px 8px; font-size: var(--type-label);"
              >
                {showToken ? 'Hide' : 'Show'}
              </button>
            </div>
          </label>
          <div class="eval-datasource-panel__actions">
            <button
              type="button"
              class="t-btn t-btn-secondary"
              onClick={handleTest}
              disabled={testing}
            >
              {testing ? 'Testing…' : 'Test now'}
            </button>
            {testResult && (
              <span class="data-mono" style={`color: ${dotColor}; font-size: var(--type-label);`}>
                {testResult.ok
                  ? `✓ ${testResult.item_count ?? '?'} items · ${testResult.response_ms ?? '?'}ms`
                  : '✗ Connection failed'}
              </span>
            )}
            <button
              type="button"
              class="t-btn t-btn-primary"
              onClick={handleSave}
              disabled={saving}
              style="margin-left: auto;"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
          {saveError && (
            <p class="eval-settings-error" role="alert">{saveError}</p>
          )}
          <p style="color: var(--text-tertiary); font-size: var(--type-label);">
            Item browser — shows items from data source. Coming in a future update.
          </p>
        </div>
      )}
    </div>
  );
}
