import { useEffect } from 'preact/hooks';
// What it shows: Eval configuration settings — data source, scorer defaults,
//   and general numeric thresholds. Shows the setup checklist for new users.
// Decision it drives: User connects data, configures scoring AI, and tunes
//   run parameters. The checklist walks first-time users through setup.

import {
  fetchEvalSettings, fetchEvalVariants, fetchEvalRuns,
  evalSettings,
} from '../stores';
import SetupChecklist   from '../components/eval/SetupChecklist.jsx';
import DataSourcePanel  from '../components/eval/DataSourcePanel.jsx';
import JudgeDefaultsForm from '../components/eval/JudgeDefaultsForm.jsx';
import GeneralSettings  from '../components/eval/GeneralSettings.jsx';

export default function EvalSettings() {
  // Fetch fresh data whenever this view mounts
  useEffect(() => {
    fetchEvalSettings();
    fetchEvalVariants();
    fetchEvalRuns();
  }, []);

  // Read .value at top of body to subscribe to signal changes (drives checklist visibility)
  const settings = evalSettings.value;
  const setupComplete = settings['eval.setup_complete'] === true || settings['eval.setup_complete'] === 'true';

  return (
    <div class="eval-settings-page animate-fade-in" style="display: flex; flex-direction: column; gap: 16px;">
      {!setupComplete && <SetupChecklist />}
      <DataSourcePanel />
      <JudgeDefaultsForm />
      <GeneralSettings />
    </div>
  );
}
