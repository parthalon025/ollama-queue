import { useState } from 'preact/hooks';
import { evalRuns } from '../../stores';
import RunRow from './RunRow';
import { ShEmptyState } from 'superhot-ui/preact';
// What it shows: The full list of past and current eval runs, newest first,
//   with a status filter bar. Defaults to hiding failed runs to reduce clutter.
//   Each row shows status, winner config, quality score, date, and item count.
//   Expandable to per-variant metrics and scored pairs.
// Decision it drives: User tracks history, spots trends, and can drill into
//   any run to understand which config won and why. Filter lets them focus on
//   completed runs or see everything including failures.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

const STATUS_FILTERS = [
  { id: 'hide_failed', label: 'Hide failed' },
  { id: 'all',         label: 'All' },
  { id: 'complete',    label: 'Complete' },
  { id: 'failed',      label: 'Failed' },
  { id: 'generating',  label: 'Generating' },
  { id: 'judging',     label: 'Judging' },
];

export default function RunHistoryTable() {
  // Read .value at top so Preact subscribes to the signal
  const runs = evalRuns.value;
  const [statusFilter, setStatusFilter] = useState('hide_failed');

  if (!runs || runs.length === 0) {
    return (
      <div class="t-frame" data-label="Run History">
        <ShEmptyState mantra="AWAITING ORDERS" hint="create a variant and run" />
      </div>
    );
  }

  // Sort newest first
  const sorted = [...runs].sort((a, b) => {
    const ta = a.started_at ? new Date(a.started_at).getTime() : 0;
    const tb = b.started_at ? new Date(b.started_at).getTime() : 0;
    return tb - ta;
  });

  // Apply status filter
  const filtered = statusFilter === 'all'
    ? sorted
    : statusFilter === 'hide_failed'
      ? sorted.filter(run => run.status !== 'failed')
      : sorted.filter(run => run.status === statusFilter);

  const failedCount = sorted.filter(run => run.status === 'failed').length;

  return (
    <div class="t-frame" data-label="Run History">
      {/* Status filter pills */}
      <div class="eval-run-filter-bar">
        {STATUS_FILTERS.map(sf => (
          <button
            key={sf.id}
            class={`eval-subnav-btn eval-run-filter-btn${statusFilter === sf.id ? ' active' : ''}`}
            onClick={() => setStatusFilter(sf.id)}
          >
            {sf.label}
            {sf.id === 'failed' && failedCount > 0 && (
              <span class="eval-run-filter-count">{failedCount}</span>
            )}
          </button>
        ))}
      </div>
      {filtered.length === 0 ? (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)', padding: '0.5rem 0' }}>
          No runs match this filter.
        </div>
      ) : (
        filtered.map(run => (
          <RunRow key={run.id} run={run} />
        ))
      )}
    </div>
  );
}
