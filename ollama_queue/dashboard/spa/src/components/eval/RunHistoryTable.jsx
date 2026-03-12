import { evalRuns } from '../../stores';
import RunRow from './RunRow';
// What it shows: The full list of past and current eval runs, newest first.
//   Each row shows status, winner config, quality score, date, and item count.
//   Expandable to per-variant metrics and scored pairs.
// Decision it drives: User tracks history, spots trends, and can drill into
//   any run to understand which config won and why.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function RunHistoryTable() {
  // Read .value at top so Preact subscribes to the signal
  const runs = evalRuns.value;

  if (!runs || runs.length === 0) {
    return (
      <div class="t-frame" data-label="Run History">
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
          No eval runs yet. Start a run above to begin.
        </div>
      </div>
    );
  }

  // Sort newest first
  const sorted = [...runs].sort((a, b) => {
    const ta = a.started_at ? new Date(a.started_at).getTime() : 0;
    const tb = b.started_at ? new Date(b.started_at).getTime() : 0;
    return tb - ta;
  });

  return (
    <div class="t-frame" data-label="Run History">
      {sorted.map(run => (
        <RunRow key={run.id} run={run} />
      ))}
    </div>
  );
}
