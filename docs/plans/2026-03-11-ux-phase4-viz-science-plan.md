# UX Phase 4: Visualization Science Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.
> **PREREQUISITE:** Phase 1 (`feature/ux-foundation`) must be merged to main before starting this phase.
> **PARALLEL:** This phase can run concurrently with Phase 3 in a separate worktree — files touched are different.

**Goal:** Enforce science-backed visualization principles — non-color priority encoding, progressive disclosure on queue rows, heatmap hover tooltips, missing sparklines, data-chroma semantic audit, animation tier enforcement, and @starting-style tab entrance animations.

**Architecture:** Pure SPA CSS/JSX changes. No new API endpoints. Focus on `index.css` and component-level data encoding improvements. All based on Cleveland & McGill, Treisman, Shneiderman, and Tufte principles.

**Tech Stack:** Preact 10, @preact/signals, Tailwind v4, CSS `@starting-style` (Chrome 117+/Safari 17.5+, graceful fallback). Build: `cd ollama_queue/dashboard/spa && npm run build`. Tests: `npm test`.

---

## Pre-flight

```bash
cd ~/Documents/projects/ollama-queue
git checkout main && git pull origin main  # Must include Phase 1
git checkout -b feature/viz-science
cd ollama_queue/dashboard/spa && npm run build && npm test
```

---

## Task 1: Non-Color Priority Discriminator (Treisman)

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/QueueList.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/Plan/index.jsx`
- Modify: `ollama_queue/dashboard/spa/src/store.js` (or wherever PRIORITY_COLORS is defined)

**Step 1: Find where priority border-left is set**

```bash
grep -n "priorityColor\|PRIORITY_COLORS\|border-left" ollama_queue/dashboard/spa/src/components/QueueList.jsx | head -10
grep -rn "priorityColor\|PRIORITY_COLORS" ollama_queue/dashboard/spa/src/ | head -10
```

**Step 2: Create priorityBorderWidth helper**

Find where `PRIORITY_COLORS` or `priorityColor()` is defined. In the same file or in a shared utils, add:

```js
// Non-color encoding for priority — Treisman (1980): combine color + independent channel
// for colorblind safety. Border thickness is independent of hue.
export function priorityBorderWidth(priority) {
  if (priority <= 2) return '4px';  // Critical
  if (priority <= 4) return '3px';  // High
  if (priority <= 6) return '2px';  // Normal
  if (priority <= 8) return '1px';  // Low
  return '1px';                      // Background (opacity handled separately)
}

export function priorityBorderOpacity(priority) {
  return priority >= 9 ? '0.4' : '1';
}
```

**Step 3: Apply to QueueList rows**

In `QueueList.jsx`, find where `border-left` color is applied (currently just color). Add width:

```jsx
style={{
  borderLeft: `${priorityBorderWidth(job.priority)} solid ${priorityColor(job.priority)}`,
  opacity: priorityBorderOpacity(job.priority),
  // ... other styles ...
}}
```

**Step 4: Apply to Plan table rows**

In `Plan/index.jsx`, find the recurring jobs table row styles. Apply the same `priorityBorderWidth()`:
```jsx
style={{ borderLeft: `${priorityBorderWidth(job.priority)} solid ${getPriorityColor(job.priority)}` }}
```

**Step 5: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 6: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/QueueList.jsx \
        ollama_queue/dashboard/spa/src/pages/Plan/index.jsx \
        ollama_queue/dashboard/spa/src/store.js
git commit -m "feat(spa): add border-width priority encoding — non-color discriminator (Treisman 1980)"
```

---

## Task 2: Progressive Disclosure on QueueList Rows (Shneiderman)

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/QueueList.jsx`

**Step 1: Add expandedId state**

In `QueueList.jsx`:
```jsx
const [expandedId, setExpandedId] = useState(null);

