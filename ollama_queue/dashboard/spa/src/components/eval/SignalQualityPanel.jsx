import { h } from 'preact';
// What it shows: Data quality indicators for the trend signal — run count,
//   item pool growth, and judge reliability.
// Decision it drives: Whether the trend line is trustworthy enough to act on
//   or needs more runs to be statistically meaningful.

import { evalTrends } from '../../stores';
import { EVAL_TRANSLATIONS } from './translations.js';

export default function SignalQualityPanel() {
  // Read .value at top of body to subscribe to signal changes
  const trends = evalTrends.value;

  if (!trends) return null;

  const completedRuns     = trends.completed_runs ?? 0;
  const itemCountGrowing  = trends.item_count_growing ? 'Growing (more lessons each run)' : 'Stable (same lessons each run)';
  const judgeReliability  = trends.judge_reliability != null
    ? Math.round(trends.judge_reliability * 100) + '%'
    : '—';

  return (
    <div class="t-frame eval-signal-quality" data-label="Is the Data Reliable?">
      <dl class="eval-signal-quality__list">
        <div class="eval-signal-quality__row">
          <dt>Finished test runs</dt>
          <dd class="data-mono">{completedRuns}</dd>
        </div>
        <div class="eval-signal-quality__row">
          <dt>Lesson pool size</dt>
          <dd class="data-mono">{itemCountGrowing}</dd>
        </div>
        <div class="eval-signal-quality__row">
          <dt>
            Scorer consistency
            {EVAL_TRANSLATIONS.judge_model?.tooltip && (
              <span
                class="eval-tooltip-trigger"
                title={EVAL_TRANSLATIONS.judge_model.tooltip}
                aria-label={EVAL_TRANSLATIONS.judge_model.tooltip}
              >
                {' '}?
              </span>
            )}
          </dt>
          <dd class="data-mono">{judgeReliability}</dd>
        </div>
      </dl>
    </div>
  );
}
