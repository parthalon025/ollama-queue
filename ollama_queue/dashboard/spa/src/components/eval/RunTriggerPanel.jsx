import { useState, useEffect } from 'preact/hooks';
import { signal } from '@preact/signals';
import {
  evalVariants, evalSettings,
  triggerEvalRun, fetchEvalRuns, startEvalPoll, evalActiveRun,
  testDataSource, primeDataSource,
} from '../../stores';
import { backendsData } from '../../stores/health.js';
import { EVAL_TRANSLATIONS } from './translations.js';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import SchedulingModeSelector from './SchedulingModeSelector.jsx';
import ModelSelect from '../ModelSelect.jsx';
// What it shows: Form to configure and start a new eval run.
//   Fields: variant multi-select, items per group, scorer AI, scheduling mode,
//   dry-run toggle, and [Start Run] button.
// Decision it drives: User controls exactly which variant configs to test,
//   how many items to sample, which judge model to use, and how aggressively
//   to consume queue capacity.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

const JUDGE_MODE_DESCRIPTIONS = {
  bayesian: 'Uses multiple signals (paired comparisons, embeddings, scope, mechanism) — most accurate. Recommended for final decisions.',
  tournament: 'Head-to-head comparisons between configs — good for ranking when testing 3+ variants.',
  binary: 'Simple YES/NO per principle — fastest, least compute. Use for quick sanity checks.',
  rubric: '1–5 scores — legacy mode. Less reliable than Bayesian; kept for backward compatibility.',
};

