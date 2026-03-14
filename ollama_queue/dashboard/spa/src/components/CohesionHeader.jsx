// What it shows: A thin sticky strip at the top of every page summarizing
//   system state in one line: jobs waiting, what's running, eval winner.
// Decision it drives: "Do I need to switch tabs to take action?"
// D19: applyFreshness on the "last updated" timestamp — header greys out before data stops updating.

import { useEffect, useRef } from 'preact/hooks';
import { applyFreshness } from 'superhot-ui';
import { dlqCount } from '../stores/health.js';
import { status } from '../stores';
import SystemSummaryLine from './SystemSummaryLine.jsx';

export default function CohesionHeader() {
  const hasDlq = dlqCount?.value > 0;
  const timestampRef = useRef(null);

  // D19: Freshness heartbeat — applies applyFreshness to the last-poll timestamp el.
  // The header greys out gradually as time passes without a new poll — warns before data goes stale.
  const lastPollTs = status.value?._poll_ts ?? Date.now() / 1000;
  useEffect(() => {
    if (timestampRef.current) {
      applyFreshness(timestampRef.current, lastPollTs);
    }
  }, [lastPollTs]);

  return (
    <header class="cohesion-header">
      <SystemSummaryLine />
      <span
        ref={timestampRef}
        class="data-mono"
        style={{ fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)', flexShrink: 0 }}
        title="Last data poll time"
      >
        {new Date(lastPollTs * 1000).toLocaleTimeString()}
      </span>
      {hasDlq && <span class="cohesion-header__dlq-badge">{dlqCount?.value} DLQ</span>}
    </header>
  );
}
