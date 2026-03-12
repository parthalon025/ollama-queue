import { useState, useEffect } from 'preact/hooks';
import { API } from '../../stores';
// What it shows: Cross-cluster transfer score heatmap for a completed eval run.
//   Diagonal cells (same cluster) should be high; off-diagonal (cross-cluster) should be low.
//   Red off-diagonal cells indicate "principle bleed" — clusters that share enough structure
//   that principles from one falsely match the other.
// Decision it drives: Identifies which cluster pairs have ambiguous boundaries, guiding
//   prompt refinement (make principles more discriminative) or cluster merging decisions.

function cellColor(avg, isSame) {
  if (avg == null) return 'transparent';
  // Same-cluster: green gradient (higher = better)
  // Cross-cluster: red gradient (higher = worse — principle bleed)
  if (isSame) {
    const intensity = Math.round((avg / 5) * 0.5 * 255);
    return `rgba(34, 197, 94, ${intensity / 255})`;
  }
  const intensity = Math.round((avg / 5) * 0.7 * 255);
  return `rgba(239, 68, 68, ${intensity / 255})`;
}

export default function ConfusionMatrix({ runId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded || data) return;
    setLoading(true);
    fetch(`${API}/eval/runs/${runId}/confusion`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(setData)
      .catch(err => setData({ error: err.message }))
      .finally(() => setLoading(false));
  }, [expanded, runId]);

  const hasData = data && !data.error && data.clusters && data.clusters.length > 0;

  return (
    <div style={{ marginBottom: '0.75rem' }}>
      <button
        class="t-btn t-btn-secondary"
        style={{ fontSize: 'var(--type-label)', padding: '3px 10px', marginBottom: expanded ? '0.5rem' : 0 }}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? '▲ Hide confusion matrix' : '▼ Confusion matrix'}
      </button>

      {expanded && loading && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', padding: '0.5rem 0' }}>
          Loading…
        </div>
      )}

      {expanded && data?.error && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-error)', padding: '0.5rem 0' }}>
          {data.error}
        </div>
      )}

      {expanded && hasData && (
        <div style={{ overflowX: 'auto' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.25rem' }}>
            Avg transfer score — green diagonal = good, red off-diagonal = principle bleed
          </div>
          <table class="eval-metrics-table" style={{ fontSize: 'var(--type-label)' }}>
            <thead>
              <tr>
                <th style={{ padding: '3px 6px', textAlign: 'left', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                  Source ↓ Target →
                </th>
                {data.clusters.map(tgt => (
                  <th key={tgt} style={{ padding: '3px 6px', textAlign: 'center', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', maxWidth: '80px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={tgt}>
                    {tgt.length > 10 ? tgt.slice(0, 10) + '…' : tgt}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.clusters.map(src => (
                <tr key={src}>
                  <td style={{ padding: '3px 6px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', maxWidth: '80px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={src}>
                    {src.length > 10 ? src.slice(0, 10) + '…' : src}
                  </td>
                  {data.clusters.map(tgt => {
                    const cell = data.matrix[src]?.[tgt];
                    const isSame = src === tgt;
                    return (
                      <td key={tgt} style={{
                        padding: '3px 6px',
                        textAlign: 'center',
                        fontFamily: 'var(--font-mono)',
                        color: 'var(--text-primary)',
                        background: cell ? cellColor(cell.avg_transfer, isSame) : 'transparent',
                        fontWeight: cell && !isSame && cell.avg_transfer >= 3.0 ? 'bold' : 'normal',
                      }} title={cell ? `${cell.count} pairs` : 'no data'}>
                        {cell ? cell.avg_transfer.toFixed(1) : '—'}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>

          {data.flagged.length > 0 && (
            <div style={{ marginTop: '0.5rem' }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-error)', marginBottom: '0.25rem' }}>
                Flagged pairs (avg transfer ≥ 3.0)
              </div>
              {data.flagged.map(flag => (
                <div key={`${flag.source}-${flag.target}`} style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', paddingLeft: '0.5rem' }}>
                  {flag.source} → {flag.target}: {flag.avg_transfer.toFixed(1)} ({flag.count} pairs)
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {expanded && !loading && data && !data.error && (!data.clusters || data.clusters.length === 0) && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', padding: '0.5rem 0' }}>
          No cluster data available for this run. Run with contrastive variants to populate.
        </div>
      )}
    </div>
  );
}
