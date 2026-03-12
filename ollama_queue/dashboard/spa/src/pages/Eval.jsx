// What it shows: The Eval page — sub-navigation and the active sub-view
// Decision it drives: Entry point for all eval functionality (runs, variants, trends, settings)

import { evalSubTab } from '../stores';
import PageBanner from '../components/PageBanner.jsx';
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
  // Read .value at top of render body so Preact subscribes this component to signal changes.
  // Without this, clicking sub-tabs would update the signal but Eval would not re-render.
  const subTab = evalSubTab.value;
  return (
    <div class="eval-page">
      <PageBanner title="Eval" subtitle="test and compare AI model configurations" />
      <nav class="eval-subnav">
        {EVAL_TABS.map(tab => (
          <button
            key={tab.id}
            class={`eval-subnav-btn${subTab === tab.id ? ' active' : ''}`}
            onClick={() => { evalSubTab.value = tab.id; }}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <div class="eval-content">
        {subTab === 'runs'           && <EvalRuns />}
        {subTab === 'configurations' && <EvalVariants />}
        {subTab === 'trends'         && <EvalTrends />}
        {subTab === 'settings'       && <EvalSettings />}
      </div>
    </div>
  );
}