function toggleExpand(jobId) {
  setExpandedId(prev => prev === jobId ? null : jobId);
}
```

**Step 2: Modify row to be clickable (excluding cancel button)**

On the row's main content area (not the × cancel button), add:
```jsx
onClick={() => toggleExpand(job.id)}
style={{ cursor: 'pointer' }}
```

**Step 3: Add expanded detail section**

Below the default row content, add:
```jsx
{expandedId === job.id && (
  <div style="margin-top:8px;padding:8px;background:var(--bg-inset);border-radius:var(--radius);font-family:var(--font-mono);font-size:var(--type-micro);color:var(--text-secondary);display:flex;flex-direction:column;gap:4px;">
    <div><span style="color:var(--text-tertiary);">enqueued:</span> {new Date(job.enqueued_at * 1000).toLocaleString()}</div>
    {job.estimated_duration && (
      <div><span style="color:var(--text-tertiary);">est. duration:</span> {formatDuration(job.estimated_duration)}</div>
    )}
    {job.retry_count > 0 && (
      <div><span style="color:var(--text-tertiary);">retries:</span> {job.retry_count}</div>
    )}
    {job.prompt && (
      <div style="max-height:60px;overflow:hidden;text-overflow:ellipsis;">
        <span style="color:var(--text-tertiary);">prompt:</span> {job.prompt.slice(0, 120)}{job.prompt.length > 120 ? '…' : ''}
      </div>
    )}
  </div>
)}
```

**Step 4: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/QueueList.jsx
git commit -m "feat(spa): progressive disclosure on queue rows — expand for enqueue time, duration, retries (Shneiderman)"
```

---

## Task 3: Heatmap Cell Hover Tooltips (Shneiderman)

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/ActivityHeatmap.jsx`

**Step 1: Read ActivityHeatmap.jsx**

```bash
cat ollama_queue/dashboard/spa/src/components/ActivityHeatmap.jsx
```

**Step 2: Add tooltip state**

```jsx
const [tooltip, setTooltip] = useState(null); // { x, y, label, value }
```

**Step 3: Add onMouseEnter/Leave to each cell**

For each cell in the 7×24 grid, add handlers:
```jsx
onMouseEnter={e => setTooltip({
  x: e.clientX + 12,
  y: e.clientY - 40,
  label: formatCellLabel(dayIndex, hourIndex), // e.g. "Wed 14:00"
  value: cellValue != null ? `${Math.round(cellValue)}% GPU` : 'No data',
})}
onMouseLeave={() => setTooltip(null)}
```

Where `formatCellLabel` is:
```js
const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
function formatCellLabel(dayIdx, hourIdx) {
  return `${DAY_LABELS[dayIdx]} ${String(hourIdx).padStart(2, '0')}:00`;
}
```

**Step 4: Render tooltip portal**

At the end of the ActivityHeatmap return:
```jsx
{tooltip && (
  <div style={`position:fixed;left:${tooltip.x}px;top:${tooltip.y}px;z-index:200;background:var(--bg-surface);border:1px solid var(--border-primary);border-radius:var(--radius);padding:6px 10px;font-family:var(--font-mono);font-size:var(--type-micro);color:var(--text-secondary);pointer-events:none;box-shadow:var(--card-shadow-hover);white-space:nowrap;`}>
    <div style="color:var(--text-primary);">{tooltip.label}</div>
    <div>{tooltip.value}</div>
  </div>
)}
```

Note: The tooltip renders inside the heatmap container but uses `position:fixed` so it escapes overflow clips.

**Step 5: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 6: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/ActivityHeatmap.jsx
git commit -m "feat(spa): hover tooltip on all 168 heatmap cells — date/hour + GPU% (Shneiderman)"
```

---

## Task 4: Sparkline Audit and Gap-Fill (Tufte)

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Now.jsx`
- Modify: `ollama_queue/dashboard/spa/src/store.js`

**Step 1: Audit existing sparklines**

```bash
grep -n "sparkData\|sparkline\|HeroCard" ollama_queue/dashboard/spa/src/pages/Now.jsx | head -20
```

**Step 2: Check what durationData contains**

```bash
grep -n "durationData\|duration_data" ollama_queue/dashboard/spa/src/store.js | head -20
```

**Step 3: Identify missing sparklines**

For each of the 4 HeroCards, check whether `sparkData` is currently passed. Identify which 2 are missing (likely Pause Time and Success Rate).

**Step 4: Compute missing sparkline data from durationData**

In `store.js` or as a computed function in `Now.jsx`, derive hourly bucketed data:

```js
// Pause time sparkline — sum paused_seconds per hour bucket
function computePauseSparkline(durationData) {
  if (!durationData?.length) return [];
  // durationData is array of { hour, paused_seconds, ... }
  // Return last 24 hourly values
  return durationData.slice(-24).map(d => d.paused_seconds || 0);
}

// Success rate sparkline — success_count / total_count per hour
function computeSuccessRateSparkline(durationData) {
  if (!durationData?.length) return [];
  return durationData.slice(-24).map(d => {
    const total = (d.success_count || 0) + (d.failed_count || 0);
    return total > 0 ? (d.success_count || 0) / total : null;
  });
}
```

**Step 5: Pass computed sparklines to HeroCards**

```jsx
<HeroCard
  label="Pause Time"
  value={formatPauseTime(kpis.pause_time_minutes)}
  tooltip="..."
  sparkData={computePauseSparkline(durationData.value)}
