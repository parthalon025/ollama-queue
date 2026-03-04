import { signal } from '@preact/signals';

export const status = signal(null);       // /api/status response
export const queue = signal([]);          // /api/queue response
export const history = signal([]);        // /api/history response
export const healthData = signal([]);     // /api/health response
export const durationData = signal([]);   // /api/durations response
export const heatmapData = signal([]);    // /api/heatmap response
export const settings = signal({});       // /api/settings response
export const currentTab = signal('now'); // 'now' | 'plan' | 'history' | 'models' | 'settings'
export const scheduleJobs = signal([]);
export const scheduleEvents = signal([]);
export const dlqEntries = signal([]);
export const dlqCount = signal(0);
export const models = signal([]);
export const modelPulls = signal([]);
export const modelCatalog = signal({ curated: [], search_results: [] });
export const queueEtas = signal([]);
export const connectionStatus = signal('ok'); // 'ok' | 'disconnected'
export const loadMap = signal([]);             // /api/schedule/load-map — 48-slot priority scores

// Derive API base from current URL so it works behind Tailscale Serve path prefix.
// /ui/ → /api, /queue/ui/ → /queue/api
const pathBase = window.location.pathname.replace(/\/ui\/.*$/, '').replace(/\/ui$/, '');
export const API = `${pathBase}/api`;

let POLL_INTERVAL = 5000;
let pollTimer = null;
let _pollFailures = 0;
let _pollCount = 0;
let _backoffMs = 5000;

export function startPolling() {
    fetchAll();
    pollTimer = setTimeout(fetchStatus, POLL_INTERVAL);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) fetchAll();
    });
}

export function stopPolling() {
    if (pollTimer) clearTimeout(pollTimer);
}

async function fetchStatus() {
    try {
        const resp = await fetch(`${API}/status`);
        if (resp.ok) {
            const data = await resp.json();
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
    try {
        const [hResp, durResp, heatResp, histResp, lmResp] = await Promise.all([
            fetch(`${API}/health`),
            fetch(`${API}/durations`),
            fetch(`${API}/heatmap`),
            fetch(`${API}/history`),
            fetch(`${API}/schedule/load-map`),
        ]);
        if (hResp.ok) healthData.value = await hResp.json();
        if (durResp.ok) durationData.value = await durResp.json();
        if (heatResp.ok) heatmapData.value = await heatResp.json();
        if (histResp.ok) history.value = await histResp.json();
        if (lmResp.ok) { const d = await lmResp.json(); loadMap.value = d.slots || []; }
    } catch (e) {
        console.error('Non-realtime refresh failed:', e);
    }
}

export async function fetchSchedule() {
    try {
        const [jobsResp, eventsResp] = await Promise.all([
            fetch(`${API}/schedule`),
            fetch(`${API}/schedule/events?limit=50`),
        ]);
        if (jobsResp.ok) scheduleJobs.value = await jobsResp.json();
        if (eventsResp.ok) scheduleEvents.value = await eventsResp.json();
        await fetchQueueEtas();
    } catch (e) {
        console.error('fetchSchedule failed:', e);
    }
}

export async function fetchLoadMap() {
    try {
        const resp = await fetch(`${API}/schedule/load-map`);
        if (resp.ok) {
            const data = await resp.json();
            loadMap.value = data.slots || [];
        }
    } catch (e) {
        console.error('fetchLoadMap failed:', e);
    }
}

export async function fetchSuggestTime(priority = 5, topN = 3) {
    const resp = await fetch(`${API}/schedule/suggest?priority=${priority}&top_n=${topN}`);
    if (!resp.ok) throw new Error(`fetchSuggestTime failed: ${resp.status}`);
    return resp.json(); // { suggestions: [{cron, score, slot}] }
}

export async function updateScheduleJob(id, fields) {
    const resp = await fetch(`${API}/schedule/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
    });
    if (!resp.ok) throw new Error(`updateScheduleJob failed: ${resp.status}`);
    await fetchSchedule();
}

export async function toggleScheduleJob(id, enabled) {
    try {
        await updateScheduleJob(id, { enabled });
    } catch (e) {
        console.error('toggleScheduleJob failed:', e);
    }
}

export async function triggerRebalance() {
    const resp = await fetch(`${API}/schedule/rebalance`, { method: 'POST' });
    if (!resp.ok) throw new Error(`triggerRebalance failed: ${resp.status}`);
    await fetchSchedule();
}

export async function runScheduleJobNow(id) {
    const resp = await fetch(`${API}/schedule/${id}/run-now`, { method: 'POST' });
    if (!resp.ok) throw new Error(`run-now failed: ${resp.status}`);
    await fetchSchedule();
}

export async function batchToggleJobs(tag, enabled) {
    const resp = await fetch(`${API}/schedule/batch-toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag, enabled }),
    });
    if (!resp.ok) throw new Error(`batch-toggle failed: ${resp.status}`);
    await fetchSchedule();
}

