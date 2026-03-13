// What it shows: A unified status badge that maps health/status strings to the
//   SUPERHOT three-color palette. Replaces the mix of t-status-*, status-pill,
//   and custom CSS across all tabs.
// Decision it drives: One look, one meaning — color = status, always.
//   'healthy'|'ok'|'active' → phosphor, 'error' → threat, 'warning' → amber,
//   'waiting'|'idle' → dim white.

const STATUS_COLOR = {
  healthy: 'var(--sh-phosphor, var(--status-healthy))',
  ok:      'var(--sh-phosphor, var(--status-healthy))',
  active:  'var(--sh-phosphor, var(--status-healthy))',
  error:   'var(--sh-threat, var(--status-error))',
  warning: 'var(--status-warning)',
  waiting: 'var(--text-tertiary)',
  idle:    'var(--text-tertiary)',
};

// What it shows: A dot + label status badge.
// Decision it drives: User knows health at a glance without reading labels.
export default function ShStatusBadgeNative({ status, label, style: extraStyle }) {
  const s = (status || 'idle').toLowerCase();
  const color = STATUS_COLOR[s] || STATUS_COLOR.idle;
  const text  = label || status || 'idle';
  return (
    <span
      class="data-mono"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '0.3em',
        fontSize: 'var(--type-label)',
        color,
        ...extraStyle,
      }}
    >
      <span style={{
        display: 'inline-block',
        width: 6,
        height: 6,
        borderRadius: '50%',
        background: color,
        flexShrink: 0,
      }} />
      {text}
    </span>
  );
}
