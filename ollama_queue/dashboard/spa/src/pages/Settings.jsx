import { h } from 'preact';
import { useCallback } from 'preact/hooks';
import { settings, status, API } from '../stores';
import SettingsForm from '../components/SettingsForm.jsx';
import { useActionFeedback } from '../hooks/useActionFeedback.js';

// What it shows: All queue configuration — health thresholds that trigger automatic pausing,
//   job defaults (timeout, priority), data retention periods, and daemon manual controls
//   (pause/resume).
// Decision it drives: At what RAM/VRAM/load level should the queue stop starting new jobs?
//   How long before a non-LLM job is killed for timeout? How many days of job history to keep?
export default function Settings() {
  const sett = settings.value;
  const st = status.value;
  const daemonState = st && st.daemon ? st.daemon.state : null;

  const [pauseFb, pauseAct] = useActionFeedback();
  const [resumeFb, resumeAct] = useActionFeedback();

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
      'Pausing daemon…',
      async () => {
        const res = await fetch(`${API}/daemon/pause`, { method: 'POST' });
        if (!res.ok) throw new Error(`Pause failed: ${res.status}`);
        // Optimistically update daemon state so the button flips immediately
        if (status.value?.daemon) {
          status.value = { ...status.value, daemon: { ...status.value.daemon, state: 'paused' } };
        }
      },
      'Daemon paused'
    );
  }, [pauseAct]);

  const handleResume = useCallback(async () => {
    await resumeAct(
      'Resuming daemon…',
      async () => {
        const res = await fetch(`${API}/daemon/resume`, { method: 'POST' });
        if (!res.ok) throw new Error(`Resume failed: ${res.status}`);
        // Optimistically update daemon state so the button flips immediately
        if (status.value?.daemon) {
          status.value = { ...status.value, daemon: { ...status.value.daemon, state: 'running' } };
        }
      },
      'Daemon resumed'
    );
  }, [resumeAct]);

  return (
    <div class="flex flex-col gap-4 animate-page-enter">
      <SettingsForm
        settings={sett}
        daemonState={daemonState}
        onSave={handleSave}
        onPause={handlePause}
        onResume={handleResume}
        pauseFb={pauseFb}
        resumeFb={resumeFb}
      />
    </div>
  );
}
