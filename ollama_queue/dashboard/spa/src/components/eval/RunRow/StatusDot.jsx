import { STATUS_DOT_COLORS } from './helpers.js';

// What it shows: A small colored circle indicating the eval run's current status.
// Decision it drives: User instantly distinguishes complete (green), failed (red),
//   in-progress (accent), and pending (gray) runs without reading the label.

export default function StatusDot({ status }) {
  return (
    <span style={{
      display: 'inline-block',
      width: '8px',
      height: '8px',
      borderRadius: '50%',
      background: STATUS_DOT_COLORS[status] ?? 'var(--text-tertiary)',
      marginRight: '0.4rem',
      flexShrink: 0,
    }} />
  );
}
