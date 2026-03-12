import { useState } from 'preact/hooks';
import { evalTemplates } from '../../stores';
import TemplateRow from './TemplateRow.jsx';
// What it shows: Collapsible section listing all prompt templates — the instructions
//   that tell the AI how to extract principles from lessons.
// Decision it drives: User understands which template each variant uses and can
//   clone and edit templates to try different extraction approaches.

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function TemplateSection() {
  // Read .value at top so Preact subscribes to signal changes
  const templates = evalTemplates.value;
  const [open, setOpen] = useState(false); // collapsed by default per spec

  return (
    <div class="t-frame">
      {/* Section header — clickable to collapse/expand */}
      <div
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', userSelect: 'none' }}
        onClick={() => setOpen(o => !o)}
        role="button"
        aria-expanded={open}
      >
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600 }}>
          Prompt templates
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
          {templates?.length ?? 0} templates {open ? '▲' : '▼'}
        </span>
      </div>

      {open && (
        <div style={{ marginTop: '0.75rem' }}>
          {!templates || templates.length === 0 ? (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
              No templates found.
            </div>
          ) : (
            templates.map(tmpl => (
              <TemplateRow key={tmpl.id} template={tmpl} />
            ))
          )}
        </div>
      )}
    </div>
  );
}
