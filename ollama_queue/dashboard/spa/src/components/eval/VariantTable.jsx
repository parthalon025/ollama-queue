import { h } from 'preact';
import { evalVariants } from '../../store.js';
import VariantRow from './VariantRow.jsx';
// What it shows: The full list of eval variant configs — system defaults first,
//   then user-created. Each row is expandable to model/template details and
//   run history.
// Decision it drives: User sees all available configs at a glance, identifies
//   the recommended one, and can clone or edit configs from here.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function VariantTable() {
  // Read .value at top so Preact subscribes to the signal
  const variants = evalVariants.value;

  if (!variants || variants.length === 0) {
    return (
      <div class="t-frame" data-label="Configurations">
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
          No configurations yet. Use the toolbar above to generate or create one.
        </div>
      </div>
    );
  }

  // Sort: system first (A-E order), then user-created by label
  const systemVariants = variants.filter(v => v.is_system).sort((a, b) => a.id.localeCompare(b.id));
  const userVariants = variants.filter(v => !v.is_system).sort((a, b) => a.label.localeCompare(b.label));
  const sorted = [...systemVariants, ...userVariants];

  return (
    <div class="t-frame" data-label="Configurations">
      {sorted.map(variant => (
        <VariantRow key={variant.id} variant={variant} />
      ))}
    </div>
  );
}
