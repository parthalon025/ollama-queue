// What it does: Manages schedule/recurring-job reactive signals and all mutation functions —
//   the job list, timeline events, load-map data, and CRUD operations for recurring jobs.
// Decision it drives: The Plan tab reads scheduleJobs/scheduleEvents/loadMap to render the
//   Gantt chart, load-map strip, and job table. All add/edit/toggle/delete/rebalance actions
//   flow through the exported functions which refresh signals on success.

import { signal } from '@preact/signals';
import { API } from './_shared.js';
import { fetchQueueEtas } from './queue.js';

export const scheduleJobs = signal([]);
export const scheduleEvents = signal([]);
export const loadMap = signal(null);  // /api/schedule/load-map response

// ── Schedule / recurring jobs ────────────────────────────────────────────────
// Fetched when the Plan tab loads, and after any mutation (toggle, run-now, etc.).
export async function fetchSchedule() {
    try {
        const [jobsResp, eventsResp] = await Promise.all([
            fetch(`${API}/schedule`),
            fetch(`${API}/schedule/events?limit=20`),
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
        if (resp.ok) loadMap.value = await resp.json();
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

// What it does: Calls the backend to ask Ollama to write a plain-English description
//   for the given recurring job. The call blocks until Ollama responds (~5-10s).
// Decision it drives: Returns {ok, description} so the UI can update the text immediately.
export async function generateJobDescription(rjId) {
    const resp = await fetch(`${API}/schedule/${rjId}/generate-description`, { method: 'POST' });
    if (!resp.ok) throw new Error(`generateJobDescription failed: ${resp.status}`);
    return resp.json(); // { ok: true, description: "..." }
}

export async function addRecurringJob(body) {
    const resp = await fetch(`${API}/schedule`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!resp.ok) {
        const text = await resp.text();
        throw new Error(text || `Add job failed: ${resp.status}`);
    }
    return resp.json();
}

export async function enableJobByName(name) {
    const resp = await fetch(`${API}/schedule/jobs/${encodeURIComponent(name)}/enable`, {
        method: 'POST',
    });
    if (!resp.ok) throw new Error(`Enable failed: ${resp.status}`);
    await fetchSchedule();
}

export async function assignModelToJob(rjId, modelName) {
    return updateScheduleJob(rjId, { model: modelName });
}
