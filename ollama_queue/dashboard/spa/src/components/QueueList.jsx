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
export default function QueueList({ jobs }) {
  const items = jobs || [];
  const [dragIdx, setDragIdx] = useState(null);
  const [dropIdx, setDropIdx] = useState(null);

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
              'border-bottom: 1px solid var(--border-subtle);',
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

            {/* Source */}
            <span
              class="data-mono"
              style="font-size: var(--type-body); color: var(--text-primary); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
            >
              {job.source || 'unknown'}
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
