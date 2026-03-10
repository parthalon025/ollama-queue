import { h } from 'preact';
import { useState } from 'preact/hooks';
import { EVAL_TRANSLATIONS } from './translations.js';
import ResultsTable from './ResultsTable.jsx';
import ConfusionMatrix from './ConfusionMatrix.jsx';
import { API, evalActiveRun, evalSubTab, evalVariants, fetchEvalRuns, fetchEvalVariants, fetchRunAnalysis, startEvalPoll } from '../../store.js';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
// What it shows: A single eval run row with 3-level progressive disclosure.
//   L1: status dot, winner config, quality score, date, item count.
//   L2: per-variant metric table, scorer info, Ollama analysis panel, action buttons.
//   L3: paginated ResultsTable of scored pairs.
// Decision it drives: User sees run history at a glance, drills into
//   per-variant breakdown + AI-generated analysis, and can re-run, compare, or export.
// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

function statusDot(status) {
  const colors = {
    complete: 'var(--status-healthy)',
    failed: 'var(--status-error)',
    cancelled: 'var(--status-waiting)',
    generating: 'var(--accent)',
    judging: 'var(--accent)',
    pending: 'var(--text-tertiary)',
  };
  return (
    <span style={{
      display: 'inline-block',
      width: '8px',
      height: '8px',
      borderRadius: '50%',
      background: colors[status] ?? 'var(--text-tertiary)',
      marginRight: '0.4rem',
      flexShrink: 0,
    }} />
  );
}

function formatDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch { return iso; }
}

function fmtPct(val) {
  if (val == null) return '—';
  return `${Math.round(val * 100)}%`;
}

