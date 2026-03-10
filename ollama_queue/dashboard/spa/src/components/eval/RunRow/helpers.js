// What it does: Pure utility functions shared across RunRow sub-components —
//   status dot colors, date formatting, percentage formatting, and markdown-to-text
//   conversion for the analysis panel.
// Decision it drives: Keeps render code clean by isolating data formatting logic
//   that doesn't depend on component state.

export const STATUS_DOT_COLORS = {
  complete: 'var(--status-healthy)',
  failed: 'var(--status-error)',
  cancelled: 'var(--status-waiting)',
  generating: 'var(--accent)',
  judging: 'var(--accent)',
  pending: 'var(--text-tertiary)',
};

export function formatDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch { return iso; }
}

export function fmtPct(val) {
  if (val == null) return '\u2014';
  return `${Math.round(val * 100)}%`;
}

// Converts AI-generated markdown prose to readable plain text.
// Handles: ## headers -> bold label, **x** -> x, - bullet -> bullet.
// No library needed — analysis_md is structured prose, not full markdown.
export function simpleRenderMd(text) {
  if (!text) return '';
  return text
    .replace(/^#{1,3} (.+)$/gm, '[$1]')       // ## Header -> [Header]
    .replace(/\*\*(.+?)\*\*/g, '$1')            // **bold** -> bold
    .replace(/^- (.+)$/gm, '\u2022 $1')         // - item -> bullet item
    .replace(/\n{3,}/g, '\n\n')                // collapse 3+ blank lines
    .trim();
}
