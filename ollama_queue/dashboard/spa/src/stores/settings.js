// What it does: Holds the global queue settings signal — thresholds, defaults, retention,
//   and daemon control values fetched from /api/settings.
// Decision it drives: The Settings page and any component that needs a config value
//   reads from this signal. Updates happen through the polling orchestrator in index.js.

import { signal } from '@preact/signals';

export const settings = signal({});       // /api/settings response
