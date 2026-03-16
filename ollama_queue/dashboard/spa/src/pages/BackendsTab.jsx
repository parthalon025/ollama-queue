// What it shows: Full fleet overview of all configured Ollama backends — health,
//   VRAM pressure, loaded models, routing weights, and last-check freshness.
//   Includes controls to add/remove/test backends and update routing weights.
// Decision it drives: Which backend is healthy? Is any GPU overloaded? Should I
//   add a node, rebalance weights, or remove a failing backend?

import { useEffect, useRef, useState } from 'preact/hooks';
import { ShFrozen, ShGlitch, ShPageBanner, ShShatter, ShThreatPulse } from 'superhot-ui/preact';
import { glitchText } from 'superhot-ui';
import { canFireEffect } from '../stores/atmosphere.js';
import TopologyDiagram from '../components/TopologyDiagram.jsx';
import {
  backendsData, backendsError, fetchBackends,
  addBackend, removeBackend, updateBackendWeight, testBackend,
  addToast, currentJob, dlqCount, status, queue, API,
} from '../stores';
import { ShStatusBadge } from 'superhot-ui/preact';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import { TAB_CONFIG } from '../config/tabs.js';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

// ── Fleet Overview card ──────────────────────────────────────────────────────

// What it shows: One card per backend — hostname, GPU name, VRAM bar, loaded models,
//   routing weight, health badge, and freshness (ShFrozen when last_checked is stale).
// Decision it drives: Which backend is healthy and ready? Which is overloaded?
function BackendCard({ backend, onRemove, onUpdateWeight }) {
  const [editWeight, setEditWeight] = useState(false);
  const [weightVal, setWeightVal] = useState(String(backend.weight ?? 1));
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [weightFb, setWeightFb] = useState('');
  const cardRef = useRef(null);

  const isUnhealthy = !backend.healthy;
  const vramPct = backend.vram_pct ?? 0;
  const vramHigh = vramPct > 90;
  // vram_pct is always 0-100 from API (round(used/total*100, 1) in sensing/health.py)
  const fillVal = Math.round(vramPct);

  let hostLabel = backend.url;
  try { hostLabel = new URL(backend.url).hostname; } catch (_) {}
  const label = backend.gpu_name || hostLabel;

  const activeModel = currentJob.value?.model ?? null;
  const isServing = activeModel && backend.healthy &&
    (backend.loaded_models || []).some(m => m === activeModel || m.startsWith(activeModel.split(':')[0] + ':'));

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testBackend(backend.url);
      setTestResult({ ok: true, latency_ms: result.latency_ms });
    } catch (e) {
      setTestResult({ ok: false, error: e.message });
      if (cardRef.current) {
        const cleanup = canFireEffect('glitch-backend-test');
        if (cleanup) glitchText(cardRef.current, { intensity: 'medium' });
      }
    } finally {
      setTesting(false);
    }
  }

  async function handleWeightSave() {
    const w = parseFloat(weightVal);
    if (isNaN(w) || w < 0) { setWeightFb('Invalid weight'); return; }
    try {
      await updateBackendWeight(backend.url, w);
      setEditWeight(false);
      setWeightFb('');
    } catch (e) {
      setWeightFb(`Error: ${e.message}`);
    }
  }

  return (
    <ShFrozen timestamp={backend.last_checked ? backend.last_checked * 1000 : null}>
      <div
        ref={cardRef}
        class={`backend-card${isUnhealthy ? ' backend-card--unhealthy' : ''}`}
        style={{ outline: isServing ? '2px solid var(--sh-phosphor, var(--accent))' : 'none' }}
      >
        {/* Threat pulse when unhealthy */}
        <ShThreatPulse active={isUnhealthy} persistent />

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <ShStatusBadge
            status={backend.healthy ? 'healthy' : 'error'}
            label={backend.healthy ? 'online' : 'offline'}
          />
          <span class="data-mono" style={{ flex: 1, fontWeight: 600, fontSize: 'var(--type-body)', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {label}
          </span>
          {isServing && (
            <span class="data-mono" style={{ fontSize: 'var(--type-micro)', color: 'var(--sh-phosphor, var(--accent))' }}>
              serving
            </span>
          )}
        </div>

        {/* URL */}
        <div class="data-mono" style={{ fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {backend.url}
        </div>

        {/* VRAM bar — B10: .sh-vram-bar with --sh-fill */}
        {backend.healthy && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <div
              class={`sh-vram-bar${vramHigh ? ' sh-vram-bar--threat' : ''}`}
              style={{ '--sh-fill': fillVal }}
              title={`VRAM: ${fillVal}%`}
            />
            <span class="data-mono" style={{ fontSize: 'var(--type-micro)', color: vramHigh ? 'var(--sh-threat, var(--status-error))' : 'var(--text-tertiary)', flexShrink: 0 }}>
              {fillVal}%
            </span>
          </div>
        )}

        {/* Loaded models */}
        {backend.loaded_models?.length > 0 && (
          <div class="data-mono" style={{ fontSize: 'var(--type-micro)', color: 'var(--sh-phosphor, var(--accent))', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            ● {backend.loaded_models.join(', ')}
          </div>
        )}

        {/* Routing weight */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {editWeight ? (
            <>
              <input
                type="number" min="0" step="0.1"
                value={weightVal}
                onInput={e => setWeightVal(e.target.value)}
                style={{ width: '4rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', background: 'var(--input-bg)', border: '1px solid var(--input-border)', borderRadius: 'var(--radius-sm)', padding: '2px 4px', color: 'var(--text-primary)' }}
              />
              <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-micro)', padding: '1px 6px' }} onClick={handleWeightSave}>✓</button>
              <button class="t-btn" style={{ fontSize: 'var(--type-micro)', padding: '1px 6px', background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer' }} onClick={() => setEditWeight(false)}>✕</button>
              {weightFb && <span class="data-mono" style={{ fontSize: 'var(--type-micro)', color: 'var(--status-error)' }}>{weightFb}</span>}
            </>
          ) : (
            <>
              <span class="data-mono" style={{ fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)' }}>
                weight: {backend.weight ?? 1}
              </span>
              <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-micro)', padding: '1px 6px', cursor: 'pointer' }} onClick={() => setEditWeight(true)}>edit</button>
            </>
          )}
        </div>

        {/* Test + Remove actions */}
        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.25rem' }}>
          <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-micro)', padding: '2px 8px' }} onClick={handleTest} disabled={testing}>
            {testing ? 'Testing…' : 'Test'}
          </button>
          {testResult && (
            <span class="data-mono" style={{ fontSize: 'var(--type-micro)', color: testResult.ok ? 'var(--sh-phosphor, var(--accent))' : 'var(--status-error)' }}>
              {testResult.ok ? `${testResult.latency_ms ?? '?'}ms` : testResult.error}
            </span>
          )}
          <ShShatter onDismiss={() => onRemove(backend.url)}>
            <button class="t-btn" style={{ fontSize: 'var(--type-micro)', padding: '2px 8px', color: 'var(--status-error)', background: 'none', border: '1px solid var(--status-error)', borderRadius: 'var(--radius-sm)', cursor: 'pointer' }}>
              Remove
            </button>
          </ShShatter>
        </div>
      </div>
    </ShFrozen>
  );
}

// ── Add Node form ────────────────────────────────────────────────────────────

// What it shows: A form to add a new Ollama backend by URL and routing weight.
// Decision it drives: User can expand the fleet on the fly without restarting the service.
function AddBackendForm({ onAdded }) {
  const [url, setUrl] = useState('');
  const [weight, setWeight] = useState('1');
  const [fb, setFb] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!url.trim()) return;
    setLoading(true);
    setFb('Adding…');
    try {
      await addBackend(url.trim(), parseFloat(weight) || 1);
      setFb('Added');
      setUrl('');
      setWeight('1');
      if (onAdded) onAdded();
      setTimeout(() => setFb(''), 2000);
    } catch (e) {
      setFb(`Error: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleTest() {
    if (!url.trim()) return;
    setFb('Testing…');
    try {
      const res = await testBackend(url.trim());
      setFb(`Reachable — ${res.latency_ms ?? '?'}ms`);
    } catch (e) {
      setFb(`Unreachable: ${e.message}`);
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
      <div style={{ flex: '2 1 200px' }}>
        <label class="data-mono" style={{ display: 'block', fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)', marginBottom: '2px' }}>Backend URL</label>
        <input
          type="url" required
          value={url}
          onInput={e => setUrl(e.target.value)}
          placeholder="http://192.168.1.x:11434"
          style={{ width: '100%', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', background: 'var(--input-bg)', border: '1px solid var(--input-border)', borderRadius: 'var(--radius-sm)', padding: '4px 8px', color: 'var(--text-primary)' }}
        />
      </div>
      <div style={{ flex: '0 0 auto' }}>
        <label class="data-mono" style={{ display: 'block', fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)', marginBottom: '2px' }}>Weight</label>
        <input
          type="number" min="0" step="0.1"
          value={weight}
          onInput={e => setWeight(e.target.value)}
          style={{ width: '4rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', background: 'var(--input-bg)', border: '1px solid var(--input-border)', borderRadius: 'var(--radius-sm)', padding: '4px 6px', color: 'var(--text-primary)' }}
        />
      </div>
      <button class="t-btn t-btn-secondary" type="button" style={{ fontSize: 'var(--type-label)', padding: '4px 10px' }} onClick={handleTest}>
        Test
      </button>
      <button class="t-btn t-btn-primary" type="submit" disabled={loading} style={{ fontSize: 'var(--type-label)', padding: '4px 10px' }}>
        Add node
      </button>
      {fb && (
        <span class="data-mono" style={{ fontSize: 'var(--type-label)', color: fb.startsWith('Error') || fb.startsWith('Unreachable') ? 'var(--status-error)' : fb.startsWith('Reachable') ? 'var(--sh-phosphor, var(--accent))' : 'var(--text-secondary)' }}>
          {fb}
        </span>
      )}
    </form>
  );
}

// ── Main BackendsTab ────────────────────────────────────────────────────────

export default function BackendsTab() {
  const _tab = TAB_CONFIG.find(t => t.id === 'backends');
  const backends = backendsData.value || [];
  const fetchError = backendsError.value;

  useEffect(() => {
    fetchBackends();
    const id = setInterval(fetchBackends, 15000);
    return () => clearInterval(id);
  }, []);

  async function handleRemove(url) {
    try {
      await removeBackend(url);
    } catch (e) {
      console.error('Remove backend failed:', e);
      addToast(`REMOVE FAILED: ${e.message}`, 'error', true);
    }
  }

  // Derive last selected backend for routing intelligence panel
  // What it shows: The 4-tier routing logic text + which backend was last used.
  // Decision it drives: User understands WHY a request went to a specific GPU.

  return (
    <div class="flex flex-col gap-6 sh-stagger-children animate-page-enter tab-content">
      <ShPageBanner namespace={_tab.namespace} page={_tab.page} subtitle={_tab.subtitle} />

      {/* 6.1 Fleet Overview */}
      <div class="t-frame" data-label="Fleet Overview">
        {fetchError ? (
          <div class="data-mono" style={{ color: 'var(--status-error)', fontSize: 'var(--type-body)', textAlign: 'center', padding: '1rem' }}>
            Failed to load backends: {fetchError}
          </div>
        ) : backends.length === 0 ? (
          <div class="data-mono" style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-body)', textAlign: 'center', padding: '1rem' }}>
            No backends configured. Add one below.
          </div>
        ) : (
          <div class="backends-grid">
            {backends.map(backend => (
              <BackendCard
                key={backend.url}
                backend={backend}
                onRemove={handleRemove}
              />
            ))}
          </div>
        )}
      </div>

      {/* 6.2 Dynamic Node Management */}
      <div class="t-frame" data-label="Add Backend Node">
        <AddBackendForm onAdded={fetchBackends} />
      </div>

      {/* 6.3 Routing Intelligence Panel */}
      <div class="t-frame" data-label="Routing Logic">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div class="data-mono" style={{ fontSize: 'var(--type-label)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <span style={{ color: 'var(--sh-phosphor, var(--accent))' }}>1. Health check</span>
            {' → '}
            <span style={{ color: 'var(--text-secondary)' }}>exclude unreachable backends</span>
          </div>
          <div class="data-mono" style={{ fontSize: 'var(--type-label)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <span style={{ color: 'var(--sh-phosphor, var(--accent))' }}>2. Model availability</span>
            {' → '}
            <span style={{ color: 'var(--text-secondary)' }}>prefer backends that have the model installed</span>
          </div>
          <div class="data-mono" style={{ fontSize: 'var(--type-label)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <span style={{ color: 'var(--sh-phosphor, var(--accent))' }}>3. Warm model</span>
            {' → '}
            <span style={{ color: 'var(--text-secondary)' }}>prefer backends with model already loaded in VRAM</span>
          </div>
          <div class="data-mono" style={{ fontSize: 'var(--type-label)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <span style={{ color: 'var(--sh-phosphor, var(--accent))' }}>4. Hardware load</span>
            {' → '}
            <span style={{ color: 'var(--text-secondary)' }}>avoid backends above VRAM pressure threshold</span>
          </div>
          <div class="data-mono" style={{ fontSize: 'var(--type-label)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <span style={{ color: 'var(--sh-phosphor, var(--accent))' }}>5. Weighted random</span>
            {' → '}
            <span style={{ color: 'var(--text-secondary)' }}>final selection by routing weight</span>
          </div>
        </div>
      </div>

      {/* 6.4 System Topology — live directed-graph diagram */}
      <div class="t-frame" data-label="System Topology">
        <TopologyDiagram
          daemonStatus={status.value?.daemon ?? null}
          currentJob={currentJob.value}
          backends={backendsData.value || []}
          dlqCount={dlqCount.value ?? 0}
          activeEval={status.value?.active_eval ?? null}
          queueDepth={queue.value?.length ?? 0}
          queueList={queue.value || []}
        />
      </div>
    </div>
  );
}
