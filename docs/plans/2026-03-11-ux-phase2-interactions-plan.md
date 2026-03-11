# UX Phase 2: Interaction Depth Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **PREREQUISITE:** Phase 1 (`feature/ux-foundation`) must be merged to main before starting this phase.

**Goal:** Add retry on failed jobs, undo-cancel with shatter, live log tail, settings restart banner, stall resolution guidance, DLQ quick-actions, performance tab headers, queue position indicator, Gantt hover tooltips, copy output, and mobile composite badge.

**Architecture:** Mix of SPA JSX changes and one new Python API endpoint (`GET /api/jobs/{id}/log`). Branch from updated main after Phase 1 merges.

**Tech Stack:** Preact 10, @preact/signals, FastAPI (Python), esbuild. Build: `cd ollama_queue/dashboard/spa && npm run build`. Tests: `npm test` (SPA), `python3 -m pytest tests/ -x -q` (Python).

---

## Pre-flight

```bash
cd ~/Documents/projects/ollama-queue
git checkout main && git pull origin main  # Must include Phase 1
git checkout -b feature/ux-interactions
cd ollama_queue/dashboard/spa && npm run build && npm test
cd ../../.. && python3 -m pytest tests/ -x -q
```

---

## Task 1: Log Tail API Endpoint

**Files:**
- Read: `ollama_queue/api/routes/` (find appropriate router file)
- Modify: appropriate API router file
- Create: `tests/api/test_log_tail.py`

**Step 1: Find where job routes live**

```bash
ls ollama_queue/api/routes/
grep -rn "jobs" ollama_queue/api/routes/ | grep "def " | head -10
```

**Step 2: Write failing Python test**

```python
# tests/api/test_log_tail.py
import pytest
from unittest.mock import patch, MagicMock

def test_get_job_log_returns_lines(client):
    """GET /api/jobs/{id}/log returns last N lines of job output."""
    mock_job = MagicMock(id=1, output="line1\nline2\nline3\nline4\nline5\nline6")
    with patch('ollama_queue.db.get_job', return_value=mock_job):
        resp = client.get('/api/jobs/1/log?tail=5')
    assert resp.status_code == 200
    data = resp.json()
    assert 'lines' in data
    assert len(data['lines']) <= 5
    assert data['lines'][-1] == 'line6'

def test_get_job_log_returns_404_for_missing_job(client):
    """GET /api/jobs/{id}/log returns 404 when job not found."""
    with patch('ollama_queue.db.get_job', return_value=None):
        resp = client.get('/api/jobs/999/log')
    assert resp.status_code == 404

def test_get_job_log_returns_empty_for_no_output(client):
    """GET /api/jobs/{id}/log returns empty lines when job has no output."""
    mock_job = MagicMock(id=1, output=None)
    with patch('ollama_queue.db.get_job', return_value=mock_job):
        resp = client.get('/api/jobs/1/log')
    assert resp.json()['lines'] == []
```

**Step 3: Run — expect FAIL**

```bash
python3 -m pytest tests/api/test_log_tail.py -x -v
```

**Step 4: Implement the endpoint**

Find the job routes file. Add:
```python
@router.get("/jobs/{job_id}/log")
async def get_job_log(job_id: int, tail: int = 5, db=Depends(get_db)):
    job = db_get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    output = job.output or ""
    lines = [l for l in output.splitlines() if l.strip()]
    return {"lines": lines[-tail:] if lines else []}
```

**Step 5: Run — expect PASS**

```bash
python3 -m pytest tests/api/test_log_tail.py -x -v
```

**Step 6: Commit**

```bash
git add ollama_queue/api/routes/<router_file>.py tests/api/test_log_tail.py
git commit -m "feat(api): add GET /api/jobs/{id}/log endpoint for live log tail"
```

---

## Task 2: Live Log Tail in CurrentJob

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/CurrentJob.jsx`

**Step 1: Read CurrentJob.jsx**

```bash
cat ollama_queue/dashboard/spa/src/components/CurrentJob.jsx
```

**Step 2: Add log tail state and polling**

In `CurrentJob.jsx`, add inside the component (after existing hooks):

```jsx
import { useState, useEffect, useRef } from 'preact/hooks';

// Log tail for running job
const [logLines, setLogLines] = useState([]);
const [logExpanded, setLogExpanded] = useState(false);

