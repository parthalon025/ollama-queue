/**
 * What it shows: The command center for your prompt optimization campaign.
 *   — Who's winning right now (variant + F1 score)
 *   — What the eval system is doing right now (if a test is running)
 *   — What you should try next (3 suggested actions)
 *   — A table of past test runs
 * Decision it drives: "Is this campaign converging toward a winner?
 *   Should I run another test, or promote the current leader?"
 */
import { h } from 'preact';
import { evalWinner, evalActiveRun, evalActiveSuggestions, evalActiveOracle } from '../stores/eval.js';
import F1Score from '../components/F1Score.jsx';
import VariantChip from '../components/VariantChip.jsx';
import EvalNextStepsCard from '../components/eval/EvalNextStepsCard.jsx';
import EvalOracleReport from '../components/eval/EvalOracleReport.jsx';
import ActiveRunProgress from '../components/eval/ActiveRunProgress.jsx';
import RunTriggerPanel from '../components/eval/RunTriggerPanel.jsx';
import RunHistoryTable from '../components/eval/RunHistoryTable.jsx';

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function EvalRuns() {
  const winner = evalWinner.value;
  const activeRun = evalActiveRun.value;
  const suggestions = evalActiveSuggestions.value;
  const oracle = evalActiveOracle.value;

  const terminalStatuses = ['complete', 'failed', 'cancelled'];
  const hasActiveRun = activeRun && !terminalStatuses.includes(activeRun.status);

  return (
    <div class="eval-campaign">
      {/* F1 leader chip — shows which variant is currently winning */}
      {winner && (
        <div class="eval-campaign__leader">
          <VariantChip
            variantId={winner.id}
            label={winner.label || winner.id}
            f1={winner.latest_f1}
            provider={winner.provider}
            isProduction={winner.is_production}
            isRecommended={winner.is_recommended}
          />
          {winner.latest_f1 != null && <F1Score value={winner.latest_f1} />}
          <span class="eval-campaign__leader-label">current leader</span>
        </div>
      )}

      {/* Active run progress — live pipeline status if a test is running */}
      {hasActiveRun && <ActiveRunProgress />}

      {/* Next steps card — 3 suggested actions based on campaign state */}
      <EvalNextStepsCard suggestions={suggestions} />

      {/* Oracle report — strategic diagnosis of the campaign */}
      <EvalOracleReport oracle={oracle} />

      {/* Run trigger — start a new evaluation run */}
      <RunTriggerPanel defaultCollapsed={hasActiveRun} />

      {/* Run history — past test runs with results */}
      <RunHistoryTable />
    </div>
  );
}