/>
<HeroCard
  label="Success Rate"
  value={`${Math.round((kpis.success_rate || 1) * 100)}%`}
  tooltip="..."
  sparkData={computeSuccessRateSparkline(durationData.value)}
/>
```

**Step 6: Verify HeroCard renders sparkline when sparkData provided**

```bash
grep -n "sparkData\|TimeChart\|sparkline" ollama_queue/dashboard/spa/src/components/HeroCard.jsx
```

HeroCard should already render a `<TimeChart>` when `sparkData` is non-empty. If not, add it following the existing sparkline pattern on Jobs/24h.

**Step 7: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 8: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Now.jsx \
        ollama_queue/dashboard/spa/src/store.js
git commit -m "feat(spa): add missing sparklines to Pause Time and Success Rate KPIs (Tufte)"
```

---

## Task 5: data-chroma Semantic Audit

**Files:**
- Modify: multiple component files — `CurrentJob.jsx`, `QueueList.jsx`, `ResourceGauges.jsx`, `HeroCard.jsx` (Now.jsx usage), `HistoryList.jsx`, `SettingsForm.jsx`

**Step 1: Audit current data-chroma usage**

```bash
grep -rn "data-chroma\|data-label" ollama_queue/dashboard/spa/src/ | head -40
```

**Step 2: Compare against the semantic mapping table**

Required mapping (from design doc):

| Card | data-chroma |
|------|-------------|
| CurrentJob `.t-frame` | `gustave` |
| QueueList `.t-frame` | `gustave` |
| ResourceGauges `.t-frame` | `lune` |
| HeroCard Jobs/24h | `lune` |
| HeroCard Avg Wait | `lune` |
| HeroCard Pause Time | `maelle` |
| HeroCard Success Rate | `maelle` |
| DLQ section `.t-frame` | `maelle` |
| Settings Health Thresholds | `lune` |
| Settings Defaults | `gustave` |
| Settings Retention | `sciel` |
| Settings Retry | `maelle` |
| Settings Stall Detection | `maelle` |
| Settings Concurrency | `lune` |
| Settings Daemon Controls | `gustave` |

**Step 3: Add missing data-chroma attributes**

For each `.t-frame` element missing the correct `data-chroma` attribute, add it:

```jsx
// CurrentJob:
<div ref={cardRef} class="t-frame" data-label="Currently Running" data-chroma="gustave" ...>

// ResourceGauges:
<div class="t-frame" data-label="Resources" data-chroma="lune">

// HeroCard — pass chroma as prop:
<HeroCard label="Pause Time" chroma="maelle" ...>
// In HeroCard.jsx: add data-chroma={chroma} to root .t-frame element
```

**Step 4: Update HeroCard to accept and apply chroma prop**

```jsx
export default function HeroCard({ label, value, delta, sparkData, tooltip, chroma }) {
  return (
    <div class="t-frame" data-chroma={chroma || undefined} ...>
```

**Step 5: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 6: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/CurrentJob.jsx \
        ollama_queue/dashboard/spa/src/components/QueueList.jsx \
        ollama_queue/dashboard/spa/src/components/ResourceGauges.jsx \
        ollama_queue/dashboard/spa/src/components/HeroCard.jsx \
        ollama_queue/dashboard/spa/src/components/HistoryList.jsx \
        ollama_queue/dashboard/spa/src/components/SettingsForm.jsx \
        ollama_queue/dashboard/spa/src/pages/Now.jsx
git commit -m "feat(spa): apply semantic data-chroma attributes across all .t-frame cards"
```

---

## Task 6: Animation Tier Audit

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/index.css`

**Step 1: Identify all animations in index.css**

```bash
grep -n "animation\|@keyframes\|transition" ollama_queue/dashboard/spa/src/index.css | head -40
```

**Step 2: Categorize each animation by tier**

| Tier | Type | Guard |
|------|------|-------|
| T1 | Ambient (scan beams, breathing, cursor blink) | `@media (prefers-reduced-motion: no-preference) and (min-width: 768px)` |
| T2 | Data refresh (progress bar, state transitions, save flash) | `@media (prefers-reduced-motion: no-preference)` |
| T3 | Status alert (shatter, glitch, threat-pulse) | No guard — safety signals |

**Step 3: Move T1 animations inside reduced-motion + mobile guard**

