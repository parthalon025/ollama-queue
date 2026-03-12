// What it shows: A compact chip summarizing an eval variant — its ID, production/recommended
//   status (star badge), provider, and current F1 score.
// Decision it drives: Gives the user a scannable at-a-glance view of which variant is
//   production, which is recommended, and how well each is performing, so they can compare
//   variants without opening the full detail panel.

import { h } from 'preact';
import F1Score from './F1Score.jsx';

export default function VariantChip({ variantId, f1, isProduction, isRecommended, provider }) {
  const star = isProduction ? '★' : isRecommended ? '☆' : null;
  return (
    <div class="variant-chip">
      {star && <span class="variant-star">{star}</span>}
      <span class="variant-id">{variantId}</span>
      {provider && <span class="provider-badge">{provider}</span>}
      {f1 != null && <F1Score value={f1} />}
    </div>
  );
}
