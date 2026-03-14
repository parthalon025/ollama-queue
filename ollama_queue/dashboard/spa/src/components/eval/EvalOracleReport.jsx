/**
 * What it shows: A report on how reliable the judge was during the eval run.
 *   Kappa score = how much the AI judge agreed with reference answers.
 *   High Kappa (green, ≥0.8) means the results are trustworthy.
 *   Low Kappa (red, <0.6) means the judge was unreliable — results may be noisy.
 * Decision it drives: "Can I trust these F1 scores? Should I run oracle calibration
 *   before promoting a variant?"
 */
import { h } from 'preact';
import { useState } from 'preact/hooks';

const KAPPA_TOOLTIP = 'Agreement between judge and reference answers. 1.0 = perfect, 0.0 = random. Below 0.6 means the judge is unreliable.';

function kappaClass(kappa) {
  if (kappa >= 0.8) return 'oracle-kappa oracle-kappa--green';
  if (kappa >= 0.6) return 'oracle-kappa oracle-kappa--amber';
  return 'oracle-kappa oracle-kappa--red';
}

export default function EvalOracleReport({ oracle }) {
  const [open, setOpen] = useState(false);
  if (!oracle) return null;

  return (
    <div class="eval-oracle-report">
      <button class="eval-oracle-report__toggle" onClick={() => setOpen(o => !o)}>
        How reliable was the judge? {open ? '▲' : '▼'}
      </button>
      {open && (
        <div class="eval-oracle-report__body">
          <div class="oracle-row">
            <span class="oracle-label" title={KAPPA_TOOLTIP}>Kappa score</span>
            <span class={kappaClass(oracle.kappa)}>{oracle.kappa?.toFixed(3) ?? '—'}</span>
          </div>
          <div class="oracle-row">
            <span class="oracle-label">Agreement</span>
            <span>{oracle.agreement_pct != null ? `${Math.round(oracle.agreement_pct)}%` : '—'}</span>
          </div>
          <div class="oracle-row">
            <span class="oracle-label">Disagreements</span>
            <span>{oracle.disagreement_count ?? '—'}</span>
          </div>
          {oracle.opro_suggestions?.length > 0 && (
            <div class="oracle-suggestions">
              <div class="oracle-label">Suggested prompt improvements:</div>
              {oracle.opro_suggestions.map((s, i) => <div key={i} class="oracle-suggestion-item">{s}</div>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
