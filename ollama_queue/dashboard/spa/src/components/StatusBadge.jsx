import { useEffect, useRef } from 'preact/hooks';
import { glitchText } from 'superhot-ui';

// What it shows: A colored dot-and-label badge for a single job or daemon state
//   (running, failed, killed, offline, paused, pending, etc.).
// Decision it drives: Lets the user instantly recognize health at a glance — green means
//   working, red means something needs attention. Glitch burst on error transition signals
//   the exact moment a job failed or the daemon went offline.

/**
 * Terminal-style status badge for queue job states.
 * Maps ollama-queue statuses to theme status classes.
 * Fires a SUPERHOT glitch burst when the state transitions into an error state (failed, killed, offline).
 *
 * @param {{ state: string }} props
 */
export default function StatusBadge({ state }) {
  const s = (state || '').toLowerCase();
  const isError = s === 'failed' || s === 'killed' || s === 'offline';

  const spanRef = useRef(null);
  const prevErrorRef = useRef(isError);

  // Glitch burst: fire once when transitioning INTO an error state.
  // The visual jolt signals "something just went wrong" — distinct from a static error badge.
  useEffect(() => {
    const wasError = prevErrorRef.current;
    prevErrorRef.current = isError;
    if (isError && !wasError && spanRef.current) {
      glitchText(spanRef.current, { intensity: 'high' });
    }
  }, [isError]);

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

    // Red — error/failure/offline
    case 'failed':
    case 'killed':
    case 'offline':
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
    <span ref={spanRef} class={`t-status ${statusClass}`}>
      <span style="display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: currentColor;" />
      {label}
    </span>
  );
}
