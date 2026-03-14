// What it does: Central re-export barrel for all domain stores + cross-domain polling
//   orchestrator. Components import from 'stores' (or 'stores/index.js') and get every
//   signal and function regardless of which domain store defines it.
// Decision it drives: Keeps the import surface identical to the old monolithic store.js —
//   no component changes needed beyond updating the import path.

import { signal, computed } from '@preact/signals';
import { API } from './_shared.js';

// Re-export API so components that import { API } from '../stores' still work
export { API } from './_shared.js';

// What it does: Guards against HTML error pages masquerading as ok responses.
// If a reverse proxy (nginx, Tailscale Serve) returns a 200 HTML error page,
// resp.ok is true but resp.json() throws, causing a silent poll failure that
// shows stale data. This check surfaces the content-type mismatch explicitly.
async function _safeJson(resp) {
    const ct = resp.headers.get('content-type') || '';
    if (!ct.includes('application/json')) {
        throw new Error(`Expected JSON, got ${ct || 'no content-type'} (HTTP ${resp.status})`);
    }
    return resp.json();
}

// ── Cross-tab navigation signals ──────────────────────────────────────────────

// What it shows: Which job ID to highlight when navigating from History to Now.
// Decision it drives: History "View context" button sets this; Now.jsx pulses that row.
export const highlightJobId = signal(null);

// What it shows: Which model name to filter to on the Models tab.
// Decision it drives: ModelChip clicks set this; ModelsTab filters/scrolls to match.
export const modelFilter = signal(null);

// Re-export all domain stores
export * from './queue.js';
export * from './eval.js';
export * from './schedule.js';
export * from './models.js';
export * from './settings.js';
export * from './health.js';

// ── Cross-component derived signals ───────────────────────────────────────────

// What it shows: The currently-running job object, or null if the queue is idle.
// Decision it drives: ActiveJobStrip and any component outside the Now tab can show
//   at-a-glance whether something is running without reading the full status signal.
import { status as _status, queue as _queue } from './queue.js';
export const currentJob = computed(() => _status.value?.current_job ?? null);

// What it shows: How many jobs are waiting behind the currently-running one.
// Decision it drives: "Is it safe to submit another job? Will it have to wait?"
export const queueDepth = computed(() => (_queue.value?.length ?? 0));

// ── Import individual signals/functions needed by the polling orchestrator ────
import { status, queue, connectionStatus } from './queue.js';
import { settings } from './settings.js';
import { healthData, cpuCount, durationData, heatmapData, dlqSchedulePreview,
         fetchDLQ, fetchDeferred, fetchDLQSchedulePreview, fetchModelPerformance,
         fetchPerformanceCurve, fetchBackends, fetchBackendMetrics } from './health.js';
import { history } from './queue.js';

// ── Polling loop ────────────────────────────────────────────────────────────
// Keeps status + queue signals fresh every 5s. Non-realtime data (health charts,
// history) refreshes every 60s (every 12 status polls) to reduce API load.
// Backs off exponentially on repeated failures and sets connectionStatus='disconnected'
// after 3 consecutive failures so the banner appears.

let POLL_INTERVAL = 5000;
let pollTimer = null;
let _pollFailures = 0;
let _pollCount = 0;
let _backoffMs = 5000;
let _visibilityHandler = null;

export function startPolling() {
    fetchAll();
    pollTimer = setTimeout(fetchStatus, POLL_INTERVAL);
    if (_visibilityHandler) document.removeEventListener('visibilitychange', _visibilityHandler);
    _visibilityHandler = () => { if (!document.hidden) fetchAll(); };
    document.addEventListener('visibilitychange', _visibilityHandler);
}

export function stopPolling() {
    if (pollTimer) clearTimeout(pollTimer);
    if (_visibilityHandler) {
        document.removeEventListener('visibilitychange', _visibilityHandler);
        _visibilityHandler = null;
    }
}

async function fetchStatus() {
    try {
        const resp = await fetch(`${API}/status`);
        if (resp.ok) {
            const data = await _safeJson(resp);
            status.value = data;
            if (Array.isArray(data.queue)) queue.value = data.queue;
            _pollFailures = 0;
            connectionStatus.value = 'ok';
            _backoffMs = POLL_INTERVAL;
            _pollCount++;
            if (_pollCount % 12 === 0) _fetchNonRealtime();
        }
        pollTimer = setTimeout(fetchStatus, POLL_INTERVAL);
    } catch (e) {
        console.error('Poll failed:', e);
        _pollFailures++;
        if (_pollFailures >= 3) connectionStatus.value = 'disconnected';
        _backoffMs = Math.min(_backoffMs * 2, 30000);
        pollTimer = setTimeout(fetchStatus, _backoffMs);
    }
}

async function _fetchNonRealtime() {
    // What it does: Refreshes slower-changing data every 60s (every 12 status polls).
    // load-map is intentionally excluded here — Plan's own 10s interval covers it when
    // the Plan tab is open, and tab-focus fetchAll() covers the cold-load case.
    try {
        const [hResp, durResp, heatResp, histResp] = await Promise.all([
            fetch(`${API}/health`),
            fetch(`${API}/durations`),
            fetch(`${API}/heatmap`),
            fetch(`${API}/history`),
        ]);
        if (hResp.ok) { const d = await _safeJson(hResp); healthData.value = Array.isArray(d) ? d : (d.log ?? []); if (d.cpu_count) cpuCount.value = d.cpu_count; }
        if (durResp.ok) durationData.value = await _safeJson(durResp);
        if (heatResp.ok) heatmapData.value = await _safeJson(heatResp);
        if (histResp.ok) history.value = await _safeJson(histResp);
        // DLQ/deferral/performance non-realtime refresh
        fetchDeferred();
        fetchDLQSchedulePreview();
        fetchModelPerformance();
        fetchPerformanceCurve();
        fetchBackendMetrics();
    } catch (e) {
        console.error('Non-realtime refresh failed:', e);
    }
}

async function fetchAll() {
    fetchStatus();
    fetchDLQ();      // populate DLQ badge on first load
    fetchBackends(); // populate backend health panel on first load
    // Fetch non-realtime data (charts, history) once on load
    try {
        const [qResp, hResp, healthResp, durResp, heatResp, setResp] = await Promise.all([
            fetch(`${API}/queue`),
            fetch(`${API}/history`),
            fetch(`${API}/health`),
            fetch(`${API}/durations`),
            fetch(`${API}/heatmap`),
            fetch(`${API}/settings`),
        ]);
        if (qResp.ok) queue.value = await _safeJson(qResp);
        if (hResp.ok) history.value = await _safeJson(hResp);
        if (healthResp.ok) { const d = await _safeJson(healthResp); healthData.value = Array.isArray(d) ? d : (d.log ?? []); }
        if (durResp.ok) durationData.value = await _safeJson(durResp);
        if (heatResp.ok) heatmapData.value = await _safeJson(heatResp);
        if (setResp.ok) settings.value = await _safeJson(setResp);
        const pi = settings.value.poll_interval_seconds;
        if (pi && pi * 1000 !== POLL_INTERVAL) {
            POLL_INTERVAL = pi * 1000;
            if (pollTimer) { clearTimeout(pollTimer); pollTimer = setTimeout(fetchStatus, POLL_INTERVAL); }
        }
    } catch (e) {
        console.error('Initial fetch failed:', e);
    }
}
