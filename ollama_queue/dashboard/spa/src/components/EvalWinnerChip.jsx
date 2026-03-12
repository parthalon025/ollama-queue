// What it shows: Always-visible chip showing which prompt variant is currently winning.
//   Gold star = it's in production. Silver star = recommended only.
// Decision it drives: "Has the winner changed? Do I need to promote it?"
//   Click to jump to the Eval tab.

import { h } from 'preact';
import { evalWinner } from '../stores/eval.js';
import { currentTab } from '../stores/health.js';
import { evalSubTab } from '../stores/eval.js';
import F1Score from './F1Score.jsx';

export default function EvalWinnerChip() {
  const winner = evalWinner.value;
  if (!winner) return null;

  function handleClick() {
    currentTab.value = 'eval';
    evalSubTab.value = 'timeline';
  }

  const star = winner.is_production ? '★' : '☆';

  return (
    <button class="eval-winner-chip" onClick={handleClick} title="View eval trends">
      <span class={winner.is_production ? 'eval-winner-chip__star--gold' : 'eval-winner-chip__star'}>{star}</span>
      <span class="eval-winner-chip__label">{winner.label || winner.variant_id || winner.id}</span>
      {winner.latest_f1 != null && <F1Score value={winner.latest_f1} />}
    </button>
  );
}
