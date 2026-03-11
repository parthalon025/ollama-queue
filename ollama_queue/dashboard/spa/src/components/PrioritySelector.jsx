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
  { label: 'Background', value: 9, color: 'var(--bg-inset, #374151)' },
];

// Maps numeric priority value to the correct category level using range boundaries.
// Backend schema: Critical=[1,2], High=[3,4], Normal=[5,6], Low=[7,8], Background=[9,10].
// Range-based lookup ensures priority=2 → Critical (not High), priority=4 → High (not Normal), etc.
function valueToLevel(v) {
  if (v <= 2) return LEVELS[0];  // Critical: 1-2
  if (v <= 4) return LEVELS[1];  // High: 3-4
  if (v <= 6) return LEVELS[2];  // Normal: 5-6
  if (v <= 8) return LEVELS[3];  // Low: 7-8
  return LEVELS[4];              // Background: 9-10
}

/**
 * PrioritySelector — segmented control for job priority.
 * Props: value (number 1-10), onChange (fn(number))
 * Maps numeric priority to named level by range. Submits representative odd value per category.
 */
export default function PrioritySelector({ value, onChange }) {
  // Derive selected level from value using range boundaries (not nearest-distance)
  const selected = valueToLevel(value);

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
