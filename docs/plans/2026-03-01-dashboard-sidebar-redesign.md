# Dashboard Sidebar Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the 5-tab centered layout with a left sidebar + 5-view layout (Now / Plan / History / Models / Settings) that surfaces running job, failures, schedule, and system health without collapsible sections.

**Architecture:** `app.jsx` gets a `Sidebar` + `BottomNav` shell and a new set of route IDs ('now' | 'plan' | 'history' | 'models' | 'settings'). Five page components replace the old four tabs — `Now.jsx` (new 2-column command center), `Plan.jsx` (rename of `ScheduleTab.jsx`), `History.jsx` (new, combines DLQ + trends + history), `ModelsTab.jsx` (unchanged), `Settings.jsx` (unchanged). No backend changes.

**Tech Stack:** Preact 10, @preact/signals, CSS custom properties (design tokens), Tailwind v4 utilities, existing components (`CurrentJob`, `QueueList`, `ResourceGauges`, `HeroCard`, `GanttChart`, `ActivityHeatmap`, `HistoryList`, `TimeChart`)

---

## Codebase Context

### Key file locations
```
ollama_queue/dashboard/spa/src/
  app.jsx                         ← main shell (replace tab nav with sidebar)
  store.js                        ← signals; currentTab default = 'dashboard' → change to 'now'
  index.css                       ← CSS tokens; add sidebar layout utilities here
  components/
    CurrentJob.jsx                ← already renders t-frame wrapper — do NOT double-wrap
    QueueList.jsx                 ← already renders t-frame wrapper — do NOT double-wrap
    ResourceGauges.jsx            ← compact gauge bars, accepts {ram, vram, load, swap, settings}
    HeroCard.jsx                  ← KPI card
    GanttChart.jsx                ← Gantt chart component
    ActivityHeatmap.jsx           ← heatmap component
    HistoryList.jsx               ← history list component
    TimeChart.jsx                 ← uPlot chart wrapper
  pages/
    Dashboard.jsx                 ← OLD command center; copy helper functions to Now.jsx, then DELETE
    ScheduleTab.jsx               ← becomes Plan.jsx; copy + rename export, then DELETE
    DLQTab.jsx                    ← DLQ content moves into History.jsx; then DELETE
    ModelsTab.jsx                 ← unchanged; stays
    Settings.jsx                  ← unchanged; stays
```

### Key signals from store.js
```js
status          // { daemon, kpis, current_job, queue }
queue           // pending job array
history         // completed job array
healthData      // health_log rows DESC (first = newest)
durationData    // duration_history rows
heatmapData     // heatmap data
settings        // settings object
scheduleJobs    // recurring jobs array
dlqEntries      // DLQ entries array
dlqCount        // number of DLQ entries
models          // model list
connectionStatus // 'ok' | 'disconnected'
currentTab      // active view ID signal
```

### JSX rule (critical)
Never name `.map()` callbacks `h` — esbuild injects `h` as the JSX factory. Use descriptive names: `job`, `entry`, `item`, `gauge`, etc.

---

## Tasks

### Task 1: Add sidebar CSS layout utilities

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/index.css` (append at the end)

**Step 1: Append CSS to the end of index.css**

```css
/* ── Sidebar layout system ── */

:root {
  --sidebar-width: 200px;
  --sidebar-width-sm: 64px;
}

/* Root flex container */
.layout-root {
  display: flex;
  min-height: 100vh;
}

/* Sidebar: fixed position, full height */
.layout-sidebar {
  width: var(--sidebar-width);
  flex-shrink: 0;
  position: fixed;
  top: 0;
  left: 0;
  height: 100vh;
  overflow-y: auto;
  background: var(--bg-surface);
  border-right: 1px solid var(--border-subtle);
  z-index: 50;
  display: flex;
  flex-direction: column;
}

/* Main content area: offset by sidebar width */
.layout-main {
  flex: 1;
  min-width: 0;
  margin-left: var(--sidebar-width);
  padding: 1.5rem;
  overflow-x: hidden;
}

/* Collapsed sidebar: icon-only (768px–1023px) */
@media (min-width: 768px) and (max-width: 1023px) {
  .layout-sidebar { width: var(--sidebar-width-sm); }
  .layout-main { margin-left: var(--sidebar-width-sm); }
  .sidebar-label { display: none; }
}

