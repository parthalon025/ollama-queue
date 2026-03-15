import { API, evalActiveRun, cancelEvalRun, startEvalPoll } from '../../stores';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import { ShPipeline } from 'superhot-ui/preact';

const PIPELINE_STAGES = [
    { id: 'queued',     label: 'Waiting' },
    { id: 'generating', label: 'Writing' },
    { id: 'judging',    label: 'Scoring' },
    { id: 'done',       label: 'Done' },
];
const PIPELINE_ORDER = ['queued', 'generating', 'judging', 'done'];
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

  const [cancelFb, cancelAct] = useActionFeedback();
  const [resumeFb, resumeAct] = useActionFeedback();
  const [retryFb, retryAct] = useActionFeedback();

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

  const isPaused = status === 'paused';
  const showFailureWarning = failure_rate > 0.05 && !isPaused;
  const etaLabel = formatEta(eta_s);

  async function handleCancel() {
    if (!confirm('Stop this test run? Any jobs already submitted will still finish, but no new ones will start.')) return;
    await cancelAct('Cancelling…', () => cancelEvalRun(run_id), `Run #${run_id} cancelled`);
  }

  async function handleResume() {
    await resumeAct(
      'Resuming…',
      async () => {
        const res = await fetch(`${API}/eval/runs/${run_id}/resume`, { method: 'POST' });
        if (!res.ok) throw new Error(`Resume failed: ${res.status}`);
        startEvalPoll(run_id);
      },
      'Resumed'
    );
  }

  async function handleRetryFailed() {
    await retryAct(
      'Retrying failed…',
      async () => {
        const res = await fetch(`${API}/eval/runs/${run_id}/retry-failed`, { method: 'POST' });
        if (!res.ok) throw new Error(`Retry failed: ${res.status}`);
        startEvalPoll(run_id);
      },
      'Failed jobs re-queued'
    );
  }

  const variantEntries = Object.entries(per_variant);

  return (
    <div class="t-frame eval-active-run-frame" data-label="Test Run in Progress">

      {/* Circuit breaker banner */}
      {isPaused && (
        <div class="eval-circuit-breaker-banner">
          <span>Too many errors — the run stopped automatically to prevent wasted work</span>
          <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.4rem', flexWrap: 'wrap' }}>
            <div>
              <button
                class="t-btn t-btn-secondary"
                style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
                disabled={resumeFb.phase === 'loading'}
                onClick={handleResume}
              >
                {resumeFb.phase === 'loading' ? 'Resuming…' : 'Continue despite errors'}
              </button>
              {resumeFb.msg && <div class={`action-fb action-fb--${resumeFb.phase}`}>{resumeFb.msg}</div>}
            </div>
            <div>
              <button
                class="t-btn t-btn-secondary"
                style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }}
                disabled={retryFb.phase === 'loading'}
                onClick={handleRetryFailed}
              >
                {retryFb.phase === 'loading' ? 'Retrying…' : 'Re-run failed jobs'}
              </button>
              {retryFb.msg && <div class={`action-fb action-fb--${retryFb.phase}`}>{retryFb.msg}</div>}
            </div>
            <div>
              <button
                class="t-btn t-btn-secondary"
                style={{ fontSize: 'var(--type-label)', padding: '3px 10px', color: 'var(--status-error)' }}
                disabled={cancelFb.phase === 'loading'}
                onClick={handleCancel}
              >
                {cancelFb.phase === 'loading' ? 'Cancelling…' : 'Cancel'}
              </button>
              {cancelFb.msg && <div class={`action-fb action-fb--${cancelFb.phase}`}>{cancelFb.msg}</div>}
            </div>
          </div>
        </div>
      )}

      {/* Pipeline swimlane: shows all stages, active step, model, per-phase progress */}
      {(() => {
          const _activeRun = evalActiveRun.value;
          const _pipelineNodes = PIPELINE_STAGES.map(stg => {
              let nodeStatus = 'pending';
              if (_activeRun) {
                  const currStatus = _activeRun.status === 'completed' ? 'done'
                      : _activeRun.stage === 'judging' ? 'judging'
                      : _activeRun.stage === 'generating' ? 'generating'
                      : 'queued';
                  const currIdx = PIPELINE_ORDER.indexOf(currStatus);
                  const nodeIdx = PIPELINE_ORDER.indexOf(stg.id);
                  nodeStatus = nodeIdx < currIdx ? 'done'
                      : nodeIdx === currIdx ? 'active'
                      : 'pending';
              }
              return { id: stg.id, label: stg.label, status: nodeStatus };
          });
          const _pipelineEdges = PIPELINE_ORDER.slice(0, -1).map((fromId, i) => ({
              from: fromId,
              to: PIPELINE_ORDER[i + 1],
          }));
          return (
              <ShPipeline
                  nodes={_pipelineNodes}
                  edges={_pipelineEdges}
                  ariaLabel="Eval pipeline progress"
                  compact={true}
              />
          );
      })()}
      {etaLabel && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', marginBottom: '0.5rem', textAlign: 'right' }}>
          {etaLabel}
        </div>
      )}

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
            ⚠ {Math.round(failure_rate * 100)}% of jobs failed
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
                    Configuration {variantId}
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
        <div>
          <button
            class="t-btn t-btn-secondary"
            style={{ fontSize: 'var(--type-label)', padding: '3px 10px', color: 'var(--status-error)' }}
            disabled={cancelFb.phase === 'loading'}
            onClick={handleCancel}
          >
            {cancelFb.phase === 'loading' ? 'Cancelling…' : 'Cancel run'}
          </button>
          {cancelFb.msg && <div class={`action-fb action-fb--${cancelFb.phase}`}>{cancelFb.msg}</div>}
        </div>
      )}
    </div>
  );
}
