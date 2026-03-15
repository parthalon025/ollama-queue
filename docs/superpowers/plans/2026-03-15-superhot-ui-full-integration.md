# superhot-ui Full Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire all remaining unused superhot-ui components into the queue dashboard, replace emoji nav icons with pixel-art PNGs, and eliminate all duplicate tab/nav data structures with a single `TAB_CONFIG` source of truth.

**Architecture:** Create `src/config/tabs.js` as the authoritative definition for all tab-aware components. Import pixel-art PNG icons via esbuild's `dataurl` loader. Replace `PageBanner` with `ShPageBanner` at all 9 call sites, replace `EvalPipelineSwimline` with `ShPipeline`, replace the uPlot `TimeChart` on the Perf tab with `ShTimeChart`, add `ShCollapsible` to Plan tag groups, and add `ShStatCard`/`ShStatsGrid` to the Now tab KPI section. All dynamic values come from existing signals — no new API calls.

**Tech Stack:** Preact 10, `@preact/signals`, esbuild 0.20, superhot-ui/preact components, PNG pixel-art icons (Pillow-generated, already exist at `src/assets/icons/*.png`)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `esbuild.config.mjs` | **Modify** | Add `'.png': 'dataurl'` to loader config |
| `src/config/tabs.js` | **Create** | TAB_CONFIG — single source of truth for all tab-aware components |
| `src/config/historyColumns.js` | **Create** | ShDataTable column definitions for History tab |
| `src/config/modelColumns.js` | **Create** | ShDataTable column definitions for Models tab |
| `src/components/Sidebar.jsx` | **Modify** | Remove local NAV_ITEMS, import TAB_CONFIG, replace emoji span with `<img>` |
| `src/components/BottomNav.jsx` | **Modify** | Remove local NAV_ITEMS, import TAB_CONFIG, replace emoji span with `<img>` |
| `src/app.jsx` | **Modify** | Derive ALL_TABS from TAB_CONFIG, update paletteItems to use TAB_CONFIG icons |
| `src/pages/Now.jsx` | **Modify** | Replace PageBanner, add ShStatCard/ShStatsGrid KPI section |
| `src/pages/Plan/index.jsx` | **Modify** | Replace PageBanner, wrap tag groups with ShCollapsible |
| `src/pages/History.jsx` | **Modify** | Replace PageBanner, add ShDataTable for history list |
| `src/pages/ModelsTab.jsx` | **Modify** | Replace PageBanner, add ShDataTable for model list |
| `src/pages/Performance.jsx` | **Modify** | Replace PageBanner, add ShTimeChart for health trends |
| `src/pages/Settings.jsx` | **Modify** | Replace PageBanner (ShCrtToggle already wired) |
| `src/pages/Consumers.jsx` | **Modify** | Replace PageBanner |
| `src/pages/BackendsTab.jsx` | **Modify** | Replace PageBanner |
| `src/views/EvalRuns.jsx` | **Modify** | Replace PageBanner, replace EvalPipelineSwimline with ShPipeline |

---

## Chunk 1: Foundation — esbuild loader + TAB_CONFIG + column files

### Task 1: Add PNG dataurl loader to esbuild

**Files:**
- Modify: `esbuild.config.mjs:31`

- [ ] **Step 1: Edit esbuild.config.mjs**

Change:
```js
loader: { '.jsx': 'jsx' },
```
To:
```js
loader: { '.jsx': 'jsx', '.png': 'dataurl' },
```