export default function RunTriggerPanel({ defaultCollapsed }) {
  // Read signals at top of render — Preact subscription pattern
  const variants = evalVariants.value;
  const sett = evalSettings.value;

  const [open, setOpen] = useState(!defaultCollapsed);
  const [selectedVariants, setSelectedVariants] = useState([]);
  const [perCluster, setPerCluster] = useState(sett?.['eval.per_cluster'] ?? 4);
  const [judgeModel, setJudgeModel] = useState(sett?.['eval.judge_model'] ?? 'deepseek-r1:8b');
  const [judgeMode, setJudgeMode] = useState(sett?.['eval.judge_mode'] ?? 'bayesian');
  const [runMode, setRunMode] = useState('batch');
  const [modeSubFields, setModeSubFields] = useState({});
  const [dryRun, setDryRun] = useState(false);
  const [genBackend, setGenBackend] = useState('');
  const [judgeBackend, setJudgeBackend] = useState('');
  const [fb, act] = useActionFeedback();

  // null = not checked yet, 'checking', 'ready', 'needs_prime', 'offline'
  const [readiness, setReadiness] = useState(null);
  const [primeFb, primeAct] = useActionFeedback();
  const [activeTooltip, setActiveTooltip] = useState(null);

  // Helper: run the readiness check and update banner state.
  // Called on open (via useEffect) and on Retry button click.
  const checkReadiness = () => {
    setReadiness({ phase: 'checking' });
    testDataSource()
      .then(result => {
        if (!result || !result.ok) {
          setReadiness({ phase: 'offline', error: result?.error || 'No response' });
        } else if (result.cluster_count < 2 || result.item_count < 10) {
          setReadiness({ phase: 'needs_prime', item_count: result.item_count ?? 0, cluster_count: result.cluster_count ?? 0 });
        } else {
          setReadiness({ phase: 'ready', item_count: result.item_count, cluster_count: result.cluster_count });
        }
      })
      .catch(err => setReadiness({ phase: 'offline', error: err.message }));
  };

  // Check readiness when panel opens. Re-runs whenever `open` toggles true.
  useEffect(() => {
    if (!open) return;
    checkReadiness();
  }, [open]);

  function toggleVariant(varId) {
    setSelectedVariants(prev =>
      prev.includes(varId) ? prev.filter(v => v !== varId) : [...prev, varId]
    );
  }

  function handleModeChange(mode, subFields) {
    setRunMode(mode);
    setModeSubFields(subFields);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (selectedVariants.length === 0) {
      return;
    }
    await act(
      'STARTING',
      async () => {
        const body = {
          variants: selectedVariants,
          per_cluster: parseInt(perCluster) || 4,
          judge_model: judgeModel,
          judge_mode: judgeMode,
          run_mode: runMode,
          dry_run: dryRun,
          ...modeSubFields,
          ...(genBackend ? { gen_backend_url: genBackend } : {}),
          ...(judgeBackend ? { judge_backend_url: judgeBackend } : {}),
        };
        const result = await triggerEvalRun(body);
        if (!dryRun && result.run_id) {
          // Set active run and start live polling
          const activeState = { run_id: result.run_id, status: 'pending' };
          evalActiveRun.value = activeState;
          sessionStorage.setItem('evalActiveRun', JSON.stringify(activeState));
          startEvalPoll(result.run_id);
        }
        await fetchEvalRuns();
        // Auto-collapse after successful start
        setOpen(false);
        return result;
      },
      result => result.run_id ? `TEST RUN #${result.run_id} STARTED` : 'PREVIEW COMPLETE — NO JOBS SUBMITTED'
    );
  }

  // Group variants: system first, then user-created
  const systemVariants = (variants || []).filter(v => v.is_system);
  const userVariants = (variants || []).filter(v => !v.is_system);

  return (
    <div class="t-frame" data-label="Start a New Evaluation Run">
      <div
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', marginBottom: open ? '1rem' : 0 }}
        onClick={() => setOpen(o => !o)}
        role="button"
        aria-expanded={open}
      >
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', color: 'var(--text-primary)' }}>
          Set up and start a test run
        </span>
        <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)' }}>
          {open ? '▲' : '▼'}
        </span>
      </div>

      {open && (
        <div>
          {/* Readiness banner — shows whether lessons-db has enough data to run eval.
              Checks on open; Prime button triggers cluster_seed backfill; Retry re-checks. */}
          {readiness && readiness.phase !== 'checking' && (
            <div class={`eval-readiness eval-readiness--${readiness.phase}`}>
              {readiness.phase === 'ready' && (
                <span>&#x2713; Ready — {readiness.item_count} lessons across {readiness.cluster_count} groups available</span>
              )}
              {readiness.phase === 'needs_prime' && (
                <span>
                  &#x26A0; Not enough data to run — {readiness.item_count} lessons / {readiness.cluster_count} groups (need 10+ lessons, 2+ groups)
                  {' '}
                  <button
                    type="button"
                    class="t-btn t-btn-secondary"
                    style={{ fontSize: 'var(--type-label)', padding: '2px 8px', marginLeft: '0.5rem' }}
                    disabled={primeFb.phase === 'loading'}
                    onClick={() => primeAct('PRIMING', () => primeDataSource(), result => {
                      // After prime succeeds, update readiness from returned counts
                      if (result.cluster_count >= 2 && result.item_count >= 10) {
                        setReadiness({ phase: 'ready', item_count: result.item_count, cluster_count: result.cluster_count });
                      } else {
                        setReadiness({ phase: 'needs_prime', item_count: result.item_count ?? 0, cluster_count: result.cluster_count ?? 0 });
                      }
                      return `PRIMED \u00b7 ${result.updated} UPDATED`;
                    })}
                  >
                    {primeFb.phase === 'loading' ? 'Priming\u2026' : 'Prepare Data'}
                  </button>
                  {primeFb.msg && <span class={`action-fb action-fb--${primeFb.phase}`} style={{ marginLeft: '0.5rem' }}>{primeFb.msg}</span>}
                </span>
              )}
              {readiness.phase === 'offline' && (
                <span>
                  &#x2717; Cannot reach the lesson data source — {readiness.error}
                  {' '}
                  <button
                    type="button"
                    class="t-btn t-btn-secondary"
                    style={{ fontSize: 'var(--type-label)', padding: '2px 8px', marginLeft: '0.5rem' }}
                    onClick={checkReadiness}
                  >
                    Try Again
                  </button>
                </span>
              )}
            </div>
          )}

          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

          {/* Variant multi-select */}
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>
              Which configurations to compare
            </div>
            {variants.length === 0 ? (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                No configurations yet — go to the Configurations tab to create one before running a test
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                {systemVariants.length > 0 && (
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '2px' }}>
                    — Built-in configurations —
                  </div>
                )}
                {systemVariants.map(variant => (
                  <label key={variant.id} class="eval-checkbox-row" style={{ alignItems: 'flex-start' }}>
                    <input
                      type="checkbox"
                      checked={selectedVariants.includes(variant.id)}
                      onChange={() => toggleVariant(variant.id)}
                      style={{ marginTop: '2px' }}
                    />
                    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, gap: '2px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)' }}>
                          {variant.id} — {variant.label}
                        </span>
                        {variant.is_recommended ? (
                          <span class="eval-badge eval-badge-recommended">★ Recommended</span>
                        ) : null}
                        {variant.latest_f1 != null && (
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
                            Score: {Math.round(variant.latest_f1 * 100)}%
                          </span>
                        )}
                      </div>
                      {variant.description && (
                        <span class="eval-variant-description">
                          {variant.description}
                        </span>
                      )}
                    </div>
                  </label>
                ))}
                {userVariants.length > 0 && (
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginTop: '4px', marginBottom: '2px' }}>
                    — Your custom configurations —
                  </div>
                )}
                {userVariants.map(variant => (
                  <label key={variant.id} class="eval-checkbox-row" style={{ alignItems: 'flex-start' }}>
                    <input
                      type="checkbox"
                      checked={selectedVariants.includes(variant.id)}
                      onChange={() => toggleVariant(variant.id)}
                      style={{ marginTop: '2px' }}
                    />
                    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, gap: '2px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)' }}>
                          {variant.label}
                        </span>
                        {variant.latest_f1 != null && (
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
                            Score: {Math.round(variant.latest_f1 * 100)}%
                          </span>
                        )}
                      </div>
                      {variant.description && (
                        <span class="eval-variant-description">
                          {variant.description}
                        </span>
                      )}
                    </div>
                  </label>
                ))}
              </div>
            )}
          </div>

          {/* Per-cluster items */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                {EVAL_TRANSLATIONS.per_cluster.label}
              </label>
              <input
                type="number"
                min="1"
                max="20"
                value={perCluster}
                onInput={e => setPerCluster(e.target.value)}
                class="t-input eval-num-input"
              />
              <button
                type="button"
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)' }}
                onClick={() => setActiveTooltip(activeTooltip === 'per_cluster' ? null : 'per_cluster')}
                aria-label="Info about items per group"
              >
                ?
              </button>
            </div>
            {activeTooltip === 'per_cluster' && (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)', marginTop: '0.25rem', lineHeight: 1.5 }}>
                {EVAL_TRANSLATIONS.per_cluster.tooltip}
              </div>
            )}
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginTop: '0.15rem' }}>
              1–20 · higher = slower but more reliable results
            </div>
          </div>

          {/* Judge model */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                {EVAL_TRANSLATIONS.judge_model.label}
              </label>
              <ModelSelect
                value={judgeModel}
                onChange={val => setJudgeModel(val)}
                backend={evalSettings.value['eval.judge_backend'] ?? 'ollama'}
                placeholder="deepseek-r1:8b"
                class="t-input"
              />
              <button
                type="button"
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)' }}
                onClick={() => setActiveTooltip(activeTooltip === 'judge_model' ? null : 'judge_model')}
                aria-label="Info about scorer AI"
              >
                ?
              </button>
            </div>
            {activeTooltip === 'judge_model' && (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)', marginTop: '0.25rem', lineHeight: 1.5 }}>
                {EVAL_TRANSLATIONS.judge_model.tooltip}
              </div>
            )}
          </div>

          {/* Judge mode — which scoring strategy to use for evaluating generated principles */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                {EVAL_TRANSLATIONS.judge_mode_selector.label}
              </label>
              <select
                value={judgeMode}
                onChange={e => setJudgeMode(e.target.value)}
                class="t-input"
                style={{ flex: 1 }}
              >
                <option value="bayesian">Bayesian Fusion (recommended)</option>
                <option value="tournament">Paired Tournament</option>
                <option value="binary">Binary YES/NO</option>
                <option value="rubric">Rubric 1-5 (legacy)</option>
              </select>
              <button
                type="button"
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)' }}
                onClick={() => setActiveTooltip(activeTooltip === 'judge_mode_selector' ? null : 'judge_mode_selector')}
                aria-label="Info about scoring strategy"
              >
                ?
              </button>
            </div>
            {activeTooltip === 'judge_mode_selector' && (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--accent)', marginTop: '0.25rem', lineHeight: 1.5 }}>
                {EVAL_TRANSLATIONS.judge_mode_selector.tooltip}
              </div>
            )}
            {judgeMode && JUDGE_MODE_DESCRIPTIONS[judgeMode] && (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginTop: '0.25rem', lineHeight: 1.5 }}>
                {JUDGE_MODE_DESCRIPTIONS[judgeMode]}
              </div>
            )}
          </div>

          {/* Backend overrides — lets the user pin generator or judge to a specific GPU
              when multiple backends are configured. Empty = use settings default. */}
          {backendsData.value.length > 1 && (
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', marginBottom: '0.4rem' }}>
                Backend overrides (optional)
              </div>
              <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                <label style={{ flex: 1, minWidth: '200px' }}>
                  <span style={{ fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>Generator</span>
                  <select value={genBackend} onChange={e => setGenBackend(e.target.value)} class="t-input" style={{ width: '100%' }}>
                    <option value="">Use settings default</option>
                    <option value="auto">Auto (smart routing)</option>
                    {backendsData.value.filter(b => b.healthy).map(b => (
                      <option key={b.url} value={b.url}>{b.gpu_name || b.url}</option>
                    ))}
                  </select>
                </label>
                <label style={{ flex: 1, minWidth: '200px' }}>
                  <span style={{ fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>Judge</span>
                  <select value={judgeBackend} onChange={e => setJudgeBackend(e.target.value)} class="t-input" style={{ width: '100%' }}>
                    <option value="">Use settings default</option>
                    <option value="auto">Auto (smart routing)</option>
                    {backendsData.value.filter(b => b.healthy).map(b => (
                      <option key={b.url} value={b.url}>{b.gpu_name || b.url}</option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
          )}

          {/* Scheduling mode */}
          <SchedulingModeSelector value={runMode} onChange={handleModeChange} />

          {/* Dry run toggle */}
          <label class="eval-checkbox-row">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={e => setDryRun(e.target.checked)}
            />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
              Preview mode — show what would run without actually submitting any jobs
            </span>
          </label>

          {/* Submit */}
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <button
              type="submit"
              class="t-btn t-btn-primary"
              style={{ fontSize: 'var(--type-label)', padding: '4px 12px' }}
              disabled={fb.phase === 'loading'}
            >
              {fb.phase === 'loading' ? 'Starting…' : 'Start Test Run'}
            </button>
            {fb.msg && <div class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</div>}
          </div>
        </form>
        </div>
      )}
    </div>
  );
}
