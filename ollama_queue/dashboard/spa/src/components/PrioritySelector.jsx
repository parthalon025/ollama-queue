import { h } from 'preact';

// What it shows: A segmented button control with 5 named priority levels
//   (Critical, High, Normal, Low, Background), highlighting the currently selected tier.
// Decision it drives: Lets the user pick a job priority by name rather than guessing
//   what numeric value means "urgent" — removes cognitive load on the submit form.

const LEVELS = [
  { label: 'Critical',   value: 1, color: 'var(--status-error)' },
  { label: 'High',       value: 3, color: 'var(--status-warning)' },
  { label: 'Normal',     value: 5, color: 'var(--accent)' },
  { label: 'Low',        value: 7, color: 'var(--text-tertiary)' },
  { label: 'Background', value: 9, color: 'var(--text-tertiary)', dim: true },
];

/**
 * PrioritySelector — segmented control for job priority.
 * Props: value (number 1-10), onChange (fn(number))
 * Maps numeric priority to nearest named level. Submits numeric value.
 */
export default function PrioritySelector({ value, onChange }) {
  // Find nearest level to current value
  const selected = LEVELS.reduce((prev, curr) =>
    Math.abs(curr.value - value) < Math.abs(prev.value - value) ? curr : prev
  );

  return (
    <div style="display:flex;gap:4px;flex-wrap:wrap;" role="group" aria-label="Priority">
      {LEVELS.map(level => {
        const isSelected = level.value === selected.value;
        return (
          <button
            key={level.value}
            type="button"
            class="t-btn"
            onClick={() => onChange(level.value)}
            style={[
              `font-size:var(--type-label);padding:4px 10px;`,
              `color:${level.color};`,
              level.dim ? 'opacity:0.6;' : '',
              isSelected
                ? `border-color:${level.color};background:color-mix(in srgb,${level.color} 12%,transparent);`
                : 'border-color:var(--border-subtle);background:transparent;',
            ].join('')}
          >
            {level.label}
          </button>
        );
      })}
    </div>
  );
}