- [ ] **Step 2: Verify build still works**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | tail -10
```
Expected: no errors, `dist/bundle.js` rebuilt.

- [ ] **Step 3: Commit**

```bash
git add esbuild.config.mjs
git commit -m "chore: add PNG dataurl loader to esbuild for pixel-art icons"
```

---

### Task 2: Create TAB_CONFIG — single source of truth for all tab-aware components

**Files:**
- Create: `src/config/tabs.js`

The 9 PNG icons already exist at `src/assets/icons/*.png`. The TAB_CONFIG `icon` field imports them. The `namespace`, `page`, and `subtitle` fields drive `ShPageBanner`. The `id` field must match the existing `ALL_TABS` values in `app.jsx`.

- [ ] **Step 1: Create `src/config/tabs.js`**

```js
// What it shows: The single authoritative definition for all tab-aware components.
// Decision it drives: ALL_TABS (keyboard shortcuts), Sidebar nav, BottomNav, ShPageBanner
//   props, and ShCommandPalette nav items all derive from this one array — no more
//   three separate NAV_ITEMS constants that can get out of sync.

import nowIcon        from '../assets/icons/now.png';
import planIcon       from '../assets/icons/plan.png';
import historyIcon    from '../assets/icons/history.png';
import modelsIcon     from '../assets/icons/models.png';
import settingsIcon   from '../assets/icons/settings.png';
import evalIcon       from '../assets/icons/eval.png';
import consumersIcon  from '../assets/icons/consumers.png';
import perfIcon       from '../assets/icons/performance.png';
import backendsIcon   from '../assets/icons/backends.png';

export const TAB_CONFIG = [
    { id: 'now',         icon: nowIcon,       label: 'Now',       tooltip: "Live view — what's running right now",           namespace: 'QUEUE',  page: 'NOW',         subtitle: 'live job status' },
    { id: 'plan',        icon: planIcon,      label: 'Schedule',  tooltip: 'Recurring jobs and upcoming run times',           namespace: 'QUEUE',  page: 'PLAN',        subtitle: 'recurring schedule' },
    { id: 'history',     icon: historyIcon,   label: 'History',   tooltip: 'Completed and failed jobs',                       namespace: 'QUEUE',  page: 'HISTORY',     subtitle: 'completed jobs' },
    { id: 'models',      icon: modelsIcon,    label: 'Models',    tooltip: 'Installed AI models and downloads',               namespace: 'OLLAMA', page: 'MODELS',      subtitle: 'installed models' },
    { id: 'settings',    icon: settingsIcon,  label: 'Settings',  tooltip: 'Configure queue thresholds and defaults',         namespace: 'QUEUE',  page: 'SETTINGS',    subtitle: 'thresholds + defaults' },
    { id: 'eval',        icon: evalIcon,      label: 'Eval',      tooltip: 'Test and compare AI model configurations',        namespace: 'EVAL',   page: 'RUNS',        subtitle: 'prompt evaluation' },
    { id: 'consumers',   icon: consumersIcon, label: 'Consumers', tooltip: 'Detected Ollama consumers and routing',           namespace: 'SYSTEM', page: 'CONSUMERS',   subtitle: 'ollama-calling services' },
    { id: 'performance', icon: perfIcon,      label: 'Perf',      tooltip: 'Model performance stats and system health',       namespace: 'SYSTEM', page: 'PERFORMANCE', subtitle: 'throughput + load' },
    { id: 'backends',    icon: backendsIcon,  label: 'Backends',  tooltip: 'Multi-backend fleet management and routing intelligence', namespace: 'SYSTEM', page: 'BACKENDS', subtitle: 'inference hosts' },
];
```

- [ ] **Step 2: Verify build (icons embed as base64)**

```bash
npm run build 2>&1 | tail -10
```
Expected: no errors. `dist/bundle.js` grows slightly (base64 PNG data).

- [ ] **Step 3: Commit**

```bash
git add src/config/tabs.js
git commit -m "feat: create TAB_CONFIG — single source of truth for all tab-aware components"
```

---

### Task 3: Create ShDataTable column definition files

**Files:**
- Create: `src/config/historyColumns.js`
- Create: `src/config/modelColumns.js`

ShDataTable expects `columns = [{ key, label }]`. The `key` must match the field name in the row object from the existing signals.

History rows from `history.value` have fields: `id`, `source`, `model`, `status`, `duration_s`, `submitted_at`.
Model rows from `models.value` have fields: `name`, `size_bytes`, `parameter_size`, `quantization_level`.

- [ ] **Step 1: Create `src/config/historyColumns.js`**

```js
// What it shows: Column definitions for the History tab ShDataTable.
// Decision it drives: Which job fields are visible and in what order.
// Key names must match history.value row objects from stores/queue.js.
export const HISTORY_COLUMNS = [
    { key: 'id',           label: 'ID'       },
    { key: 'source',       label: 'Source'   },
    { key: 'model',        label: 'Model'    },
    { key: 'status',       label: 'Status'   },
    { key: 'duration_s',   label: 'Duration' },
    { key: 'submitted_at', label: 'Submitted'},
];
```

- [ ] **Step 2: Create `src/config/modelColumns.js`**

```js
// What it shows: Column definitions for the Models tab ShDataTable.
// Decision it drives: Which model fields are visible and in what order.
// Key names must match models.value row objects from stores/models.js.
export const MODEL_COLUMNS = [
    { key: 'name',               label: 'Model'         },
    { key: 'size_bytes',         label: 'Size'          },
    { key: 'parameter_size',     label: 'Parameters'    },
    { key: 'quantization_level', label: 'Quantization'  },
];
```

- [ ] **Step 3: Commit**

```bash
git add src/config/historyColumns.js src/config/modelColumns.js
git commit -m "feat: add ShDataTable column definitions for History and Models tabs"
```

---

## Chunk 2: Navigation — Sidebar, BottomNav, app.jsx

### Task 4: Update Sidebar to use TAB_CONFIG with pixel-art icons

**Files:**
- Modify: `src/components/Sidebar.jsx`

Current code defines `NAV_ITEMS` locally at lines 9-19. The icon renders at line 105 as `<span style="font-size: 1rem; flex-shrink: 0;">{item.icon}</span>`. Replace both.

- [ ] **Step 1: Replace NAV_ITEMS with TAB_CONFIG import**

At the top of `Sidebar.jsx`, remove the `NAV_ITEMS` constant and add the import:
```js
import { TAB_CONFIG } from '../config/tabs.js';
```

Replace every occurrence of `NAV_ITEMS` with `TAB_CONFIG`.

- [ ] **Step 2: Replace emoji span with pixel-art img**

Find:
```jsx
<span style="font-size: 1rem; flex-shrink: 0;">{item.icon}</span>
```
Replace with:
```jsx
<img
    src={item.icon}
    width={18}
    height={18}
    alt=""
    aria-hidden="true"
    style={{ imageRendering: 'pixelated', opacity: isActive ? 1.0 : 0.55, flexShrink: 0 }}
/>
```

- [ ] **Step 3: Build and verify visually**

```bash
npm run build 2>&1 | tail -5
```
Expected: build succeeds. Sidebar should show pixel-art icons at correct opacity.

- [ ] **Step 4: Commit**

```bash
git add src/components/Sidebar.jsx
git commit -m "feat(sidebar): replace NAV_ITEMS with TAB_CONFIG, emoji icons with pixel-art PNGs"
```

---

### Task 5: Update BottomNav to use TAB_CONFIG with pixel-art icons

**Files:**
- Modify: `src/components/BottomNav.jsx`

Current: `NAV_ITEMS` at lines 6-16, emoji rendered at line 83 as `<span style="font-size: 1.1rem;">{item.icon}</span>`.

- [ ] **Step 1: Replace NAV_ITEMS with TAB_CONFIG import**

Remove `NAV_ITEMS` constant. Add:
```js
import { TAB_CONFIG } from '../config/tabs.js';
```

Replace every `NAV_ITEMS` reference with `TAB_CONFIG`.

- [ ] **Step 2: Replace emoji span with pixel-art img**

Find:
```jsx
<span style="font-size: 1.1rem;">{item.icon}</span>
```
Replace with:
```jsx
<img
    src={item.icon}
    width={18}
    height={18}
    alt=""
    aria-hidden="true"
    style={{ imageRendering: 'pixelated', opacity: isActive ? 1.0 : 0.55 }}
/>
```

- [ ] **Step 3: Build and verify**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add src/components/BottomNav.jsx
git commit -m "feat(bottomnav): replace NAV_ITEMS with TAB_CONFIG, emoji icons with pixel-art PNGs"
```

---

### Task 6: Update app.jsx — derive ALL_TABS from TAB_CONFIG, update paletteItems

**Files:**
- Modify: `src/app.jsx`

Current: `ALL_TABS` at line 96 is a hardcoded array. `paletteItems` at lines 210-217 uses an inline emoji array `['●','◫','◷','⊞','⚙','⊡','⇄','⊘','⊟']`.

- [ ] **Step 1: Import TAB_CONFIG and derive ALL_TABS**

Add import at top of `app.jsx`:
```js
import { TAB_CONFIG } from './config/tabs.js';
```

Replace:
```js
const ALL_TABS = ['now', 'plan', 'history', 'models', 'settings', 'eval', 'consumers', 'performance', 'backends'];
```
With:
```js
const ALL_TABS = TAB_CONFIG.map(t => t.id);
```

- [ ] **Step 2: Update paletteItems to use TAB_CONFIG icons**

Replace the `...ALL_TABS.map(...)` block inside `paletteItems`:
```js
const paletteItems = [
    { id: 'action-submit', icon: '●', label: 'Submit job', group: 'Actions', action: handleSubmitRequest },
    { id: 'action-eval', icon: '⊡', label: 'Trigger eval run', group: 'Actions', action: () => handleNavigate('eval') },
    ...TAB_CONFIG.map((tab, i) => ({
        id: `nav-${tab.id}`,
        icon: tab.icon,
        label: `Go to ${tab.label}`,
        group: 'Navigate',
        shortcut: `${i + 1}`,
        action: () => handleNavigate(tab.id),
    })),
];
```

Note: The `icon` field for the two action items stays as emoji strings ('●', '⊡') — ShCommandPalette renders both strings and img src values the same way via its own icon slot.

- [ ] **Step 3: Build and verify Ctrl+K palette shows pixel-art icons**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add src/app.jsx
git commit -m "feat(app): derive ALL_TABS from TAB_CONFIG, update command palette to use PNG icons"
```

---

## Chunk 3: ShPageBanner across all 9 pages

### Task 7: Replace PageBanner with ShPageBanner on all pages

**Files:**
- Modify: `src/pages/Now.jsx`, `src/pages/Plan/index.jsx`, `src/pages/History.jsx`, `src/pages/ModelsTab.jsx`, `src/pages/Performance.jsx`, `src/pages/Settings.jsx`, `src/pages/Consumers.jsx`, `src/pages/BackendsTab.jsx`, `src/views/EvalRuns.jsx`

`ShPageBanner` accepts `{ namespace, page, subtitle }`. All values come from `TAB_CONFIG`. The current `PageBanner` accepts `{ title, subtitle }`.

**Pattern to apply to each file:**

1. Remove `import PageBanner from '...'`
2. Add `import { ShPageBanner } from 'superhot-ui/preact';` (if not already present) and `import { TAB_CONFIG } from '../config/tabs.js';` (adjust path depth)
3. Add a tab config lookup near the top of the component:
   ```js
   const _tab = TAB_CONFIG.find(t => t.id === 'TABID');
   ```
4. Replace `<PageBanner title="..." subtitle="..." />` with:
   ```jsx
   <ShPageBanner namespace={_tab.namespace} page={_tab.page} subtitle={_tab.subtitle} />
   ```

Tab IDs to lookup per page:
- `Now.jsx` → `'now'`
- `Plan/index.jsx` → `'plan'`
- `History.jsx` → `'history'`
- `ModelsTab.jsx` → `'models'`
- `Performance.jsx` → `'performance'`
- `Settings.jsx` → `'settings'`
- `Consumers.jsx` → `'consumers'`
- `BackendsTab.jsx` → `'backends'`
- `EvalRuns.jsx` → `'eval'` (check if PageBanner is present first)

**CRITICAL:** Never use `h` or `Fragment` as callback parameter names — esbuild JSX factory collision. Use descriptive names.

- [ ] **Step 1: Apply ShPageBanner to Now.jsx**

In `src/pages/Now.jsx`:
- Remove `import PageBanner from '../components/PageBanner.jsx';`
- Add to imports:
  ```js
  import { ShPageBanner } from 'superhot-ui/preact';
  import { TAB_CONFIG } from '../config/tabs.js';
  ```
- After existing variable declarations, add:
  ```js
  const _tab = TAB_CONFIG.find(t => t.id === 'now');
  ```
- Find and replace:
  ```jsx
  <PageBanner title="Now" subtitle="..." />
  ```
  With:
  ```jsx
  <ShPageBanner namespace={_tab.namespace} page={_tab.page} subtitle={_tab.subtitle} />
  ```

- [ ] **Step 2: Apply ShPageBanner to remaining 8 pages (one commit each)**

Repeat the same pattern for each file listed above. For `Plan/index.jsx` the import path for `tabs.js` is `../../config/tabs.js`.

- [ ] **Step 3: Check that PageBanner component is no longer imported anywhere**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
grep -r "import PageBanner" src/
```
Expected: no output (all replaced).

- [ ] **Step 4: Build**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/pages/ src/views/
git commit -m "feat: replace PageBanner with ShPageBanner across all 9 pages (TAB_CONFIG driven)"
```

---

## Chunk 4: ShStatCard + ShStatsGrid on Now tab

### Task 8: Add ShStatsGrid KPI section to Now tab

**Files:**
- Modify: `src/pages/Now.jsx`

Data sources from existing signals (already imported at top of Now.jsx):
- `status.value.kpis` — `{ jobs_24h, success_rate_24h, avg_duration_s }` (may be null)
- `status.value.daemon` — `{ state }` — map: `'running'` → `'active'`, `'paused'` → `'warning'`, `'offline'` → `'error'`
- `healthData.value[0]` — `{ ram_pct, vram_pct }` (latest health reading, may be null)
- `settings.value.concurrency` — max concurrent jobs integer

- [ ] **Step 1: Add ShStatCard/ShStatsGrid imports to Now.jsx**

```js
import { ShPageBanner, ShStatCard, ShStatsGrid } from 'superhot-ui/preact';
```

- [ ] **Step 2: Build stats array from signals**

Add after existing variable declarations near top of component:
```js
// What it shows: KPI summary cards — daemon state, queue depth, 24h job count,
//   RAM/VRAM utilization. Derived from live signals, not hardcoded.
// Decision it drives: Is the daemon healthy? Is RAM under pressure? How busy was the
//   queue today?
const daemonStatStatus =
    !st ? 'waiting' :
    st.daemon?.state === 'running' ? 'active' :
    st.daemon?.state?.startsWith('paused') ? 'warning' :
    st.daemon?.state === 'offline' ? 'error' : 'ok';

const kpiStats = [
    {
        label: 'Daemon',
        value: st?.daemon?.state ?? '—',
        status: daemonStatStatus,
    },
    {
        label: 'Queue Depth',
        value: q?.length ?? 0,
        status: (q?.length ?? 0) > 0 ? 'warning' : 'ok',
        detail: sett?.concurrency ? `max ${sett.concurrency}` : undefined,
    },
    {
        label: 'Jobs (24h)',
        value: kpis?.jobs_24h ?? '—',
        status: 'ok',
        detail: kpis?.success_rate_24h != null ? `${Math.round(kpis.success_rate_24h * 100)}% success` : undefined,
    },
    {
        label: 'RAM',
        value: latestHealth?.ram_pct != null ? `${Math.round(latestHealth.ram_pct)}%` : '—',
        status: latestHealth?.ram_pct > 85 ? 'error' : latestHealth?.ram_pct > 70 ? 'warning' : 'ok',
    },
];
// Only add VRAM card if we have VRAM data
if (latestHealth?.vram_pct != null) {
    kpiStats.push({
        label: 'VRAM',
        value: `${Math.round(latestHealth.vram_pct)}%`,
        status: latestHealth.vram_pct > 85 ? 'error' : latestHealth.vram_pct > 70 ? 'warning' : 'ok',
    });
}
```

- [ ] **Step 3: Render ShStatsGrid in JSX**

Find the existing KPI section (look for HeroCard or KPI cards in the JSX return). Add before or after the existing HeroCard/KPI section:
```jsx
{/* KPI stat cards — live queue health at a glance */}
<ShStatsGrid stats={kpiStats} />
```

Note: Keep the existing HeroCard and other Now tab content — this adds the ShStatsGrid alongside it, not replacing the full Now tab layout.

- [ ] **Step 4: Build and verify**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/pages/Now.jsx
git commit -m "feat(now): add ShStatsGrid KPI section — daemon state, queue depth, jobs 24h, RAM/VRAM"
```

