# Max UX — All Pages Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bring ScheduleTab, ModelsTab, and DLQTab to the same quality bar as Dashboard after the 17-improvement pass.

**Architecture:** Pure frontend changes — JSX component edits only. No new API endpoints. All helpers implemented inline per component (DRY within file, not across files — cross-component imports add coupling). Debounce via 3-line inline hook. Sort via `useState`.

**Tech Stack:** Preact 10, @preact/signals, Tailwind v4 (inline style fallback), uPlot (not used here), esbuild JSX (CAUTION: never name loop variables `h` or `Fragment` — shadows injected JSX factory)

---

### Task 1: ScheduleTab — Horizontal scroll + sticky Name column

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx`

**Step 1: Locate the table wrapper**

Read ScheduleTab.jsx. Find the outermost `<div>` wrapping the `<table>`. It will be something like `<div class="...">` immediately before `<table`.

**Step 2: Add scroll wrapper + sticky column**

Wrap the table in an overflow-x container:
```jsx
<div style="overflow-x: auto; -webkit-overflow-scrolling: touch;">
  <table style="min-width: 700px; width: 100%;">
    ...
  </table>
</div>
```

Find the Name `<td>` and `<th>` cells and add sticky positioning:
```jsx
// th
<th style="position: sticky; left: 0; background: var(--bg-panel); z-index: 1; white-space: nowrap;">Name</th>

// td in each row
<td style="position: sticky; left: 0; background: var(--bg-panel); z-index: 1;">
  {job.name}
</td>
```

**Step 3: Build and verify**
```bash
cd ollama_queue/dashboard/spa && npm run build
```
Expected: clean build, no errors.

**Step 4: Commit**
```bash
git add ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
git commit -m "feat(schedule): horizontal scroll with sticky Name column"
```

---

### Task 2: ScheduleTab — Humanize interval + relative Next Run

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx`

**Step 1: Add helper functions at bottom of file**

After the last function in the file, add:
```jsx
function humanInterval(seconds) {
  if (!seconds) return '—';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function relNext(ts) {
  if (!ts) return '—';
  const diff = Math.round(ts - Date.now() / 1000);
  if (diff < -60) return 'overdue';
  if (diff < 0) return 'now';
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `in ${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `in ${Math.floor(diff / 3600)}h`;
  return `in ${Math.floor(diff / 86400)}d`;
}

function absDatetime(ts) {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleString();
}
```

**Step 2: Replace interval cell**

Find where `interval_seconds` is rendered (likely `{job.interval_seconds}` or similar). Replace with:
```jsx
{humanInterval(job.interval_seconds)}
```

**Step 3: Replace Next Run cell**

Find where `next_run` is rendered. Replace with:
```jsx
<span title={absDatetime(job.next_run)}>{relNext(job.next_run)}</span>
```

**Step 4: Build**
```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**
```bash
git add ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
git commit -m "feat(schedule): humanize interval, relative next-run with absolute tooltip"
```

---

### Task 3: ScheduleTab — Overdue badge

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx`

**Step 1: Add overdue badge to Next Run cell**

In the Next Run cell (updated in Task 2), detect overdue and add a pill:
```jsx
{(() => {
  const diff = job.next_run ? Math.round(job.next_run - Date.now() / 1000) : null;
  const isOverdue = diff !== null && diff < 0;
  const isSevere = isOverdue && Math.abs(diff) > (job.interval_seconds || 0) * 2;
  return (
    <>
      <span title={absDatetime(job.next_run)}>{relNext(job.next_run)}</span>
      {isOverdue && (
        <span style={{
          marginLeft: 6,
          fontSize: 'var(--type-micro)',
          color: isSevere ? 'var(--status-error)' : '#f97316',
          background: isSevere ? 'rgba(239,68,68,0.12)' : 'rgba(249,115,22,0.12)',
          border: `1px solid ${isSevere ? 'rgba(239,68,68,0.4)' : 'rgba(249,115,22,0.4)'}`,
          borderRadius: 4,
          padding: '1px 5px',
        }}>OVERDUE</span>
      )}
    </>
  );
})()}
```

**Step 2: Build**
```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 3: Commit**
```bash
git add ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
git commit -m "feat(schedule): overdue badge in next-run cell (amber/red severity)"
```

---

