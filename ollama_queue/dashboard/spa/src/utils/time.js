// What it does: Pure duration formatting utility shared across components.
// Decision it drives: Consistent human-readable time strings everywhere (queue ETAs,
//   job elapsed time, estimated durations) without duplicating the logic.

/**
 * Format a duration in seconds to a human-readable string.
 * e.g. 45 → "45s", 90 → "1m 30s", 3720 → "1h 2m"
 */
export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || seconds < 0) return '--';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
