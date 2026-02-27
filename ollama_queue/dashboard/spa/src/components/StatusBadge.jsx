/**
 * Terminal-style status badge for queue job states.
 * Maps ollama-queue statuses to theme status classes.
 *
 * @param {{ state: string }} props
 */
export default function StatusBadge({ state }) {
  const s = (state || '').toLowerCase();

  let statusClass;
  const label = state;

  switch (s) {
    // Green — healthy/complete
    case 'running':
    case 'completed':
    case 'idle':
      statusClass = 't-status-healthy';
      break;

    // Orange — warning/paused
    case 'paused_health':
    case 'paused_manual':
    case 'paused_interactive':
      statusClass = 't-status-warning';
      break;

    // Red — error/failure
    case 'failed':
    case 'killed':
      statusClass = 't-status-error';
      break;

    // Amber/yellow — pending/waiting
    case 'pending':
      statusClass = 't-status-waiting';
      break;

    // Gray — cancelled/unknown
    case 'cancelled':
      statusClass = 't-status-waiting';
      break;

    default:
      statusClass = 't-status-waiting';
      break;
  }

  return (
    <span class={`t-status ${statusClass}`}>
      <span style="display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: currentColor;" />
      {label}
    </span>
  );
}