Find ambient animations (scan-sweep, cursor blink, breathing). Wrap them:
```css
@media (prefers-reduced-motion: no-preference) and (min-width: 768px) {
  .t-section-header::after { animation: scan-sweep 4s ease-in-out infinite; }
  .cursor-active { animation: cursor-blink 1s step-end infinite; }
  /* etc. */
}
```

**Step 4: Move T2 animations inside reduced-motion guard**

Transitions on progress bars, state badge changes, save flash:
```css
@media (prefers-reduced-motion: no-preference) {
  .progress-bar { transition: width 1s linear, background 0.3s ease; }
  /* etc. */
}
```

**Step 5: Verify T3 (shatter, glitch, threat-pulse) has NO guard**

These are safety signals. Check that their `@keyframes` definitions are at root level, not inside any `@media` block.

**Step 6: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build && npm test
```

**Step 7: Commit**

```bash
git add ollama_queue/dashboard/spa/src/index.css
git commit -m "feat(spa): enforce animation tier discipline — T1 off on mobile/reduced-motion, T3 always-on"
```

---

## Task 7: @starting-style Tab Entrance Animations

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/index.css`
- Modify: `ollama_queue/dashboard/spa/src/app.jsx` (or each page component)

**Step 1: Add tab-enter CSS**

In `index.css`, add:

```css
/* Tab entrance animation — @starting-style — Chrome 117+/Safari 17.5+ */
/* Fallback: graceful (instant render, no layout shift) */
@starting-style {
  .tab-enter {
    opacity: 0;
    transform: translateY(8px);
  }
}

.tab-enter {
  opacity: 1;
  transform: translateY(0);
}

@media (prefers-reduced-motion: no-preference) {
  .tab-enter {
    transition: opacity 0.2s ease, transform 0.2s ease;
  }
}
```

**Step 2: Apply tab-enter class to page roots**

**Option A (preferred):** In `app.jsx`, wrap the rendered page component in a `<div key={currentTab.value} class="tab-enter">`. The `key` change forces remount on tab switch, which re-triggers `@starting-style`.

```jsx
<div key={currentTab.value} class="tab-enter" style="flex:1;overflow-y:auto;">
  {/* current page component */}
</div>
```

**Option B:** Add `class="tab-enter"` to each page component's root element manually. Less preferred — requires touching 7 files.

Use Option A.

**Step 3: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build
```

**Step 4: Manual browser test (jsdom cannot test @starting-style)**

After build, start the dev server and navigate between tabs. Verify:
- Tab content fades/slides in on switch
- No layout shift
- No animation in reduced-motion mode
- Works on mobile (tablets/phones)

If dev server exists:
```bash
grep -n "start\|serve\|dev" ollama_queue/dashboard/spa/package.json
```

**Step 5: Commit**

```bash
git add ollama_queue/dashboard/spa/src/index.css \
        ollama_queue/dashboard/spa/src/app.jsx
git commit -m "feat(spa): @starting-style tab entrance animations — world unfreezes on navigation"
```

---

## Task 8: Final Build, Test, Push, PR

**Step 1: Full build and test suite**

```bash
cd ollama_queue/dashboard/spa && npm run build && npm test
cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/ -x -q
```

**Step 2: Push**

```bash
cd ~/Documents/projects/ollama-queue
git push -u origin feature/viz-science
```

**Step 3: Create PR**

```bash
gh pr create \
  --title "feat(spa): UX Phase 4 — visualization science (priority encoding, progressive disclosure, heatmap hover, sparklines, data-chroma, animation tiers, tab entrance)" \
  --body "## UX Phase 4: Visualization Science

Implements items 34–40 from the UX & design philosophy improvements design.

### Changes
- Border-width priority encoding — color + thickness (Treisman 1980, colorblind-safe)
- Progressive disclosure on queue rows — expand for details (Shneiderman)
- Hover tooltip on all 168 heatmap cells — date/hour + GPU% (Shneiderman)
- Missing sparklines added to Pause Time and Success Rate KPIs (Tufte)
- data-chroma semantic audit — all .t-frame cards correctly attributed
- Animation tier enforcement — T1 off on mobile/reduced-motion, T2 off on reduced-motion, T3 always-on
- @starting-style tab entrance animations — world unfreezes on navigation

### Science references
- Cleveland & McGill (1984) — perceptual accuracy: position > color
- Treisman & Gelade (1980) — preattentive processing, multi-channel encoding
- Shneiderman (1996) — overview first, details on demand
- Tufte (2006) — sparklines: a number without trend is half a story

### Design doc
\`docs/plans/2026-03-11-ux-design-philosophy-improvements-design.md\`" \
  --base main
```
