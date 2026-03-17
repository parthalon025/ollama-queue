import { useState, useEffect } from 'preact/hooks';
import { ShEmptyState } from 'superhot-ui/preact';
import { fetchConfusionMatrix } from '../../stores/eval.js';
// What it shows: Cross-cluster transfer score heatmap for a completed eval run.
//   Diagonal cells (same cluster) should be high; off-diagonal (cross-cluster) should be low.
//   Red off-diagonal cells indicate "principle bleed" — clusters that share enough structure
//   that principles from one falsely match the other.
// Decision it drives: Identifies which cluster pairs have ambiguous boundaries, guiding
//   prompt refinement (make principles more discriminative) or cluster merging decisions.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

function cellColor(src, tgt, avg) {
  if (src === tgt) {
    // Diagonal: high same-cluster score is good
    if (avg >= 3.5) return 'rgba(34,197,94,0.2)';
    if (avg >= 2)   return 'rgba(234,179,8,0.2)';
    return 'rgba(239,68,68,0.2)';
  }
  // Off-diagonal: low cross-cluster score is good (no bleed)
  if (avg <= 2) return 'rgba(34,197,94,0.2)';
  if (avg <= 3) return 'rgba(234,179,8,0.2)';
  return 'rgba(239,68,68,0.2)';
}

export default function ConfusionMatrix({ runId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded || data) return;
    setLoading(true);
    fetchConfusionMatrix(runId).then(result => {
      setData(result);
      setLoading(false);
    });
  }, [expanded, runId]);

  const hasData = data && data.clusters && data.clusters.length > 0;

  return (
    <div style={{ marginBottom: '0.75rem' }}>
      <button
        class="t-btn t-btn-secondary"
        style={{ fontSize: 'var(--type-label)', padding: '3px 10px', marginBottom: expanded ? '0.5rem' : 0, textTransform: 'uppercase' }}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? '▲ HIDE CLUSTER MATRIX' : '▼ CLUSTER TRANSFER MATRIX'}
      </button>

      {expanded && loading && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', padding: '0.5rem 0', textTransform: 'uppercase' }}>
          LOADING…
        </div>
      )}

      {expanded && !loading && !hasData && (
        <ShEmptyState message="NO CLUSTER DATA" />
      )}

      {expanded && hasData && (
        <div class="t-frame" style={{ marginTop: '0.25rem' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            CLUSTER TRANSFER MATRIX
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table class="eval-metrics-table" style={{ fontSize: 'var(--type-label)', width: 'auto' }}>
              <thead>
                <tr>
                  <th style={{ padding: '3px 6px', textAlign: 'left', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', textTransform: 'uppercase' }}>
                    SOURCE \ TARGET
                  </th>
                  {data.clusters.map(tgt => (
                    <th key={tgt} style={{ padding: '3px 6px', textAlign: 'center', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', maxWidth: '80px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textTransform: 'uppercase' }} title={tgt}>
                      {tgt.length > 10 ? tgt.slice(0, 10) + '…' : tgt}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.clusters.map(src => (
                  <tr key={src}>
                    <td style={{ padding: '3px 6px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', maxWidth: '80px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textTransform: 'uppercase' }} title={src}>
                      {src.length > 10 ? src.slice(0, 10) + '…' : src}
                    </td>
                    {data.clusters.map(tgt => {
                      const cell = data.matrix[src]?.[tgt];
                      const avg = cell?.avg_transfer;
                      const bg = avg != null ? cellColor(src, tgt, avg) : 'transparent';
                      return (
                        <td key={tgt} style={{ padding: '3px 6px', textAlign: 'center', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', background: bg }} title={cell ? `${cell.count} PAIRS` : 'NO DATA'}>
                          {avg != null ? avg.toFixed(1) : '—'}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {data.flagged && data.flagged.length > 0 && (
            <div style={{ marginTop: '0.5rem' }}>
              {data.flagged.map(pair => (
                <div key={`${pair.source}-${pair.target}`} style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-warning)', textTransform: 'uppercase' }}>
                  HIGH BLEED: {pair.source} → {pair.target} ({pair.avg_transfer.toFixed(1)})
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
