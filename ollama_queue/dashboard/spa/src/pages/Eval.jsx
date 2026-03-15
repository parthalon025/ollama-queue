// What it shows: The Eval page — sub-navigation and the active sub-view
// Decision it drives: Entry point for all eval functionality (runs, variants, trends, settings)

import { evalSubTab } from '../stores';
import { ShPageBanner } from 'superhot-ui/preact';
import { TAB_CONFIG } from '../config/tabs.js';
import EvalRuns from '../views/EvalRuns.jsx';
import EvalVariants from '../views/EvalVariants.jsx';
import EvalTrends from '../views/EvalTrends.jsx';
import EvalSettings from '../views/EvalSettings.jsx';

// NOTE: .map() callback uses descriptive name 'tab' — never 'h' (shadows JSX factory)
const TABS = [
  { id: 'campaign',  label: 'Campaign' },
  { id: 'variants',  label: 'Variants' },
  { id: 'timeline',  label: 'Timeline' },
  { id: 'config',    label: 'Config' },
];

export default function Eval() {
  const _tab = TAB_CONFIG.find(t => t.id === 'eval');
  // Read .value at top of render body so Preact subscribes this component to signal changes.
  // Without this, clicking sub-tabs would update the signal but Eval would not re-render.
  const subTab = evalSubTab.value;
  return (
    <div class="eval-page">
      <ShPageBanner namespace={_tab.namespace} page={_tab.page} subtitle={_tab.subtitle} />
      <nav class="eval-subnav">
        {TABS.map(tab => (
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
        {subTab === 'campaign'  && <EvalRuns />}
        {subTab === 'variants'  && <EvalVariants />}
        {subTab === 'timeline'  && <EvalTrends />}
        {subTab === 'config'    && <EvalSettings />}
      </div>
    </div>
  );
}
