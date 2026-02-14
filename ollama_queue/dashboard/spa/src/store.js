import { signal } from '@preact/signals';

export const status = signal(null);       // /api/status response
export const queue = signal([]);          // /api/queue response
export const history = signal([]);        // /api/history response
export const healthData = signal([]);     // /api/health response
export const durationData = signal([]);   // /api/durations response
export const heatmapData = signal([]);    // /api/heatmap response
export const settings = signal({});       // /api/settings response
export const currentTab = signal('dashboard'); // 'dashboard' | 'settings'

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

async function fetchAll() {
    fetchStatus();
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
