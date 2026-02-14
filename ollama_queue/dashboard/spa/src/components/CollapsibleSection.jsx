import { useState } from 'preact/hooks';

/**
 * ASCII-framed collapsible section with cursor state affordance.
 * Cursor IS the expand/collapse indicator:
 * - cursor-active (block blink): expanded
 * - cursor-working (half fast blink): loading
 * - cursor-idle (_ slow blink): collapsed
 */
export default function CollapsibleSection({
  title,
  subtitle,
  summary,
  defaultOpen = true,
  loading = false,
  children,
}) {
  const [open, setOpen] = useState(defaultOpen);

  let cursorClass = 'cursor-idle';
  if (loading) cursorClass = 'cursor-working';
  else if (open) cursorClass = 'cursor-active';

  function toggle() {
    if (!loading) setOpen(!open);
  }

  return (
    <section>
      <button
        type="button"
        onClick={toggle}
        class={`${cursorClass} w-full text-left flex items-center justify-between`}
        style="padding: 8px 0; cursor: pointer; background: none; border: none; border-bottom: 1px solid var(--border-subtle);"
        aria-expanded={open}
        aria-label={`${open ? 'Collapse' : 'Expand'} ${title}`}
      >
        <div class="flex-1 min-w-0">
          <h2
            style="font-size: var(--type-headline); color: var(--text-primary); font-family: var(--font-mono); font-weight: 700;"
          >
            {title}
          </h2>
          {subtitle && !open && summary && (
            <span
              class="t-bracket"
              style="margin-left: 8px;"
            >
              {summary}
            </span>
          )}
          {subtitle && open && (
            <p style="font-size: var(--type-label); color: var(--text-tertiary); margin-top: 2px;">
              {subtitle}
            </p>
          )}
        </div>
      </button>

      {open && (
        <div
          class="animate-page-enter"
          style="padding-top: 12px;"
        >
          {children}
        </div>
      )}
    </section>
  );
}