/* Mobile: hide sidebar, show bottom nav, restore full-width main */
@media (max-width: 767px) {
  .layout-sidebar { display: none; }
  .layout-main {
    margin-left: 0;
    padding: 1rem;
    padding-bottom: 5rem; /* clear bottom nav height */
  }
  .mobile-bottom-nav { display: flex !important; }
}

/* Now view: 2-column → 1-column on mobile */
.now-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);
  gap: 1rem;
  align-items: start;
}
@media (max-width: 767px) {
  .now-grid { grid-template-columns: 1fr; }
}

/* History view: 2-column top → 1-column on mobile */
.history-top-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 1rem;
}
@media (max-width: 767px) {
  .history-top-grid { grid-template-columns: 1fr; }
}
```

**Step 2: Verify build**

```bash
cd ollama_queue/dashboard/spa
npm run build
```
Expected: build succeeds, no errors.

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/index.css
git commit -m "feat(sidebar): add sidebar layout CSS utilities"
```

---

### Task 2: Build Sidebar.jsx

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/Sidebar.jsx`

**Step 1: Create the file**

```jsx
import { h } from 'preact';

// NOTE: callback params use descriptive names (item, etc.) — never 'h' (shadows JSX factory)
const NAV_ITEMS = [
    { id: 'now',      icon: '●', label: 'Now' },
    { id: 'plan',     icon: '◫', label: 'Plan' },
    { id: 'history',  icon: '◷', label: 'History' },
    { id: 'models',   icon: '⊞', label: 'Models' },
    { id: 'settings', icon: '⚙', label: 'Settings' },
];

export default function Sidebar({ active, onNavigate, daemonState, dlqCount }) {
    const state = daemonState?.state || 'idle';
    const isRunning = state === 'running';
    const isPaused  = state.startsWith('paused');

    const chipColor = isRunning ? 'var(--status-healthy)'
        : isPaused ? 'var(--status-warning)'
        : 'var(--text-tertiary)';

    const chipDot  = isRunning ? '▶' : isPaused ? '⏸' : '○';
    const chipText = isRunning
        ? (daemonState?.current_job_source || 'running')
        : isPaused ? 'paused' : 'idle';

    return (
        <aside class="layout-sidebar">
            {/* Daemon status chip */}
            <div style={{
                padding: '1rem 0.75rem 0.75rem',
                borderBottom: '1px solid var(--border-subtle)',
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                color: chipColor,
                fontFamily: 'var(--font-mono)',
                fontSize: 'var(--type-label)',
                fontWeight: 600,
                overflow: 'hidden',
                flexShrink: 0,
            }}>
                <span style="flex-shrink: 0;">{chipDot}</span>
                <span class="sidebar-label" style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    {chipText}
                </span>
            </div>

            {/* Nav items */}
            <nav style="flex: 1; padding: 0.5rem 0;">
                {NAV_ITEMS.map(item => {
                    const isActive = active === item.id;
                    const badge = item.id === 'history' && dlqCount > 0 ? dlqCount : null;
                    return (
                        <button
                            key={item.id}
                            onClick={() => onNavigate(item.id)}
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: '0.75rem',
                                width: '100%',
                                padding: '0.625rem 0.75rem',
                                textAlign: 'left',
                                background: isActive ? 'var(--accent-glow)' : 'transparent',
                                color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
                                borderLeft: isActive ? '3px solid var(--accent)' : '3px solid transparent',
                                fontSize: 'var(--type-body)',
                                fontWeight: isActive ? 600 : 400,
                                cursor: 'pointer',
                                border: 'none',
                                outline: 'none',
                                transition: 'background 0.15s ease, color 0.15s ease',
                                position: 'relative',
                                whiteSpace: 'nowrap',
                            }}
                        >
                            <span style="font-size: 1rem; flex-shrink: 0;">{item.icon}</span>
                            <span class="sidebar-label">{item.label}</span>
                            {badge && (
                                <span style={{
                                    marginLeft: 'auto',
                                    background: 'var(--status-error)',
                                    color: '#fff',
                                    fontSize: 'var(--type-micro)',
                                    fontFamily: 'var(--font-mono)',
                                    padding: '1px 5px',
                                    borderRadius: 10,
                                    fontWeight: 700,
                                    flexShrink: 0,
                                }}>
                                    {badge}
                                </span>
                            )}
                        </button>
                    );
                })}
            </nav>
        </aside>
    );
}
```

**Step 2: Verify build**

```bash
npm run build
```
Expected: no errors.

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/Sidebar.jsx
git commit -m "feat(sidebar): add Sidebar nav component with daemon status chip"
```