// Converts AI-generated markdown prose to readable plain text.
// Handles: ## headers → bold label, **x** → x, - bullet → • bullet.
// No library needed — analysis_md is structured prose, not full markdown.
function simpleRenderMd(text) {
  if (!text) return '';
  return text
    .replace(/^#{1,3} (.+)$/gm, '[$1]')       // ## Header → [Header]
    .replace(/\*\*(.+?)\*\*/g, '$1')            // **bold** → bold
    .replace(/^- (.+)$/gm, '• $1')             // - item → • item
    .replace(/\n{3,}/g, '\n\n')                // collapse 3+ blank lines
    .trim();
}

export default function RunRow({ run }) {
  const [level, setLevel] = useState(1); // 1 | 2 | 3
  const [repeatFb, repeatAct] = useActionFeedback();
  const [analyzeFb, analyzeAct] = useActionFeedback();
  const [promoteFb, promoteAct] = useActionFeedback();
  const [reanalyzeFb, reanalyzeAct] = useActionFeedback();
  const [analysis, setAnalysis] = useState(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [showBreakdown, setShowBreakdown] = useState(false);
  const [showAllItems, setShowAllItems] = useState(false);

  const {
    id,
    status,
    winner_variant,
    metrics,
    item_count,
    started_at,
    judge_model,
    judge_mode,
    item_ids,
    analysis_md,
  } = run;

  // Look up winner variant label for display. Falls back to bare ID if variants not loaded yet.
  const winnerVariantRow = winner_variant
    ? (evalVariants.value || []).find(v => v.id === winner_variant)
    : null;
  const winnerLabel = winnerVariantRow
    ? `${winner_variant} — ${winnerVariantRow.label}`
    : winner_variant;

  // Bayesian/tournament runs use AUC as primary quality metric instead of F1
  const isBayesian = judge_mode === 'bayesian' || judge_mode === 'tournament';

  // Only show Repeat button for runs that have reproducibility data persisted
  const canRepeat = Boolean(item_ids);

  async function handleAnalyze(evt) {
    evt.stopPropagation();
    await analyzeAct(
      'Generating analysis…',
      async () => {
        const res = await fetch(`${API}/eval/runs/${id}/analyze`, { method: 'POST' });
        let data = null;
        try { data = await res.json(); } catch { /* non-JSON body */ }
        if (!res.ok) throw new Error(data?.detail || `Analyze failed: ${res.status}`);
        // Refresh runs list so analysis_md appears once the background job finishes
        setTimeout(() => fetchEvalRuns(), 8000);
        return data;
      },
      () => `Analysis started for run #${id} — refresh in a moment`
    );
  }

  async function handleRepeat(evt) {
    evt.stopPropagation();
    await repeatAct(
      'Repeating run…',
      async () => {
        const res = await fetch(`${API}/eval/runs/${id}/repeat`, { method: 'POST' });
        let data = null;
        try { data = await res.json(); } catch { /* non-JSON body */ }
        if (!res.ok) throw new Error(data?.detail || `Repeat failed: ${res.status}`);
        evalSubTab.value = 'runs';
        const activeState = { run_id: data.run_id, status: 'queued' };
        evalActiveRun.value = activeState;
        sessionStorage.setItem('evalActiveRun', JSON.stringify(activeState));
        startEvalPoll(data.run_id);
        await fetchEvalRuns();
        return data;
      },
      data => `Run #${data.run_id} started`
    );
  }

  // Writes winner variant to lessons-db as production config and refreshes local eval_variants.
  async function handlePromote(evt) {
    evt.stopPropagation();
    await promoteAct(
      'Promoting…',
      async () => {
        const res = await fetch(`${API}/eval/runs/${id}/promote`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        let data = null;
        try { data = await res.json(); } catch { /* non-JSON body */ }
        if (!res.ok) throw new Error(data?.detail || `Promote failed: ${res.status}`);
        await fetchEvalRuns();
        await fetchEvalVariants();
        return data;
      },
      data => `Config ${data?.variant_id ?? winner_variant} promoted to production`
    );
  }

  // Parse metrics JSON if it came as string
  let parsedMetrics = {};
  try {
    parsedMetrics = typeof metrics === 'string' ? JSON.parse(metrics) : (metrics ?? {});
  } catch { parsedMetrics = {}; }

  // Winner quality — AUC for bayesian/tournament runs, F1 for legacy
  const winnerMetrics = winner_variant ? parsedMetrics[winner_variant] : null;
  const winnerQuality = isBayesian
    ? (winnerMetrics?.auc ?? null)
    : (winnerMetrics?.f1 ?? null);
  const winnerQualityLabel = isBayesian ? EVAL_TRANSLATIONS.auc.label : EVAL_TRANSLATIONS.f1.label;

  const variantIds = Object.keys(parsedMetrics);

  // Count judge calls: sum of item_count across variants (approx)
  const judgeCallCount = item_count ?? 0;

  function toggleLevel(next) {
    const newLevel = level === next ? 1 : next;
    setLevel(newLevel);
    // Fetch structured analysis when expanding to L2 for complete runs
    if (newLevel >= 2 && !analysis && status === 'complete') {
      setAnalysisLoading(true);
      fetchRunAnalysis(id).then(data => {
        setAnalysis(data);
        setAnalysisLoading(false);
      });
    }
  }

  const [tooltip, setTooltip] = useState(null);

  function handleTooltip(key) {
    setTooltip(tooltip === key ? null : key);
  }

  return (
    <div class="eval-run-row-container" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
      {/* L1 */}
      <div
        class="eval-run-row"
        style={{ cursor: 'pointer', userSelect: 'none' }}
        onClick={() => toggleLevel(2)}
        role="button"
        aria-expanded={level >= 2}
      >
        <div style={{ display: 'flex', alignItems: 'center', flex: 1, gap: '0.5rem', flexWrap: 'wrap' }}>
          {statusDot(status)}
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', textTransform: 'lowercase' }}>
            {EVAL_TRANSLATIONS[status]?.label ?? status}
          </span>
          {winner_variant && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)' }}>
              Winner: {winnerLabel}
            </span>
          )}
          {winnerQuality != null && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)' }}>
              {winnerQualityLabel}: {fmtPct(winnerQuality)}
            </span>
          )}
          {started_at && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
              {formatDate(started_at)}
            </span>
          )}
          {item_count != null && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
              · {item_count} items
            </span>
          )}
        </div>
        <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)', flexShrink: 0 }}>
          {level >= 2 ? '▲' : '▼'}
        </span>
      </div>

      {/* L2 */}
      {level >= 2 && (
        <div class="eval-run-row-l2">
          {/* Per-variant metric table — different columns for Bayesian vs legacy runs */}
          {variantIds.length > 0 && (() => {
            // Bayesian runs show AUC, Same-Category, Diff-Category, Score Gap
            // Legacy runs show F1, Recall, Precision, Actionability
            const metricKeys = isBayesian
              ? ['auc', 'same_mean_posterior', 'diff_mean_posterior', 'separation']
              : ['f1', 'recall', 'precision', 'actionability'];
            const colCount = metricKeys.length + 1; // +1 for Config column
            return (
              <div style={{ marginBottom: '0.75rem', overflowX: 'auto' }}>
                <table class="eval-metrics-table">
                  <thead>
                    <tr>
                      <th style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'left' }}>
                        Config
                      </th>
                      {metricKeys.map(metric => (
                        <th key={metric} style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', textAlign: 'center', cursor: 'pointer' }}>
                          {EVAL_TRANSLATIONS[metric]?.label ?? metric}
                          {EVAL_TRANSLATIONS[metric]?.tooltip && (
                            <button
                              onClick={e => { e.stopPropagation(); handleTooltip(metric); }}
                              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '0 2px', fontSize: '0.7rem' }}
                              aria-label={`Info about ${metric}`}
                            >
                              ?
                            </button>
                          )}
                        </th>
                      ))}
                    </tr>
                    {tooltip && EVAL_TRANSLATIONS[tooltip]?.tooltip && (
                      <tr>
                        <td colSpan={colCount} style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)', background: 'var(--bg-base)' }}>
                          {EVAL_TRANSLATIONS[tooltip].tooltip}
                        </td>
                      </tr>
                    )}
                  </thead>
                  <tbody>
                    {variantIds.map(vid => {
                      const vm = parsedMetrics[vid] ?? {};
                      const isWinner = vid === winner_variant;
                      return (
                        <tr key={vid} style={{ background: isWinner ? 'var(--accent-glow)' : 'transparent' }}>
                          <td style={{ padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: isWinner ? 'var(--accent)' : 'var(--text-primary)' }}>
                            {isWinner && '★ '}Config {vid}
                          </td>
                          {metricKeys.map(metric => (
                            <td key={metric} style={{ padding: '4px 8px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                              {metric === 'f1' && analysis?.confidence_intervals?.[vid]
                                ? `${fmtPct(vm[metric])} ±${Math.round((analysis.confidence_intervals[vid].high - analysis.confidence_intervals[vid].low) / 2 * 100)}`
                                : (vm[metric] != null ? fmtPct(vm[metric]) : '—')}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            );
          })()}

          {/* Posterior Separation — visual bar comparison for Bayesian/tournament runs.
              Shows same-category (green) vs diff-category (red) average posteriors
              so the user can see the discrimination gap at a glance. */}
          {isBayesian && winnerMetrics && winnerMetrics.same_mean_posterior != null && (
            <div style={{ marginBottom: '0.75rem', padding: '0.5rem 0' }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.25rem' }}>
                Posterior Separation (winner)
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
                Green bar = same-category avg, red bar = different-category avg. Wider gap = better discrimination.
              </div>
              {/* Same-category bar (green) */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '4px' }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '80px', textAlign: 'right', flexShrink: 0 }}>
                  Same
                </span>
                <div style={{ flex: 1, background: 'var(--bg-raised)', borderRadius: '3px', height: '16px', position: 'relative' }}>
                  <div style={{
                    width: `${Math.round((winnerMetrics.same_mean_posterior ?? 0) * 100)}%`,
                    height: '100%',
                    background: 'var(--status-healthy)',
                    borderRadius: '3px',
                    opacity: 0.7,
                  }} />
                </div>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '40px', flexShrink: 0 }}>
                  {fmtPct(winnerMetrics.same_mean_posterior)}
                </span>
              </div>
              {/* Diff-category bar (red) */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '80px', textAlign: 'right', flexShrink: 0 }}>
                  Different
                </span>
                <div style={{ flex: 1, background: 'var(--bg-raised)', borderRadius: '3px', height: '16px', position: 'relative' }}>
                  <div style={{
                    width: `${Math.round((winnerMetrics.diff_mean_posterior ?? 0) * 100)}%`,
                    height: '100%',
                    background: 'var(--status-error)',
                    borderRadius: '3px',
                    opacity: 0.7,
                  }} />
                </div>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', width: '40px', flexShrink: 0 }}>
                  {fmtPct(winnerMetrics.diff_mean_posterior)}
                </span>
              </div>
            </div>
          )}

          {/* Scorer info */}
          {judge_model && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem' }}>
              Scorer: {judge_model} · {judgeCallCount} items
            </div>
          )}

          {/* Winner model — shown when we can resolve the winning variant's model from evalVariants */}
          {winnerVariantRow?.model && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.25rem' }}>
              Winner model: {winnerVariantRow.model}
            </div>
          )}

          {/* Per-item breakdown — shows which items were hardest for this variant */}
          {status === 'complete' && analysis?.per_item?.length > 0 && analysis.per_item[0]?.status !== 'no_cluster_data' && (
            <div style={{
              marginBottom: '0.75rem',
              padding: '0.75rem',
              background: 'var(--bg-raised)',
              borderRadius: '4px',
              borderLeft: '2px solid var(--accent)',
            }}>
              <div style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 'var(--type-label)',
                color: 'var(--text-tertiary)',
                marginBottom: '0.4rem',
                cursor: 'pointer',
              }} onClick={() => setShowBreakdown(prev => !prev)}>
                Item Difficulty ({analysis.per_item.length} items) {showBreakdown ? '\u25B2' : '\u25BC'}
              </div>
              {showBreakdown && (
                <table class="eval-metrics-table" style={{ fontSize: 'var(--type-body)' }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left' }}>Item</th>
                      <th>F1</th>
                      <th>TP</th>
                      <th>FN</th>
                      <th>FP</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(showAllItems ? analysis.per_item : analysis.per_item.slice(0, 5)).map((item, idx) => (
                      <tr key={idx} style={{
                        background: item.f1 < 0.5 ? 'rgba(239,68,68,0.08)' : 'transparent',
                      }}>
                        <td style={{ textAlign: 'left', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {item.source_item_title || item.source_item_id}
                        </td>
                        <td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)' }}>{fmtPct(item.f1)}</td>
                        <td style={{ textAlign: 'center' }}>{item.tp}</td>
                        <td style={{ textAlign: 'center', color: item.fn > 0 ? 'var(--status-error)' : 'inherit' }}>{item.fn}</td>
                        <td style={{ textAlign: 'center', color: item.fp > 0 ? 'var(--status-error)' : 'inherit' }}>{item.fp}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {showBreakdown && analysis.per_item.length > 5 && (
                <button
                  style={{ marginTop: '0.3rem', fontSize: 'var(--type-label)', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}
                  onClick={() => setShowAllItems(prev => !prev)}
                >
                  {showAllItems ? 'Show top 5' : `Show all ${analysis.per_item.length} items`}
                </button>
              )}
            </div>
          )}

          {/* Analysis not computed indicator */}
          {status === 'complete' && analysis?.status === 'not_computed' && (
            <div style={{
              marginBottom: '0.75rem',
              padding: '0.5rem 0.75rem',
              background: 'var(--bg-raised)',
              borderRadius: '4px',
              fontSize: 'var(--type-label)',
              color: 'var(--text-tertiary)',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
            }}>
              Analysis not computed
              <button
                style={{ fontSize: 'var(--type-label)', color: 'var(--accent)', background: 'none', border: '1px solid var(--accent)', borderRadius: '3px', padding: '2px 8px', cursor: 'pointer' }}
                disabled={reanalyzeFb.phase === 'loading'}
                onClick={() => reanalyzeAct(
                  'Computing\u2026',
                  async () => {
                    const res = await fetch(`${API}/eval/runs/${id}/reanalyze`, { method: 'POST' });
                    if (!res.ok) throw new Error('Reanalyze failed');
                    return await res.json();
                  },
                  () => {
                    fetchRunAnalysis(id).then(data => setAnalysis(data));
                    return 'Analysis computed';
                  }
                )}
              >
                {reanalyzeFb.phase === 'loading' ? 'Computing\u2026' : 'Compute'}
              </button>
              {reanalyzeFb.msg && <span class={`action-fb action-fb--${reanalyzeFb.phase}`}>{reanalyzeFb.msg}</span>}
            </div>
          )}

          {/* Analysis panel — shows Ollama-generated explanation of why the run succeeded/failed */}
          {status === 'complete' && analysis_md && (
            <div style={{
              marginBottom: '0.75rem',
              padding: '0.75rem',
              background: 'var(--bg-raised)',
              borderRadius: '4px',
              borderLeft: '2px solid var(--accent)',
            }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.4rem' }}>
                Analysis
              </div>
              <div style={{
                fontFamily: 'var(--font-body)',
                fontSize: 'var(--type-body)',
                color: 'var(--text-primary)',
                whiteSpace: 'pre-line',
                wordBreak: 'break-word',
                margin: 0,
                lineHeight: 1.6,
              }}>
                {simpleRenderMd(analysis_md)}
              </div>
            </div>
          )}

          {/* Confusion matrix — shows cross-cluster principle bleed for completed runs */}
          {status === 'complete' && <ConfusionMatrix runId={id} />}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
            {status === 'complete' && winner_variant && (
              <div>
                <button
                  class="t-btn t-btn-primary"
                  style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
                  disabled={promoteFb.phase === 'loading'}
                  onClick={handlePromote}
                  title="Promote winning config to production"
                >
                  {promoteFb.phase === 'loading' ? 'Promoting…' : 'Use this config'}
                </button>
                {promoteFb.msg && <div class={`action-fb action-fb--${promoteFb.phase}`}>{promoteFb.msg}</div>}
              </div>
            )}
            {status === 'complete' && (
              <div>
                <button
                  class="t-btn t-btn-secondary"
                  style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
                  disabled={analyzeFb.phase === 'loading'}
                  onClick={handleAnalyze}
                  title={analysis_md ? 'Regenerate analysis' : 'Analyze this run with Ollama'}
                >
                  {analyzeFb.phase === 'loading' ? 'Analysing…' : (analysis_md ? '↺ Re-analyze' : '✦ Analyze')}
                </button>
                {analyzeFb.msg && <div class={`action-fb action-fb--${analyzeFb.phase}`}>{analyzeFb.msg}</div>}
              </div>
            )}
            {canRepeat && (
              <div>
                <button
                  class="t-btn t-btn-secondary"
                  style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
                  disabled={repeatFb.phase === 'loading'}
                  onClick={handleRepeat}
                  title="Re-run this eval with the same items and seed"
                >
                  {repeatFb.phase === 'loading' ? 'Repeating…' : '↺ Repeat'}
                </button>
                {repeatFb.msg && <div class={`action-fb action-fb--${repeatFb.phase}`}>{repeatFb.msg}</div>}
              </div>
            )}
            <button
              class="t-btn t-btn-secondary"
              style={{ fontSize: 'var(--type-label)', padding: '3px 10px', marginLeft: 'auto' }}
              onClick={() => toggleLevel(3)}
            >
              {level === 3 ? '▲ Hide scored pairs' : `▼ Show scored pairs (${item_count ?? '?'})`}
            </button>
          </div>
        </div>
      )}

      {/* L3 */}
      {level >= 3 && <ResultsTable runId={id} />}
    </div>
  );
}
