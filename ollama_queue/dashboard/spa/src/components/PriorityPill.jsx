// What it shows: A color-coded badge displaying a job's numeric priority as a human-readable
//   tier label (critical / high / normal / low).
// Decision it drives: Lets the user instantly see how urgent a job is relative to others
//   in the queue, so they can decide whether to bump, cancel, or leave it alone.

import { h } from 'preact';

const PRIORITY_TIERS = [
  { max: 3,  label: 'critical', cls: 'priority-critical' },
  { max: 5,  label: 'high',     cls: 'priority-high'     },
  { max: 7,  label: 'normal',   cls: 'priority-normal'   },
  { max: 10, label: 'low',      cls: 'priority-low'      },
];

export default function PriorityPill({ priority }) {
  const tier = PRIORITY_TIERS.find(t => priority >= 1 && priority <= t.max);
  const label = tier ? tier.label : '?';
  const cls   = tier ? tier.cls   : 'priority-unknown';
  return <span class={`priority-pill ${cls}`}>{label}</span>;
}