---

## Chunk 5: ShCollapsible, ShDataTable, ShTimeChart, ShPipeline

### Task 9: Wrap Plan tab tag groups with ShCollapsible

**Files:**
- Modify: `src/pages/Plan/index.jsx`

The Plan tab already has `collapsedGroups` state + localStorage persistence for group collapse. ShCollapsible is **uncontrolled** — it manages open state internally via `defaultOpen`. The `localStorage` approach stays in place and is compatible: ShCollapsible's internal state is separate from the existing `collapsedGroups` localStorage logic.

Actually, re-read the constraint from the design doc: ShCollapsible is uncontrolled — no `open`/`onToggle` prop, and localStorage persistence is NOT in scope. This means we should use ShCollapsible with `defaultOpen={true}` and let it manage its own open/close state. The existing `collapsedGroups` localStorage logic in the Plan component can be left in place OR removed — since ShCollapsible replaces the UI, the old collapse state variables become unused.

**Approach:** Use ShCollapsible to replace the existing manual expand/collapse UI for each tag group. `defaultOpen={true}` means all groups start expanded. Pass `summary` as the job count string.

- [ ] **Step 1: Add ShCollapsible import to Plan/index.jsx**

```js
import { ShCollapsible } from 'superhot-ui/preact';
```

- [ ] **Step 2: Find the tag group rendering section in Plan/index.jsx**

