import { signal } from '@preact/signals';

export const status = signal(null);       // /api/status response
export const queue = signal([]);          // /api/queue response
export const history = signal([]);        // /api/history response
export const healthData = signal([]);     // /api/health response
export const durationData = signal([]);   // /api/durations response
export const heatmapData = signal([]);    // /api/heatmap response
export const settings = signal({});       // /api/settings response
export const currentTab = signal('dashboard'); // 'dashboard' | 'schedule' | 'dlq' | 'settings'
export const scheduleJobs = signal([]);
export const scheduleEvents = signal([]);
export const dlqEntries = signal([]);
export const dlqCount = signal(0);

// Derive API base from current URL so it works behind Tailscale Serve path prefix.
// /ui/ → /api, /queue/ui/ → /queue/api
const pathBase = window.location.pathname.replace(/\/ui\/.*$/, '').replace(/\/ui$/, '');
export const API = `${pathBase}/api`;

const POLL_INTERVAL = 5000;
let pollTimer = null;

export function startPolling() {
    fetchAll();
    pollTimer = setInterval(fetchStatus, POLL_INTERVAL);
}

export function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
}

async function fetchStatus() {
    try {
        const resp = await fetch(`${API}/status`);
        if (resp.ok) status.value = await resp.json();
    } catch (e) {
        console.error('Poll failed:', e);
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
    } catch (e) {
        console.error('fetchSchedule failed:', e);
    }
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
    try {
        await fetch(`${API}/schedule/rebalance`, { method: 'POST' });
        await fetchSchedule();
    } catch (e) {
        console.error('triggerRebalance failed:', e);
    }
}

export async function runScheduleJobNow(id) {
    const resp = await fetch(`${API}/schedule/${id}/run-now`, { method: 'POST' });
    if (!resp.ok) throw new Error(`run-now failed: ${resp.status}`);
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
    } catch (e) {
        console.error('Initial fetch failed:', e);
    }
}
