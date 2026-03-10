import { h } from 'preact';
import { useState } from 'preact/hooks';
import { API, evalVariants } from '../../stores';

// What it shows: Side-by-side comparison of two variant configs — model, temperature,
//   context window, prompt template differences.
// Decision it drives: Helps user understand what changed between variants
//   so they can attribute F1 differences to specific config changes.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function ConfigDiffPanel() {
  const [varA, setVarA] = useState('');
  const [varB, setVarB] = useState('');
  const [changes, setChanges] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const variants = evalVariants.value || [];

  async function handleCompare() {
    if (!varA || !varB) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/eval/variants/${encodeURIComponent(varA)}/diff/${encodeURIComponent(varB)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setChanges(data.changes);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{
      padding: '0.75rem',
      background: 'var(--bg-raised)',
      borderRadius: '4px',
      marginBottom: '0.75rem',
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--type-label)',
        color: 'var(--text-tertiary)',
        marginBottom: '0.4rem',
      }}>
        Compare Configs
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={varA} onChange={evt => setVarA(evt.target.value)}
          style={{ padding: '4px 8px', borderRadius: '3px', border: '1px solid var(--border)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}>
          <option value="">Select A</option>
          {variants.map(variant => (
            <option key={variant.id} value={variant.id}>{variant.id} — {variant.label || variant.model}</option>
          ))}
        </select>
        <span style={{ color: 'var(--text-tertiary)' }}>vs</span>
        <select value={varB} onChange={evt => setVarB(evt.target.value)}
          style={{ padding: '4px 8px', borderRadius: '3px', border: '1px solid var(--border)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}>
          <option value="">Select B</option>
          {variants.map(variant => (
            <option key={variant.id} value={variant.id}>{variant.id} — {variant.label || variant.model}</option>
          ))}
        </select>
        <button onClick={handleCompare} disabled={!varA || !varB || loading}
          style={{ padding: '4px 12px', borderRadius: '3px', border: '1px solid var(--accent)', background: 'var(--accent-glow)', color: 'var(--accent)', cursor: 'pointer' }}>
          {loading ? 'Comparing\u2026' : 'Compare'}
        </button>
      </div>
      {error && <div style={{ color: 'var(--status-error)', fontSize: 'var(--type-label)', marginTop: '0.3rem' }}>{error}</div>}
      {changes !== null && (
        <div style={{ marginTop: '0.5rem', fontSize: 'var(--type-body)' }}>
          {changes.length === 0
            ? <span style={{ color: 'var(--text-tertiary)' }}>Identical configuration</span>
            : <ul style={{ margin: 0, paddingLeft: '1.2rem' }}>
                {changes.map((change, idx) => <li key={idx} style={{ marginBottom: '0.2rem' }}>{change}</li>)}
              </ul>
          }
        </div>
      )}
    </div>
  );
}
