// What it shows: An F1 score value color-coded by quality tier (green/amber/red),
//   with an optional delta badge showing change from a previous run.
// Decision it drives: Lets the user immediately see whether a prompt variant is performing
//   well, degrading, or improving — so they can decide whether to promote, investigate, or
//   run another eval.

import { h } from 'preact';

function getF1Class(value) {
  if (value >= 0.80) return 'f1-good';
  if (value >= 0.60) return 'f1-warn';
  return 'f1-bad';
}

export default function F1Score({ value, delta }) {
  if (value == null) {
    return <span class="f1-score f1-null">—</span>;
  }

  const cls = getF1Class(value);
  const formatted = value.toFixed(2);

  let deltaEl = null;
  if (delta != null) {
    const deltaCls = delta >= 0 ? 'f1-delta-pos' : 'f1-delta-neg';
    const deltaFormatted = (delta >= 0 ? '+' : '') + delta.toFixed(2);
    deltaEl = <span class={deltaCls}>{deltaFormatted}</span>;
  }

  return (
    <span class={`f1-score ${cls}`}>
      {formatted}
      {deltaEl}
    </span>
  );
}
