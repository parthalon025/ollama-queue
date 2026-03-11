import { h } from 'preact';

// What it shows: A centered placeholder when a list or section has no data.
//   Displays a short headline and a one-line explanation. Optionally shows a CTA button.
// Decision it drives: Tells the user why the panel is blank and what to do next —
//   removes ambiguity between "loading" and "genuinely empty".

/**
 * EmptyState — shown when a list or section has no data.
 * Props:
 *   headline (string) — short title
 *   body (string) — explanation
 *   action ({ label, onClick }) — optional CTA button
 */
export default function EmptyState({ headline, body, action }) {
  return (
    <div style="display:flex;flex-direction:column;align-items:center;gap:8px;padding:24px 16px;color:var(--text-tertiary);text-align:center;">
      <span style="font-size:var(--type-body);color:var(--text-secondary);">{headline}</span>
      <span style="font-size:var(--type-label);">{body}</span>
      {action && (
        <button class="t-btn" onClick={action.onClick} style="margin-top:8px;font-size:var(--type-label);">
          {action.label}
        </button>
      )}
    </div>
  );
}