export async function batchRunJobs(tag) {
    const resp = await fetch(`${API}/schedule/batch-run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag }),
    });
    if (!resp.ok) throw new Error(`batch-run failed: ${resp.status}`);
    await fetchSchedule();
}

export async function fetchJobRuns(rjId, limit = 5) {
    const resp = await fetch(`${API}/schedule/${rjId}/runs?limit=${limit}`);
    if (!resp.ok) throw new Error(`fetchJobRuns failed: ${resp.status}`);
    return resp.json();
}

export async function deleteScheduleJob(rjId) {
    const resp = await fetch(`${API}/schedule/${rjId}`, { method: 'DELETE' });
    if (!resp.ok) throw new Error(`Delete failed: ${resp.status}`);
    await fetchSchedule();
}

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
        await fetch(`${API}/dlq/${id}/retry`, { method: 'POST' });
        await fetchDLQ();
    } catch (e) {
        console.error('retryDLQEntry failed:', e);
    }
}

export async function retryAllDLQ() {
    try {
        await fetch(`${API}/dlq/retry-all`, { method: 'POST' });
        await fetchDLQ();
    } catch (e) {
        console.error('retryAllDLQ failed:', e);
    }
}

export async function dismissDLQEntry(id) {
    try {
        await fetch(`${API}/dlq/${id}/dismiss`, { method: 'POST' });
        await fetchDLQ();
    } catch (e) {
        console.error('dismissDLQEntry failed:', e);
    }
}

export async function clearDLQ() {
    try {
        await fetch(`${API}/dlq`, { method: 'DELETE' });
        await fetchDLQ();
    } catch (e) {
        console.error('clearDLQ failed:', e);
    }
}

export async function fetchModels() {
    try {
        const resp = await fetch(`${API}/models`);
        if (resp.ok) models.value = await resp.json();
    } catch (e) {
        console.error('fetchModels failed:', e);
    }
}

export async function fetchModelCatalog(query = '') {
    try {
        const url = query ? `${API}/models/catalog?q=${encodeURIComponent(query)}`
                          : `${API}/models/catalog`;
        const resp = await fetch(url);
        if (resp.ok) modelCatalog.value = await resp.json();
    } catch (e) {
        console.error('fetchModelCatalog failed:', e);
    }
}

export async function startModelPull(modelName) {
    const resp = await fetch(`${API}/models/pull`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelName }),
    });
    if (!resp.ok) throw new Error(`Pull failed: ${resp.status}`);
    const { pull_id } = await resp.json();
    return pull_id;
}

export async function cancelModelPull(pullId) {
    const resp = await fetch(`${API}/models/pull/${pullId}`, { method: 'DELETE' });
    if (!resp.ok) {
        const msg = `Cancel pull failed: ${resp.status}`;
        console.error(msg);
        throw new Error(msg);
    }
}

export async function fetchQueueEtas() {
    try {
        const resp = await fetch(`${API}/queue/etas`);
        if (resp.ok) queueEtas.value = await resp.json();
    } catch (e) {
        console.error('fetchQueueEtas failed:', e);
    }
}

export async function assignModelToJob(rjId, modelName) {
    return updateScheduleJob(rjId, { model: modelName });
}

async function fetchAll() {
    fetchStatus();
    fetchDLQ(); // populate DLQ badge on first load
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
        if (qResp.ok) queue.value = await qResp.json();
        if (hResp.ok) history.value = await hResp.json();
        if (healthResp.ok) healthData.value = await healthResp.json();
        if (durResp.ok) durationData.value = await durResp.json();
        if (heatResp.ok) heatmapData.value = await heatResp.json();
        if (setResp.ok) settings.value = await setResp.json();
        const pi = settings.value.poll_interval_seconds;
        if (pi && pi * 1000 !== POLL_INTERVAL) {
            POLL_INTERVAL = pi * 1000;
            if (pollTimer) { clearTimeout(pollTimer); pollTimer = setTimeout(fetchStatus, POLL_INTERVAL); }
        }
    } catch (e) {
        console.error('Initial fetch failed:', e);
    }
}
