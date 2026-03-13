// What it shows: A toggle that controls the CRT scanline intensity overlay.
// Decision it drives: Users who find the CRT effect distracting can dial it down
//   or turn it off entirely; power users can crank it up for full terminal aesthetic.
//   Preference persists across sessions in localStorage.

import { useEffect, useState } from 'preact/hooks';

const STORAGE_KEY = 'crt-prefs';

export default function ShCrtToggleNative() {
  const [intensity, setIntensity] = useState(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const prefs = JSON.parse(raw);
        return prefs.intensity ?? 'medium';
      }
    } catch (_) {}
    return 'medium';
  });

  useEffect(() => {
    const prefs = { intensity };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs)); } catch (_) {}

    // Apply CRT intensity by manipulating a CSS custom property on the root
    const root = document.documentElement;
    const opacityMap = { off: 0, low: 0.02, medium: 0.05, high: 0.1 };
    root.style.setProperty('--crt-scanline-opacity', opacityMap[intensity] ?? 0.05);
  }, [intensity]);

  const levels = ['off', 'low', 'medium', 'high'];
  const levelLabels = { off: 'Off', low: 'Low', medium: 'Med', high: 'High' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      <label class="data-mono" style={{ fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
        CRT Scanlines
      </label>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        {levels.map(lvl => (
          <button
            key={lvl}
            onClick={() => setIntensity(lvl)}
            class="data-mono"
            style={{
              padding: '0.25rem 0.625rem',
              border: `1px solid ${intensity === lvl ? 'var(--sh-phosphor, var(--accent))' : 'var(--border-subtle)'}`,
              borderRadius: 'var(--radius-sm)',
              background: intensity === lvl ? 'var(--accent-glow)' : 'transparent',
              color: intensity === lvl ? 'var(--sh-phosphor, var(--accent))' : 'var(--text-tertiary)',
              fontSize: 'var(--type-micro)',
              cursor: 'pointer',
              transition: 'var(--transition-fast, all 0.1s)',
            }}
          >
            {levelLabels[lvl]}
          </button>
        ))}
      </div>
    </div>
  );
}
