// What it shows: A colored pill badge displaying a job's current status (queued, running, complete, etc.)
// Decision it drives: Lets the user instantly identify job state at a glance across any list or table,
//   so they can prioritize attention — e.g. a failed job needs action, a running job just needs patience.

import { h } from 'preact';

const STATUS_STYLES = {
  queued:    { label: 'queued',    cls: 'status-pill status-queued' },
  running:   { label: 'running',   cls: 'status-pill status-running status-running-active' },
  complete:  { label: 'complete',  cls: 'status-pill status-complete' },
  failed:    { label: 'failed',    cls: 'status-pill status-failed status-error' },
  deferred:  { label: 'deferred',  cls: 'status-pill status-deferred' },
  cancelled: { label: 'cancelled', cls: 'status-pill status-cancelled' },
};

export default function StatusPill({ status }) {
  const s = STATUS_STYLES[status] || { label: status, cls: 'status-pill status-unknown' };
  return <span class={s.cls}>{s.label}</span>;
}
