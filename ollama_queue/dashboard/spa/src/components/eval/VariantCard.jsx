/**
 * What it shows: A summary card for one prompt variant — like a profile card
 *   for a prompt. Shows its name, which AI service it uses, how well it scored,
 *   how consistent it is, and a preview of the prompt text.
 *   Gold star = this is in production. Silver = recommended. Checkbox = select for compare.
 * Decision it drives: "Should I promote this variant? Is it stable enough to trust?
 *   Should I clone it and try a variation?"
 */
import { h } from 'preact';
import F1Score from '../F1Score.jsx';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';

// NOTE: All .map() callbacks use descriptive parameter names — never 'h' (shadows JSX factory)

export default function VariantCard({ variant, selected = false, onSelect, onClone, onEdit, onDelete }) {
  const [fb, act] = useActionFeedback();

  // Top 3 non-default params as pills
  const paramPills = Object.entries(variant.params || {}).slice(0, 3);
  const promptPreview = variant.system_prompt
    ? variant.system_prompt.slice(0, 60) + (variant.system_prompt.length > 60 ? '\u2026' : '')
    : null;

  const stabilityBadge = variant.f1_stdev != null
    ? (variant.f1_stdev < 0.03 ? { label: 'stable', cls: 'badge-stable' }
      : variant.f1_stdev < 0.07 ? { label: 'variable', cls: 'badge-variable' }
      : { label: 'unstable', cls: 'badge-unstable' })
    : null;

  return (
    <div class={`variant-card${selected ? ' variant-card--selected' : ''}`}>
      <div class="variant-card__header">
        <label class="variant-card__checkbox">
          <input type="checkbox" checked={selected} onChange={e => onSelect?.(e.target.checked)} />
        </label>
        <span class="variant-card__label">{variant.label || variant.id}</span>
        <span class={`variant-card__provider variant-card__provider--${variant.provider || 'ollama'}`}>
          {variant.provider || 'ollama'}
        </span>
        {variant.is_production && <span class="variant-card__badge variant-card__badge--gold">\u2605 Production</span>}
        {variant.is_recommended && !variant.is_production && <span class="variant-card__badge variant-card__badge--silver">\u2606 Recommended</span>}
      </div>

      <div class="variant-card__scores">
        {variant.latest_f1 != null && <F1Score value={variant.latest_f1} />}
        {stabilityBadge && <span class={`stability-badge ${stabilityBadge.cls}`}>{stabilityBadge.label}</span>}
      </div>

      {paramPills.length > 0 && (
        <div class="variant-card__params">
          {paramPills.map(([k, v]) => (
            <span key={k} class="param-pill">{k} {v}</span>
          ))}
        </div>
      )}

      {promptPreview && <div class="variant-card__prompt-preview">{promptPreview}</div>}

      <div class="variant-card__actions">
        <button onClick={() => onClone?.()}>Clone</button>
        <button onClick={() => onEdit?.()}>Edit</button>
        <button
          class="variant-card__delete"
          disabled={fb.phase === 'loading'}
          onClick={() => act('Deleting\u2026', () => onDelete?.(), () => 'Deleted')}
        >
          {fb.phase === 'loading' ? '\u2026' : 'Delete'}
        </button>
        {fb.msg && <span class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</span>}
      </div>
    </div>
  );
}
