import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import { API } from '../../store.js';
import ResultRow from './ResultRow.jsx';
// What it shows: Paginated list of scored pairs for a completed eval run.
//   First 20 pairs loaded immediately; more available via [Load more] button.
// Decision it drives: User can inspect specific pair scores to understand
//   why a variant won or lost on particular item pairs.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

const PAGE_SIZE = 20;

export default function ResultsTable({ runId }) {
  const [results, setResults] = useState([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);

  async function loadPage(off) {
    setLoading(true);
    try {
      const res = await fetch(`${API}/eval/runs/${runId}/results?limit=${PAGE_SIZE}&offset=${off}`);
      if (res.ok) {
        const data = await res.json();
        if (off === 0) {
          setResults(data);
        } else {
          setResults(prev => [...prev, ...data]);
        }
        setHasMore(data.length === PAGE_SIZE);
        setOffset(off + data.length);
      }
    } catch (e) {
      console.error('ResultsTable load failed:', e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (runId) loadPage(0);
  }, [runId]);

  if (!runId) return null;

  return (
    <div class="eval-run-row-l3">
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Scored pairs ({results.length}{hasMore ? '+' : ''})
      </div>
      <div>
        {results.map((result, idx) => (
          <ResultRow key={result.id ?? idx} result={result} />
        ))}
      </div>
      {loading && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', padding: '0.5rem' }}>
          Loading…
        </div>
      )}
      {hasMore && !loading && (
        <button
          class="t-btn t-btn-secondary"
          style={{ marginTop: '0.5rem', fontSize: 'var(--type-label)', padding: '4px 12px' }}
          onClick={() => loadPage(offset)}
        >
          Load more
        </button>
      )}
      {results.length === 0 && !loading && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
          No scored pairs yet.
        </div>
      )}
    </div>
  );
}
