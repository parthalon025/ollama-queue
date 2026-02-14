import { h } from 'preact';

/**
 * Priority-sorted list of pending jobs.
 *
 * @param {{ jobs: Array<object> }} props
 *   Each job: { id, command, model, priority, source, estimated_duration }
 */
export default function QueueList({ jobs }) {
  const items = jobs || [];

  if (items.length === 0) {
    return (
      <div class="t-frame" data-label="Queue">
        <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center;">
          Queue empty
        </p>
      </div>
    );
  }

  const totalWait = items.reduce((sum, j) => sum + (j.estimated_duration || 0), 0);

  return (
    <div class="t-frame" data-label="Queue" data-footer={`Est. total wait: ${formatWait(totalWait)}`}>
      <div class="flex flex-col gap-1">
        {items.map((job) => (
          <div key={job.id}
            class="flex items-center gap-2 py-1"
            style="border-bottom: 1px solid var(--border-subtle);"
          >
            {/* Priority stars */}
            <span class="data-mono" style="font-size: var(--type-micro); color: var(--accent); width: 40px; text-align: center;"
              title={`Priority ${job.priority}`}>
              {'★'.repeat(Math.max(1, Math.min(5, Math.ceil((10 - (job.priority || 5)) / 2))))}
            </span>
            {/* Source */}
            <span class="data-mono" style="font-size: var(--type-body); color: var(--text-primary); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              {job.source || 'unknown'}
            </span>
            {/* Model */}
            <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary); max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              {job.model || '--'}
            </span>
            {/* Estimated duration */}
            <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); width: 48px; text-align: right;">
              {job.estimated_duration ? formatWait(job.estimated_duration) : '--'}
            </span>
            {/* Cancel button */}
            <button
              class="t-btn"
              style="background: none; border: none; color: var(--status-error); font-size: 14px; cursor: pointer; padding: 2px 6px; line-height: 1;"
              title="Cancel job"
              onClick={() => cancelJob(job.id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function cancelJob(id) {
  fetch(`/api/queue/cancel/${id}`, { method: 'POST' }).catch(console.error);
}

function formatWait(seconds) {
  if (!seconds || seconds <= 0) return '0s';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
