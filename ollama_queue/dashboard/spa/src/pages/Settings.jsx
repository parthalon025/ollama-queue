import { h } from 'preact';
import { useCallback } from 'preact/hooks';
import { settings, status, API } from '../store';
import SettingsForm from '../components/SettingsForm.jsx';

/**
 * Settings page — reads signals from store, delegates to SettingsForm.
 */
export default function Settings() {
  const sett = settings.value;
  const st = status.value;
  const daemonState = st && st.daemon ? st.daemon.state : null;

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
    try {
      await fetch(`${API}/daemon/pause`, { method: 'POST' });
    } catch (e) {
      console.error('Pause failed:', e);
    }
  }, []);

  const handleResume = useCallback(async () => {
    try {
      await fetch(`${API}/daemon/resume`, { method: 'POST' });
    } catch (e) {
      console.error('Resume failed:', e);
    }
  }, []);

  return (
    <div class="flex flex-col gap-4 animate-page-enter">
      <SettingsForm
        settings={sett}
        daemonState={daemonState}
        onSave={handleSave}
        onPause={handlePause}
        onResume={handleResume}
      />
    </div>
  );
}
