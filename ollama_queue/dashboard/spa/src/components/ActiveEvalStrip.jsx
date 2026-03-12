/**
 * What it shows: A thin bar showing eval test progress from any page.
 *   Phase label, a progress bar, and the current best F1 score.
 *   Disappears when no eval is running.
 * Decision it drives: "Is the eval test still running? What phase is it in?
 *   Should I wait for results before changing anything?"
 */
import { h } from 'preact';
import { evalActiveRun, cancelEvalRun } from '../stores/eval.js';
import { currentTab } from '../stores/health.js';
import F1Score from './F1Score.jsx';
import { useActionFeedback } from '../hooks/useActionFeedback.js';

const PHASE_LABELS = {
  generating: 'Generating outputs',
  judging:    'Scoring with judge',
  analyzing:  'Analyzing results',
  promoting:  'Deciding winner',
};

export default function ActiveEvalStrip() {
  // Rules of Hooks: call hooks before any conditional return
  const [fb, act] = useActionFeedback();

  const run = evalActiveRun.value;
  if (!run || ['complete', 'failed', 'cancelled'].includes(run.status)) return null;

  const phaseLabel = PHASE_LABELS[run.phase] || run.phase || 'Running';
  const progress = run.progress_pct ?? 0;

  function handleClick() { currentTab.value = 'eval'; }

  return (
    <div class="active-eval-strip" role="status" aria-live="polite">
      <span class="active-eval-strip__label" onClick={handleClick} style="cursor:pointer">
        Eval: {phaseLabel}
      </span>
      <div class="active-eval-strip__bar">
        <div class="active-eval-strip__fill" style={`width:${progress}%`} />
      </div>
      {run.best_f1_so_far != null && <F1Score value={run.best_f1_so_far} />}
      <button
        class="active-eval-strip__cancel"
        disabled={fb.phase === 'loading'}
        onClick={() => act('Cancelling…', () => cancelEvalRun(run.run_id), () => 'Cancelled')}
      >
        {fb.phase === 'loading' ? '…' : '✕'}
      </button>
      {fb.msg && <span class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</span>}
    </div>
  );
}