---

### Task 3: Build BottomNav.jsx

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/BottomNav.jsx`

**Step 1: Create the file**

```jsx
import { h } from 'preact';

// NOTE: callback params use descriptive names — never 'h'
const NAV_ITEMS = [
    { id: 'now',      icon: '●', label: 'Now' },
    { id: 'plan',     icon: '◫', label: 'Plan' },
    { id: 'history',  icon: '◷', label: 'History' },
    { id: 'models',   icon: '⊞', label: 'Models' },
    { id: 'settings', icon: '⚙', label: 'Settings' },
];

export default function BottomNav({ active, onNavigate, dlqCount }) {
    return (
        <nav
            class="mobile-bottom-nav"
            style={{
                display: 'none', /* shown via CSS on mobile */
                position: 'fixed',
                bottom: 0, left: 0, right: 0,
                background: 'var(--bg-surface)',
                borderTop: '1px solid var(--border-subtle)',
                zIndex: 50,
            }}
        >
            {NAV_ITEMS.map(item => {
                const isActive = active === item.id;
                const badge = item.id === 'history' && dlqCount > 0 ? dlqCount : null;
                return (
                    <button
                        key={item.id}
                        onClick={() => onNavigate(item.id)}
                        style={{
                            flex: 1,
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            gap: '2px',
                            padding: '0.5rem 0.25rem',
                            color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
                            fontSize: 'var(--type-micro)',
                            cursor: 'pointer',
                            background: 'transparent',
                            border: 'none',
                            outline: 'none',
                            position: 'relative',
                        }}
                    >
                        <span style="font-size: 1.1rem;">{item.icon}</span>
                        <span>{item.label}</span>
                        {badge && (
                            <span style={{
                                position: 'absolute',
                                top: 4, right: '18%',
                                background: 'var(--status-error)',
                                color: '#fff',
                                fontSize: '0.5rem',
                                padding: '1px 3px',
                                borderRadius: 8,
                                fontFamily: 'var(--font-mono)',
                                fontWeight: 700,
                            }}>
                                {badge}
                            </span>
                        )}
                    </button>
                );
            })}
        </nav>
    );
}
```

**Step 2: Verify build**

```bash
npm run build
```

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/BottomNav.jsx
git commit -m "feat(sidebar): add BottomNav for mobile"
```

---

### Task 4: Refactor app.jsx

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/app.jsx` (full rewrite)
- Modify: `ollama_queue/dashboard/spa/src/store.js` (one-line change)

**Step 1: Update store.js — change default tab ID**

In `store.js` line 10, change:
```js
export const currentTab = signal('dashboard');
```
to:
```js
export const currentTab = signal('now');
```

**Step 2: Rewrite app.jsx**

```jsx
import { h } from 'preact';
import { useEffect } from 'preact/hooks';
import {
    currentTab, dlqCount, fetchModels, fetchSchedule,
    startPolling, stopPolling, status,
} from './store';
import Sidebar from './components/Sidebar.jsx';
import BottomNav from './components/BottomNav.jsx';
import Now from './pages/Now.jsx';
import Plan from './pages/Plan.jsx';
import History from './pages/History.jsx';
import ModelsTab from './pages/ModelsTab.jsx';
import Settings from './pages/Settings.jsx';

