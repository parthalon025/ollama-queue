import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import { useSignal } from '@preact/signals';
import { settings, status, API, restartDaemon } from '../stores';
import SettingsForm from '../components/SettingsForm';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import { ShCrtToggle, ShPageBanner } from 'superhot-ui/preact';
import { TAB_CONFIG } from '../config/tabs.js';

// Intensity → CSS var opacity map. Matches the values used by ShCrtToggleNative.
const CRT_OPACITY = { off: 0, low: 0.02, medium: 0.05, high: 0.1 };
const CRT_STORAGE_KEY = 'crt-prefs';

function readCrtIntensity() {
  try {
    const raw = localStorage.getItem(CRT_STORAGE_KEY);
    if (raw) return JSON.parse(raw).intensity ?? 'medium';
  } catch (_) {}
  return 'medium';
}

function applyCrtIntensity(intensity) {
  document.documentElement.style.setProperty(
    '--crt-scanline-opacity',
    CRT_OPACITY[intensity] ?? CRT_OPACITY.medium,
  );
  try {
    localStorage.setItem(CRT_STORAGE_KEY, JSON.stringify({ intensity }));
  } catch (_) {}
}

// Fields that require a daemon restart to take effect. Saved on blur like all
// other settings, but the banner stays visible until the daemon cycles through
// 'restarting' → 'running'.
const RESTART_REQUIRED_KEYS = new Set(['concurrency', 'stall_threshold_seconds', 'burst_detection_enabled']);

// What it shows: All queue configuration — health thresholds that trigger automatic pausing,
//   job defaults (timeout, priority), data retention periods, and daemon manual controls
//   (pause/resume/restart). Also shows a persistent warning banner whenever a
//   daemon-affecting setting has been saved but the daemon hasn't restarted yet.
// Decision it drives: At what RAM/VRAM/load level should the queue stop starting new jobs?
//   How long before a non-LLM job is killed for timeout? How many days of job history to keep?
//   The banner tells the user they need to restart the daemon for certain changes to take effect.
export default function Settings() {
  const _tab = TAB_CONFIG.find(t => t.id === 'settings');
  const sett = settings.value;
  const st = status.value;
  const daemonState = st && st.daemon ? st.daemon.state : null;

  const [intensity, setIntensity] = useState(() => readCrtIntensity());

  // Apply CSS var on mount to match persisted preference
  useEffect(() => {
    applyCrtIntensity(intensity);
  }, []);

  // Tracks whether a restart-required field was saved since the last daemon restart.
  // Component-scoped signal (not module-level) so it resets on unmount.
  const restartRequired = useSignal(false);
  const prevDaemonState = useRef(null);

  // Clear the banner once the daemon has cycled back to 'running' after a restart.
  useEffect(() => {
    if (daemonState === 'running' && prevDaemonState.current !== 'running') {
      restartRequired.value = false;
    }
    prevDaemonState.current = daemonState;
  }, [daemonState]);

  const [pauseFb, pauseAct] = useActionFeedback();
  const [resumeFb, resumeAct] = useActionFeedback();
  const [restartFb, restartAct] = useActionFeedback();

  /** Save a single setting key via PUT /api/settings. Returns true on success. */
  const handleSave = useCallback(async (key, value) => {
    try {
      const resp = await fetch(`${API}/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
      });
      if (resp.ok) {
        // Update local signal immediately
        settings.value = { ...settings.value, [key]: value };
        // Show restart banner if this field needs a daemon restart to take effect
        if (RESTART_REQUIRED_KEYS.has(key)) {
          restartRequired.value = true;
        }
        return true;
      }
      console.error('Settings save failed:', resp.status);
      return false;
    } catch (e) {
      console.error('Settings save error:', e);
      return false;
    }
  }, []);

  const handlePause = useCallback(async () => {
    await pauseAct(
      'PAUSING',
      async () => {
        const res = await fetch(`${API}/daemon/pause`, { method: 'POST' });
        if (!res.ok) throw new Error(`Pause failed: ${res.status}`);
        // Optimistically update daemon state so the button flips immediately
        if (status.value?.daemon) {
          status.value = { ...status.value, daemon: { ...status.value.daemon, state: 'paused' } };
        }
      },
      'PAUSED'
    );
  }, [pauseAct]);

  const handleResume = useCallback(async () => {
    await resumeAct(
      'RESUMING',
      async () => {
        const res = await fetch(`${API}/daemon/resume`, { method: 'POST' });
        if (!res.ok) throw new Error(`Resume failed: ${res.status}`);
        // Optimistically update daemon state so the button flips immediately
        if (status.value?.daemon) {
          status.value = { ...status.value, daemon: { ...status.value.daemon, state: 'running' } };
        }
      },
      'RESUMED'
    );
  }, [resumeAct]);

  return (
    <div class="flex flex-col gap-4 sh-stagger-children animate-page-enter" data-mood="nostalgic">
      <ShPageBanner namespace={_tab.namespace} page={_tab.page} subtitle={_tab.subtitle} />
      {restartRequired.value && (
        <div role="alert" style="background:color-mix(in srgb,var(--status-warning) 12%,transparent);border:1px solid var(--status-warning);border-radius:var(--radius);padding:10px 14px;display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px;">
          <span style="font-size:var(--type-label);color:var(--status-warning);">
            ⚠ Daemon restart required for these changes to take effect.
          </span>
          <button
            class="t-btn"
            style="font-size:var(--type-micro);padding:2px 10px;"
            disabled={restartFb.phase === 'loading'}
            onClick={() => restartAct('RESTARTING', restartDaemon, () => 'RESTARTED')}
          >
            {restartFb.phase === 'loading' ? 'Restarting…' : 'Restart daemon'}
          </button>
          {restartFb.msg && <div class={`action-fb action-fb--${restartFb.phase}`}>{restartFb.msg}</div>}
        </div>
      )}
      <SettingsForm
        settings={sett}
        daemonState={daemonState}
        onSave={handleSave}
        onPause={handlePause}
        onResume={handleResume}
        pauseFb={pauseFb}
        resumeFb={resumeFb}
      />
      {/* D20: CRT scanline intensity preference */}
      <div class="t-frame" data-label="Display" style="margin-top:1rem;">
        <ShCrtToggle
          intensity={intensity}
          onIntensityChange={({ intensity: lvl }) => {
            setIntensity(lvl);
            applyCrtIntensity(lvl);
          }}
        />
      </div>
      <div aria-label="Keyboard shortcuts" style="margin-top:24px;padding-top:16px;border-top:1px solid var(--border-subtle);">
        <p style="font-family:var(--font-mono);font-size:var(--type-micro);color:var(--text-tertiary);">
          Keyboard shortcuts:{'  '}
          <kbd>1</kbd> Now{'  ·  '}
          <kbd>2</kbd> Plan{'  ·  '}
          <kbd>3</kbd> History{'  ·  '}
          <kbd>4</kbd> Models{'  ·  '}
          <kbd>5</kbd> Settings{'  ·  '}
          <kbd>Cmd+K</kbd> Command palette
        </p>
      </div>
    </div>
  );
}
