import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import { evalActiveRun, fetchEvalRuns, fetchEvalVariants, fetchEvalSettings, startEvalPoll } from '../store.js';
import ActiveRunProgress from '../components/eval/ActiveRunProgress.jsx';
import RunTriggerPanel from '../components/eval/RunTriggerPanel.jsx';
import RunHistoryTable from '../components/eval/RunHistoryTable.jsx';
// What it shows: The Eval Runs view — live run progress (if active), a collapsible
//   trigger panel for starting new runs, and the full run history table.
// Decision it drives: User sees what's running now, can start new runs, and
//   reviews past results to track which config is winning over time.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function EvalRuns() {
  // Read .value at top so Preact subscribes to the signal
  const activeRun = evalActiveRun.value;

  const terminalStatuses = ['complete', 'failed', 'cancelled'];
  const hasActiveRun = activeRun && !terminalStatuses.includes(activeRun.status);

  useEffect(() => {
    // Load data when view mounts
    fetchEvalRuns();
    fetchEvalVariants();
    fetchEvalSettings();
    // If an active run was restored from sessionStorage, restart polling so the
    // progress bar continues updating after a page reload.
    if (hasActiveRun) startEvalPoll(activeRun.run_id);
  }, []);

  return (
    <div class="flex flex-col gap-4 animate-page-enter">
      {/* Live progress panel — shown only when a run is active */}
      {hasActiveRun && <ActiveRunProgress />}

      {/* Trigger panel — auto-collapsed when a run is active */}
      <RunTriggerPanel defaultCollapsed={hasActiveRun} />

      {/* Run history */}
      <RunHistoryTable />
    </div>
  );
}