useEffect(() => {
  if (!isRunning || !currentJob?.id) {
    setLogLines([]);
    return;
  }
  let cancelled = false;
  async function fetchLog() {
    try {
      const r = await fetch(`/api/jobs/${currentJob.id}/log?tail=5`);
      if (!cancelled && r.ok) {
        const data = await r.json();
        setLogLines(data.lines || []);
      }
    } catch (_) { /* silent — log tail is best-effort */ }
  }
  fetchLog();
  const interval = setInterval(fetchLog, 5000);
  return () => { cancelled = true; clearInterval(interval); };
}, [isRunning, currentJob?.id]);
```

**Step 3: Render log tail section**

In the `isRunning` JSX block, after the progress bar and before closing div, add:

```jsx
{/* Live log tail — collapsible, best-effort */}
<details style="margin-top:4px;" open={logExpanded} onToggle={e => setLogExpanded(e.target.open)}>
  <summary style="font-family:var(--font-mono);font-size:var(--type-micro);color:var(--text-tertiary);cursor:pointer;user-select:none;">
    Output {logLines.length > 0 ? `(${logLines.length} lines)` : ''}
  </summary>
  <div style="margin-top:6px;padding:8px;background:var(--bg-terminal);border-radius:var(--radius);font-family:var(--font-mono);font-size:var(--type-micro);color:var(--text-secondary);white-space:pre-wrap;word-break:break-all;">
    {logLines.length > 0
      ? logLines.map((line, i) => <div key={i}>{line}</div>)
      : <span style="color:var(--text-tertiary);">No output yet</span>
    }
  </div>
</details>
```

**Step 4: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/CurrentJob.jsx
git commit -m "feat(spa): add collapsible live log tail to CurrentJob (polls /api/jobs/{id}/log)"
```

---

## Task 3: Retry Button on Failed History Entries

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/HistoryList.jsx`
- Modify: `ollama_queue/dashboard/spa/src/store.js`

**Step 1: Read HistoryList.jsx**

```bash
cat ollama_queue/dashboard/spa/src/components/HistoryList.jsx | head -80
```

**Step 2: Add retryJob function to store**

In `store.js`, add:
```js
export async function retryJob(jobId) {
  const resp = await fetch(`/api/jobs/${jobId}`);
  if (!resp.ok) throw new Error('Job not found');
  const job = await resp.json();
  const retryResp = await fetch('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source: job.source,
      model: job.model,
      prompt: job.prompt,
      priority: job.priority,
    }),
  });
  if (!retryResp.ok) throw new Error('Retry failed');
  return retryResp.json();
}
```

**Step 3: Add retry button to HistoryList expanded row**

In `HistoryList.jsx`, in the expanded row section, add a retry button for failed/killed jobs:

```jsx
import { retryJob } from '../store.js';

// In expanded row:
{(job.status === 'failed' || job.status === 'killed') && (
  <button
    class="t-btn"
    style="font-size:var(--type-micro);padding:2px 8px;color:var(--status-warning);"
    onClick={async () => {
      try {
        await retryJob(job.id);
        // Show brief success indicator
        setRetrySuccess(job.id);
        setTimeout(() => setRetrySuccess(null), 2000);
      } catch (e) {
        console.error('Retry failed:', e);
      }
    }}
  >
    {retrySuccess === job.id ? '✓ Requeued' : '↺ Retry'}
  </button>
)}
```

Add `const [retrySuccess, setRetrySuccess] = useState(null);` to component state.

**Step 4: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/HistoryList.jsx \
        ollama_queue/dashboard/spa/src/store.js
git commit -m "feat(spa): add retry button on failed/killed history entries"
```

---

## Task 4: Quick-Cancel with 5s Undo Window

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/QueueList.jsx`

**Step 1: Read QueueList.jsx cancel handler**

```bash
grep -n "cancel\|DELETE\|shatter" ollama_queue/dashboard/spa/src/components/QueueList.jsx
```

**Step 2: Add pending-cancel state with undo**

In `QueueList.jsx`, add:
```jsx
const [pendingCancel, setPendingCancel] = useState({}); // { [jobId]: { timer, rowRef } }

