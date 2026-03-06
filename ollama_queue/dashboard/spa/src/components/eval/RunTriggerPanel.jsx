import { h } from 'preact';
import { useState } from 'preact/hooks';
import { signal } from '@preact/signals';
import {
  evalVariants, evalSettings,
  triggerEvalRun, fetchEvalRuns, startEvalPoll, evalActiveRun,
} from '../../store.js';
import { EVAL_TRANSLATIONS } from './translations.js';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import SchedulingModeSelector from './SchedulingModeSelector.jsx';
// What it shows: Form to configure and start a new eval run.
//   Fields: variant multi-select, items per group, scorer AI, scheduling mode,
//   dry-run toggle, and [Start Run] button.
// Decision it drives: User controls exactly which variant configs to test,
//   how many items to sample, which judge model to use, and how aggressively
//   to consume queue capacity.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function RunTriggerPanel({ defaultCollapsed }) {
  // Read signals at top of render — Preact subscription pattern
  const variants = evalVariants.value;
  const sett = evalSettings.value;

  const [open, setOpen] = useState(!defaultCollapsed);
  const [selectedVariants, setSelectedVariants] = useState([]);
  const [perCluster, setPerCluster] = useState(sett?.['eval.per_cluster'] ?? 4);
  const [judgeModel, setJudgeModel] = useState(sett?.['eval.judge_model'] ?? 'deepseek-r1:8b');
  const [runMode, setRunMode] = useState('batch');
  const [modeSubFields, setModeSubFields] = useState({});
  const [dryRun, setDryRun] = useState(false);
  const [fb, act] = useActionFeedback();

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
      'Starting run…',
      async () => {
        const body = {
          variants: selectedVariants,
          per_cluster: parseInt(perCluster) || 4,
          judge_model: judgeModel,
          run_mode: runMode,
          dry_run: dryRun,
          ...modeSubFields,
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
      result => result.run_id ? `Run #${result.run_id} started` : 'Dry run complete — check console for preview'
    );
  }

  // Group variants: system first, then user-created
  const systemVariants = (variants || []).filter(v => v.is_system);
  const userVariants = (variants || []).filter(v => !v.is_system);

  return (
    <div class="t-frame" data-label="Start New Run">
      <div
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', marginBottom: open ? '1rem' : 0 }}
        onClick={() => setOpen(o => !o)}
        role="button"
        aria-expanded={open}
      >
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', color: 'var(--text-primary)' }}>
          Configure run
        </span>
        <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)' }}>
          {open ? '▲' : '▼'}
        </span>
      </div>

      {open && (
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

          {/* Variant multi-select */}
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '0.4rem' }}>
              Configurations to test
            </div>
            {variants.length === 0 ? (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                No configurations found. Go to the Configurations tab to create some.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                {systemVariants.length > 0 && (
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '2px' }}>
                    — System defaults —
                  </div>
                )}
                {systemVariants.map(variant => (
                  <label key={variant.id} class="eval-checkbox-row">
                    <input
                      type="checkbox"
                      checked={selectedVariants.includes(variant.id)}
                      onChange={() => toggleVariant(variant.id)}
                    />
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)' }}>
                      {variant.id} — {variant.label}
                    </span>
                    {variant.is_recommended ? (
                      <span class="eval-badge eval-badge-recommended">★ Recommended</span>
                    ) : null}
                    {variant.latest_f1 != null && (
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
                        {EVAL_TRANSLATIONS.f1.label}: {Math.round(variant.latest_f1 * 100)}%
                      </span>
                    )}
                  </label>
                ))}
                {userVariants.length > 0 && (
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginTop: '4px', marginBottom: '2px' }}>
                    — Custom configs —
                  </div>
                )}
                {userVariants.map(variant => (
                  <label key={variant.id} class="eval-checkbox-row">
                    <input
                      type="checkbox"
                      checked={selectedVariants.includes(variant.id)}
                      onChange={() => toggleVariant(variant.id)}
                    />
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-primary)' }}>
                      {variant.label}
                    </span>
                    {variant.latest_f1 != null && (
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
                        {EVAL_TRANSLATIONS.f1.label}: {Math.round(variant.latest_f1 * 100)}%
                      </span>
                    )}
                  </label>
                ))}
              </div>
            )}
          </div>

          {/* Per-cluster items */}
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
              onClick={() => alert(EVAL_TRANSLATIONS.per_cluster.tooltip)}
              aria-label="Info about items per group"
            >
              ?
            </button>
          </div>

          {/* Judge model */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
              {EVAL_TRANSLATIONS.judge_model.label}
            </label>
            <input
              type="text"
              value={judgeModel}
              onInput={e => setJudgeModel(e.target.value)}
              class="t-input"
              style={{ padding: '4px 8px', fontSize: 'var(--type-label)', flex: 1 }}
              placeholder="deepseek-r1:8b"
            />
            <button
              type="button"
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)' }}
              onClick={() => alert(EVAL_TRANSLATIONS.judge_model.tooltip)}
              aria-label="Info about scorer AI"
            >
              ?
            </button>
          </div>

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
              Dry run — show what would run without submitting jobs
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
              {fb.phase === 'loading' ? 'Starting…' : 'Start Run'}
            </button>
            {fb.msg && <div class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</div>}
          </div>
        </form>
      )}
    </div>
  );
}