export function App() {
    useEffect(() => {
        startPolling();
        return () => stopPolling();
    }, []);

    function handleNavigate(viewId) {
        currentTab.value = viewId;
        if (viewId === 'models') fetchModels();
        if (viewId === 'plan') fetchSchedule();
    }

    function renderView() {
        switch (currentTab.value) {
            case 'plan':     return <Plan />;
            case 'history':  return <History />;
            case 'models':   return <ModelsTab />;
            case 'settings': return <Settings />;
            default:         return <Now />;
        }
    }

    const daemonState = status.value?.daemon ?? null;

    return (
        <div class="layout-root" style="background: var(--bg-base); color: var(--text-primary);">
            <Sidebar
                active={currentTab.value}
                onNavigate={handleNavigate}
                daemonState={daemonState}
                dlqCount={dlqCount.value}
            />
            <main class="layout-main animate-page-enter">
                {renderView()}
            </main>
            <BottomNav
                active={currentTab.value}
                onNavigate={handleNavigate}
                dlqCount={dlqCount.value}
            />
        </div>
    );
}
```

**Step 3: Verify build**

```bash
npm run build
```

Note: build will fail if `Now.jsx`, `Plan.jsx`, or `History.jsx` don't exist yet. If it fails, that's expected — the import errors will guide you to the next tasks.

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/app.jsx ollama_queue/dashboard/spa/src/store.js
git commit -m "feat(sidebar): refactor app.jsx to sidebar layout, update route IDs"
```

---

### Task 5: Build Now.jsx (command center)

**Files:**
- Create: `ollama_queue/dashboard/spa/src/pages/Now.jsx`

The helper functions at the bottom of this file come from `Dashboard.jsx` lines 178–338. Copy them verbatim — do not modify.

**Step 1: Note what NOT to do**

`CurrentJob` renders its own `t-frame` with `data-label="Current"`. `QueueList` renders its own `t-frame` with `data-label="Queue"`. Do NOT wrap them in another `t-frame` — that would double-nest the frames.

**Step 2: Create Now.jsx**

