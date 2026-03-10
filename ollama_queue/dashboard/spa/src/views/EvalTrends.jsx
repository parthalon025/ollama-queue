import { h } from 'preact';
import { useEffect } from 'preact/hooks';
// What it shows: F1 trend charts and stability indicators for all eval variants.
// Decision it drives: Is the eval system improving? Which variant is best?
//   Should the user act on a regression or stay the course?

import { fetchEvalTrends, fetchEvalVariants } from '../stores';
import TrendSummaryBar       from '../components/eval/TrendSummaryBar.jsx';
import F1LineChart           from '../components/eval/F1LineChart.jsx';
import VariantStabilityTable from '../components/eval/VariantStabilityTable.jsx';
import SignalQualityPanel    from '../components/eval/SignalQualityPanel.jsx';

export default function EvalTrends() {
  // Fetch fresh data whenever this view mounts
  useEffect(() => {
    fetchEvalTrends();
    fetchEvalVariants();
  }, []);

  return (
    <div class="eval-trends-page animate-fade-in">
      <TrendSummaryBar />
      <div style="margin-top: 16px;">
        <F1LineChart />
      </div>
      <div style="margin-top: 16px;">
        <VariantStabilityTable />
      </div>
      <div style="margin-top: 16px;">
        <SignalQualityPanel />
      </div>
    </div>
  );
}
