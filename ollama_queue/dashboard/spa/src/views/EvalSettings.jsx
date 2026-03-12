/**
 * What it shows: The full configuration for the eval system. Set up which AI
 *   services to use for each role, where lesson data comes from, and when to
 *   automatically promote a winning variant.
 * Decision it drives: "Is the eval system correctly wired up and ready to run?
 *   What rules control automatic promotion?"
 */
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import {
  fetchEvalSettings, fetchEvalVariants, fetchEvalRuns,
  evalSettings,
} from '../stores';
import ProviderRoleSection from '../components/eval/ProviderRoleSection.jsx';
import SetupChecklist    from '../components/eval/SetupChecklist.jsx';
import DataSourcePanel   from '../components/eval/DataSourcePanel.jsx';
import JudgeDefaultsForm from '../components/eval/JudgeDefaultsForm.jsx';
import GeneralSettings   from '../components/eval/GeneralSettings.jsx';

// NOTE: ROLES.map() callback uses 'role' — never 'h' (shadows JSX factory)
const ROLES = ['generator', 'judge', 'optimizer', 'oracle'];

export default function EvalSettings() {
  const [activeRole, setActiveRole] = useState('judge');

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
    <div class="eval-config animate-fade-in" style="display: flex; flex-direction: column; gap: 16px;">
      {!setupComplete && <SetupChecklist />}

      <section class="eval-config__section">
        <h3>Provider Configuration</h3>
        <p class="eval-config__hint">
          Generator = which AI creates the test outputs.
          Judge = which AI scores them.
          Optimizer = which AI suggests better prompts.
          Oracle = reference AI to check the judge's accuracy.
        </p>
        <div class="provider-role-tabs">
          {ROLES.map(role => (
            <button
              key={role}
              class={`provider-role-tab${activeRole === role ? ' provider-role-tab--active' : ''}`}
              onClick={() => setActiveRole(role)}
            >
              {role.charAt(0).toUpperCase() + role.slice(1)}
            </button>
          ))}
        </div>
        <ProviderRoleSection
          role={activeRole}
          settings={settings ? {
            provider: settings[`${activeRole}_provider`],
            model: settings[`${activeRole}_model`],
          } : {}}
        />
      </section>

      {/* Data source — where the eval gets its lesson items */}
      <DataSourcePanel />

      {/* Auto-promote rules — judge defaults + promotion thresholds */}
      <JudgeDefaultsForm />

      {/* General settings — numeric thresholds and scheduling */}
      <GeneralSettings />
    </div>
  );
}
