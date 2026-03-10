// src/components/consumers/ConsumerRow.jsx
// What it shows: A single detected consumer service — name, type, streaming risk, request count,
//   last seen time, patch status, and health status.
// Decision it drives: User decides whether to include or ignore the service and whether to
//   apply patch immediately or defer until next restart.
import { h } from 'preact';
import { useState } from 'preact/hooks';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import { includeConsumer, ignoreConsumer, revertConsumer } from '../../stores';

const STATUS_BADGE = {
  discovered:      { cls: 'badge--neutral',  label: 'Discovered' },
  included:        { cls: 'badge--info',     label: 'Included' },
  pending_restart: { cls: 'badge--warning',  label: 'Pending restart' },
  patched:         { cls: 'badge--success',  label: 'Patched' },
  ignored:         { cls: 'badge--neutral',  label: 'Ignored' },
  error:           { cls: 'badge--error',    label: 'Error' },
};

const HEALTH_BADGE = {
  unknown:   { cls: '',               label: '' },
  verifying: { cls: 'badge--info',    label: '⏳ Verifying' },
  confirmed: { cls: 'badge--success', label: '✓ Confirmed' },
  partial:   { cls: 'badge--warning', label: '⚠ Partial' },
  failed:    { cls: 'badge--error',   label: '✗ Failed' },
};

export function ConsumerRow({ consumer }) {
  const [fb, run] = useActionFeedback();
  const [showStreamingConfirm, setShowStreamingConfirm] = useState(false);
  const [restartPolicy, setRestartPolicy] = useState('deferred');

  const statusInfo = STATUS_BADGE[consumer.status] || STATUS_BADGE.discovered;
  const healthInfo = HEALTH_BADGE[consumer.health_status] || HEALTH_BADGE.unknown;

  async function doInclude(opts = {}) {
    await run(
      'Including…',
      () => includeConsumer(consumer.id, { restart_policy: restartPolicy, ...opts }),
      result => `Included — ${result.patch_type || 'snippet generated'}`,
    );
  }

  function handleInclude() {
    if (consumer.is_managed_job) return;
    if (consumer.streaming_confirmed && !showStreamingConfirm) {
      setShowStreamingConfirm(true);
      return;
    }
    doInclude({ force_streaming_override: showStreamingConfirm });
  }

  const isDisabled = consumer.is_managed_job || fb.phase === 'loading';
  const streamingLabel = consumer.streaming_confirmed
    ? '⚠ Streaming confirmed'
    : consumer.streaming_suspect
    ? '⚠ Streaming suspected'
    : null;

  return (
    <tr class={`consumer-row consumer-row--${consumer.status}`}>
      <td>
        <span class="consumer-name">{consumer.name}</span>
        {consumer.is_managed_job && (
          <span class="badge badge--lock" title="Queue job — cannot include">🔒 Queue job</span>
        )}
        {consumer.patch_path && consumer.patch_path.startsWith('/etc/systemd') && (
          <span class="badge badge--system">🛡 System path</span>
        )}
      </td>
      <td>{consumer.type}</td>
      <td>
        {streamingLabel
          ? <span class={`badge ${consumer.streaming_confirmed ? 'badge--warning' : 'badge--caution'}`}>{streamingLabel}</span>
          : <span class="badge badge--ok">Safe</span>}
      </td>
      <td>{consumer.request_count ?? 0}</td>
      <td>{consumer.last_seen ? new Date(consumer.last_seen * 1000).toLocaleTimeString() : '—'}</td>
      <td>
        <span class={`badge ${statusInfo.cls}`}>{statusInfo.label}</span>
        {healthInfo.label && <span class={`badge ${healthInfo.cls}`} style="margin-left:4px">{healthInfo.label}</span>}
      </td>
      <td class="consumer-actions">
        {showStreamingConfirm && (
          <div class="streaming-confirm">
            <span>Proxy forces stream=False. Streaming responses will break.</span>
            <button onClick={() => doInclude({ force_streaming_override: true })}>Confirm include</button>
            <button onClick={() => setShowStreamingConfirm(false)}>Cancel</button>
          </div>
        )}
        {!showStreamingConfirm && (consumer.status === 'discovered' || consumer.status === 'ignored') && (
          <span>
            <select
              value={restartPolicy}
              onChange={evt => setRestartPolicy(evt.target.value)}
              disabled={isDisabled}
            >
              <option value="deferred">Apply on next restart</option>
              <option value="immediate">Apply now (restarts service)</option>
            </select>
            <button
              onClick={handleInclude}
              disabled={isDisabled}
              title={consumer.is_managed_job ? 'Cannot include managed queue jobs' : undefined}
            >
              {fb.phase === 'loading' ? fb.msg : 'Include'}
            </button>
            {fb.msg && fb.phase !== 'loading' && fb.phase !== 'error' && (
              <div class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</div>
            )}
            {consumer.status !== 'ignored' && (
              <button onClick={() => run('Ignoring…', () => ignoreConsumer(consumer.id), () => 'Ignored')}>
                Ignore
              </button>
            )}
          </span>
        )}
        {(consumer.status === 'pending_restart' || consumer.status === 'patched') && (
          <button onClick={() => run('Reverting…', () => revertConsumer(consumer.id), () => 'Reverted')}>
            Revert
          </button>
        )}
        {consumer.patch_snippet && (
          <details>
            <summary>Manual snippet</summary>
            <pre class="snippet">{consumer.patch_snippet}</pre>
            <button onClick={() => navigator.clipboard.writeText(consumer.patch_snippet)}>Copy</button>
          </details>
        )}
        {fb.phase === 'error' && <span class="action-fb--error">{fb.msg}</span>}
      </td>
    </tr>
  );
}
