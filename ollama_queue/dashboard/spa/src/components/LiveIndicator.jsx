// What it shows: A small animated dot showing that something is active right now.
//   Blue pulse = actively running, slow grey = queued, blue accent = in eval.
// Decision it drives: "Is this model/job live right now? Safe to change settings?"

import { h } from 'preact';

export default function LiveIndicator({ state = 'running', pulse = true }) {
  const stateClass = {
    running:   'live-indicator live-indicator--running',
    queued:    'live-indicator live-indicator--queued',
    'in-eval': 'live-indicator live-indicator--eval',
  }[state] || 'live-indicator live-indicator--running';

  return <span class={`${stateClass}${pulse ? ' live-indicator--pulse' : ''}`} aria-label={`${state}`} />;
}
