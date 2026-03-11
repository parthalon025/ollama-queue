import { h } from 'preact';
import { useState, useMemo, useEffect, useRef } from 'preact/hooks';
import { applyFreshness, shatterElement } from 'superhot-ui';
import { queue, API, refreshQueue } from '../stores';
import EmptyState from './EmptyState.jsx';

/**
 * What it shows: Every job waiting to run, priority-ordered, with estimated duration.
 *   The colored left border shows priority tier (red=critical → grey=background).
 *   Total wait estimate in the footer shows how backed-up the queue is.
 * Decision it drives: Is my job running soon or buried deep? Drag a row up to promote
 *   it, or hit × to cancel. Click a row to expand and see the full command.
 *   Tag filter chips let you focus on a specific job group.
 *
 * Drag a row to a new position to reprioritize. On drop, priorities are
 * renumbered 1..N and persisted via PUT /api/queue/{id}/priority.
 *
 * @param {{ jobs: Array<object>, currentJob: object|null }} props
 */
const PRIORITY_COLORS = {
  critical: '#ef4444', high: '#f97316',
  normal: '#3b82f6', low: '#6b7280', background: '#374151',
};

function priorityColor(p) {
  if (p <= 2) return PRIORITY_COLORS.critical;
  if (p <= 4) return PRIORITY_COLORS.high;
  if (p <= 6) return PRIORITY_COLORS.normal;
  if (p <= 8) return PRIORITY_COLORS.low;
  return PRIORITY_COLORS.background;
}

async function cancelJob(id, isRunning, setError, cardEl) {
  if (isRunning && !confirm('Cancel this running job? The process will be killed.')) return;
  if (cardEl) shatterElement(cardEl, { fragments: 6 });
  try {
    const res = await fetch(`${API}/queue/cancel/${id}`, { method: 'POST' });
    if (!res.ok) { setError(`Cancel failed: HTTP ${res.status}`); return; }
    await refreshQueue();
  } catch (e) {
    setError(`Cancel failed: ${e.message}`);
  }
}

// What it shows: Visual freshness state on a queue row based on how long ago the job was submitted.
// Decision it drives: Old jobs sitting in the queue stand out visually (cooling → frozen → stale),
//   prompting the user to investigate why they haven't started.
function FreshRow({ job, children }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!ref.current || !job.submitted_at) return;
    const ts = job.submitted_at * 1000; // convert seconds to ms
    applyFreshness(ref.current, ts, { cooling: 300, frozen: 1800, stale: 3600 });
    const interval = setInterval(() => {
      if (ref.current) applyFreshness(ref.current, ts, { cooling: 300, frozen: 1800, stale: 3600 });
    }, 30000);
    return () => clearInterval(interval);
  }, [job.submitted_at]);

  return <div ref={ref} data-fresh-row>{children}</div>;
}