Look for the `groupJobsByTag` usage and the section that renders collapsed/expanded groups. The pattern is: `collapsedGroups.includes(tag)` toggled by clicking a group header.

- [ ] **Step 3: Wrap each tag group with ShCollapsible**

Replace the existing manual collapse UI around each tag group body with:
```jsx
<ShCollapsible
    title={tag || 'Untagged'}
    defaultOpen={true}
    summary={`${tagJobs.length} job${tagJobs.length !== 1 ? 's' : ''}`}
>
    {/* existing job row content */}
</ShCollapsible>
```

Note: `title` = the tag name, `summary` = job count (shown when collapsed), `defaultOpen={true}` = starts open.

- [ ] **Step 4: Build and verify**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/pages/Plan/index.jsx
git commit -m "feat(plan): wrap tag groups with ShCollapsible — uncontrolled, defaultOpen=true"
```

---

### Task 10: Add ShDataTable for History and Models tabs

**Files:**
- Modify: `src/pages/History.jsx`
- Modify: `src/pages/ModelsTab.jsx`

ShDataTable expects `{ columns, rows, label }`. Rows must be plain objects — the signal values are already arrays of plain objects.

**History.jsx:**
The existing `<HistoryList>` component renders the full job history. We're adding ShDataTable as an alternative compact view using the column definitions from `historyColumns.js`. Keep `HistoryList` — add ShDataTable below (or above) the existing content as a supplementary view.

**ModelsTab.jsx:**
The existing model table is a custom `<table>` element. Replace with ShDataTable.

- [ ] **Step 1: Add ShDataTable to History.jsx**

Add import:
```js
import { ShDataTable } from 'superhot-ui/preact';
import { HISTORY_COLUMNS } from '../config/historyColumns.js';
```

In the JSX return, add after the existing DLQ section or before/after HistoryList:
```jsx
{/* Compact tabular job history — searchable and sortable */}
<ShDataTable
    label="Job History"
    columns={HISTORY_COLUMNS}
    rows={hist || []}
