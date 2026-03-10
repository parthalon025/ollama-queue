// What it does: Manages queue/job reactive signals — the live queue list, job history,
//   ETA estimates, and connection health status.
// Decision it drives: Components that show the current queue, submit new jobs, or display
//   connection banners read from these signals without needing their own fetch logic.

import { signal } from '@preact/signals';
import { API } from './_shared.js';

export const status = signal(null);       // /api/status response
export const queue = signal([]);          // /api/queue response
export const history = signal([]);        // /api/history response
export const queueEtas = signal([]);
export const connectionStatus = signal('ok'); // 'ok' | 'disconnected'

export async function fetchQueueEtas() {
    try {
        const resp = await fetch(`${API}/queue/etas`);
        if (resp.ok) queueEtas.value = await resp.json();
    } catch (e) {
        console.error('fetchQueueEtas failed:', e);
    }
}

// What it does: Submits a one-off job to the queue.
// Decision it drives: Returns { job_id } on success so the caller can show confirmation.
export async function submitJob(body) {
    const resp = await fetch(`${API}/queue/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!resp.ok) {
        const text = await resp.text();
        throw new Error(text || `Submit failed: ${resp.status}`);
    }
    return resp.json(); // { job_id: N }
}

export async function refreshQueue() {
    try {
        const resp = await fetch(`${API}/status`);
        if (resp.ok) {
            const data = await resp.json();
            status.value = data;
            if (Array.isArray(data.queue)) queue.value = data.queue;
        }
    } catch (e) {
        console.error('refreshQueue failed:', e);
    }
}
