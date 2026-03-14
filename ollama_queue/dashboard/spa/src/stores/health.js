// What it does: Manages health/dashboard/DLQ/deferred/performance/consumer reactive signals
//   and their fetch functions — everything related to system observability and failure recovery.
// Decision it drives: The History, Performance, and Consumers tabs read these signals to show
//   DLQ entries, deferred jobs, system health charts, and consumer detection state.

import { signal } from '@preact/signals';
import { API } from './_shared.js';

export const healthData = signal([]);     // /api/health log array
export const cpuCount = signal(1);        // cpu_count from /api/health — used to convert raw load_avg to %
export const backendsData = signal([]);   // /api/backends — per-backend health, model count, VRAM%
export const durationData = signal([]);   // /api/durations response
export const heatmapData = signal([]);    // /api/heatmap response
export const deferredJobs = signal([]);       // /api/deferred response
export const modelPerformance = signal({});   // /api/metrics/models response
export const performanceCurve = signal(null); // /api/metrics/performance-curve response
export const backendMetrics = signal([]);     // /api/metrics/backends — per-backend per-model throughput
export const dlqEntries = signal([]);
export const dlqCount = signal(0);
export const dlqSchedulePreview = signal({ entries: [], count: 0 }); // /api/dlq/schedule-preview
export const currentTab = signal('now'); // 'now' | 'plan' | 'history' | 'models' | 'settings'

// ── Toast notification system ─────────────────────────────────────────────────
// What it shows: Transient feedback messages (success/error/info) stacked at top-right.
// Decision it drives: All action buttons push toasts here instead of inline text.
//   Error toasts persist until dismissed; success toasts auto-dismiss after 3s.
export const toasts = signal([]); // Array<{ id, type, msg, persistent }>

let _toastId = 0;
export function addToast(msg, type = 'info', persistent = false) {
  const id = ++_toastId;
  toasts.value = [...toasts.value, { id, type, msg, persistent }];
  if (!persistent && type !== 'error') {
    setTimeout(() => removeToast(id), 3000);
  }
  return id;
}

export function removeToast(id) {
  toasts.value = toasts.value.filter(t => t.id !== id);
}

// ── Backends management ────────────────────────────────────────────────────────
// What it shows: Per-backend CRUD signals for the new Backends tab.
// Decision it drives: Users can add/remove/test backends from the dashboard.
export const backendsLoading = signal(false);
export const backendsError = signal(null); // null = ok, string = last fetch error message

export async function addBackend(url, weight = 1) {
  const res = await fetch(`${API}/backends`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, weight }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  await fetchBackends();
  return res.json();
}