/>
```

- [ ] **Step 2: Add ShDataTable to ModelsTab.jsx**

Add import:
```js
import { ShDataTable } from 'superhot-ui/preact';
import { MODEL_COLUMNS } from '../config/modelColumns.js';
```

Find the custom `<table>` for installed models. Add ShDataTable below or alongside:
```jsx
{/* Installed models — searchable, sortable */}
<ShDataTable
    label="Installed Models"
    columns={MODEL_COLUMNS}
    rows={models.value || []}
/>
```

Note: Keep the existing custom model table — ShDataTable provides a supplementary searchable view. The existing table has custom rendering (progress bars, pull buttons) that ShDataTable can't replicate.

- [ ] **Step 3: Build and verify**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add src/pages/History.jsx src/pages/ModelsTab.jsx
git commit -m "feat: add ShDataTable to History and Models tabs for searchable/sortable view"
```

---

### Task 11: Add ShTimeChart to Performance tab

**Files:**
- Modify: `src/pages/Performance.jsx`

`ShTimeChart` expects `data = [{t: unixSeconds, v: number}]`. The existing `healthData.value` is an array of health log entries with `{ timestamp, ram_pct, vram_pct, load_avg }`. A conversion helper maps the health log format to ShTimeChart's format.

The existing `TimeChart` component (uPlot-based) in `History.jsx` uses duration data with format `{ timestamps: [], values: [] }`. For the Performance tab, we're adding a health trend chart using `healthData` — different data source, different format.

