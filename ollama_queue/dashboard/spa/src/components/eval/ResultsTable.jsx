import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import { API } from '../../stores';
import ResultRow from './ResultRow.jsx';
// What it shows: Paginated list of scored pairs for a completed eval run,
//   with filter tabs for error class drill-down (All/TP/TN/FP/FN).
// Decision it drives: User can filter to specific error types to understand
//   where a variant's principle transfer breaks down.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

const PAGE_SIZE = 20;

export default function ResultsTable({ runId }) {
  const [results, setResults] = useState([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [activeFilter, setActiveFilter] = useState(null);

  async function loadPage(off, filter) {
    setLoading(true);
    setError(null);
    try {
      const filterParam = filter ? `&classification=${filter}` : '';
      const res = await fetch(`${API}/eval/runs/${runId}/results?limit=${PAGE_SIZE}&offset=${off}${filterParam}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (off === 0) {
        setResults(data);
      } else {
        setResults(prev => [...prev, ...data]);
      }
      setHasMore(data.length === PAGE_SIZE);
      setOffset(off + data.length);
    } catch (err) {
      console.error('ResultsTable load failed:', err);
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (runId) loadPage(0, activeFilter);
  }, [runId]);

  // Reset and reload when filter changes
  function handleFilterChange(newFilter) {
    setActiveFilter(newFilter);
    setResults([]);
    setOffset(0);
    setHasMore(true);
    loadPage(0, newFilter);
  }

  if (!runId) return null;

  return (
    <div class="eval-run-row-l3">
      {/* Filter tabs */}
      <div style={{
        display: 'flex', gap: '0.25rem', marginBottom: '0.5rem',
        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
      }}>
        {[
          { key: null, label: 'All' },
          { key: 'tp', label: 'TP' },
          { key: 'tn', label: 'TN' },
          { key: 'fp', label: 'FP' },
          { key: 'fn', label: 'FN' },
        ].map(tab => (
          <button
            key={tab.key ?? 'all'}
            onClick={() => handleFilterChange(tab.key)}
            style={{
              padding: '2px 8px',
              borderRadius: '3px',
              border: activeFilter === tab.key ? '1px solid var(--accent)' : '1px solid var(--border)',
              background: activeFilter === tab.key ? 'var(--accent-glow)' : 'transparent',
              color: activeFilter === tab.key ? 'var(--accent)' : 'var(--text-secondary)',
              cursor: 'pointer',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Scored pairs ({results.length}{hasMore ? '+' : ''})
      </div>

      {error && (
        <div style={{ padding: '0.75rem', color: 'var(--status-error)', fontSize: 'var(--type-label)' }}>
          Failed to load results: {error}
          <button
            style={{ marginLeft: '0.5rem', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}
            onClick={() => loadPage(0, activeFilter)}
          >
            Retry
          </button>
        </div>
      )}

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
          onClick={() => loadPage(offset, activeFilter)}
        >
          Load more
        </button>
      )}
      {results.length === 0 && !loading && !error && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
          {activeFilter ? `No ${activeFilter.toUpperCase()} pairs found.` : 'No scored pairs yet.'}
        </div>
      )}
    </div>
  );
}
