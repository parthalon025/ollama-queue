// What it shows: A thin sticky strip at the top of every page summarizing
//   system state in one line: jobs waiting, what's running, eval winner.
// Decision it drives: "Do I need to switch tabs to take action?"

import { h } from 'preact';
import { dlqCount } from '../stores/health.js';
import SystemSummaryLine from './SystemSummaryLine.jsx';

export default function CohesionHeader() {
  const hasDlq = dlqCount?.value > 0;
  return (
    <header class="cohesion-header">
      <SystemSummaryLine />
      {hasDlq && <span class="cohesion-header__dlq-badge">{dlqCount?.value} DLQ</span>}
    </header>
  );
}
