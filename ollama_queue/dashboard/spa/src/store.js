// What it does: Central data layer — reactive signals (auto-updating state) + all API fetch
//   functions. Every component reads from signals; when a signal changes, only the components
//   that use it re-render. No component fetches data directly — all API calls live here.
// Decision it drives: Keeps the whole UI in sync with the backend without each component
//   needing its own fetch logic or prop drilling.

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
export const loadMap = signal(null);  // /api/schedule/load-map response

// ── Eval pipeline signals ─────────────────────────────────────────────────────

// What it shows: Current sub-tab within the Eval page (Runs / Configurations / Trends / Settings)
// Decision it drives: Which eval view is displayed
export const evalSubTab = signal('runs');

// What it shows: List of all eval runs (summary)
// Decision it drives: Run history table and active run tracking
export const evalRuns = signal([]);

// What it shows: All eval variant configs (system defaults + user-created)
// Decision it drives: Variant selection in run trigger panel, config management
export const evalVariants = signal([]);

// What it shows: All eval prompt templates
// Decision it drives: Template selection when creating/editing variants
export const evalTemplates = signal([]);

// What it shows: F1 trend data per variant across completed runs
// Decision it drives: Trend chart and stability indicators
export const evalTrends = signal(null);

// What it shows: The currently-active eval run being monitored (persisted to sessionStorage)
// Decision it drives: Whether to show the live progress panel; resumes after page refresh
export const evalActiveRun = signal(
  (() => { try { const v = sessionStorage.getItem('evalActiveRun'); return v ? JSON.parse(v) : null; } catch { return null; } })()
);

// What it shows: All eval.* settings (data source URL, judge config, etc.)
// Decision it drives: Settings form state and setup checklist progress
export const evalSettings = signal({});

// Polling interval ID for active run progress
let _evalPollId = null;

// What it shows: nothing — controls live progress polling
// Decision it drives: Starts 5s polling of /api/eval/runs/{id}/progress, stops when run completes
export function startEvalPoll(runId) {
  stopEvalPoll();
  _evalPollId = setInterval(async () => {
    try {
      const res = await fetch(`${API}/eval/runs/${runId}/progress`);
      if (!res.ok) return;
      const data = await res.json();
      evalActiveRun.value = data;
      sessionStorage.setItem('evalActiveRun', JSON.stringify(data));
      if (['complete', 'failed', 'cancelled'].includes(data.status)) {
        stopEvalPoll();
        fetchEvalRuns(); // refresh history table so final metrics appear immediately
      }
    } catch (e) {
      console.error('evalPoll failed:', e);
    }
  }, 5000);
}

export function stopEvalPoll() {
  if (_evalPollId !== null) { clearInterval(_evalPollId); _evalPollId = null; }
}

// ── Eval data fetch functions ─────────────────────────────────────────────────

// What it shows: nothing — fetches and updates eval variants signal
// Decision it drives: keeps variant list in sync with backend
export async function fetchEvalVariants() {
  try {
    const res = await fetch(`${API}/eval/variants`);
    if (res.ok) evalVariants.value = await res.json();
  } catch (e) { console.error('fetchEvalVariants failed:', e); }
}

// What it shows: nothing — fetches and updates eval templates signal
// Decision it drives: keeps template list in sync with backend
export async function fetchEvalTemplates() {
  try {
    const res = await fetch(`${API}/eval/templates`);
    if (res.ok) evalTemplates.value = await res.json();
  } catch (e) { console.error('fetchEvalTemplates failed:', e); }
}

// What it shows: nothing — fetches and updates eval runs signal
// Decision it drives: keeps run history table and active run tracking in sync
export async function fetchEvalRuns() {
  try {
    const res = await fetch(`${API}/eval/runs`);
    if (res.ok) evalRuns.value = await res.json();
  } catch (e) { console.error('fetchEvalRuns failed:', e); }
}

// What it shows: nothing — fetches and updates eval settings signal
// Decision it drives: keeps settings form and defaults in sync with backend
export async function fetchEvalSettings() {
  try {
    const res = await fetch(`${API}/eval/settings`);
    if (res.ok) evalSettings.value = await res.json();
  } catch (e) { console.error('fetchEvalSettings failed:', e); }
}