- [ ] **Step 1: Add ShTimeChart import to Performance.jsx**

```js
import { ShTimeChart } from 'superhot-ui/preact';
```

Also add import for `healthData` and `cpuCount` from stores (check if already imported):
```js
import { healthData, cpuCount } from '../stores';
```

- [ ] **Step 2: Add conversion helper and chart data**

Near the top of the `Performance` component function (after existing variable declarations):
```js
// What it shows: RAM usage trend over the last 24h from the health log.
// Decision it drives: Is RAM pressure increasing over time? Should a job be
//   deferred or concurrency reduced to avoid an OOM condition?
const ramChartData = (healthData.value || [])
    .filter(entry => entry.timestamp != null && entry.ram_pct != null)
    .map(entry => ({ t: entry.timestamp, v: entry.ram_pct }))
    .reverse(); // healthData is newest-first; ShTimeChart expects oldest-first
```

- [ ] **Step 3: Render ShTimeChart in Performance JSX**

Find the existing `<SystemHealth />` render in Performance.jsx. Add after it:
```jsx
{/* RAM usage trend — last 24h from health log */}
{ramChartData.length > 0 && (
    <ShTimeChart
        data={ramChartData}
        label="RAM %"
        color="var(--sh-phosphor)"
    />
)}
```

- [ ] **Step 4: Build and verify**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/pages/Performance.jsx
git commit -m "feat(performance): add ShTimeChart for RAM usage trend from health log"
```

---

### Task 12: Replace EvalPipelineSwimline with ShPipeline in EvalRuns

**Files:**
- Modify: `src/views/EvalRuns.jsx`

`ShPipeline` accepts `{ nodes, edges, compact, ariaLabel }`.
- `nodes`: `[{ id, label, status }]` — status is `'active'|'done'|'pending'|'error'`
- `edges`: `[{ from, to }]` — directed graph edges

The existing `EvalPipelineSwimline` already defines `STAGES` and `STAGE_ORDER` constants. We need to:
1. Build `nodes` from `STAGES` with status derived from `evalActiveRun.value.progress`
2. Build `edges` as sequential pairs from `STAGE_ORDER`
3. Use `normalizeStage()` logic to determine the current pipeline position

`evalActiveRun` comes from `stores/eval.js`.

- [ ] **Step 1: Read EvalRuns.jsx to find where EvalPipelineSwimline is rendered**

```bash
grep -n "EvalPipelineSwimline\|activeRun\|pipeline" src/views/EvalRuns.jsx | head -20
```

- [ ] **Step 2: Add ShPipeline import to EvalRuns.jsx**

```js
import { ShPipeline } from 'superhot-ui/preact';
```

- [ ] **Step 3: Build pipeline nodes and edges from existing constants**

The `STAGES` and `STAGE_ORDER` constants are defined in `EvalPipelineSwimline.jsx`. Import them:
```js
import { STAGES, STAGE_ORDER } from '../components/eval/EvalPipelineSwimline.jsx';
```

Wait — check if these are exported. If not, duplicate the minimal versions inline in EvalRuns.jsx:
```js
const PIPELINE_STAGES = [
    { id: 'queued',     label: 'Waiting' },
    { id: 'generating', label: 'Writing' },
    { id: 'judging',    label: 'Scoring' },
    { id: 'done',       label: 'Done' },
];
const PIPELINE_ORDER = ['queued', 'generating', 'judging', 'done'];
```

- [ ] **Step 4: Build ShPipeline props from evalActiveRun**

Where EvalPipelineSwimline is currently rendered, replace with a helper:
```jsx
// Build pipeline nodes/edges from active run progress
const _activeRun = evalActiveRun.value;
const _pipelineNodes = PIPELINE_STAGES.map(stg => {
    let nodeStatus = 'pending';
    if (_activeRun) {
        const currIdx = PIPELINE_ORDER.indexOf(
            _activeRun.status === 'completed' ? 'done' :
            _activeRun.stage === 'judging' ? 'judging' :
            _activeRun.stage === 'generating' ? 'generating' :
            _activeRun.status === 'queued' ? 'queued' : 'queued'
        );
        const nodeIdx = PIPELINE_ORDER.indexOf(stg.id);
        nodeStatus = nodeIdx < currIdx ? 'done' : nodeIdx === currIdx ? 'active' : 'pending';
    }
    return { id: stg.id, label: stg.label, status: nodeStatus };
});
const _pipelineEdges = PIPELINE_ORDER.slice(0, -1).map((id, i) => ({
    from: id,
    to: PIPELINE_ORDER[i + 1],
}));
```

- [ ] **Step 5: Replace EvalPipelineSwimline with ShPipeline**

Find:
```jsx
<EvalPipelineSwimline stage={...} status={...} ... />
```
Replace with:
```jsx
<ShPipeline
    nodes={_pipelineNodes}
    edges={_pipelineEdges}
    ariaLabel="Eval pipeline progress"
    compact={true}