function requestCancel(jobId, rowRef) {
  // Start 5s countdown
  const timer = setTimeout(() => {
    // After 5s: execute the cancel + shatter
    setPendingCancel(prev => {
      const next = { ...prev };
      delete next[jobId];
      return next;
    });
    // Fire shatter then DELETE
    if (rowRef?.current) {
      import('superhot-ui').then(({ shatterElement }) => {
        shatterElement(rowRef.current, {
          onComplete: () => fetch(`/api/jobs/${jobId}`, { method: 'DELETE' }).then(() => triggerRefresh()),
        });
      });
    } else {
      fetch(`/api/jobs/${jobId}`, { method: 'DELETE' }).then(() => triggerRefresh());
    }
  }, 5000);
  setPendingCancel(prev => ({ ...prev, [jobId]: { timer, rowRef } }));
}

function undoCancel(jobId) {
  const pending = pendingCancel[jobId];
  if (pending) clearTimeout(pending.timer);
  setPendingCancel(prev => { const next = { ...prev }; delete next[jobId]; return next; });
}
```

**Step 3: Replace direct cancel with requestCancel in each row's × button**

Find the cancel button onClick and replace with `() => requestCancel(job.id, rowRef)`.

**Step 4: Add undo toast**

At the bottom of the QueueList render, add a fixed toast area:
```jsx
{Object.entries(pendingCancel).map(([jobId]) => (
  <div key={jobId} style="position:fixed;bottom:80px;left:50%;transform:translateX(-50%);z-index:200;background:var(--bg-surface);border:1px solid var(--border-primary);padding:8px 16px;border-radius:var(--radius);display:flex;align-items:center;gap:12px;font-size:var(--type-label);">
    <span>Cancelled.</span>
    <button class="t-btn" style="font-size:var(--type-micro);padding:2px 8px;" onClick={() => undoCancel(jobId)}>
      Undo
    </button>
  </div>
))}
```

**Step 5: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 6: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/QueueList.jsx
git commit -m "feat(spa): add 5s undo window on queue row cancel before shatter fires"
```

---

## Task 5: Settings Restart-Required Banner

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/SettingsForm.jsx` (or `pages/Settings.jsx`)

**Step 1: Read SettingsForm**

```bash
cat ollama_queue/dashboard/spa/src/components/SettingsForm.jsx | head -60
grep -n "restart\|concurren\|stall" ollama_queue/dashboard/spa/src/components/SettingsForm.jsx | head -10
```

**Step 2: Add restart-required tracking**

Identify which field names in the settings form correspond to daemon-affecting settings. Add to `SettingsForm.jsx`:

```jsx
const RESTART_REQUIRED_FIELDS = new Set(['concurrency', 'stall_threshold_seconds', 'burst_detection_enabled']);
const [restartRequired, setRestartRequired] = useState(false);

// In the save/blur handler, after saving:
function onFieldSave(fieldName, value) {
  // ... existing save logic ...
  if (RESTART_REQUIRED_FIELDS.has(fieldName)) {
    setRestartRequired(true);
  }
}
```

**Step 3: Add restart banner**

At the top of the SettingsForm render (or Settings.jsx), add:

```jsx
{restartRequired && (
  <div style="background:color-mix(in srgb,var(--status-warning) 12%,transparent);border:1px solid var(--status-warning);border-radius:var(--radius);padding:10px 14px;display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px;">
    <span style="font-size:var(--type-label);color:var(--status-warning);">
      ⚠ Daemon restart required for these changes to take effect.
    </span>
    <button
      class="t-btn"
      style="font-size:var(--type-micro);padding:2px 10px;color:var(--status-warning);border-color:var(--status-warning);"
      onClick={restartDaemon}
    >
      Restart daemon
    </button>
  </div>
)}
```

Where `restartDaemon` calls the existing daemon restart API. Find it in the codebase:
```bash
grep -n "restart" ollama_queue/dashboard/spa/src/store.js
```

**Step 4: Clear banner when daemon transitions through restart**

Add a `useEffect` watching `daemonState`: when `daemonState` changes from `restarting` to `running`, set `setRestartRequired(false)`.

**Step 5: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 6: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/SettingsForm.jsx
git commit -m "feat(spa): add restart-required banner for daemon-affecting settings changes"
```

---

