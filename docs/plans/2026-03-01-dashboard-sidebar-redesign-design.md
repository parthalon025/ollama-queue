# Dashboard Sidebar Redesign — Design Doc

**Date:** 2026-03-01
**Status:** Approved

---

## Goal

Replace the 5-tab dashboard with a sidebar-nav + command-center layout that surfaces all critical information (running job, failures, schedule, system health) without collapsible sections or multiple clicks.

---

## Mental Model

The current dashboard buries most content in collapsible sections below the fold. The new design is organized around **time horizon**:

| View | Question it answers | Navigation |
|------|---------------------|------------|
| **Now** | What is happening right now? | Primary / home |
| **Plan** | What is scheduled to run? | Second item |
| **History** | What happened? | Third item |
| **Models** | What models are installed? | Fourth item |
| **Settings** | How is the system configured? | Bottom (gear icon) |

---

## Navigation Structure

### Desktop

200px left sidebar, always visible. Items: icon + label. Daemon status chip in the sidebar header — visible on every view.

```
┌──────────────────┬─────────────────────────────────┐
│ ● ollama-queue   │                                 │
│──────────────────│         content area            │
│ ⊞  Now           │                                 │
│ 📅  Plan          │                                 │
│ ⏱  History       │                                 │
│ ⚙  Models        │                                 │
│                  │                                 │
│ ⚙  Settings      │                                 │
└──────────────────┴─────────────────────────────────┘
```

### Mobile

Bottom tab bar with the same 5 items (icon only, label below). Sidebar hidden. Content area is full-width single column.

### Daemon status chip (sidebar header)

- `● running` — green, shows current job name truncated
- `○ idle` — gray
- `✕ stalled` — red, pulses

Always visible regardless of which view is active.

---

## View Designs

### Now (Command Center)

Two columns on desktop, single column on mobile.

```
┌─────────────────────────┬──────────────────────────────┐
│ RUNNING                 │  ⚠ 2 jobs in DLQ             │
│ ┌─────────────────────┐ │  (strip hidden when clean)   │
│ │ aria-morning        │ │──────────────────────────────│
│ │ via aria · qwen2.5  │ │  RAM    ████████░░  76%      │
│ │ 4m 12s elapsed      │ │  VRAM   █████░░░░░  51%      │
│ │ [████████░░] ~10m   │ │  Load   ████░░░░░░  2.1      │
│ └─────────────────────┘ │  Swap   █░░░░░░░░░   3%      │
│                         │──────────────────────────────│
│ QUEUE  (3 pending)      │  Jobs/24h   Avg Wait         │
│ ┌─────────────────────┐ │  ┌───────┐  ┌──────────┐    │
│ │ #42 notion-sync  P3 │ │  │  18   │  │   12s    │    │
│ │ #43 telegram-am  P2 │ │  └───────┘  └──────────┘    │
│ │ #44 aria-intra   P1 │ │  Pause Time  Success         │
│ └─────────────────────┘ │  ┌───────┐  ┌──────────┐    │
│                         │  │  0 min│  │   100%   │    │
│                         │  └───────┘  └──────────┘    │
└─────────────────────────┴──────────────────────────────┘
```

**Left column:**
- Running job card: name, source, model, elapsed time, estimated duration progress bar, stall warning if stalled
- If no job running: "Idle — daemon active" placeholder
- Queue list: compact rows (job name, source, priority badge). No expand/collapse — all rows visible.

**Right column:**
- Alert strip (top): DLQ entry count + failure count from last 24h. Shown only when count > 0. Clicking navigates to History.
- Resource gauges: RAM, VRAM, Load, Swap as labeled progress bars with current value. Always visible — no collapsible.
- KPI cards: 4 cards (Jobs/24h, Avg Wait, Pause Time, Success Rate) in 2×2 grid.

**Mobile column order:** alert strip → running job card → KPI cards → queue list → resource gauges

---

### Plan

Full-width. Gantt chart at top, recurring job table below. No collapsibles.

- **Header row:** "Plan — next 24h" on left, "Spread run times" button on right
- **Gantt chart:** Existing `GanttChart` component, full content-area width. All existing features retained (source colors, density strip, conflict badges, status dots).
- **Recurring jobs table:** Below the chart. Columns: Name, Source, Interval, Next Run, Last Run (status dot from `runStatus`), Model. Compact rows.

---

### History

Three sections stacked. No collapsibles.

**Desktop:** duration trends and activity heatmap side-by-side (2 columns), completed jobs list below full-width.

**Mobile:** single column — heatmap → trends → jobs list.

- **Duration trends:** existing `TimeChart` multiples by source
- **Activity heatmap:** existing `ActivityHeatmap` component
- **Completed jobs list:** existing `HistoryList` component

---

### Models

Unchanged from current design — clean table with NAME, TYPE, SIZE, VRAM, AVG DURATION, STATUS. Gets sidebar nav treatment, no structural changes.

---

### Settings

Full-page form. Same sections as today: Health Thresholds, Defaults, Retention, Retry, Stall Detection, Concurrency, Daemon Controls. No structural changes to the form itself — just removes the tab chrome.

---

## Files to Change

| File | Change |
|------|--------|
| `spa/src/app.jsx` (or `main.jsx`) | Replace tab router with sidebar + route logic |
| `spa/src/components/Sidebar.jsx` | New: sidebar nav + daemon status chip |
| `spa/src/components/BottomNav.jsx` | New: mobile bottom tab bar |
| `spa/src/pages/Dashboard.jsx` | Rename → `Now.jsx`; 2-column layout; remove collapsibles; inline resource gauges |
| `spa/src/pages/ScheduleTab.jsx` | Rename → `Plan.jsx`; remove tab chrome |
| `spa/src/pages/HistoryTab.jsx` (new or existing) | Rename/create `History.jsx`; all three sections no collapsibles |
| `spa/src/pages/ModelsTab.jsx` | Rename → `Models.jsx`; no structural changes |
| `spa/src/pages/SettingsTab.jsx` | Rename → `Settings.jsx`; no structural changes |
| `spa/src/components/ResourceGauges.jsx` | New (or repurpose existing): always-visible compact bars |
| `spa/src/store.js` | No changes expected |
| `spa/src/styles/` | Sidebar CSS tokens |

---

## Responsive Strategy

- **≥ 1024px:** sidebar visible, 2-column Now layout
- **768–1023px:** sidebar collapses to icon-only (64px), 2-column Now layout
- **< 768px:** sidebar hidden, bottom tab bar, single-column Now layout

CSS: sidebar width via CSS custom property `--sidebar-width: 200px` (collapses to `64px`, then `0`). Content area uses `margin-left: var(--sidebar-width)`.

---

## Out of Scope

- Dark/light theme toggle (already handled by CSS tokens)
- Job detail drill-down view
- Real-time push (remains polling via signals)
- Changing any backend API endpoints
- Changing any existing component logic (only layout/routing changes)
