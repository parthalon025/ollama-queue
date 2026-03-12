// What it shows: Whether this AI model is currently the "judge" (scores outputs)
//   or "generator" (being tested) in the most recent eval run.
// Decision it drives: "Should I keep using this model as my judge?
//   Is a better option available?"
import { h } from 'preact';
import F1Score from './F1Score.jsx';
import { currentTab } from '../stores/health.js';

export default function EvalRoleBadge({ role, f1 }) {
  const label = role === 'judge' ? 'judge' : 'generator';

  function handleClick(e) {
    e.stopPropagation();
    currentTab.value = 'eval';
  }

  return (
    <button class={`eval-role-badge eval-role-badge--${role}`} onClick={handleClick} title="View eval results">
      <span class="eval-role-badge__label">{label}</span>
      {f1 != null && <F1Score value={f1} />}
    </button>
  );
}