export async function removeBackend(url) {
  const res = await fetch(`${API}/backends/${encodeURIComponent(url)}`, { method: 'DELETE' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  await fetchBackends();
}

export async function updateBackendWeight(url, weight) {
  // weight is a query param, not a body param — matches PUT /api/backends/{url}/weight?weight=N
  const res = await fetch(`${API}/backends/${encodeURIComponent(url)}/weight?weight=${weight}`, {
    method: 'PUT',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  await fetchBackends();
}

export async function testBackend(url) {
  const res = await fetch(`${API}/backends/${encodeURIComponent(url)}/test`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Intercept mode state ──────────────────────────────────────────────────────

// What it shows: Whether iptables intercept mode is enabled and whether the rule is live.
// Decision it drives: User sees at a glance if all :11434 traffic is being routed through
//   the queue; enables or disables MITM interception for services with hardcoded Ollama URLs.
export const interceptStatus = signal({ enabled: false, rule_present: false });

// ── Consumers signals ─────────────────────────────────────────────────────────

// What it shows: List of detected Ollama-calling services and scan progress state.
// Decision it drives: User sees which services need to be routed through the queue.
export const consumers = signal([]);
export const consumersScanning = signal(false);

// ── Backend status ───────────────────────────────────────────────────────────

// What it shows: Health, model count, loaded models, and VRAM% for each configured
//   Ollama backend. Only meaningful when OLLAMA_BACKENDS has more than one URL.
// Decision it drives: BackendsPanel on the Now tab shows which machine is handling
//   inference and whether any backend is overloaded or unreachable.
export async function fetchBackends() {
    try {
        const res = await fetch(`${API}/backends`);
        if (res.ok) {
            backendsData.value = await res.json();
            backendsError.value = null;
        } else {
            console.error('fetchBackends: HTTP', res.status);
            backendsError.value = `HTTP ${res.status}`;
        }
    } catch (e) {
        console.error('fetchBackends failed:', e);
        backendsError.value = e.message;
    }
}

// ── Dead Letter Queue ────────────────────────────────────────────────────────
// Fetched on initial load + after any retry/dismiss. dlqCount drives the alert badge.
export async function fetchDLQ() {
    try {
        const resp = await fetch(`${API}/dlq`);
        if (resp.ok) {
            const entries = await resp.json();
            dlqEntries.value = entries;
            dlqCount.value = entries.length;
        }
    } catch (e) {
        console.error('fetchDLQ failed:', e);
    }
}

export async function retryDLQEntry(id) {
    try {
        const res = await fetch(`${API}/dlq/${id}/retry`, { method: 'POST' });
        if (!res.ok) throw new Error(`retryDLQEntry failed: HTTP ${res.status}`);
        await fetchDLQ();
    } catch (e) {
        console.error('retryDLQEntry failed:', e);
    }
}

export async function retryAllDLQ() {
    try {
        const res = await fetch(`${API}/dlq/retry-all`, { method: 'POST' });
        if (!res.ok) throw new Error(`retryAllDLQ failed: HTTP ${res.status}`);
        await fetchDLQ();
    } catch (e) {
        console.error('retryAllDLQ failed:', e);
    }
}

export async function dismissDLQEntry(id) {
    try {
        const res = await fetch(`${API}/dlq/${id}/dismiss`, { method: 'POST' });
        if (!res.ok) throw new Error(`dismissDLQEntry failed: HTTP ${res.status}`);
        await fetchDLQ();
    } catch (e) {
        console.error('dismissDLQEntry failed:', e);
    }
}

export async function clearDLQ() {
    try {
        const res = await fetch(`${API}/dlq`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`clearDLQ failed: HTTP ${res.status}`);
        await fetchDLQ();
    } catch (e) {
        console.error('clearDLQ failed:', e);
    }
}

// ── Deferred jobs & performance metrics ──────────────────────────────────────

export async function fetchDeferred() {
    try {
        const resp = await fetch(`${API}/deferred`);
        if (resp.ok) deferredJobs.value = await resp.json();
    } catch (e) {
        console.error('fetchDeferred failed:', e);
    }
}

export async function fetchModelPerformance() {
    try {
        const resp = await fetch(`${API}/metrics/models`);
        if (resp.ok) modelPerformance.value = await resp.json();
    } catch (e) {
        console.error('fetchModelPerformance failed:', e);
    }
}

export async function fetchPerformanceCurve() {
    try {
        const resp = await fetch(`${API}/metrics/performance-curve`);
        if (resp.ok) performanceCurve.value = await resp.json();
    } catch (e) {
        console.error('fetchPerformanceCurve failed:', e);
    }
}

export async function fetchBackendMetrics() {
    // What it fetches: Per-backend, per-model throughput data — which GPU is serving each model
    //   and how fast it runs. Only populated after proxy requests have been made.
    try {
        const resp = await fetch(`${API}/metrics/backends`);
        if (resp.ok) backendMetrics.value = await resp.json();
    } catch (e) {
        console.error('fetchBackendMetrics failed:', e);
    }
}

export async function fetchDLQSchedulePreview() {
    try {
        const resp = await fetch(`${API}/dlq/schedule-preview`);
        if (resp.ok) dlqSchedulePreview.value = await resp.json();
    } catch (e) {
        console.error('fetchDLQSchedulePreview failed:', e);
    }
}

export async function rescheduleDLQEntry(id) {
    const resp = await fetch(`${API}/dlq/${id}/reschedule`, { method: 'POST' });
    if (!resp.ok) throw new Error(`Reschedule failed: ${resp.status}`);
    await fetchDLQ();
    return resp.json();
}

export async function deferJob(jobId, reason = 'manual') {
    const resp = await fetch(`${API}/jobs/${jobId}/defer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
    });
    if (!resp.ok) throw new Error(`Defer failed: ${resp.status}`);
    await fetchDeferred();
    return resp.json();
}

export async function resumeDeferred(deferralId) {
    const resp = await fetch(`${API}/deferred/${deferralId}/resume`, { method: 'POST' });
    if (!resp.ok) throw new Error(`Resume failed: ${resp.status}`);
    await fetchDeferred();
    return resp.json();
}

export async function fetchInterceptStatus() {
  try {
    const res = await fetch(`${API}/consumers/intercept/status`);
    if (!res.ok) { console.warn('fetchInterceptStatus: HTTP', res.status); return; }
    interceptStatus.value = await res.json();
  } catch (e) { console.error('fetchInterceptStatus failed:', e); }
}

export async function enableIntercept() {
  const res = await fetch(`${API}/consumers/intercept/enable`, { method: 'POST' });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  await fetchInterceptStatus();
  return body;
}

export async function disableIntercept() {
  const res = await fetch(`${API}/consumers/intercept/disable`, { method: 'POST' });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  await fetchInterceptStatus();
}

export async function fetchConsumers() {
  const res = await fetch(`${API}/consumers`);
  if (!res.ok) throw new Error(`Failed to load consumers: HTTP ${res.status}`);
  consumers.value = await res.json();
}

export async function scanConsumers() {
  consumersScanning.value = true;
  try {
    const res = await fetch(`${API}/consumers/scan`, { method: 'POST' });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `Scan failed: HTTP ${res.status}`);
    consumers.value = body;
  } finally {
    consumersScanning.value = false;
  }
}

export async function includeConsumer(id, opts = {}) {
  const res = await fetch(`${API}/consumers/${id}/include`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ restart_policy: 'deferred', ...opts }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  await fetchConsumers();
  return res.json();
}

export async function ignoreConsumer(id) {
  const res = await fetch(`${API}/consumers/${id}/ignore`, { method: 'POST' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  await fetchConsumers();
}

export async function revertConsumer(id) {
  const res = await fetch(`${API}/consumers/${id}/revert`, { method: 'POST' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  await fetchConsumers();
}

export async function fetchConsumerHealth(id) {
  const res = await fetch(`${API}/consumers/${id}/health`);
  if (!res.ok) throw new Error(`Health check failed: HTTP ${res.status}`);
  return res.json();
}
