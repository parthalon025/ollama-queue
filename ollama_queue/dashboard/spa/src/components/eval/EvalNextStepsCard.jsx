/**
 * What it shows: Up to 3 recommended next steps after an eval run completes.
 *   Each suggestion is a concrete action (clone a variant, run oracle calibration,
 *   expand the test set) with a one-click button to act on it.
 * Decision it drives: "What should I try next to improve the F1 score?"
 *   Removes guesswork — the system suggests the most likely improvement.
 */
import { h } from 'preact';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import { useShatter } from '../../hooks/useShatter.js';
import { currentTab } from '../../stores/health.js';
import { evalSubTab, focusVariantId } from '../../stores/eval.js';

const ACTION_HANDLERS = {
  clone_variant: (suggestion) => {
    if (suggestion.base_variant_id && focusVariantId) focusVariantId.value = suggestion.base_variant_id;
    evalSubTab.value = 'variants';
    currentTab.value = 'eval';
  },
  run_oracle: () => {
    evalSubTab.value = 'config';
    currentTab.value = 'eval';
  },
  expand_eval_set: () => {
    evalSubTab.value = 'config';
    currentTab.value = 'eval';
  },
};

function SuggestionCard({ suggestion }) {
  const [fb, act] = useActionFeedback();
  const [actionRef, actionShatter] = useShatter('routine');
  const handler = ACTION_HANDLERS[suggestion.action_type] || (() => {});

  return (
    <div class="suggestion-card">
      <div class="suggestion-card__title">{suggestion.title}</div>
      {suggestion.description && <div class="suggestion-card__desc">{suggestion.description}</div>}
      <button
        ref={actionRef}
        class="suggestion-card__action"
        disabled={fb.phase === 'loading'}
        onClick={() => { actionShatter(); act('OPENING', async () => { handler(suggestion); }, () => 'DONE'); }}
      >
        {fb.phase === 'loading' ? '…' : (suggestion.action_label || 'Try this')}
      </button>
      {fb.msg && <span class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</span>}
    </div>
  );
}

export default function EvalNextStepsCard({ suggestions = [] }) {
  if (!suggestions.length) return null;
  const top3 = suggestions.slice(0, 3);

  return (
    <div class="eval-next-steps">
      <h3 class="eval-next-steps__heading">Next Steps</h3>
      <div class="eval-next-steps__cards">
        {top3.map((s, idx) => <SuggestionCard key={idx} suggestion={s} />)}
      </div>
    </div>
  );
}
