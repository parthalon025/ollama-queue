// What it does: Holds the global queue settings signal — thresholds, defaults, retention,
//   and daemon control values fetched from /api/settings.
// Decision it drives: The Settings page and any component that needs a config value
//   reads from this signal. Updates happen through the polling orchestrator in index.js.

import { signal } from '@preact/signals';
import { API } from './_shared.js';

export const settings = signal({});       // /api/settings response

// What it does: POSTs to /api/daemon/restart, transitioning the daemon through
//   'restarting' → 'idle'/'running'. Used by the restart-required banner so the
//   user can apply daemon-affecting setting changes without leaving the Settings page.
// Decision it drives: After saving concurrency / stall_threshold_seconds /
//   burst_detection_enabled, the user can immediately restart from the banner.
export async function restartDaemon() {
    const res = await fetch(`${API}/daemon/restart`, { method: 'POST' });
    if (!res.ok) throw new Error(`Daemon restart failed: HTTP ${res.status}`);
    return res.json();
}