export default function QueueList({ jobs, currentJob }) {
  const allItems = jobs || [];
  const [tagFilter, setTagFilter] = useState(null);
  const [dragIdx, setDragIdx] = useState(null);
  const [dropIdx, setDropIdx] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [cancelError, setCancelError] = useState(null);

  const tags = useMemo(() => [...new Set(allItems.map(j => j.tag).filter(Boolean))], [allItems]);
  const items = tagFilter ? allItems.filter(j => j.tag === tagFilter) : allItems;

  // Prepend the running job at position 0 (not draggable, not counted in wait)
  const displayItems = currentJob ? [{ ...currentJob, _isRunning: true }, ...items] : items;

  if (allItems.length === 0 && !currentJob) {
    return (
      <div class="t-frame" data-label="Waiting to Run">
        <EmptyState headline="Queue is empty" body="Jobs you submit will appear here." />
      </div>
    );
  }

  // Exclude the running job from the total wait estimate
  const totalWait = items.reduce((sum, j) => sum + (j.estimated_duration || 0), 0);

  function handleDragStart(e, idx) {
    setDragIdx(idx);
    e.dataTransfer.effectAllowed = 'move';
  }

  function handleDragOver(e, idx) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (idx !== dropIdx) setDropIdx(idx);
  }

  function handleDragEnd() {
    setDragIdx(null);
    setDropIdx(null);
  }

  function handleDrop(e, targetIdx) {
    e.preventDefault();
    if (dragIdx === null || dragIdx === targetIdx) {
      setDragIdx(null);
      setDropIdx(null);
      return;
    }

    // Reorder array: remove dragged item, insert at target
    const reordered = [...items];
    const [dragged] = reordered.splice(dragIdx, 1);
    reordered.splice(targetIdx, 0, dragged);

    // Assign priorities 1..N sequentially
    const updated = reordered.map((job, i) => ({ ...job, priority: i + 1 }));

    // Optimistic update — signal update causes Dashboard re-render
    queue.value = updated;

    // Persist changed priorities to backend
    updated.forEach((job, i) => {
      const original = items.find((j) => j.id === job.id);
      if (!original || original.priority !== i + 1) {
        fetch(`${API}/queue/${job.id}/priority`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ priority: i + 1 }),
        }).then((res) => {
          if (!res.ok) console.error(`Priority update failed for job ${job.id}: HTTP ${res.status}`);
        }).catch(console.error);
      }
    });

    setDragIdx(null);
    setDropIdx(null);
  }

  return (
    <div class="t-frame" data-label="Waiting to Run" data-footer={`Estimated wait for all jobs: ${formatWait(totalWait)}`}>
      {/* Tag filter chips */}
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

      {cancelError && (
        <div style="font-family: var(--font-mono); font-size: var(--type-label); color: var(--status-error); margin-bottom: 0.4rem;" role="alert">
          {cancelError}
        </div>
      )}
      <div class="flex flex-col gap-1">
        {displayItems.map((job, idx) => {
          // Running job uses a fixed display index; pending jobs use idx offset by 1 if running job present
          const dragIndex = job._isRunning ? null : (currentJob ? idx - 1 : idx);

          return (
            <FreshRow key={job.id} job={job}>
              {/* Row */}
              <div
                draggable={!job._isRunning}
                onDragStart={job._isRunning ? undefined : (e) => handleDragStart(e, dragIndex)}
                onDragOver={job._isRunning ? undefined : (e) => handleDragOver(e, dragIndex)}
                onDragEnd={job._isRunning ? undefined : handleDragEnd}
                onDrop={job._isRunning ? undefined : (e) => handleDrop(e, dragIndex)}
                onClick={() => setExpandedId(expandedId === job.id ? null : job.id)}
                class="flex items-center gap-2 py-1"
                style={[
                  `border-bottom: 1px solid var(--border-subtle);`,
                  job._isRunning
                    ? `border-left: 3px solid var(--accent);`
                    : `border-left: 3px solid ${priorityColor(job.priority)};`,
                  `padding-left: 6px;`,
                  job._isRunning ? 'cursor: pointer;' : 'cursor: grab;',
                  'transition: opacity 0.1s, background 0.1s;',
                  !job._isRunning && dragIndex !== null && dragIdx === dragIndex ? 'opacity: 0.35;' : 'opacity: 1;',
                  !job._isRunning && dragIndex !== null && dropIdx === dragIndex && dragIdx !== dragIndex
                    ? 'background: var(--bg-surface-raised); border-radius: 4px;' : '',
                ].join(' ')}
              >
                {/* Drag handle — hidden/greyed for running job */}
                <span
                  style={`color: ${job._isRunning ? 'transparent' : 'var(--text-tertiary)'}; font-size: 12px; user-select: none; flex-shrink: 0;`}
                  title={job._isRunning ? undefined : 'Drag up or down to change when this job runs'}
                >
                  ⠿
                </span>

                {/* Priority badge or RUNNING chip */}
                {job._isRunning ? (
                  <span
                    class="data-mono"
                    style="font-size: var(--type-micro); background: var(--accent); color: #fff;
                           padding: 1px 5px; border-radius: 3px; width: 36px; text-align: center;
                           flex-shrink: 0; letter-spacing: 0.03em;"
                  >
                    RUN
                  </span>
                ) : (
                  <span
                    class="data-mono"
                    style="font-size: var(--type-micro); color: var(--accent); width: 36px; text-align: center; flex-shrink: 0;"
                    title={`Priority ${job.priority}`}
                  >
                    {'★'.repeat(Math.max(1, Math.min(5, Math.ceil((10 - (job.priority || 5)) / 2))))}
                  </span>
                )}

                {/* Source + retry badge */}
                <span
                  class="data-mono"
                  style="font-size: var(--type-body); color: var(--text-primary); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                >
                  {job.source || 'unknown'}
                  {job.retry_count > 0 && (
                    <span style="font-size: 10px; background: var(--status-warning); color: #fff;
                                 padding: 0.1rem 0.3rem; border-radius: 3px; margin-left: 4px;"
                          title={`This job has been re-tried ${job.retry_count} time${job.retry_count > 1 ? 's' : ''} after failing`}>
                      retry #{job.retry_count}
                    </span>
                  )}
                </span>

                {/* Model */}
                <span
                  class="data-mono"
                  style="font-size: var(--type-label); color: var(--text-secondary); max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 0;"
                >
                  {job.model || '--'}
                </span>

                {/* Estimated duration */}
                <span
                  class="data-mono"
                  style="font-size: var(--type-micro); color: var(--text-tertiary); width: 48px; text-align: right; flex-shrink: 0;"
                >
                  {job.estimated_duration ? formatWait(job.estimated_duration) : '--'}
                </span>

                {/* Cancel button */}
                <button
                  class="t-btn"
                  style="background: none; border: none; color: var(--status-error); font-size: 14px; cursor: pointer; padding: 2px 6px; line-height: 1; flex-shrink: 0;"
                  title="Remove this job from the queue — cannot be undone"
                  onClick={(e) => { e.stopPropagation(); cancelJob(job.id, job._isRunning, setCancelError, e.currentTarget.closest('[data-fresh-row]')); }}
                >
                  ×
                </button>
              </div>

              {/* Expandable command panel */}
              {expandedId === job.id && (
                <div class="data-mono" style="font-size: var(--type-micro); color: var(--text-secondary);
                                              padding: 4px 8px 8px 32px; background: var(--bg-inset);">
                  <div style="color: var(--text-tertiary); text-transform: uppercase; font-size: 10px; margin-bottom: 2px;">shell command</div>
                  <div style="color: var(--text-primary); white-space: pre-wrap; word-break: break-all;">{job.command}</div>
                  {job.timeout && <div style="margin-top: 4px; color: var(--text-tertiary);">time limit: {job.timeout}s  •  resource type: {job.resource_profile || 'ollama'}</div>}
                </div>
              )}
            </FreshRow>
          );
        })}
      </div>
    </div>
  );
}

function formatWait(seconds) {
  if (!seconds || seconds <= 0) return '0s';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const hrs = Math.floor(m / 60);
  return `${hrs}h ${m % 60}m`;
}
