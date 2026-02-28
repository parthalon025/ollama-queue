import { h } from 'preact';
import { useState, useMemo } from 'preact/hooks';

const STATUS_CONFIG = {
  completed: { icon: '\u2713', color: 'var(--status-healthy)' },
  failed: { icon: '\u2717', color: 'var(--status-error)' },
  killed: { icon: '\u2717', color: 'var(--status-error)' },
  cancelled: { icon: '\u2298', color: 'var(--text-secondary)' },
};

/**
 * Recent jobs history list with expandable failed rows.
 *
 * @param {{ jobs: Array<object> }} props
 *   Each job: { id, status, source, model, completed_at, started_at, outcome_reason }
 */
export default function HistoryList({ jobs }) {
  const allItems = jobs || [];
  const [tagFilter, setTagFilter] = useState(null);
  const tags = useMemo(() => [...new Set(allItems.map(j => j.tag).filter(Boolean))], [allItems]);
  const items = (tagFilter ? allItems.filter(j => j.tag === tagFilter) : allItems).slice(0, 20);

  if (allItems.length === 0) {
    return (
      <div class="t-frame" data-label="History">
        <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center;">
          No completed jobs yet
        </p>
      </div>
    );
  }

  return (
    <div class="t-frame" data-label="History">
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
          <HistoryRow key={job.id} job={job} />
        ))}
      </div>
    </div>
  );
}

function HistoryRow({ job }) {
  const [expanded, setExpanded] = useState(false);
  const cfg = STATUS_CONFIG[job.status] || STATUS_CONFIG.cancelled;
  const hasReason = (job.status === 'failed' || job.status === 'killed') && job.outcome_reason;
  const hasStall = !!job.stall_detected_at;
  const isExpandable = hasReason || hasStall;
  const duration = job.started_at && job.completed_at ? job.completed_at - job.started_at : null;

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
      </div>
      {expanded && (
        <div style="padding: 2px 0 6px 24px; display: flex; flex-direction: column; gap: 3px;">
          {hasStall && stallSignals && (
            <div class="data-mono" style="font-size: var(--type-micro); color: #f97316;">
              stall  posterior={pct(stallSignals.posterior)}  process={fmt(stallSignals.process)}  cpu={fmt(stallSignals.cpu)}  silence={fmt(stallSignals.silence)}  ps={fmt(stallSignals.ps)}
            </div>
          )}
          {hasReason && (
            <div class="data-mono" style="font-size: var(--type-micro); color: var(--status-error); white-space: pre-wrap;">
              {job.outcome_reason}
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
