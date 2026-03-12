import { useState, useMemo } from 'preact/hooks';
import { useSignal } from '@preact/signals';
import EmptyState from './EmptyState.jsx';
import { retryJob } from '../stores/queue.js';

const STATUS_CONFIG = {
  completed: { icon: '\u2713', color: 'var(--status-healthy)' },
  failed: { icon: '\u2717', color: 'var(--status-error)' },
  killed: { icon: '\u2717', color: 'var(--status-error)' },
  cancelled: { icon: '\u2298', color: 'var(--text-secondary)' },
};

/**
 * What it shows: The last N completed jobs in reverse-chronological order, filterable by tag.
 *   Each row shows status icon, source name, model, and duration. Failed/killed rows are
 *   expandable to reveal the full command, error output, and failure reason.
 * Decision it drives: What failed and why? Is there a pattern (same source, same model, same
 *   time of day)? Which jobs run clean vs consistently crash? Expand a failed row to see the
 *   exact command output before deciding whether to retry or fix the underlying job.
 *
 * @param {{ jobs: Array<object> }} props
 *   Each job: { id, status, source, model, completed_at, started_at, outcome_reason }
 */
export default function HistoryList({ jobs }) {
  const allItems = jobs || [];
  const [tagFilter, setTagFilter] = useState(null);
  const tags = useMemo(() => [...new Set(allItems.map(j => j.tag).filter(Boolean))], [allItems]);
  const items = (tagFilter ? allItems.filter(j => j.tag === tagFilter) : allItems).slice(0, 20);
  // What it shows: Tracks which job's output was most recently copied to the clipboard.
  // Decision it drives: Flips the copy button label to "✓ Copied" for 2s as confirmation.
  const copied = useSignal(null);

  if (allItems.length === 0) {
    return (
      <div class="t-frame" data-label="Recent Jobs">
        <EmptyState headline="No history yet" body="Run your first job to see results here." />
      </div>
    );
  }

  return (
    <div class="t-frame" data-label="Recent Jobs">
      {tags.length > 0 && (
        <div style="display: flex; gap: 0.4rem; margin-bottom: 0.5rem; flex-wrap: wrap;">
          <span
            style={`padding: 0.2rem 0.6rem; border-radius: 12px; cursor: pointer; font-size: var(--type-label);
                    background: ${tagFilter === null ? 'var(--accent)' : 'var(--bg-inset)'}; color: #fff;`}
            onClick={() => setTagFilter(null)}
          >All</span>
          {tags.map(tag => (
            <span key={tag}
                  style={`padding: 0.2rem 0.6rem; border-radius: 12px; cursor: pointer; font-size: var(--type-label);
                          background: ${tagFilter === tag ? 'var(--accent)' : 'var(--bg-inset)'}; color: #fff;`}
                  onClick={() => setTagFilter(tag)}>{tag}</span>
          ))}
        </div>
      )}
      <div class="flex flex-col">
        {items.map((job) => (
          <HistoryRow key={job.id} job={job} copied={copied} />
        ))}
      </div>
    </div>
  );
}

