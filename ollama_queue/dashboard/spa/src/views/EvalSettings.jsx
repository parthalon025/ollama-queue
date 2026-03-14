/**
 * What it shows: The full configuration for the eval system. Set up which AI
 *   services to use for each role, where lesson data comes from, and when to
 *   automatically promote a winning variant.
 * Decision it drives: "Is the eval system correctly wired up and ready to run?
 *   What rules control automatic promotion?"
 */
import { useEffect, useState } from 'preact/hooks';
import {
  fetchEvalSettings, fetchEvalVariants, fetchEvalRuns,
  evalSettings, API,
} from '../stores';
import ProviderRoleSection from '../components/eval/ProviderRoleSection.jsx';
import SetupChecklist    from '../components/eval/SetupChecklist.jsx';
import DataSourcePanel   from '../components/eval/DataSourcePanel.jsx';
import JudgeDefaultsForm from '../components/eval/JudgeDefaultsForm.jsx';
import GeneralSettings   from '../components/eval/GeneralSettings.jsx';

// NOTE: ROLES.map() callback uses 'role' — never 'h' (shadows JSX factory)
const ROLES = ['generator', 'judge', 'optimizer', 'oracle'];

// C29: Eval Auto-Schedule panel
// What it shows: Controls for automatically triggering eval runs on a schedule.
// Decision it drives: User sets an interval + time and the queue runs evals automatically.
function EvalAutoSchedulePanel() {
  const [interval, setInterval] = useState('24h');
  const [hour, setHour] = useState('02');
  const [enabled, setEnabled] = useState(false);
  const [fb, setFb] = useState('');

  async function handleSave() {
    setFb('Saving…');
    try {
      const res = await fetch(`${API}/eval/schedule`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interval_hours: parseInt(interval) || 24, preferred_hour: parseInt(hour, 10), enabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFb('Saved');
      setTimeout(() => setFb(''), 2000);
    } catch (e) {
      setFb(`Error: ${e.message}`);
    }
  }

  return (
    <section class="eval-config__section">
      <h3 style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)', marginBottom: '0.5rem', color: 'var(--text-primary)' }}>
        Auto-Schedule
      </h3>
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
          Interval (hours)
          <input
            type="number" min="1" max="168"
            value={interval}
            onInput={e => setInterval(e.target.value)}
            style={{ marginLeft: '0.5rem', width: '4rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', background: 'var(--input-bg)', border: '1px solid var(--input-border)', borderRadius: 'var(--radius-sm)', padding: '2px 6px', color: 'var(--text-primary)' }}
          />
        </label>
        <label style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
          Preferred hour (0-23)
          <input
            type="number" min="0" max="23"
            value={hour}
            onInput={e => setHour(e.target.value)}
            style={{ marginLeft: '0.5rem', width: '3.5rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', background: 'var(--input-bg)', border: '1px solid var(--input-border)', borderRadius: 'var(--radius-sm)', padding: '2px 6px', color: 'var(--text-primary)' }}
          />
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
          Enabled
        </label>
        <button class="t-btn t-btn-secondary" style={{ fontSize: 'var(--type-label)', padding: '3px 10px' }} onClick={handleSave}>
          Save
        </button>
        {fb && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: fb.startsWith('Error') ? 'var(--status-error)' : 'var(--text-secondary)' }}>{fb}</span>}
      </div>
    </section>
  );
}

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
          key={activeRole}
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

      {/* C29: Auto-Schedule — periodic eval triggering */}
      <EvalAutoSchedulePanel />
    </div>
  );
}
