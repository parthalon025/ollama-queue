// What it does: Manages all eval pipeline reactive signals and fetchers — run history,
//   variant configs, templates, trends, stability, settings, and live run polling.
// Decision it drives: The Eval tab's four sub-views (Runs / Variants / Trends / Settings)
//   all read from these signals; mutations (trigger, cancel, save settings) go through
//   the exported functions which update signals on success.

import { signal, computed } from '@preact/signals';
import { API } from './_shared.js';

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

// What it shows: Cross-run F1 stdev and stable/unstable badge data per variant
// Decision it drives: Tells the user whether a variant's quality is consistent enough to trust
export const evalStability = signal({});

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
    // Fire-and-forget: update scheduled eval count for the next 4 hours
    fetch(`${API}/eval/runs?status=scheduled&within_hours=4`)
      .then(r => r.ok ? r.json() : [])
      .then(data => { scheduledEvalCount.value = Array.isArray(data) ? data.length : (data.items?.length || 0); })
      .catch(() => {});
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
  if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
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

// ── Cross-component navigation signals ───────────────────────────────────────

// What it shows: The current recommended/production variant (the "winner").
// Decision it drives: Every place that shows "who's winning" reads this.
export const evalWinner = computed(() =>
  (evalVariants.value || []).find(v => v.is_recommended || v.is_production) || null
);

// What it shows: Number of eval runs scheduled in the next 4 hours.
// Decision it drives: Plan tab badge — is there an eval coming that will use the GPU?
export const scheduledEvalCount = signal(0);

// What it shows: Which variant the user wants to focus on in the Variants tab.
// Decision it drives: VariantChip clicks set this; Variants tab scrolls to match.
export const focusVariantId = signal(null);

// What it shows: Eval runs scheduled within the next 4 hours.
// Decision it drives: Plan tab Gantt renders these as indigo blocks so the user can see
//   upcoming eval runs alongside recurring jobs and avoid scheduling conflicts.
export const scheduledEvalRuns = signal([]);

export async function fetchScheduledEvalRuns() {
  try {
    const res = await fetch(`${API}/eval/runs?status=scheduled`);
    if (!res.ok) return;
    const data = await res.json();
    scheduledEvalRuns.value = Array.isArray(data) ? data : (data.items || []);
    scheduledEvalCount.value = scheduledEvalRuns.value.filter(run => {
      const inFourHours = Date.now() / 1000 + 4 * 3600;
      return run.scheduled_for && run.scheduled_for < inFourHours;
    }).length;
  } catch { /* silent — eval scheduled runs are optional */ }
}

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