// What it shows: Cross-run F1 stdev and stable/unstable badge data per variant
// Decision it drives: Tells the user whether a variant's quality is consistent enough to trust
export const evalStability = signal({});

// What it shows: nothing — fetches cross-run stability data per variant
// Decision it drives: provides stdev/stable badge data to VariantStabilityTable
export async function fetchVariantStability() {
  try {
    const res = await fetch(`${API}/eval/variants/stability`);
    if (!res.ok) return;
    evalStability.value = await res.json();
  } catch (err) {
    console.warn('fetchVariantStability failed:', err);
  }
}

// What it shows: nothing — fetches structured analysis for one run
// Decision it drives: provides CI, per-item breakdown, and failure data to RunRow
export async function fetchRunAnalysis(runId) {
  try {
    const res = await fetch(`${API}/eval/runs/${runId}/analysis`);
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.warn('fetchRunAnalysis failed:', err);
    return null;
  }
}

// Normalize raw /eval/trends response so components receive consistent shapes:
//  - variants: object keyed by id → array with id field attached
//  - each run: started_at ISO string → timestamp (unix seconds) added
//  - trend_direction: aggregated across variants (regressing > stable > improving)
//  - completed_runs, judge_reliability, item_count_growing: aggregated at top level
function normalizeTrends(raw) {
  const variantsArr = Object.entries(raw.variants || {}).map(([id, v]) => ({
    id,
    ...v,
    runs: (v.runs || []).map(r => ({
      ...r,
      timestamp: r.timestamp ?? Math.floor(new Date(r.started_at).getTime() / 1000),
    })),
  }));

  const dirs = variantsArr.map(v => v.trend_direction);
  const overallDir = dirs.includes('regressing') ? 'regressing'
    : dirs.includes('improving') ? 'improving'
    : 'stable';

  const completedRuns = variantsArr.reduce((s, v) => s + (v.runs || []).length, 0);

  const reliabilities = variantsArr.map(v => v.judge_agreement_rate).filter(r => r != null);
  const judgeReliability = reliabilities.length > 0
    ? reliabilities.reduce((a, b) => a + b, 0) / reliabilities.length
    : null;

  const itemCountGrowing = variantsArr.some(v => {
    const runs = v.runs || [];
    for (let i = 1; i < runs.length; i++) {
      if ((runs[i].item_count || 0) > (runs[i - 1].item_count || 0)) return true;
    }
    return false;
  });

  return {
    ...raw,
    variants: variantsArr,
    trend_direction: overallDir,
    completed_runs: completedRuns,
    judge_reliability: judgeReliability,
    item_count_growing: itemCountGrowing,
  };
}

// What it shows: nothing — fetches F1 trend data per variant across completed runs
// Decision it drives: keeps trend chart and stability table in sync with backend
export async function fetchEvalTrends() {
  try {
    const res = await fetch(`${API}/eval/trends`);
    if (res.ok) evalTrends.value = normalizeTrends(await res.json());
  } catch (e) { console.error('fetchEvalTrends failed:', e); }
}

// What it shows: nothing — tests the configured data source connection
// Decision it drives: returns {ok, item_count, cluster_count, response_ms} for setup checklist + status display
export async function testDataSource() {
  const res = await fetch(`${API}/eval/datasource/test`);
  return res.json();
}

// What it shows: nothing — triggers cluster_seed backfill on the data source
// Decision it drives: after this resolves, /eval/items will return lessons that
//   were previously missing cluster_seed, making them visible to the eval pipeline
export async function primeDataSource() {
  const res = await fetch(`${API}/eval/datasource/prime`, { method: 'POST' });
  if (!res.ok) throw new Error(`Prime failed: HTTP ${res.status}`);
  return res.json();
}

