/**
 * What it shows: Your library of prompt variants — each displayed as a card
 *   showing its score, stability, provider, and key settings. Like a deck of
 *   recipe cards, each for a different way to ask the AI to find lessons.
 * Decision it drives: "Which variant should I promote? Which one to clone for
 *   the next round of testing? Which ones to compare side-by-side?"
 */
import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import { evalVariants, fetchEvalVariants, focusVariantId } from '../stores/eval.js';
import { API } from '../stores/_shared.js';
import VariantCard from '../components/eval/VariantCard.jsx';
import ConfigDiffPanel from '../components/eval/ConfigDiffPanel.jsx';

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

const SWEEP_DIMENSIONS = ['temperature', 'num_ctx', 'model'];

export default function EvalVariants() {
  const [selected, setSelected] = useState([]);

  // Sweep form state
  // What it shows: A form that clones one base variant multiple times, each with a
  //   different value for a single dimension (temperature, context window, or model).
  //   Creates N variants in one click instead of cloning manually N times.
  // Decision it drives: Quickly populate a sweep for parameter tuning experiments.
  const [showSweep, setShowSweep] = useState(false);
  const [sweepBaseId, setSweepBaseId] = useState('');
  const [sweepDim, setSweepDim] = useState('temperature');
  const [sweepValues, setSweepValues] = useState('');
  const [sweepFb, setSweepFb] = useState('');

  useEffect(() => {
    fetchEvalVariants();
  }, []);

  // Sort variants by latest_f1 descending; variants without a score go last
  const variants = [...evalVariants.value].sort((a, b) =>
    (b.latest_f1 ?? -1) - (a.latest_f1 ?? -1)
  );

  function toggleSelect(id, checked) {
    setSelected(prev => checked ? [...prev, id] : prev.filter(x => x !== id));
  }

  async function handleSweep(evt) {
    evt.preventDefault();
    // Parse comma-separated values; convert to numbers where possible
    const rawValues = sweepValues.split(',').map(s => s.trim()).filter(Boolean);
    const values = rawValues.map(v => {
      const n = parseFloat(v);
      return isNaN(n) ? v : n;
    });
    setSweepFb('Creating sweep variants…');
    try {
      const res = await fetch(`${API}/eval/variants/sweep`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_variant_id: sweepBaseId, dimension: sweepDim, values }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setSweepFb(`Created ${data.created} variant${data.created !== 1 ? 's' : ''}`);
      await fetchEvalVariants();
      setTimeout(() => { setShowSweep(false); setSweepFb(''); }, 1500);
    } catch (e) {
      setSweepFb(`Error: ${e.message}`);
    }
  }

  return (
    <div class="eval-variants">
      <div class="eval-variants__toolbar">
        <button onClick={() => { /* open create form */ }}>+ Create</button>
        <button
          class="t-btn t-btn-secondary"
          style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
          onClick={() => { setShowSweep(s => !s); setSweepFb(''); }}
        >
          {showSweep ? '✕ Cancel sweep' : '⤢ Sweep'}
        </button>
        {selected.length >= 2 && <span>{selected.length} selected for compare</span>}
      </div>

      {/* Parameter sweep form — creates N clones of a base variant varying one dimension */}
      {showSweep && (
        <form class="eval-sweep-form" onSubmit={handleSweep} style={{ margin: '0.5rem 0', padding: '0.75rem', border: '1px solid var(--border-subtle)', borderRadius: '4px', display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'flex-end' }}>
          <div>
            <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', display: 'block', marginBottom: '2px' }}>Base variant</label>
            <select value={sweepBaseId} onChange={evt => setSweepBaseId(evt.target.value)} required style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)' }}>
              <option value="">— choose —</option>
              {variants.map(v => (
                <option key={v.id} value={v.id}>{v.label || v.id}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', display: 'block', marginBottom: '2px' }}>Dimension</label>
            <select value={sweepDim} onChange={evt => setSweepDim(evt.target.value)} style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)' }}>
              {SWEEP_DIMENSIONS.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          <div style={{ flex: '1 1 200px' }}>
            <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', display: 'block', marginBottom: '2px' }}>
              Values <span style={{ color: 'var(--text-tertiary)' }}>(comma-separated)</span>
            </label>
            <input
              type="text"
              value={sweepValues}
              onInput={evt => setSweepValues(evt.target.value)}
              placeholder={sweepDim === 'model' ? 'qwen2.5:7b, qwen2.5:14b' : '0.3, 0.7, 1.0'}
              required
              style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', width: '100%' }}
            />
          </div>
          <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
            <button type="submit" class="t-btn t-btn-primary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}>
              Run sweep
            </button>
            {sweepFb && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: sweepFb.startsWith('Error') ? 'var(--status-error)' : 'var(--text-secondary)' }}>
                {sweepFb}
              </span>
            )}
          </div>
        </form>
      )}

      {selected.length >= 2 && <ConfigDiffPanel />}

      <div class="variant-grid">
        {variants.map(v => (
          <VariantCard
            key={v.id}
            variant={v}
            selected={selected.includes(v.id)}
            onSelect={checked => toggleSelect(v.id, checked)}
            onClone={() => { /* clone logic */ }}
            onEdit={() => { focusVariantId.value = v.id; }}
            onDelete={() => fetchEvalVariants()}
          />
        ))}
      </div>
    </div>
  );
}