/>
```

- [ ] **Step 6: Build and verify**

```bash
npm run build 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add src/views/EvalRuns.jsx
git commit -m "feat(eval): replace EvalPipelineSwimline with ShPipeline DAG component"
```

---

## Chunk 6: Final build, visual verification, and branch finish

### Task 13: Full production build + visual smoke test

- [ ] **Step 1: Run full production build**

```bash
cd /home/justin/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1
```
Expected: no errors. `dist/bundle.js` and `dist/bundle.css` written. `dist/index.html` updated with cache hash.

- [ ] **Step 2: Restart ollama-queue service to pick up new static files**

```bash
systemctl --user restart ollama-queue.service
systemctl --user status ollama-queue.service | head -5
```
Expected: `active (running)`.

- [ ] **Step 3: Verify pixel-art icons appear in Sidebar and BottomNav**

Open browser at `https://justin-linux.tail828051.ts.net/queue/ui/`. Check:
- Sidebar shows 18px pixel-art PNG icons (not emoji)
- Active tab icon has full opacity; inactive tabs at ~55% opacity
- BottomNav (mobile) shows same icons

- [ ] **Step 4: Verify ShPageBanner on each tab**

Navigate to each of the 9 tabs. Confirm ShPageBanner renders with correct NAMESPACE / PAGE / subtitle for each.

