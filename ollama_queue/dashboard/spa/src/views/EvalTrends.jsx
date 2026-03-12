/**
 * What it shows: The full history of your optimization campaign as a timeline.
 *   Each dot is a test run; each star is when a winner was promoted.
 *   Background bands show what "level" the campaign is at:
 *     Level 0 (grey) = trying different prompt wordings
 *     Level 1 (blue) = fine-tuning parameters like temperature
 *     Level 2 (purple) = training a custom AI model
 * Decision it drives: "Is the system still improving or has it plateaued?
 *   Are we ready to try Level 1 (params tuning) or Level 2 (fine-tuning)?"
 */
import { h } from 'preact';
import { evalTrends, evalRuns, evalVariants } from '../stores/eval.js';
import F1LineChart from '../components/eval/F1LineChart.jsx';
import VariantStabilityTable from '../components/eval/VariantStabilityTable.jsx';
import SignalQualityPanel from '../components/eval/SignalQualityPanel.jsx';

function getCurrentLevel(variants) {
  // Level 2 if any promoted variant has training_config set
  if (variants.some(v => v.is_production && v.training_config)) return 2;
  // Level 1 if any promoted variant has non-empty params
  if (variants.some(v => v.is_production && v.params && Object.keys(v.params).length > 0)) return 1;
  return 0;
}

const LEVEL_LABELS = {
  0: 'Level 0 — Prompt Engineering',
  1: 'Level 1 — Parameter Tuning',
  2: 'Level 2 — Model Fine-Tuning',
};

export default function EvalTrends() {
  const variants = evalVariants.value;
  const runs = evalRuns.value;
  const currentLevel = getCurrentLevel(variants || []);

  // Build event markers from run history
  // NOTE: .flatMap() callback uses descriptive name 'run' — never 'h' (shadows JSX factory)
  const events = (runs || []).flatMap(run => {
    const evts = [];
    if (run.completed_at && run.status === 'complete') {
      evts.push({ type: 'run_completed', timestamp: run.completed_at, label: `Run #${run.id} complete` });
    }
    // NOTE: promotion event markers require a `promoted_at` field on the run object.
    // That field is not yet in the API response — placeholder removed until backend adds it.
    return evts;
  });

  return (
    <div class="eval-timeline">
      <div class="eval-timeline__level-indicator">
        <span class={`level-badge level-badge--${currentLevel}`}>{LEVEL_LABELS[currentLevel]}</span>
      </div>

      {/* F1LineChart reads evalTrends from the store directly — levelBands/currentLevel/events
          are passed as props but will be silently ignored by the current implementation */}
      <F1LineChart
        trends={evalTrends.value}
        events={events}
        levelBands={[
          { level: 0, color: 'rgba(107,114,128,0.08)', label: 'Level 0' },
          { level: 1, color: 'rgba(59,130,246,0.08)',  label: 'Level 1' },
          { level: 2, color: 'rgba(139,92,246,0.08)',  label: 'Level 2' },
        ]}
        currentLevel={currentLevel}
      />

      <VariantStabilityTable />
      <SignalQualityPanel />
    </div>
  );
}