// What it shows: A single history entry row — status icon, relative time, source name,
//   model, and duration. Failed/killed rows show a ↺ Retry button to re-queue the job.
// Decision it drives: Lets the user immediately retry a failed job without re-entering
//   parameters, and expand the row to read the failure reason before deciding to retry.
function HistoryRow({ job, copied }) {
  const [expanded, setExpanded] = useState(false);
  // retrySuccess tracks which job id was most recently retried, to flip the button label
  // to "✓ Requeued" for 2 seconds as visual confirmation.
  const retrySuccess = useSignal(null);
  const cfg = STATUS_CONFIG[job.status] || STATUS_CONFIG.cancelled;
  const hasReason = (job.status === 'failed' || job.status === 'killed') && job.outcome_reason;
  const hasStall = !!job.stall_detected_at;
  const isExpandable = hasReason || hasStall;
  const duration = job.started_at && job.completed_at ? job.completed_at - job.started_at : null;
  // preemption_count: how many times this job was paused mid-run for a higher-priority job.
  // Shows as ↺N when > 0 so the user knows the job was interrupted before finishing.
  const preempted = job.preemption_count > 0;

  let stallSignals = null;
  if (hasStall && job.stall_signals) {
    try { stallSignals = JSON.parse(job.stall_signals); } catch (_) {}
  }

  return (
    <div style="border-bottom: 1px solid var(--border-subtle);">
      <div
        class="flex items-center gap-2 py-1"
        style={isExpandable ? 'cursor: pointer;' : ''}
        onClick={isExpandable ? () => setExpanded(!expanded) : undefined}
      >
        {/* Status icon */}
        <span class="data-mono" style={`font-size: var(--type-body); color: ${cfg.color}; width: 16px; text-align: center;`}>
          {cfg.icon}
        </span>
        {/* Relative time */}
        <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); width: 52px; text-align: right;">
          {relativeTime(job.completed_at)}
        </span>
        {/* Source + stall indicator */}
        <span class="data-mono" style="font-size: var(--type-body); color: var(--text-primary); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
          {job.source || 'unknown'}
          {hasStall && (
            <span style="color: #f97316; margin-left: 4px;">⚠</span>
          )}
        </span>
        {/* Preemption badge — only shown when job.preemption_count > 0.
            Shows ↺N to indicate the job was interrupted N times by a higher-priority job. */}
        {preempted && (
          <span class="data-mono" title={`Interrupted ${job.preemption_count} time${job.preemption_count > 1 ? 's' : ''} — a higher-priority job needed to run first`}
                style="font-size: var(--type-micro); color: #f97316; white-space: nowrap;">
            ↺{job.preemption_count}×
          </span>
        )}
        {/* Model */}
        <span class="data-mono" style="font-size: var(--type-label); color: var(--text-secondary); max-width: 100px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
          {job.model || '--'}
        </span>
        {/* Duration */}
        <span class="data-mono" style="font-size: var(--type-micro); color: var(--text-tertiary); width: 44px; text-align: right;">
          {duration !== null ? formatDur(duration) : '--'}
        </span>
        {/* Expand indicator */}
        {isExpandable && (
          <span style="font-size: 10px; color: var(--text-tertiary); width: 12px; text-align: center;">
            {expanded ? '\u25B4' : '\u25BE'}
          </span>
        )}
        {/* Retry button — only on failed/killed rows. Fetches original job details and
            re-submits with the same parameters. Flips to "✓ Requeued" for 2s on success. */}
        {(job.status === 'failed' || job.status === 'killed') && (
          <button
            class="t-btn"
            style="font-size:var(--type-micro);padding:2px 8px;margin-left:8px;color:var(--status-warning);"
            onClick={async e => {
              e.stopPropagation();
              try {
                await retryJob(job.id);
                retrySuccess.value = job.id;
                setTimeout(() => { retrySuccess.value = null; }, 2000);
              } catch (err) {
                console.error('Retry failed:', err);
              }
            }}
          >
            {retrySuccess.value === job.id ? '\u2713 Requeued' : '\u21BA Retry'}
          </button>
        )}
      </div>
      {expanded && (
        <div style="padding: 2px 0 6px 24px; display: flex; flex-direction: column; gap: 3px;">
          {hasStall && stallSignals && (
            <div class="data-mono" style="font-size: var(--type-micro); color: #f97316;">
              frozen job — confidence: {pct(stallSignals.posterior)}  stdout-silence: {fmt(stallSignals.silence)}  cpu: {fmt(stallSignals.cpu)}  process: {fmt(stallSignals.process)}
            </div>
          )}
          {hasReason && (
            <div class="data-mono" style="font-size: var(--type-micro); color: var(--status-error); white-space: pre-wrap;">
              {job.outcome_reason}
            </div>
          )}
          {/* Copy output button — lets the user grab the job's stdout for debugging without leaving the dashboard */}
          {job.output && (
            <div style="margin-top:6px;display:flex;align-items:center;gap:8px;">
              <button
                class="t-btn"
                style="font-size:var(--type-micro);padding:2px 8px;"
                onClick={async () => {
                  await navigator.clipboard.writeText(job.output);
                  copied.value = job.id;
                  setTimeout(() => { copied.value = null; }, 2000);
                }}
              >
                {copied.value === job.id ? '\u2713 Copied' : '\u2398 Copy output'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function pct(v) { return v != null ? `${Math.round(v * 100)}%` : '?'; }
function fmt(v) { return v != null ? v.toFixed(2) : '?'; }

function relativeTime(ts) {
  if (!ts) return '--';
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function formatDur(seconds) {
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}
