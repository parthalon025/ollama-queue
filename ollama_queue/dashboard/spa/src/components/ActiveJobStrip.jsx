/**
 * What it shows: A thin bar showing what job is running right now —
 *   model name, how long it's been running, and how many jobs are waiting.
 *   Disappears when nothing is running.
 * Decision it drives: "Is the system busy? Can I submit another job?"
 *   Visible even when you've switched away from the Now tab.
 */
import { h } from 'preact';
import { currentJob, queueDepth } from '../stores/index.js';
import LiveIndicator from './LiveIndicator.jsx';
import ModelChip from './ModelChip.jsx';
import { formatDuration } from '../utils/time.js';

export default function ActiveJobStrip() {
  const job = currentJob.value;
  if (!job) return null;

  const elapsed = job.started_at ? Math.floor(Date.now() / 1000 - job.started_at) : null;

  return (
    <div class="active-job-strip" role="status" aria-live="polite">
      <LiveIndicator state="running" />
      <ModelChip model={job.model} />
      {elapsed !== null && <span class="active-job-strip__time">{formatDuration(elapsed)}</span>}
      {queueDepth.value > 0 && (
        <span class="active-job-strip__queue">{queueDepth.value} waiting</span>
      )}
    </div>
  );
}