// What it shows: nothing — saves eval settings to the backend
// Decision it drives: updates evalSettings signal on success; throws on validation or server error
export async function saveEvalSettings(updates) {
  const res = await fetch(`${API}/eval/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || 'Settings save failed');
  }
  const updated = await res.json();
  evalSettings.value = updated;
  return updated;
}

// What it shows: nothing — submits a new eval run
// Decision it drives: returns { run_id } so caller can start polling progress
export async function triggerEvalRun(body) {
  const res = await fetch(`${API}/eval/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Trigger failed: ${res.status}`);
  return res.json(); // { run_id }
}

// What it shows: nothing — cancels an active eval run
// Decision it drives: stops live progress polling and refreshes run history
export async function cancelEvalRun(runId) {
  const res = await fetch(`${API}/eval/runs/${runId}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Cancel failed: ${res.status}`);
  stopEvalPoll();
  evalActiveRun.value = null;
  sessionStorage.removeItem('evalActiveRun');
  await fetchEvalRuns();
}

// Derive API base from current URL so it works behind Tailscale Serve path prefix.
// /ui/ → /api, /queue/ui/ → /queue/api
const pathBase = window.location.pathname.replace(/\/ui\/.*$/, '').replace(/\/ui$/, '');
export const API = `${pathBase}/api`;

// On startup: verify stored active run is still live — clear it if the API says it's terminal.
// This prevents a stale "Run #N generating" panel persisting after a service restart.
if (evalActiveRun.value) {
  const _storedId = evalActiveRun.value.run_id;
  fetch(`${API}/eval/runs/${_storedId}/progress`)
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (evalActiveRun.value?.run_id !== _storedId) return; // user started a new run
      if (!data || ['complete', 'failed', 'cancelled'].includes(data.status)) {
        evalActiveRun.value = null;
        sessionStorage.removeItem('evalActiveRun');
      }
    })
    .catch(() => {
      // If API is unreachable, leave the stored run — it may come back
    });
}

let POLL_INTERVAL = 5000;
let pollTimer = null;
let _pollFailures = 0;
let _pollCount = 0;
let _backoffMs = 5000;

// ── Polling loop ────────────────────────────────────────────────────────────
// Keeps status + queue signals fresh every 5s. Non-realtime data (health charts,
// history) refreshes every 60s (every 12 status polls) to reduce API load.
// Backs off exponentially on repeated failures and sets connectionStatus='disconnected'
// after 3 consecutive failures so the banner appears.
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
        if (hResp.ok) { const d = await hResp.json(); healthData.value = Array.isArray(d) ? d : (d.log ?? []); }
        if (durResp.ok) durationData.value = await durResp.json();
        if (heatResp.ok) heatmapData.value = await heatResp.json();
        if (histResp.ok) history.value = await histResp.json();
        if (lmResp.ok) loadMap.value = await lmResp.json();
    } catch (e) {
        console.error('Non-realtime refresh failed:', e);
    }
}

// ── Schedule / recurring jobs ────────────────────────────────────────────────
// Fetched when the Plan tab loads, and after any mutation (toggle, run-now, etc.).
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

// ── Model management ─────────────────────────────────────────────────────────
// Lists installed models and searches the downloadable catalog.
// Pull lifecycle: startModelPull → poll /api/models/pull/{id} → cancelModelPull.
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

// ── One-off job submission ────────────────────────────────────────────────────
// Called by SubmitJobModal. Returns { job_id } on success.
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

// ── Intercept mode state ──────────────────────────────────────────────────────

// What it shows: Whether iptables intercept mode is enabled and whether the rule is live.
// Decision it drives: User sees at a glance if all :11434 traffic is being routed through
//   the queue; enables or disables MITM interception for services with hardcoded Ollama URLs.
export const interceptStatus = signal({ enabled: false, rule_present: false });

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

// ── Consumers signals ─────────────────────────────────────────────────────────

// What it shows: List of detected Ollama-calling services and scan progress state.
// Decision it drives: User sees which services need to be routed through the queue.
export const consumers = signal([]);
export const consumersScanning = signal(false);

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
        if (healthResp.ok) { const d = await healthResp.json(); healthData.value = Array.isArray(d) ? d : (d.log ?? []); }
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
