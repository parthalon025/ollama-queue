// What it shows: Animated placeholder rows while data loads — prevents blank flashes
//   and layout shift. One uniform pattern across all loading states.
// Decision it drives: User knows data is coming, not missing.

export default function ShSkeletonNative({ rows = 3, style: extraStyle }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', ...extraStyle }}>
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          class="animate-pulse"
          style={{
            height: i === 0 ? '1.25rem' : '0.875rem',
            width: i % 3 === 0 ? '80%' : i % 3 === 1 ? '60%' : '70%',
            background: 'var(--bg-surface-raised)',
            borderRadius: 'var(--radius-sm)',
            opacity: 1 - i * 0.08,
          }}
        />
      ))}
    </div>
  );
}
