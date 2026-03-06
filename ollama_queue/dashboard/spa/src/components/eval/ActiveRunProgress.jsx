import { h } from 'preact';
import { evalActiveRun, cancelEvalRun, startEvalPoll } from '../../store.js';
import { EVAL_TRANSLATIONS } from './translations.js';
// What it shows: Live progress of the currently-running eval run, including
//   stage (Writing/Scoring), overall % complete, per-variant progress bars,
//   ETA, failure rate, and circuit breaker banner if the run is paused.
// Decision it drives: User knows whether to wait, cancel, or intervene.
//   Circuit breaker buttons let them resume, retry failed jobs, or cancel.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

function formatEta(etaSeconds) {
  if (!etaSeconds || etaSeconds <= 0) return null;
  const mins = Math.round(etaSeconds / 60);
  if (mins <= 0) return 'Almost done';
  if (mins === 1) return 'About 1 min remaining';
  if (mins < 60) return `About ${mins} min remaining`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return `About ${hrs}h ${rem}m remaining`;
}

export default function ActiveRunProgress() {
  // Read .value at top so Preact subscribes to signal — triggers re-render on update
  const activeRun = evalActiveRun.value;

  const terminalStatuses = ['complete', 'failed', 'cancelled'];
  if (!activeRun || terminalStatuses.includes(activeRun.status)) return null;

  const {
    run_id,
    status,
    stage,
    completed = 0,
    total = 0,
    pct = 0,
    per_variant = {},
    eta_s,
    failure_rate = 0,
  } = activeRun;

  // DB stage values: 'generating', 'judging', 'fetch_items', 'fetch_targets'
  // Fall back to status when stage is null (e.g. run just started)
  const stageContext = stage || status;
  const stageLabel = (stageContext === 'generating' || stageContext === 'generate')
    ? EVAL_TRANSLATIONS.generating?.label ?? 'Writing principles…'
    : (stageContext === 'judging' || stageContext === 'judge' || stageContext === 'fetch_targets')
    ? EVAL_TRANSLATIONS.judging?.label ?? 'Scoring results…'
    : 'Working…';

  const isPaused = status === 'paused';
  const showFailureWarning = failure_rate > 0.05 && !isPaused;
  const etaLabel = formatEta(eta_s);

  async function handleCancel() {
    if (!confirm('Cancel this eval run? In-progress jobs will still complete.')) return;
    await cancelEvalRun(run_id);
  }

  async function handleResume() {
    await fetch(`/api/eval/runs/${run_id}/resume`, { method: 'POST' });
    startEvalPoll(run_id);
  }

  async function handleRetryFailed() {
    await fetch(`/api/eval/runs/${run_id}/retry-failed`, { method: 'POST' });
    startEvalPoll(run_id);
  }

  const variantEntries = Object.entries(per_variant);

  return (
    <div class="t-frame eval-active-run-frame" data-label="Live Run Progress">

      {/* Circuit breaker banner */}
      {isPaused && (
        <div class="eval-circuit-breaker-banner">
          <span>Too many failures — run paused</span>
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.4rem' }}>
            <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }} onClick={handleResume}>
              Resume anyway
            </button>
            <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }} onClick={handleRetryFailed}>
              Retry failed
            </button>
            <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px', color: 'var(--status-error)' }} onClick={handleCancel}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Stage indicator */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
        <span class="cursor-working" style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', color: 'var(--accent)' }}>
          {stageLabel} ({completed}/{total})
        </span>
        {etaLabel && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
            {etaLabel}
          </span>
        )}
      </div>

      {/* Overall progress bar */}
      <div class="eval-progress-track" style={{ marginBottom: '0.75rem' }}>
        <div
          class="eval-progress-bar"
          style={{ width: `${Math.min(pct, 100).toFixed(1)}%` }}
          role="progressbar"
          aria-valuenow={Math.round(pct)}
          aria-valuemin="0"
          aria-valuemax="100"
        />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '1rem' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
          {Math.round(pct)}% complete
        </span>
        {showFailureWarning && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--status-warning)' }}>
            ⚠ {Math.round(failure_rate * 100)}% failure rate
          </span>
        )}
      </div>

      {/* Per-variant progress bars */}
      {variantEntries.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginBottom: '1rem' }}>
          {variantEntries.map(([variantId, vdata]) => {
            const vpct = vdata.total > 0 ? (vdata.completed / vdata.total) * 100 : 0;
            return (
              <div key={variantId}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '2px' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
                    Config {variantId}
                  </span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
                    {vdata.completed}/{vdata.total}
                    {vdata.failed > 0 && (
                      <span style={{ color: 'var(--status-error)', marginLeft: '0.4rem' }}>
                        ({vdata.failed} failed)
                      </span>
                    )}
                  </span>
                </div>
                <div class="eval-progress-track eval-progress-track-sm">
                  <div class="eval-progress-bar" style={{ width: `${vpct.toFixed(1)}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Cancel button */}
      {!isPaused && (
        <button
          class="t-btn t-btn-secondary"
          style={{ fontSize: 'var(--type-label)', padding: '3px 10px', color: 'var(--status-error)' }}
          onClick={handleCancel}
        >
          Cancel run
        </button>
      )}
    </div>
  );
}