## Task 6: Stall Resolution Guidance

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/CurrentJob.jsx`

**Step 1: Replace frozen badge with expandable guidance**

Find the `⚠ frozen` badge in `CurrentJob.jsx`:
```jsx
{isStalled && (
  <span title="...">⚠ frozen</span>
)}
```

Replace with:
```jsx
{isStalled && (
  <details style="display:inline;">
    <summary style="cursor:pointer;font-size:var(--type-label);color:var(--status-warning);background:rgba(245,158,11,0.1);padding:1px 6px;border-radius:3px;border:1px solid var(--status-warning);list-style:none;display:inline-flex;align-items:center;gap:4px;">
      ⚠ frozen — what should I do?
    </summary>
    <div style="position:absolute;z-index:10;background:var(--bg-surface);border:1px solid var(--border-primary);border-radius:var(--radius);padding:12px;max-width:320px;font-size:var(--type-label);color:var(--text-secondary);box-shadow:var(--card-shadow-hover);margin-top:4px;">
      <p style="margin:0 0 8px;font-weight:600;color:var(--status-warning);">Job is not producing output.</p>
      <ol style="margin:0;padding-left:16px;display:flex;flex-direction:column;gap:4px;">
        <li>Wait 2 more minutes — some models are slow to start</li>
        <li>Cancel and retry — click × in the queue below</li>
        <li>Check Ollama: run <code style="font-family:var(--font-mono);">ollama ps</code> to verify model is loaded</li>
        <li>Restart daemon from Settings if Ollama itself is stuck</li>
      </ol>
    </div>
  </details>
)}
```

**Step 2: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/CurrentJob.jsx
git commit -m "feat(spa): replace frozen badge with expandable stall resolution guidance"
```

---

## Task 7: DLQ Quick-Actions in Now Alert Strip

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Now.jsx`
- Modify: `ollama_queue/dashboard/spa/src/store.js`

**Step 1: Read the alert strip in Now.jsx**

```bash
grep -n "dlq\|alert\|DLQ" ollama_queue/dashboard/spa/src/pages/Now.jsx | head -20
```

**Step 2: Add dismissAllDlq to store**

```js
export async function dismissAllDlq() {
  const resp = await fetch('/api/dlq', { method: 'DELETE' });
  if (!resp.ok) throw new Error('Failed to dismiss DLQ entries');
  dlqCount.value = 0;
}
```
Check if `DELETE /api/dlq` exists:
```bash
grep -rn "DELETE.*dlq\|dlq.*DELETE" ollama_queue/api/ | head -5
```
If not, add it following the same pattern as Task 1.

**Step 3: Add quick-action buttons to alert strip in Now.jsx**

Find the DLQ alert strip. Add after the count text:
```jsx
<div style="display:flex;gap:8px;margin-left:auto;">
  <button class="t-btn" style="font-size:var(--type-micro);padding:2px 8px;" onClick={() => { currentTab.value = 'history'; }}>
    View failed
  </button>
  <button class="t-btn" style="font-size:var(--type-micro);padding:2px 8px;color:var(--text-tertiary);" onClick={dismissAllDlq}>
    Dismiss all
  </button>
</div>
```

**Step 4: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Now.jsx \
        ollama_queue/dashboard/spa/src/store.js
git commit -m "feat(spa): add View failed / Dismiss all quick-actions to DLQ alert strip"
```

---

## Task 8: Performance Tab Headers, Queue Position, Gantt Hover, Copy Output, Mobile Badge

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Performance.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/QueueList.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/HistoryList.jsx`
- Modify: `ollama_queue/dashboard/spa/src/components/BottomNav.jsx`

**Step 1: Performance tab chart headers**

Read `Performance.jsx`. For each chart section, add a `<p>` explanation above the chart component:

```jsx
// Duration trend:
<p style="font-size:var(--type-label);color:var(--text-secondary);margin-bottom:8px;">
  How long jobs take to run over time. Spikes indicate model loading delays or system pressure.
</p>

// Wait time trend:
<p style="font-size:var(--type-label);color:var(--text-secondary);margin-bottom:8px;">
  How long jobs wait before starting. Rising trends mean the queue is backing up faster than the daemon can drain it.
</p>

// Throughput:
<p style="font-size:var(--type-label);color:var(--text-secondary);margin-bottom:8px;">
  Jobs completed per hour. Use this to predict capacity for batch workloads.