- [ ] **Step 5: Verify ShPipeline on Eval tab**

Navigate to Eval tab → start or view a run. Confirm the pipeline shows 4 nodes (Waiting → Writing → Scoring → Done) with proper active/done/pending states.

- [ ] **Step 6: Verify ShStatsGrid on Now tab**

Navigate to Now tab. Confirm stat cards show daemon state, queue depth, 24h jobs, RAM % (and VRAM % if GPU present).

- [ ] **Step 7: Verify ShCollapsible on Plan tab**

Navigate to Plan tab. Confirm tag groups are collapsible via ShCollapsible. Click to collapse/expand.

- [ ] **Step 8: Verify ShDataTable on History and Models tabs**

Navigate to History — confirm searchable table is present.
Navigate to Models — confirm searchable table is present.

- [ ] **Step 9: Verify ShTimeChart on Performance tab**

Navigate to Performance tab. If health log has data, confirm RAM trend chart renders.

- [ ] **Step 10: Verify Ctrl+K command palette shows PNG icons**

Press Ctrl+K. Confirm palette items show pixel-art icons for navigation items.

- [ ] **Step 11: Final commit if any last-minute fixes needed**

```bash
git add -p  # stage only your changes
git commit -m "fix: address smoke test issues from final visual verification"
```

---

## Constraints (CRITICAL — read before touching any file)

1. **Never use `h` or `Fragment` as callback parameter names** — esbuild JSX factory collision. `.map(item => ...)` not `.map(h => ...)`.
2. **ShCollapsible is uncontrolled** — no `open`/`onToggle` prop. Only `defaultOpen` is accepted.
3. **ShNav excluded** — path-based routing incompatible with signal-based `currentTab`. Sidebar and BottomNav stay custom.
4. **All dynamic values from signals** — zero hardcoded counts, thresholds, or labels outside `TAB_CONFIG`.
5. **UI Layman Comments required** — every modified component must have "What it shows / Decision it drives" comment block.
6. **Import paths**: `superhot-ui/preact` (not `superhot-ui`). Components are named exports: `import { ShPageBanner, ShStatCard } from 'superhot-ui/preact';`
7. **PNG icons already generated** — do NOT re-run `gen_icons.py` unless an icon needs changing. The 9 PNG files at `src/assets/icons/*.png` are committed.

## Reference

- superhot-ui components: `src/node_modules/superhot-ui/preact/` (local install)
- Design doc: `docs/plans/2026-03-15-superhot-ui-full-integration-design.md`
- Branch: `feature/superhot-ui-full-integration`
