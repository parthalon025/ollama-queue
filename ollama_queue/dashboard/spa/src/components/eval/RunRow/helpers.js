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

// What it does: Resolves the generator model name for a run by cross-referencing
//   the run's variant list against the loaded evalVariants signal data.
// Decision it drives: Lets the RunRow L1 show which model is actively generating,
//   so the user knows what's running without expanding the row.
export function resolveGenModel(run, variants) {
  if (!variants || !run) return null;
  // run.variants is a comma-separated string of variant IDs (or a JSON array)
  let variantIds = [];
  try {
    if (typeof run.variants === 'string') {
      variantIds = run.variants.includes(',')
        ? run.variants.split(',').map(s => s.trim())
        : JSON.parse(run.variants);
    } else if (Array.isArray(run.variants)) {
      variantIds = run.variants;
    }
  } catch { /* fallback to empty */ }
  if (variantIds.length === 0) return null;
  // Collect unique model names from variants used in this run
  const modelNames = variantIds
    .map(vid => (variants || []).find(v => v.id === vid)?.model)
    .filter(Boolean);
  const unique = [...new Set(modelNames)];
  if (unique.length === 0) return null;
  if (unique.length === 1) return unique[0];
  return unique.join(', ');
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