</p>
```

**Step 2: Queue position per source**

In `QueueList.jsx`, before the return, compute per-source positions:
```jsx
const sourcePositions = {};
(jobs || []).forEach(job => {
  if (!sourcePositions[job.source]) sourcePositions[job.source] = [];
  sourcePositions[job.source].push(job.id);
});
```

In the row render, where source count > 1:
```jsx
{sourcePositions[job.source]?.length > 1 && (
  <span class="data-mono" style="font-size:var(--type-micro);color:var(--text-tertiary);">
    #{sourcePositions[job.source].indexOf(job.id) + 1} of {sourcePositions[job.source].length}
  </span>
)}
```

**Step 3: Gantt hover tooltip**

In `GanttChart.jsx`, add tooltip state:
```jsx
const [tooltip, setTooltip] = useState(null); // { x, y, job }
```

On each bar div, add:
```jsx
onMouseEnter={e => setTooltip({ x: e.clientX + 16, y: e.clientY, job })}
onMouseLeave={() => setTooltip(null)}
```

Render tooltip at bottom of component:
```jsx
{tooltip && (
  <div style={`position:fixed;left:${tooltip.x}px;top:${tooltip.y}px;z-index:100;background:var(--bg-surface);border:1px solid var(--border-primary);border-radius:var(--radius);padding:10px 12px;font-size:var(--type-label);color:var(--text-secondary);pointer-events:none;box-shadow:var(--card-shadow-hover);`}>
    <div style="color:var(--text-primary);margin-bottom:4px;">{tooltip.job.source}</div>
    <div>{tooltip.job.model}</div>
    <div style="color:var(--text-tertiary);">{PRIORITY_LABELS[getPriorityCategory(tooltip.job.priority)]}</div>
  </div>
)}
```

**Step 4: Copy output on history rows**

In `HistoryList.jsx`, expanded row, add after job output display:
```jsx
{job.output && (
  <button
    class="t-btn"
    style="font-size:var(--type-micro);padding:2px 8px;"
    onClick={async () => {
      await navigator.clipboard.writeText(job.output);
      setCopied(job.id);
      setTimeout(() => setCopied(null), 2000);
    }}
  >
    {copied === job.id ? '✓ Copied' : '⎘ Copy output'}
  </button>
)}
```
Add `const [copied, setCopied] = useState(null);` to state.

**Step 5: Mobile composite notification badge**

In `BottomNav.jsx`, compute:
```jsx
const issueCount = (dlqCount.value || 0) +
  (isStalled ? 1 : 0) +
  (resourceCritical ? 1 : 0);
```

Replace per-tab DLQ badge with a single badge on the History tab icon when `issueCount > 0`.

**Step 6: Build + full test**

```bash
cd ollama_queue/dashboard/spa && npm run build && npm test
cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/ -x -q
```

**Step 7: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Performance.jsx \
        ollama_queue/dashboard/spa/src/components/QueueList.jsx \
        ollama_queue/dashboard/spa/src/components/GanttChart.jsx \
        ollama_queue/dashboard/spa/src/components/HistoryList.jsx \
        ollama_queue/dashboard/spa/src/components/BottomNav.jsx
git commit -m "feat(spa): performance headers, queue position, Gantt hover, copy output, mobile badge"
```

---

## Task 9: Final Push + PR

**Step 1: Full build and test**

```bash
cd ollama_queue/dashboard/spa && npm run build && npm test
cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/ -x -q
```

**Step 2: Push**

```bash
cd ~/Documents/projects/ollama-queue
git push -u origin feature/ux-interactions
```

**Step 3: Create PR**

```bash
gh pr create \
  --title "feat(spa): UX Phase 2 — interaction depth (retry, undo cancel, log tail, stall guidance, DLQ actions, performance headers)" \
  --body "## UX Phase 2: Interaction Depth

Implements items 8–15, 17–19 from the UX & design philosophy improvements design.

### Changes
- GET /api/jobs/{id}/log endpoint (Python)
- Live log tail in CurrentJob (5s poll, collapsible)
- Retry button on failed/killed history entries
- Quick-cancel with 5s undo window (shatter fires after undo expires)
- Settings restart-required banner
- Stall resolution guidance (expandable from frozen badge)
- DLQ quick-actions: View failed / Dismiss all in Now alert strip
- Performance tab chart explanation headers
- Queue position per source (#2 of 3)
- Gantt bar hover tooltip
- Copy output button on history rows
- Mobile composite notification badge

### Design doc
\`docs/plans/2026-03-11-ux-design-philosophy-improvements-design.md\`" \
  --base main
```
