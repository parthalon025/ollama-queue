import { h } from 'preact';
// What it shows: The Eval page — sub-navigation and the active sub-view
// Decision it drives: Entry point for all eval functionality (runs, variants, trends, settings)

import { evalSubTab } from '../store.js';
import EvalRuns from '../views/EvalRuns.jsx';
import EvalVariants from '../views/EvalVariants.jsx';
import EvalTrends from '../views/EvalTrends.jsx';
import EvalSettings from '../views/EvalSettings.jsx';

// NOTE: .map() callback uses descriptive name 'tab' — never 'h' (shadows JSX factory)
const EVAL_TABS = [
  { id: 'runs',           label: 'Runs' },
  { id: 'configurations', label: 'Configurations' },
  { id: 'trends',         label: 'Trends' },
  { id: 'settings',       label: 'Settings' },
];

export default function Eval() {
  return (
    <div class="eval-page">
      <nav class="eval-subnav">
        {EVAL_TABS.map(tab => (
          <button
            key={tab.id}
            class={`eval-subnav-btn${evalSubTab.value === tab.id ? ' active' : ''}`}
            onClick={() => { evalSubTab.value = tab.id; }}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <div class="eval-content">
        {evalSubTab.value === 'runs'           && <EvalRuns />}
        {evalSubTab.value === 'configurations' && <EvalVariants />}
        {evalSubTab.value === 'trends'         && <EvalTrends />}
        {evalSubTab.value === 'settings'       && <EvalSettings />}
      </div>
    </div>
  );
}
