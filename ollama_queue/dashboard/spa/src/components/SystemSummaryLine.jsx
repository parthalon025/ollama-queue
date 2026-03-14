// What it shows: One sentence describing the whole system: jobs waiting,
//   what's running, and which eval variant is winning.
// Decision it drives: "Do I need to take action anywhere, or is everything fine?"

import { h, Fragment } from 'preact';
import { currentJob, queueDepth } from '../stores/index.js';
import { evalWinner } from '../stores/eval.js';
import ModelChip from './ModelChip.jsx';
import VariantChip from './VariantChip.jsx';

export default function SystemSummaryLine() {
  const job = currentJob.value;
  const depth = queueDepth.value || 0;
  const winner = evalWinner.value;

  return (
    <div class="system-summary-line">
      <span>{depth} queued</span>
      {job
        ? <><span>·</span><ModelChip model={job.model} /></>
        : <span>· idle</span>
      }
      {winner && (
        <><span>·</span>
        <VariantChip variantId={winner.id || winner.variant_id} f1={winner.latest_f1} isProduction={winner.is_production} isRecommended={winner.is_recommended} /></>
      )}
    </div>
  );
}