```jsx
import { h } from 'preact';
import {
    status, queue, history, healthData, durationData, settings,
    dlqEntries, dlqCount, connectionStatus, currentTab,
} from '../store';
import CurrentJob from '../components/CurrentJob.jsx';
import QueueList from '../components/QueueList.jsx';
import HeroCard from '../components/HeroCard.jsx';
import ResourceGauges from '../components/ResourceGauges.jsx';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

export default function Now() {
    const st = status.value;
    const q = queue.value;
    const hist = history.value;
    const health = healthData.value;
    const durations = durationData.value;
    const sett = settings.value;
    const dlqCnt = dlqCount.value;

    const daemon = st?.daemon ?? null;
    const kpis = st?.kpis ?? null;
    const currentJob = st?.current_job ?? null;
    const latestHealth = health?.length > 0 ? health[0] : null;

    // Count failures in last 24h for alert strip
    const oneDayAgo = Date.now() / 1000 - 86400;
    const recentFailures = (hist || []).filter(
        job => (job.status === 'failed' || job.status === 'killed') && (job.completed_at ?? 0) >= oneDayAgo
    ).length;
    const showAlerts = dlqCnt > 0 || recentFailures > 0;

    return (
        <div class="flex flex-col gap-4 animate-page-enter">
            {/* Disconnected banner */}
            {connectionStatus.value === 'disconnected' && (
                <div style={{
                    background: '#1c1917', color: '#f97316',
                    padding: '0.5rem 1rem', borderRadius: 4,
                    border: '1px solid rgba(249,115,22,0.4)',
                }}>
                    ⚠ Disconnected — retrying...
                </div>
            )}

            {/* 2-column layout: left = operations, right = health + KPIs */}
            <div class="now-grid">

                {/* LEFT: running job + queue */}
                <div class="flex flex-col gap-4">
                    {/* CurrentJob renders its own t-frame — no wrapper needed */}
                    <CurrentJob
                        daemon={daemon}
                        currentJob={currentJob}
                        latestHealth={latestHealth}
                        settings={sett}
                    />
                    {/* QueueList renders its own t-frame — no wrapper needed */}
                    <QueueList jobs={q} currentJob={currentJob} />
                </div>

                {/* RIGHT: alerts + resource gauges + KPI cards */}
                <div class="flex flex-col gap-4">
                    {/* Alert strip — only when something needs attention */}
                    {showAlerts && (
                        <div style={{
                            display: 'flex',
                            flexWrap: 'wrap',
                            gap: '0.5rem',
                            padding: '0.625rem 0.75rem',
                            background: 'var(--status-error-glow)',
                            border: '1px solid var(--status-error)',
                            borderRadius: 'var(--radius)',
                            alignItems: 'center',
                        }}>
                            <span style={{
                                fontSize: 'var(--type-label)',
                                color: 'var(--status-error)',
                                fontWeight: 700,
                                fontFamily: 'var(--font-mono)',
                                flexShrink: 0,
                            }}>
                                ⚠ ALERTS
                            </span>
                            {dlqCnt > 0 && (
                                <button
                                    onClick={() => { currentTab.value = 'history'; }}
                                    style={{
                                        fontSize: 'var(--type-label)',
                                        color: 'var(--status-error)',
                                        background: 'transparent',
                                        border: 'none',
                                        cursor: 'pointer',
                                        textDecoration: 'underline',
                                        fontFamily: 'var(--font-mono)',
                                        padding: 0,
                                    }}
                                >
                                    {dlqCnt} DLQ {dlqCnt === 1 ? 'entry' : 'entries'}
                                </button>
                            )}
                            {recentFailures > 0 && (
                                <button
                                    onClick={() => { currentTab.value = 'history'; }}
                                    style={{
                                        fontSize: 'var(--type-label)',
                                        color: 'var(--status-error)',
                                        background: 'transparent',
                                        border: 'none',
                                        cursor: 'pointer',
                                        textDecoration: 'underline',
                                        fontFamily: 'var(--font-mono)',
                                        padding: 0,
                                    }}
                                >
                                    {recentFailures} failure{recentFailures > 1 ? 's' : ''} today
                                </button>
                            )}
                        </div>
                    )}

                    {/* Resource gauges */}
                    {latestHealth && (
                        <div class="t-frame" data-label="Resources">
                            <ResourceGauges
                                ram={latestHealth.ram_pct}
                                vram={latestHealth.vram_pct}
                                load={latestHealth.load_avg}
                                swap={latestHealth.swap_pct}
                                settings={sett}
                            />
                        </div>
                    )}

                    {/* KPI cards — 2×2 grid */}
                    <div class="grid grid-cols-2 gap-3">
                        <HeroCard
                            label="Jobs / 24h"
                            value={kpis ? kpis.jobs_24h : '--'}
                            sparkData={buildHealthSparkline(health, 'ram_pct')}
                            sparkColor="var(--accent)"
                            delta={kpis ? buildJobsDelta(kpis, hist) : null}
                        />
                        <HeroCard
                            label="Avg Wait"
                            value={kpis ? formatWaitReadable(kpis.avg_wait_seconds) : '--'}
                            sparkData={buildDurationSparkline(durations)}
                            sparkColor="var(--accent)"
                            delta={kpis ? buildWaitDelta(kpis.avg_wait_seconds) : null}
                        />
                        <HeroCard
                            label="Pause Time"
                            value={kpis ? `${kpis.pause_minutes_24h}` : '--'}
                            unit="min"
                            warning={kpis && kpis.pause_minutes_24h > 30}
                            sparkData={buildHealthSparkline(health, 'ram_pct')}
                            sparkColor="var(--status-warning)"
                            delta={kpis ? buildPauseDelta(kpis.pause_minutes_24h) : null}
                        />
                        <HeroCard
                            label="Success Rate"
                            value={kpis ? `${Math.round(kpis.success_rate_7d * 100)}` : '--'}
                            unit="%"
                            warning={kpis && kpis.success_rate_7d < 0.9}
                            delta={kpis ? buildSuccessRateDelta(kpis, hist) : null}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
}

// ── Data helpers (copied verbatim from Dashboard.jsx) ────────────────────────

function buildHealthSeries(rows, field) {
    if (!rows || rows.length === 0) return [[], []];
    const sorted = [...rows].reverse();
    const ts = sorted.map((r) => r.timestamp);
    const vals = sorted.map((r) => r[field] ?? null);
    return [ts, vals];
}

function buildDurationSparkline(rows) {
    if (!rows || rows.length < 2) return null;
    const sorted = [...rows].sort((a, b) => a.recorded_at - b.recorded_at).slice(-24);
    return [sorted.map((r) => r.recorded_at), sorted.map((r) => r.duration)];
}

function buildHealthSparkline(rows, field) {
    if (!rows || rows.length < 2) return null;
    const sorted = [...rows].reverse();
    return [sorted.map((r) => r.timestamp), sorted.map((r) => r[field] ?? null)];
}

function formatWaitReadable(seconds) {
    if (seconds === null || seconds <= 0) return '0s';
    const s = Math.round(seconds);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    const hr = Math.floor(m / 60);
    return `${hr}h ${m % 60}m`;
}

function buildJobsDelta(kpis, hist) {
    if (!kpis || kpis.jobs_24h === 0) return 'no jobs in the last 24h';
    const oneDayAgo = Date.now() / 1000 - 86400;
    const todayFailed = (hist || []).filter(
        (j) => (j.status === 'failed' || j.status === 'killed') && (j.completed_at ?? 0) >= oneDayAgo
    ).length;
    if (todayFailed === 0) return 'all completed successfully';
    const s = todayFailed === 1 ? '' : 's';
    return `${todayFailed} job${s} failed today`;
}

function buildWaitDelta(seconds) {
    if (seconds === null || seconds <= 0) return 'no wait data yet';
    if (seconds <= 30) return 'queue flowing smoothly';
    if (seconds <= 120) return 'light wait — normal range';
    if (seconds <= 300) return 'moderate backlog — check queue';
    return 'heavy wait — jobs are stacking up';
}

function buildPauseDelta(minutes) {
    if (!minutes || minutes <= 0) return 'no pauses — running clean';
    if (minutes <= 30) return 'some pauses — health thresholds triggered';
    return 'frequent pauses — lower thresholds in Settings';
}

function buildSuccessRateDelta(kpis, hist) {
    const ok = kpis.jobs_7d_ok ?? 0;
    const bad = kpis.jobs_7d_bad ?? 0;
    const total = ok + bad;
    if (total === 0) return 'no jobs run in the last 7 days';
    if (bad === 0) return 'everything is running clean';

    const sevenDaysAgo = Date.now() / 1000 - 7 * 86400;
    const recentFails = (hist || []).filter(
        (j) => (j.status === 'failed' || j.status === 'killed') && j.completed_at >= sevenDaysAgo
    );

    const timeouts = recentFails.filter((j) => j.outcome_reason && /timeout/i.test(j.outcome_reason));
    const stalls = recentFails.filter((j) => j.stall_detected_at);
    const crashes = recentFails.filter((j) => j.outcome_reason && /exit code [^0]|non.zero|crash|error/i.test(j.outcome_reason));

    const n = bad;
    const s = n === 1 ? '' : 's';

    if (timeouts.length > 0 && timeouts.length >= recentFails.length / 2)
        return `${n} job${s} ran past their time limit — raise Default Timeout in Settings`;
    if (stalls.length > 0 && stalls.length >= recentFails.length / 2)
        return `${n} job${s} appeared stuck and were killed — review Stall Detection in Settings`;
    if (crashes.length > 0 && crashes.length >= recentFails.length / 2)
        return `${n} job${s} crashed with an error — check History for the command output`;
    if (bad === 1) return '1 job failed — tap History to see what went wrong';
    return `${n} jobs failed this week — check History or DLQ for patterns`;
}
```

