// What it shows: A clickable chip displaying a model name, used inline wherever a job or
//   eval result references a specific model.
// Decision it drives: Clicking navigates to the Models tab pre-filtered to that model,
//   so the user can immediately investigate performance, usage stats, or history for it.

import { h } from 'preact';
import { currentTab } from '../stores/health.js';

export default function ModelChip({ model }) {
  function handleClick() {
    currentTab.value = 'models';
  }
  return <button class="model-chip" onClick={handleClick}>{model}</button>;
}