### Task 4: ScheduleTab — Rebalance button feedback

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx`

**Step 1: Find the rebalance button and its handler**

Look for the `onClick` handler that calls the rebalance API endpoint. It will be something like `fetch('/api/schedule/rebalance', { method: 'POST' })`.

**Step 2: Add loading/success/error state**

Add state near the top of the component (or the sub-component containing the button):
```jsx
const [rebalancing, setRebalancing] = useState(false);
const [rebalanceFlash, setRebalanceFlash] = useState(null); // 'ok' | 'error' | null
```

Wrap the handler:
```jsx
const handleRebalance = async () => {
  setRebalancing(true);
  setRebalanceFlash(null);
  try {
    const res = await fetch('/api/schedule/rebalance', { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    setRebalanceFlash('ok');
    setTimeout(() => setRebalanceFlash(null), 2000);
    // trigger a refresh of schedule data here (call existing refresh fn if present)
  } catch (err) {
    setRebalanceFlash('error');
    setTimeout(() => setRebalanceFlash(null), 4000);
  } finally {
    setRebalancing(false);
  }
};
```

**Step 3: Update button JSX**
```jsx
<button
  class="t-btn t-btn-secondary px-3 py-1 text-sm"
  onClick={handleRebalance}
  disabled={rebalancing}
  style={{
    background: rebalanceFlash === 'ok' ? 'var(--status-healthy-glow)' : undefined,
    transition: 'background 0.3s ease',
    opacity: rebalancing ? 0.6 : 1,
  }}
>
  {rebalancing ? '⟳ Rebalancing…' : 'Rebalance'}
</button>
{rebalanceFlash === 'error' && (
  <span style="font-size: var(--type-micro); color: var(--status-error); margin-left: 8px;">
    Rebalance failed
  </span>
)}
```

**Step 4: Build**
```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**
```bash
git add ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
git commit -m "feat(schedule): rebalance button loading/success/error feedback"
```

---

### Task 5: ScheduleTab — Rebalance log relative timestamps

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx`

**Step 1: Find the rebalance log rendering**

Look for where rebalance log entries are rendered. Each entry likely has a timestamp field (may be `logged_at`, `timestamp`, or similar). It will be rendered as a formatted time string.

**Step 2: Add relativeTime helper (if not already present)**

At bottom of file:
```jsx
function relativeTimeLog(ts) {
  if (!ts) return '—';
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  const d = new Date(ts * 1000);
  return `yesterday ${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`;
}
```

**Step 3: Replace timestamp rendering in log entries**

Find the log timestamp cell and replace with:
```jsx
<span title={new Date(entry.timestamp * 1000).toLocaleString()}>
  {relativeTimeLog(entry.timestamp)}
</span>
```
(Adjust field name `entry.timestamp` to match actual field name found in step 1.)

**Step 4: Build + commit**
```bash
cd ollama_queue/dashboard/spa && npm run build
git add ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
git commit -m "feat(schedule): rebalance log uses relative timestamps"
```

---

### Task 6: ScheduleTab — Live search filter

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx`

**Step 1: Add search state and debounce hook**

Near top of component (or at top of file):
```jsx
function useDebounce(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}
```

In the component:
```jsx
const [search, setSearch] = useState('');
const debouncedSearch = useDebounce(search, 300);
```

**Step 2: Filter jobs array**

Find where `jobs` (the schedule jobs array) is iterated. Before the table, filter:
```jsx
const visible = debouncedSearch
  ? jobs.filter(j => j.name.toLowerCase().includes(debouncedSearch.toLowerCase()))
  : jobs;
```
Replace `jobs.map(...)` with `visible.map(...)` in the table body.

**Step 3: Add search input above table**
```jsx
<div style="display: flex; gap: 8px; align-items: center; margin-bottom: 8px;">
  <input
    class="t-input"
    type="text"
    placeholder="Search jobs…"
    value={search}
    onInput={(e) => setSearch(e.target.value)}
    style="width: 200px; padding: 4px 8px; font-size: var(--type-body);"
  />
  {search && (
    <button
      class="t-btn t-btn-secondary"
      style="padding: 4px 8px; font-size: var(--type-label);"
      onClick={() => setSearch('')}
    >✕</button>
  )}
</div>
{visible.length === 0 && debouncedSearch && (
  <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center; padding: 16px 0;">
    No jobs match "{debouncedSearch}"
  </p>
)}
```

**Step 4: Import useEffect if not already imported**

Check top of file for `import { ..., useEffect, useState } from 'preact/hooks';` — add missing hooks.

**Step 5: Build + commit**
```bash
cd ollama_queue/dashboard/spa && npm run build
git add ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
git commit -m "feat(schedule): live debounced search with empty state"
```

---

### Task 7: ScheduleTab — Run Now confirmation

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx`

**Step 1: Find Run Now handler**

Look for the handler that POSTs to trigger an immediate run (likely `/api/schedule/<id>/run` or similar `onClick` on a Run button).

**Step 2: Wrap with confirmation**

```jsx
const handleRunNow = async (job) => {
  if (job.estimated_duration > 300) {
    const ok = window.confirm(
      `Run "${job.name}" now? Estimated duration: ~${Math.round(job.estimated_duration / 60)}m`
    );
    if (!ok) return;
  }
  // existing run logic here
};
```

Update the button: `onClick={() => handleRunNow(job)}`

**Step 3: Build + commit**
```bash
cd ollama_queue/dashboard/spa && npm run build
git add ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
git commit -m "feat(schedule): confirm Run Now for long jobs (>5min estimated)"
```

---

### Task 8: ModelsTab — Live debounced catalog search

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx`

**Step 1: Read ModelsTab.jsx carefully**

Identify: where the search input is, where the search button is, what state drives filtering (`searchQuery`, `query`, etc.), and what the catalog data structure looks like.

**Step 2: Add useDebounce hook at top of file (same pattern as Task 6)**
```jsx
function useDebounce(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}
```

**Step 3: Convert search to live**

Find the search state (e.g. `const [query, setQuery] = useState('')`). Add:
```jsx
const debouncedQuery = useDebounce(query, 300);
```

Find where catalog items are filtered (e.g. `catalog.filter(m => m.name.includes(query))`). Replace `query` with `debouncedQuery`.

**Step 4: Remove search button**

Find `<button onClick={handleSearch}>` or similar. Remove it. The input's `onInput` (or `onChange`) sets query state — filtering happens automatically via debounce.

Ensure the input uses `onInput` not `onChange` for Preact:
```jsx
<input
  type="text"
  value={query}
  onInput={(e) => setQuery(e.target.value)}
  placeholder="Search catalog…"
  class="t-input"
  style="..."
/>
```

**Step 5: Add empty state for catalog search**
```jsx
{debouncedQuery && filteredCatalog.length === 0 && (
  <div style="text-align: center; padding: 24px; color: var(--text-tertiary); font-size: var(--type-body);">
    No models match "{debouncedQuery}"
    <br />
    <button
      class="t-btn t-btn-secondary"
      style="margin-top: 8px; padding: 4px 12px; font-size: var(--type-label);"
      onClick={() => setQuery('')}
    >Clear search</button>
  </div>
)}
```

**Step 6: Build + commit**
```bash
cd ollama_queue/dashboard/spa && npm run build
git add ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx
git commit -m "feat(models): live debounced catalog search, remove button, empty state"
```

---

### Task 9: ModelsTab — Sortable installed table

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx`

**Step 1: Add sort state**
```jsx
const [sortCol, setSortCol] = useState('size');
const [sortDir, setSortDir] = useState('desc');

const handleSort = (col) => {
  if (sortCol === col) {
    setSortDir(d => d === 'asc' ? 'desc' : 'asc');
  } else {
    setSortCol(col);
    setSortDir('desc');
  }
};
```

**Step 2: Sort the installed models array**

Find where installed models are rendered. Before the `.map()`:
```jsx
const sorted = [...installedModels].sort((a, b) => {
  let av = a[sortCol], bv = b[sortCol];
  if (typeof av === 'string') av = av.toLowerCase();
  if (typeof bv === 'string') bv = bv.toLowerCase();
  if (av < bv) return sortDir === 'asc' ? -1 : 1;
  if (av > bv) return sortDir === 'asc' ? 1 : -1;
  return 0;
});
```

Note: the size field from Ollama's `/api/tags` is `size` in bytes. Adjust field name to match actual data shape.

**Step 3: Add sort indicators to column headers**

Find the `<th>` for Name and Size. Replace:
```jsx
<th
  style="cursor: pointer; user-select: none; white-space: nowrap;"
  onClick={() => handleSort('name')}
>
  Name {sortCol === 'name' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
</th>
<th
  style="cursor: pointer; user-select: none; white-space: nowrap;"
  onClick={() => handleSort('size')}
>
  Size {sortCol === 'size' ? (sortDir === 'asc' ? '↑' : '↓') : ''}
</th>
```

**Step 4: Build + commit**
```bash
cd ollama_queue/dashboard/spa && npm run build
git add ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx
git commit -m "feat(models): click-to-sort installed table by name/size"
```

---

### Task 10: ModelsTab — VRAM badge on catalog cards

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx`

**Step 1: Inspect catalog data**

Read what fields are available on catalog model objects. Look for: `vram`, `size_gb`, `parameters`, `tags`, or similar. Check what the catalog API endpoint (`/api/models/catalog`) returns.

**Step 2: Add VRAM badge to catalog card**

Find where catalog cards are rendered. In the card JSX, add after the model name/description:
```jsx
{(model.vram_required_gb || model.size_gb) && (
  <span style={{
    fontSize: 'var(--type-micro)',
    color: 'var(--accent)',
    background: 'rgba(var(--accent-rgb), 0.12)',
    border: '1px solid rgba(var(--accent-rgb), 0.3)',
    borderRadius: 4,
    padding: '1px 5px',
    marginLeft: 4,
  }}>
    {model.vram_required_gb ? `${model.vram_required_gb}GB VRAM` : `~${model.size_gb}GB`}
  </span>
)}
{!model.vram_required_gb && !model.size_gb && (
  <span style="font-size: var(--type-micro); color: var(--text-tertiary); margin-left: 4px;">VRAM: —</span>
)}
```

Adjust field names based on what you found in Step 1.

**Step 3: Build + commit**
```bash
cd ollama_queue/dashboard/spa && npm run build
git add ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx
git commit -m "feat(models): VRAM badge on catalog cards"
```

---

### Task 11: ModelsTab — Pull progress elapsed time + remove Assign to Job

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx`

**Step 1: Remove "Assign to Job" select**

Find the `<select>` or control labeled "Assign to Job" (or similar). Delete it and any associated state (`assignJobId`, handler, etc.). This is dead UI — assignment belongs in the submit CLI.

**Step 2: Add elapsed time to pull progress**

Find where active pull progress is rendered. Each pull entry likely has a `started_at` timestamp and a `completed` percentage. Add elapsed:
```jsx
{pull.started_at && (
  <span style="font-size: var(--type-micro); color: var(--text-tertiary); margin-left: 8px;">
    {formatElapsed(Date.now() / 1000 - pull.started_at)}
  </span>
)}
```

Add helper at bottom of file:
```jsx
function formatElapsed(seconds) {
  if (!seconds || seconds < 0) return '';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}
```

If `started_at` isn't available on pull objects, check what fields the pulls API returns and use the available timestamp.

**Step 3: Build + commit**
```bash
cd ollama_queue/dashboard/spa && npm run build
git add ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx
git commit -m "feat(models): pull progress shows elapsed time, remove Assign to Job"
```

---

### Task 12: DLQTab — Retry All button

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/DLQTab.jsx`

**Step 1: Read DLQTab.jsx to find existing retry handler and item count**

Look for how individual retry is called (likely `fetch('/api/dlq/<id>/retry', { method: 'POST' })`). Note the DLQ items array name.

**Step 2: Add Retry All handler**
```jsx
const [retryingAll, setRetryingAll] = useState(false);

const handleRetryAll = async () => {
  if (!window.confirm(`Retry all ${items.length} failed jobs?`)) return;
  setRetryingAll(true);
  try {
    await Promise.all(items.map(item =>
      fetch(`/api/dlq/${item.id}/retry`, { method: 'POST' })
    ));
    // trigger refresh here (call existing refresh/refetch fn)
  } finally {
    setRetryingAll(false);
  }
};
```

**Step 3: Add Retry All button — show only when count > 3**
```jsx
{items.length > 3 && (
  <button
    class="t-btn t-btn-secondary px-3 py-1 text-sm"
    onClick={handleRetryAll}
    disabled={retryingAll}
    style={{ opacity: retryingAll ? 0.6 : 1 }}
  >
    {retryingAll ? '⟳ Retrying all…' : `Retry All (${items.length})`}
  </button>
)}
```

Place this button in the header row alongside the existing actions (above the item list).

**Step 4: Build + commit**
```bash
cd ollama_queue/dashboard/spa && npm run build
git add ollama_queue/dashboard/spa/src/pages/DLQTab.jsx
git commit -m "feat(dlq): retry all button when > 3 failed jobs"
```

---

### Task 13: Final verification

**Step 1: Run full test suite**
```bash
cd /home/justin/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q
```
Expected: 226 passed (no backend changes, so nothing should break)

**Step 2: Final build**
```bash
cd ollama_queue/dashboard/spa && npm run build
```
Expected: clean build, bundle < 200KB

**Step 3: Deploy**
```bash
systemctl --user restart ollama-queue.service
systemctl --user status ollama-queue.service
```
Expected: active (running)

**Step 4: Spot-check endpoints**
```bash
curl -s http://localhost:7683/api/schedule | python3 -m json.tool | head -20
curl -s http://localhost:7683/api/dlq | python3 -m json.tool | head -20
curl -s http://localhost:7683/api/models/installed | python3 -m json.tool | head -20
```

**Step 5: Final commit (if any cleanup needed)**
```bash
git add -p  # stage only what's changed
git commit -m "chore: max-ux final verification pass"
```
