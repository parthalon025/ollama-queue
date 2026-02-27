import { h } from 'preact';
import { useState } from 'preact/hooks';
import { queue, API } from '../store';

/**
 * Priority-sorted list of pending jobs with drag-to-reorder.
 *
 * Drag a row to a new position to reprioritize. On drop, priorities are
 * renumbered 1..N and persisted via PUT /api/queue/{id}/priority.
 *
 * @param {{ jobs: Array<object> }} props
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

export default function QueueList({ jobs }) {
  const allItems = jobs || [];
  const [tagFilter, setTagFilter] = useState(null);
  const [dragIdx, setDragIdx] = useState(null);
  const [dropIdx, setDropIdx] = useState(null);

  const tags = [...new Set(allItems.map(j => j.tag).filter(Boolean))];
  const items = tagFilter ? allItems.filter(j => j.tag === tagFilter) : allItems;

  if (allItems.length === 0) {
    return (
      <div class="t-frame" data-label="Queue">
        <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center;">
          Queue empty
        </p>
      </div>
    );
  }

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
        }).catch(console.error);
      }
    });

    setDragIdx(null);
    setDropIdx(null);
  }

  return (
    <div class="t-frame" data-label="Queue" data-footer={`Est. total wait: ${formatWait(totalWait)}`}>
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

      <div class="flex flex-col gap-1">
        {items.map((job, idx) => (
          <div
            key={job.id}
            draggable
            onDragStart={(e) => handleDragStart(e, idx)}
            onDragOver={(e) => handleDragOver(e, idx)}
            onDragEnd={handleDragEnd}
            onDrop={(e) => handleDrop(e, idx)}
            class="flex items-center gap-2 py-1"
            style={[
              `border-bottom: 1px solid var(--border-subtle);`,
              `border-left: 3px solid ${priorityColor(job.priority)};`,
              `padding-left: 6px;`,
              'cursor: grab;',
              'transition: opacity 0.1s, background 0.1s;',
              dragIdx === idx ? 'opacity: 0.35;' : 'opacity: 1;',
              dropIdx === idx && dragIdx !== idx ? 'background: var(--surface-raised); border-radius: 4px;' : '',
            ].join(' ')}
          >
            {/* Drag handle */}
            <span
              style="color: var(--text-tertiary); font-size: 12px; user-select: none; flex-shrink: 0;"
              title="Drag to reprioritize"
            >
              ⠿
            </span>

            {/* Priority badge */}
            <span
              class="data-mono"
              style="font-size: var(--type-micro); color: var(--accent); width: 36px; text-align: center; flex-shrink: 0;"
              title={`Priority ${job.priority}`}
            >
              {'★'.repeat(Math.max(1, Math.min(5, Math.ceil((10 - (job.priority || 5)) / 2))))}
            </span>

            {/* Source + retry badge */}
            <span
              class="data-mono"
              style="font-size: var(--type-body); color: var(--text-primary); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
            >
              {job.source || 'unknown'}
              {job.retry_count > 0 && (
                <span style="font-size: 10px; background: #f97316; color: #fff;
                             padding: 0.1rem 0.3rem; border-radius: 3px; margin-left: 4px;">
                  retry {job.retry_count}
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
  fetch(`${API}/queue/cancel/${id}`, { method: 'POST' }).catch(console.error);
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