**Step 3: Verify build**

```bash
npm run build
```
Expected: no errors.

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat(now): add Now command center with 2-column layout, alerts, KPIs"
```

---

### Task 6: Build Plan.jsx

**Files:**
- Create: `ollama_queue/dashboard/spa/src/pages/Plan.jsx` (copy of ScheduleTab.jsx with export renamed)

**Step 1: Copy ScheduleTab.jsx to Plan.jsx**

```bash
cp ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx \
   ollama_queue/dashboard/spa/src/pages/Plan.jsx
```

**Step 2: Change the export name in Plan.jsx**

Open `Plan.jsx` and find the function declaration:
```js
export default function ScheduleTab() {
```
Change it to:
```js
export default function Plan() {
```

That is the only change needed. All imports, logic, Gantt chart, recurring job table, "Spread run times" button, and inline edit stay exactly as-is.

**Step 3: Verify build**

```bash
npm run build
```
Expected: no errors.

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Plan.jsx
git commit -m "feat(plan): add Plan view (ScheduleTab renamed)"
```

---

### Task 7: Build History.jsx

**Files:**
- Create: `ollama_queue/dashboard/spa/src/pages/History.jsx`

DLQ section is shown at top only when `dlqCount > 0`. Duration trends and activity heatmap sit side-by-side on desktop. Completed job list is below, full width.

**Step 1: Create History.jsx**

```jsx
import { h } from 'preact';
import {
    dlqEntries, dlqCount, durationData, heatmapData, history,
    retryDLQEntry, retryAllDLQ, dismissDLQEntry, clearDLQ, fetchDLQ,
} from '../store';
import { useEffect, useState } from 'preact/hooks';
import ActivityHeatmap from '../components/ActivityHeatmap.jsx';
import HistoryList from '../components/HistoryList.jsx';
import TimeChart from '../components/TimeChart.jsx';

// NOTE: all .map() callbacks use descriptive names — never 'h' (shadows JSX factory)

export default function History() {
    const dlq = dlqEntries.value;
    const dlqCnt = dlqCount.value;
    const durations = durationData.value;
    const heatmap = heatmapData.value;
    const hist = history.value;
    const [retryingAll, setRetryingAll] = useState(false);

    useEffect(() => { fetchDLQ(); }, []);

    async function handleRetryAll() {
        if (!window.confirm(`Retry all ${dlq.length} failed jobs?`)) return;
        setRetryingAll(true);
        try { await retryAllDLQ(); }
        finally { setRetryingAll(false); }
    }

    async function handleClearDLQ() {
        if (!window.confirm('Clear all DLQ entries? This cannot be undone.')) return;
        await clearDLQ();
    }

    return (
        <div class="flex flex-col gap-6 animate-page-enter">

            {/* DLQ section — only shown when entries exist */}
            {dlqCnt > 0 && (
                <div class="t-frame" data-label={`Failed Jobs (${dlqCnt})`}>
                    <div style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        marginBottom: '0.75rem',
                        flexWrap: 'wrap',
                        gap: '0.5rem',
                    }}>
                        <span style={{
                            color: 'var(--status-error)',
                            fontSize: 'var(--type-label)',
                            fontFamily: 'var(--font-mono)',
                        }}>
                            {dlqCnt} {dlqCnt === 1 ? 'entry' : 'entries'} in dead-letter queue
                        </span>
                        <div class="flex gap-2">
                            <button
                                class="t-btn t-btn-secondary"
                                style="font-size: var(--type-label); padding: 3px 10px;"
                                onClick={handleRetryAll}
                                disabled={retryingAll}
                            >
                                {retryingAll ? 'Retrying...' : 'Retry all'}
                            </button>
                            <button
                                class="t-btn t-btn-secondary"
                                style="font-size: var(--type-label); padding: 3px 10px;"
                                onClick={handleClearDLQ}
                            >
                                Clear
                            </button>
                        </div>
                    </div>
                    {dlq.map(entry => (
                        <div key={entry.id} style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            padding: '0.4rem 0',
                            borderBottom: '1px solid var(--border-subtle)',
                            gap: '0.5rem',
                            flexWrap: 'wrap',
                        }}>
                            <div style="display: flex; flex-direction: column; gap: 2px; min-width: 0;">
                                <span style={{
                                    fontSize: 'var(--type-body)',
                                    color: 'var(--text-primary)',
                                    fontFamily: 'var(--font-mono)',
                                }}>
                                    {entry.source || '—'} #{entry.job_id}
                                </span>
                                <span style={{
                                    fontSize: 'var(--type-label)',
                                    color: 'var(--text-tertiary)',
                                }}>
                                    {entry.failure_reason || 'unknown reason'}
                                    {entry.retry_count > 0 && ` · ${entry.retry_count} retries`}
                                </span>
                            </div>
                            <div class="flex gap-2" style="flex-shrink: 0;">
                                <button
                                    class="t-btn t-btn-secondary"
                                    style="font-size: var(--type-label); padding: 2px 8px;"
                                    onClick={() => retryDLQEntry(entry.id)}
                                >
                                    Retry
                                </button>
                                <button
                                    class="t-btn t-btn-secondary"
                                    style="font-size: var(--type-label); padding: 2px 8px;"
                                    onClick={() => dismissDLQEntry(entry.id)}
                                >
                                    Dismiss
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            {/* Duration trends + Activity heatmap — side by side on desktop */}
            <div class="history-top-grid">
                <div class="t-frame" data-label="Duration Trends">
                    {durations && durations.length > 0 ? (
                        buildDurationBySources(durations).map(({ source, data }) => (
                            <div key={source} style="margin-bottom: 0.75rem;">
                                <div style={{
                                    fontSize: 'var(--type-micro)',
                                    color: 'var(--text-tertiary)',
                                    fontFamily: 'var(--font-mono)',
                                    marginBottom: '2px',
                                }}>
                                    {source}
                                </div>
                                <TimeChart
                                    data={data}
                                    series={[{ label: source, color: 'var(--accent)', width: 1.5 }]}
                                    height={60}
                                />
                            </div>
                        ))
                    ) : (
                        <p style="color: var(--text-tertiary); font-size: var(--type-body); text-align: center;">
                            No data yet
                        </p>
                    )}
                </div>

                <div class="t-frame" data-label="Activity — last 7 days">
                    <ActivityHeatmap data={heatmap} />
                </div>
            </div>

            {/* Completed jobs list */}
            <div class="t-frame" data-label={`History — ${(hist || []).length} jobs`}>
                <HistoryList jobs={hist} />
            </div>
        </div>
    );
}

// ── Data helper ────────────────────────────────────────────────────────────

function buildDurationBySources(rows) {
    const bySource = {};
    for (const r of rows) {
        const s = r.source || 'unknown';
        if (!bySource[s]) bySource[s] = [];
        bySource[s].push(r);
    }
    return Object.entries(bySource).map(([source, items]) => {
        const sorted = [...items].sort((a, b) => a.recorded_at - b.recorded_at);
        return {
            source,
            data: [sorted.map(r => r.recorded_at), sorted.map(r => r.duration)],
        };
    });
}
```

**Step 2: Verify build**

```bash
npm run build
```
Expected: clean build, no errors.

**Step 3: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/History.jsx
git commit -m "feat(history): add History view — DLQ + trends + heatmap + job list"
```

---

### Task 8: Delete deprecated page files

The old `Dashboard.jsx`, `ScheduleTab.jsx`, and `DLQTab.jsx` are replaced by `Now.jsx`, `Plan.jsx`, and `History.jsx`. Remove them so they don't create confusion.

**Step 1: Delete old files**

```bash
rm ollama_queue/dashboard/spa/src/pages/Dashboard.jsx
rm ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
rm ollama_queue/dashboard/spa/src/pages/DLQTab.jsx
```

**Step 2: Verify build**

```bash
npm run build
```
Expected: clean build — no remaining imports of the deleted files.

**Step 3: Commit**

```bash
git add -u ollama_queue/dashboard/spa/src/pages/
git commit -m "chore: remove deprecated Dashboard, ScheduleTab, DLQTab pages"
```

---

### Task 9: Deploy and verify

**Step 1: Restart service**

```bash
systemctl --user restart ollama-queue
```

**Step 2: Check service is running**

```bash
systemctl --user status ollama-queue
```
Expected: `active (running)`.

**Step 3: Open dashboard in browser**

Navigate to `http://127.0.0.1:7683/queue/ui/`

**Step 4: Vertical trace — check each view**

| View | What to verify |
|------|----------------|
| Now | Sidebar visible on left, "Now" item highlighted, 2-column layout: CurrentJob + QueueList on left, ResourceGauges + 4 KPI cards on right |
| Plan | Gantt chart renders with source-colored bars, "Spread run times" button visible, recurring job table below |
| History | Duration trend charts, activity heatmap, job list visible — no collapsibles; DLQ section appears only if entries exist |
| Models | Model table unchanged |
| Settings | Settings form unchanged |

**Step 5: Mobile check (DevTools responsive mode, width 375px)**

- Sidebar hidden
- Bottom nav visible with 5 items
- Now view is single column

**Step 6: Commit any final fixes, then push**

```bash
git push
```

---

## What is NOT changed

- No backend API changes
- No changes to `store.js` signal logic (only the default tab ID)
- No changes to `ModelsTab.jsx`, `Settings.jsx`, `SettingsForm.jsx`
- No changes to `GanttChart.jsx` or its tests
- `ResourceGauges.jsx`, `CurrentJob.jsx`, `QueueList.jsx`, `HeroCard.jsx`, `ActivityHeatmap.jsx`, `HistoryList.jsx`, `TimeChart.jsx` — all unchanged
