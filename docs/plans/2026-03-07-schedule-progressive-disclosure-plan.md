# Schedule Tab Progressive Disclosure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add progressive disclosure to the Schedule tab — tap Gantt bars for detail cards,
click density buckets to brush/link, ρ visual bar, colorblind legend, mobile full-screen expand,
column tooltips, and schedule health callout.

**Architecture:** Two file streams (GanttChart.jsx and Plan.jsx) that can execute in parallel.
Stream A owns all GanttChart changes; Stream B owns all Plan.jsx changes. Stream B's Task 7
wires the new GanttChart props and must run after Stream A completes. No backend changes.

**Tech Stack:** Preact 10, @preact/signals, esbuild/JSX, expedition33-ui CSS tokens.
Build: `cd ollama_queue/dashboard/spa && npm run build` (must pass after every task).

---

## Critical Rules Before Touching Anything

1. **Never name a `.map()` callback `h`** — esbuild injects `h` as the JSX factory. `.map(h => ...)` silently breaks rendering. Use descriptive names: `.map(job => ...)`, `.map(slot => ...)`.
2. **`npm run build` after every task** — JSX errors are silent at runtime. The build catches them.
3. **Exact file paths:** `ollama_queue/dashboard/spa/src/components/GanttChart.jsx` and `ollama_queue/dashboard/spa/src/pages/Plan.jsx`
4. **Work from the project root:** `~/Documents/projects/ollama-queue/`
5. **CSS tokens:** use `var(--status-healthy)`, `var(--status-warning)`, `var(--status-error)`, `var(--accent)`, `var(--text-tertiary)`, `var(--bg-inset)`, `var(--bg-surface-raised)`, `var(--border-subtle)`, `var(--font-mono)`, `var(--type-label)`, `var(--type-micro)`, `var(--radius)`.

---

## STREAM A — GanttChart.jsx

### Task 1: Export `runStatus` and add colorblind legend symbols

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`

**Context:**
`runStatus()` is currently not exported. The legend at the bottom of GanttChart shows color swatches only. Plan.jsx will need `runStatus` for the health callout (Task 5).

**Step 1: Add `export` to `runStatus`**

Find this line (around line 116):
```js
export function runStatus(lastRun, intervalSeconds, _now = Date.now() / 1000) {
```

It already starts with `export` — verify this. If it doesn't have `export`, add it.

Run:
```bash
grep -n "function runStatus" ollama_queue/dashboard/spa/src/components/GanttChart.jsx
```

Expected: line like `116: export function runStatus(`

If missing the `export`, add it.

**Step 2: Update legend to add shape symbols**

Find the legend block (around line 449). It starts with:
```jsx
{/* Legend — anchors the visual encoding so bars are readable without prior knowledge */}
<div style={{
    display: 'flex', flexWrap: 'wrap', gap: '0.3rem 0.9rem',
```

Replace the color legend entries array from:
```js
[
    { color: 'var(--accent)',      label: 'aria'     },
    { color: '#f97316',            label: 'telegram' },
    { color: '#a78bfa',            label: 'notion'   },
    { color: 'var(--text-tertiary)', label: 'other'  },
]
```

To (adds shape symbols as redundant encoding per §6.3 colorblind research):
```js
[
    { color: 'var(--accent)',        label: 'aria',     symbol: '◆' },
    { color: '#f97316',              label: 'telegram', symbol: '●' },
    { color: '#a78bfa',              label: 'notion',   symbol: '▲' },
    { color: 'var(--text-tertiary)', label: 'other',    symbol: '·' },
]
```

And update the map render to include the symbol before the label:
```jsx
{[
    { color: 'var(--accent)',        label: 'aria',     symbol: '◆' },
    { color: '#f97316',              label: 'telegram', symbol: '●' },
    { color: '#a78bfa',              label: 'notion',   symbol: '▲' },
    { color: 'var(--text-tertiary)', label: 'other',    symbol: '·' },
].map(({ color, label, symbol }) => (
    <span key={label} style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
        <span style={{
            display: 'inline-block', width: 10, height: 10,
            borderRadius: 2, background: color, opacity: 0.85, flexShrink: 0,
        }} />
        <span style={{ color }}>{symbol}</span>
        {label}
    </span>
))}
```

**Step 3: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -8
```

Expected: `⚡ Done in XXms` with no errors.

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/GanttChart.jsx
git commit -m "feat(spa): colorblind redundant encoding in Gantt legend

Add shape symbols (◆●▲·) alongside color swatches in GanttChart legend
so source-program encoding is readable without color discrimination.
Export runStatus for Plan.jsx health callout.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Density bucket brushing & linking

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`

**Context:**
When a user clicks a density bucket in the load strip, all Gantt bars NOT active in that
30-min window should dim to 15% opacity (Shneiderman §10.2 brushing & linking). Clicking
the same bucket or outside clears the selection.

**Step 1: Add `buildBucketJobIds` helper after `buildDensityBuckets`**

Find `buildDensityBuckets` (around line 73). Add this new exported function immediately after it:

```js
// What it shows: For each 30-min density bucket, the set of job ids active in that window.
// Used for brushing — clicking a bucket dims unrelated Gantt bars.
export function buildBucketJobIds(jobs, now, windowSecs, bucketCount) {
    const bucketSecs = windowSecs / bucketCount;
    return Array.from({ length: bucketCount }, (_, i) => {
        const bucketStart = now + i * bucketSecs;
        const bucketEnd = bucketStart + bucketSecs;
        const ids = new Set();
        for (const job of jobs) {
            const jobEnd = job.next_run + (job.estimated_duration || 600);
            if (job.next_run < bucketEnd && jobEnd > bucketStart) ids.add(job.id);
        }
        return ids;
    });
}
```

**Step 2: Add `selectedBucketIdx` state to GanttChart component**

Find the `export function GanttChart(...)` line (around line 150). Add inside the function body,
right after the `void tick;` line:

```js
const [selectedBucketIdx, setSelectedBucketIdx] = useState(null);
```

Make sure `useState` is imported — check the import at the top:
```js
import { h } from 'preact';
```

It likely only imports `h`. Change to:
```js
import { h } from 'preact';
import { useState } from 'preact/hooks';
```

**Step 3: Build the bucketJobIds map**

After the existing `densityBuckets` computation (after the `const densityBuckets = ...` block),
add:

```js
// Pre-compute which job ids are active in each density bucket — used for brushing
const bucketJobIds = buildBucketJobIds(
    jobs.filter(job => job.next_run < windowEnd),
    now, windowSecs, bucketCount
);
```

**Step 4: Update density strip — add click handler and selection highlight**

Find the density strip outer `<div>` with the `title` attribute (around line 190). Add an
`onClick` handler to clear selection when clicking outside a bucket:

The inner bucket `<div>` elements (the `.map((score, bucketIdx) => {...})`) need:
1. `cursor: 'pointer'`
2. `onClick` handler
3. Selection outline when `bucketIdx === selectedBucketIdx`

Replace the bucket div style/props (find the `<div key={bucketIdx} style={{...}}` inside the map):

```jsx
{densityBuckets.map((score, bucketIdx) => {
    const isSuggested = suggestDisplayIndices.has(bucketIdx);
    const isSelected = bucketIdx === selectedBucketIdx;
    return (
        <div
            key={bucketIdx}
            onClick={() => setSelectedBucketIdx(isSelected ? null : bucketIdx)}
            style={{
                flex: 1,
                position: 'relative',
                background: useLoadMap
                    ? loadMapSlotColor(score)
                    : (score === 0
                        ? 'var(--bg-inset)'
                        : score === 1
                            ? 'rgba(99,179,237,0.25)'
                            : score === 2
                                ? 'rgba(99,179,237,0.55)'
                                : 'rgba(99,179,237,0.9)'),
                borderRight: bucketIdx < densityBuckets.length - 1 ? '1px solid var(--border-subtle)' : 'none',
                outline: isSelected
                    ? '2px solid var(--accent)'
                    : isSuggested ? '2px solid rgba(52,211,153,0.9)' : 'none',
                outlineOffset: '-2px',
                cursor: 'pointer',
            }}
            title={isSuggested
                ? `Good time to add a job — low traffic, suggested by the scheduler`
                : useLoadMap && score > 0
                    ? (score >= LOAD_MAP_PIN_SCORE ? 'Locked slot — the scheduler keeps this window free and won\'t add new jobs here' : `Busy level: ${score} — higher = more work competing in this window`)
                    : (score > 0 ? `${score} job${score > 1 ? 's are' : ' is'} active in this 30-minute window` : undefined)}
        />
    );
})}
```

**Step 5: Add selection label above density strip**

The density strip outer wrapper div (the `position: relative` div that wraps everything)
currently has no label above the strip. Add a label that shows the selected window when active.

Find the outer return `<div style={{ position: 'relative', width: '100%' }}>` (line 185).
Inside it, before the load density strip section, add:

```jsx
{/* Brushing label — shows selected time window when a density bucket is clicked */}
{selectedBucketIdx !== null && (() => {
    const bucketSecs = windowSecs / bucketCount;
    const bucketStart = now + selectedBucketIdx * bucketSecs;
    const bucketEnd = bucketStart + bucketSecs;
    const activeIds = bucketJobIds[selectedBucketIdx] || new Set();
    const startStr = new Date(bucketStart * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const endStr = new Date(bucketEnd * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return (
        <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
            color: 'var(--accent)', marginBottom: '0.2rem',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
            <span>{startStr} – {endStr} · {activeIds.size} job{activeIds.size !== 1 ? 's' : ''} active</span>
            <button
                onClick={() => setSelectedBucketIdx(null)}
                style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: 'var(--text-tertiary)', fontSize: 'var(--type-micro)',
                    fontFamily: 'var(--font-mono)', padding: '0 4px',
                }}
            >✕ clear</button>
        </div>
    );
})()}
```

**Step 6: Apply dim opacity to non-selected Gantt bars**

In the job bars render section (`{laneJobs.map(job => {...})`), compute whether this bar
should be dimmed. Add at the top of the map callback (after `const isConcurrent = ...`):

```js
const isDimmed = selectedBucketIdx !== null &&
    !(bucketJobIds[selectedBucketIdx]?.has(job.id));
```

Then in the bar div style, add:
```js
opacity: isDimmed ? 0.15 : 0.85,
transition: 'opacity 0.2s ease',
```

(The existing `opacity: 0.85` should be replaced with the above.)

**Step 7: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -8
```

Expected: clean build.

**Step 8: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/GanttChart.jsx
git commit -m "feat(spa): brushing & linking between density strip and Gantt bars

Clicking a density bucket dims unrelated Gantt bars to 15% opacity,
highlighting only jobs active in that 30-min window. Shows time range
and job count label. Click again to clear. Implements Shneiderman §10.2.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Gantt bar tap → detail card

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`

**Context:**
`title` tooltips don't work on mobile touch. Clicking a bar should show a rich detail card
with all job info, run history dots, and action buttons. On mobile (≤640px) it shows as a
fixed bottom sheet; on desktop it floats near the click point.

**Step 1: Add new props to `GanttChart`**

Find the `export function GanttChart({ jobs, tick, windowHours = 24, loadMapSlots = [], suggestSlots = [] })` signature.

Update to:
```js
export function GanttChart({
    jobs, tick, windowHours = 24, loadMapSlots = [], suggestSlots = [],
    onRunJob = () => {}, onScrollToJob = () => {},
}) {
```

**Step 2: Add `selectedBarId` and `barRuns` state**

After the existing `useState(null)` for `selectedBucketIdx`, add:

```js
const [selectedBarId, setSelectedBarId] = useState(null);
const [barRuns, setBarRuns] = useState({});  // { [jobId]: runs[] }
const [barRunsLoading, setBarRunsLoading] = useState(false);
```

Also add these imports at the top of the file:
```js
import { fetchJobRuns } from '../store';
```

**Step 3: Add `handleBarClick` function**

Inside the GanttChart component body, add:

```js
// Fetch run history when a bar is selected; toggle off on second click
async function handleBarClick(job) {
    if (selectedBarId === job.id) {
        setSelectedBarId(null);
        return;
    }
    setSelectedBarId(job.id);
    if (!barRuns[job.id]) {
        setBarRunsLoading(true);
        try {
            const runs = await fetchJobRuns(job.id, 5);
            setBarRuns(prev => ({ ...prev, [job.id]: runs }));
        } catch (err) {
            console.error('fetchJobRuns failed:', err);
        } finally {
            setBarRunsLoading(false);
        }
    }
}
```

**Step 4: Update bar divs to be clickable**

In the `{laneJobs.map(job => {...})` section, find the bar `<div>` and:
1. Change `cursor: 'default'` to `cursor: 'pointer'`
2. Add `onClick={() => handleBarClick(job)}`
3. Add `role="button"` and `aria-label={job.name}` for accessibility

```jsx
<div
    key={job.id}
    role="button"
    aria-label={`${job.name} — click for details`}
    title={buildTooltip(job, isConcurrent)}
    onClick={() => handleBarClick(job)}
    style={{
        position: 'absolute',
        left: `${Math.min(leftPct, 99.5)}%`,
        width: `${barWidth}%`,
        top: job._lane * laneHeight + 4,
        height: laneHeight - 8,
        /* Touch slop: invisible 8px padding expands touch target without visual change */
        margin: '-4px',
        padding: '4px',
        background: color,
        opacity: isDimmed ? 0.15 : 0.85,
        transition: 'opacity 0.2s ease',
        borderRadius: 'var(--radius)',
        borderLeft: isHeavy ? '3px solid var(--status-warning)' : undefined,
        outline: conflictIds.has(job.id) ? '2px solid var(--status-error)' : undefined,
        outlineOffset: conflictIds.has(job.id) ? '-2px' : undefined,
        overflow: 'hidden',
        display: 'flex',
        alignItems: 'center',
        paddingLeft: isHeavy ? '0.3rem' : '0.4rem',
        gap: '0.3rem',
        cursor: 'pointer',
        boxSizing: 'border-box',
    }}
>
```

NOTE: The `margin: '-4px'; padding: '4px'` trick expands the hit area. If it causes layout issues
(bars overlapping their lane boundaries), remove those two lines — the click still works, just
with the smaller visual target.

**Step 5: Add `BarDetailCard` component**

Add this component just before the `export function GanttChart(...)` declaration:

```jsx
// What it shows: Full job details for a selected Gantt bar — description, program,
//   model, start time, estimated duration, last-run history, and action buttons.
// Decision it drives: User can quickly see everything about a job and trigger it
//   without navigating away from the schedule view. Works on touch (no title tooltip).
function BarDetailCard({ job, runs, runsLoading, onClose, onRunJob, onScrollToJob }) {
    const { label: runLabel, color: runColor } = runStatus(job.last_run, job.interval_seconds);
    const startStr = new Date(job.next_run * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const lastRunStr = job.last_run
        ? `${relativeTime(job.last_run)} ago`
        : 'never';
    const modelStr = job.model || job.model_profile || 'default';
    const isMobile = typeof window !== 'undefined' && window.innerWidth <= 640;

    const cardStyle = isMobile
        ? {
            position: 'fixed', bottom: 0, left: 0, right: 0, zIndex: 100,
            background: 'var(--bg-surface-raised)',
            borderTop: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius) var(--radius) 0 0',
            padding: '1rem',
            boxShadow: '0 -4px 24px rgba(0,0,0,0.4)',
            animation: 'slideUp 0.15s ease-out',
        }
        : {
            position: 'absolute', zIndex: 50,
            bottom: '100%', left: '50%', transform: 'translateX(-50%)',
            marginBottom: '0.5rem',
            background: 'var(--bg-surface-raised)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 'var(--radius)',
            padding: '0.75rem 1rem',
            minWidth: 260, maxWidth: 320,
            boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
        };

    return (
        <div style={cardStyle} onClick={evt => evt.stopPropagation()}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' }}>
                <div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                                  fontSize: 'var(--type-body)', color: 'var(--text-primary)' }}>
                        {job.name}
                    </div>
                    {job.description && (
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
                                      color: 'var(--text-secondary)', marginTop: '0.2rem', lineHeight: 1.4 }}>
                            {job.description}
                        </div>
                    )}
                </div>
                <span style={{ fontSize: 'var(--type-micro)', color: runColor,
                               whiteSpace: 'nowrap', marginLeft: '0.5rem' }}>
                    {runLabel} ●
                </span>
            </div>

            {/* Details grid */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.25rem 1rem',
                          fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
                          marginBottom: '0.5rem' }}>
                <span style={{ color: 'var(--text-tertiary)' }}>program</span>
                <span style={{ color: 'var(--text-primary)' }}>{job.source || '—'}</span>
                <span style={{ color: 'var(--text-tertiary)' }}>model</span>
                <span style={{ color: 'var(--text-primary)' }}>{modelStr}</span>
                <span style={{ color: 'var(--text-tertiary)' }}>starts</span>
                <span style={{ color: 'var(--text-primary)' }}>{startStr}</span>
                <span style={{ color: 'var(--text-primary)' }}>runs</span>
                <span style={{ color: 'var(--text-primary)' }}>~{formatDuration(job.estimated_duration)}</span>
                <span style={{ color: 'var(--text-tertiary)' }}>last ran</span>
                <span style={{ color: 'var(--text-primary)' }}>{lastRunStr}</span>
                <span style={{ color: 'var(--text-tertiary)' }}>interval</span>
                <span style={{ color: 'var(--text-primary)' }}>{formatIntervalShort(job.interval_seconds)}</span>
            </div>

            {/* Warnings */}
            {job.model_profile === 'heavy' && (
                <div style={{ fontSize: 'var(--type-micro)', color: 'var(--status-warning)',
                              marginBottom: '0.4rem', fontFamily: 'var(--font-mono)' }}>
                    ⚠ large model — needs ≥16GB VRAM
                </div>
            )}

            {/* Run history */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem',
                          marginBottom: '0.6rem', fontFamily: 'var(--font-mono)',
                          fontSize: 'var(--type-micro)', color: 'var(--text-tertiary)' }}>
                <span>history</span>
                {runsLoading ? (
                    <span>…</span>
                ) : runs && runs.length > 0 ? (
                    runs.slice(0, 5).map((run, idx) => (
                        <span
                            key={idx}
                            title={`${run.status} — ${run.completed_at ? new Date(run.completed_at * 1000).toLocaleString() : 'in progress'}`}
                            style={{ color: run.status === 'completed' ? 'var(--status-healthy)' : 'var(--status-error)' }}
                        >
                            {run.status === 'completed' ? '✓' : '✗'}
                        </span>
                    ))
                ) : (
                    <span>no history yet</span>
                )}
            </div>

            {/* Actions */}
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                <button
                    style={{
                        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
                        background: 'var(--accent)', color: 'var(--accent-text)',
                        border: 'none', borderRadius: 'var(--radius)',
                        padding: '3px 10px', cursor: 'pointer',
                    }}
                    onClick={() => { onRunJob(job.id); onClose(); }}
                >
                    ▶ Run now
                </button>
                <button
                    style={{
                        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
                        background: 'none', color: 'var(--accent)',
                        border: '1px solid var(--accent)', borderRadius: 'var(--radius)',
                        padding: '3px 10px', cursor: 'pointer',
                    }}
                    onClick={() => { onScrollToJob(job.id); onClose(); }}
                >
                    → job
                </button>
                <button
                    style={{
                        marginLeft: 'auto', fontFamily: 'var(--font-mono)',
                        fontSize: 'var(--type-micro)', background: 'none',
                        color: 'var(--text-tertiary)', border: 'none', cursor: 'pointer',
                        padding: '3px 6px',
                    }}
                    onClick={onClose}
                >
                    ✕
                </button>
            </div>
        </div>
    );
}

// Quick relative time helper for BarDetailCard
function relativeTime(ts) {
    if (!ts) return '—';
    const diff = Math.floor(Date.now() / 1000 - ts);
    if (diff < 60) return `${diff}s`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
    return `${Math.floor(diff / 86400)}d`;
}

// Short interval formatter for BarDetailCard
function formatIntervalShort(seconds) {
    if (!seconds) return '—';
    if (seconds % 86400 === 0) return `${seconds / 86400}d`;
    if (seconds % 3600 === 0) return `${seconds / 3600}h`;
    if (seconds % 60 === 0) return `${seconds / 60}m`;
    return `${seconds}s`;
}
```

**Step 6: Render BarDetailCard and click-outside dismiss**

The chart area div (the `<div style={{ position: 'relative', height: chartHeight, ...}}>`)
needs to:
1. Accept clicks to dismiss (click-outside behavior)
2. Render the BarDetailCard for the selected bar

Add `onClick={() => setSelectedBarId(null)}` to the chart area div.

For each bar, when it is selected, render the BarDetailCard inside it. Find the end of the bar
`<div>` content (after the status dot span) and add:

```jsx
{/* Detail card anchored to selected bar */}
{selectedBarId === job.id && (
    <BarDetailCard
        job={job}
        runs={barRuns[job.id] || null}
        runsLoading={barRunsLoading}
        onClose={() => setSelectedBarId(null)}
        onRunJob={onRunJob}
        onScrollToJob={onScrollToJob}
    />
)}
```

NOTE: On mobile the card is `position: fixed` so it renders relative to the viewport regardless
of where it's placed in the DOM. On desktop it's `position: absolute; bottom: 100%` so it
appears above the bar.

**Step 7: Add slideUp keyframe to index.css**

```bash
grep -n "slideUp\|@keyframes" ollama_queue/dashboard/spa/src/index.css | head -5
```

If `slideUp` doesn't exist, add to `index.css`:
```css
@keyframes slideUp {
    from { transform: translateY(100%); }
    to   { transform: translateY(0); }
}
```

**Step 8: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -8
```

Expected: clean build. If there are JSX errors, check for unclosed tags or missing imports.

**Step 9: Commit**

```bash
git add ollama_queue/dashboard/spa/src/components/GanttChart.jsx ollama_queue/dashboard/spa/src/index.css
git commit -m "feat(spa): Gantt bar tap → detail card with run history and actions

Clicking any Gantt bar shows a detail card with description, program,
model, start time, run history dots (last 5), Run now button, and → job
link. Bottom sheet on mobile (≤640px), floating card on desktop.
Adds onRunJob/onScrollToJob props (default: no-op).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## STREAM B — Plan.jsx

### Task 4: Column header tooltips

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Plan.jsx`

**Context:**
The COLUMNS array drives the `<th>` render. Currently no `title` attributes.
The `COLUMNS.map(col => <th ...>{col}</th>)` pattern is at line ~1096.

**Step 1: Replace COLUMNS array with COLUMN_DEFS**

Find this line (~152):
```js
const COLUMNS = ['Name', 'Model', 'GPU Mem', 'Repeats', 'Priority', 'Due In', 'Est. Time', '\u2713', 'Limit', '\u{1F4CC}', 'On', ''];
```

Replace with:
```js
const COLUMN_DEFS = [
    { label: 'Name',      title: 'Job name — set when the recurring job was created' },
    { label: 'Model',     title: 'Ollama model this job uses (overrides the system default)' },
    { label: 'GPU Mem',   title: 'Memory profile: light · standard · heavy. Heavy needs ≥16GB VRAM and cannot overlap another heavy job' },
    { label: 'Repeats',   title: 'How often this job runs — interval (e.g. 4h) or cron expression' },
    { label: 'Priority',  title: '1=highest, 10=lowest. Lower number dequeues first when multiple jobs are waiting' },
    { label: 'Due In',    title: 'Time until the next scheduled run' },
    { label: 'Est. Time', title: 'Estimated run duration based on recent run history' },
    { label: '\u2713',    title: 'Number of completed successful runs' },
    { label: 'Limit',     title: 'Max retry attempts before the job is moved to the Dead Letter Queue (DLQ)' },
    { label: '\u{1F4CC}', title: 'Pinned slot — the rebalancer will not move this job\'s scheduled run time' },
    { label: 'On',        title: 'Enable or disable this recurring job' },
    { label: '',          title: undefined },
];
const COLUMNS = COLUMN_DEFS.map(d => d.label);
const COL_COUNT = COLUMNS.length;
```

This keeps all existing `COLUMNS` usages working while enabling tooltips on headers.

**Step 2: Update the `<th>` render**

Find the `{COLUMNS.map(col => (<th key={col} style={...}>{col}</th>))}` block (~line 1096).

Replace with:
```jsx
{COLUMN_DEFS.map(({ label, title }) => (
    <th key={label || 'actions'} title={title} style={{
        textAlign: label === 'Name' ? 'left' : 'center',
        padding: '0.5rem 0.75rem',
        fontSize: 'var(--type-label)',
        color: 'var(--text-secondary)',
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        fontFamily: 'var(--font-mono)',
        whiteSpace: 'nowrap',
        cursor: title ? 'help' : undefined,
        ...(label === 'Name' ? {
            position: 'sticky', left: 0,
            background: 'var(--bg-surface-raised)', zIndex: 1,
        } : {}),
    }}>{label}</th>
))}
```

`cursor: 'help'` signals to mouse users that hovering reveals information (standard convention).

**Step 3: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -8
```

Expected: clean build.

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Plan.jsx
git commit -m "feat(spa): add descriptive tooltips to all schedule table column headers

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Schedule health callout

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Plan.jsx`

**Context:**
When enabled jobs are running behind schedule, show a warning strip above the table.
Uses `runStatus()` from `GanttChart.jsx` (exported in Task 1).

**Step 1: Import `runStatus` from GanttChart**

Find the existing GanttChart import (~line 11):
```js
import { GanttChart } from '../components/GanttChart';
```

Change to:
```js
import { GanttChart, runStatus } from '../components/GanttChart';
```

**Step 2: Compute late jobs in the render section**

In the `// --- Derived data ---` section (around line ~455), add after the `const groups = ...` line:

```js
// Jobs that are enabled and running behind schedule — drives the health callout strip
const lateJobs = jobs.filter(rj =>
    rj.enabled && runStatus(rj.last_run, rj.interval_seconds).label === 'running behind'
);
```

**Step 3: Add refs for row scrolling**

The "→ job" link in the detail card calls `onScrollToJob(id)`. Plan.jsx needs a ref map to
scroll to rows. Add to the state declarations at the top of `Plan()`:

```js
const jobRowRefs = useRef({});  // { [rjId]: DOM element ref }
```

And add a `handleScrollToJob` function in the handlers section:

```js
function handleScrollToJob(rjId) {
    const el = jobRowRefs.current[rjId];
    if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        // Brief highlight flash
        el.style.outline = '2px solid var(--accent)';
        el.style.transition = 'outline 0.3s ease';
        setTimeout(() => { el.style.outline = ''; }, 1500);
    }
    // Also expand the detail panel for that job
    if (expandedJobId !== rjId) toggleJobDetail(rjId);
}
```

**Step 4: Add `ref` to job rows**

Find `renderJobRow` function. In the returned `<tr>` element, add:
```jsx
ref={el => { if (el) jobRowRefs.current[rjId] = el; else delete jobRowRefs.current[rjId]; }}
```

**Step 5: Add health callout in render**

Find the job table section. Just before the `<div class="t-frame" style={{ padding: 0, overflowX: 'auto' }}>` that wraps the table (~line 1089), add:

```jsx
{/* Schedule health callout — only visible when jobs are running behind */}
{lateJobs.length > 0 && (
    <div style={{
        display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap',
        padding: '0.4rem 0.75rem',
        background: 'var(--status-warning-subtle, rgba(251,146,60,0.08))',
        border: '1px solid var(--status-warning)',
        borderRadius: 'var(--radius)',
        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
        marginBottom: '0.25rem',
    }}>
        <span style={{ color: 'var(--status-warning)', fontWeight: 700, whiteSpace: 'nowrap' }}>
            ⚠ {lateJobs.length} job{lateJobs.length > 1 ? 's' : ''} running behind schedule —
        </span>
        {lateJobs.map((rj, idx) => (
            <span key={rj.id}>
                <button
                    onClick={() => handleScrollToJob(rj.id)}
                    style={{
                        background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                        color: 'var(--accent)', textDecoration: 'underline',
                    }}
                >
                    {rj.name}
                </button>
                {idx < lateJobs.length - 1 ? ', ' : ''}
            </span>
        ))}
    </div>
)}
```

**Step 6: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -8
```

Expected: clean build.

**Step 7: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Plan.jsx
git commit -m "feat(spa): schedule health callout for jobs running behind schedule

Shows warning strip above the job table listing all enabled jobs
whose last run was more than 5% past their interval. Job names are
clickable and scroll to + highlight the row. Strip absent when all
jobs are on schedule (zero noise for the happy path).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 6: ρ visual bar

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Plan.jsx`

**Context:**
Replace the text badge `load 0.23 — light load` with a filled bar + 0.80 reference line.
Based on Cleveland & McGill: position/length encoding is more accurately judged than text.

**Step 1: Find the ρ indicator section**

The ρ section is around line 987–1035. It starts with:
```jsx
{/* ρ traffic intensity indicator */}
{jobs.length > 0 && (() => {
    const rho = computeRho(jobs);
    const { label, color } = rhoStatus(rho);
    return (
        <div style={{...
```

**Step 2: Replace with visual bar**

Replace the entire ρ section with:

```jsx
{/* ρ traffic intensity — visual bar shows daily load vs 0.80 warn threshold */}
{/* What it shows: How full the day's schedule is (0=empty, 1=non-stop). */}
{/* Decision: Keep below 0.80 — above that, Kingman's formula predicts queue wait times grow sharply. */}
{jobs.length > 0 && (() => {
    const rho = computeRho(jobs);
    const { label, color } = rhoStatus(rho);
    const fillPct = Math.min(rho, 1) * 100;
    return (
        <div style={{ marginBottom: '0.4rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.3rem' }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                               color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                    Daily load
                </span>
                {/* Bar track */}
                <div style={{
                    position: 'relative', flex: 1, height: 8,
                    background: 'var(--bg-inset)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius)',
                    overflow: 'visible',
                    minWidth: 80,
                }}>
                    {/* Fill */}
                    <div style={{
                        position: 'absolute', left: 0, top: 0, bottom: 0,
                        width: `${fillPct}%`,
                        background: color,
                        borderRadius: 'var(--radius)',
                        transition: 'width 0.4s ease, background 0.3s ease',
                    }} />
                    {/* 0.80 reference line */}
                    <div
                        aria-hidden="true"
                        title="Warning threshold — keep below 0.80 to avoid job pile-up"
                        style={{
                            position: 'absolute', left: '80%', top: -3, bottom: -3,
                            width: 1, borderLeft: '1px dashed var(--status-warning)',
                            zIndex: 2,
                        }}
                    />
                    {/* 0.80 label */}
                    <span style={{
                        position: 'absolute', left: '80%', top: -16,
                        transform: 'translateX(-50%)',
                        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-micro)',
                        color: 'var(--status-warning)',
                        whiteSpace: 'nowrap', pointerEvents: 'none',
                    }}>0.80</span>
                </div>
                {/* Numeric value + label */}
                <span
                    style={{
                        fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                        fontWeight: 700, color, whiteSpace: 'nowrap',
                    }}
                    title={`How packed is your daily schedule? 0.0 = nothing scheduled, 1.0 = queue running non-stop. Keep below 0.80 to avoid jobs piling up and waiting for each other. Current: ${rho.toFixed(2)}`}
                    aria-label={`Traffic intensity: ${rho.toFixed(2)}, status: ${label}`}
                >
                    {rho.toFixed(2)} — {label}
                </span>
                {/* Find best slot button */}
                <button
                    class="t-btn t-btn--ghost"
                    style={{ fontSize: 'var(--type-label)', padding: '1px 8px', whiteSpace: 'nowrap' }}
                    disabled={suggestLoading}
                    onClick={async () => {
                        if (suggestSlots !== null) { setSuggestSlots(null); return; }
                        setSuggestLoading(true);
                        try {
                            const data = await fetchSuggestTime(5, 3);
                            setSuggestSlots(data.suggestions || []);
                        } catch (e) {
                            console.error('fetchSuggestTime failed:', e);
                        } finally {
                            setSuggestLoading(false);
                        }
                    }}
                    title="Find the best time windows to add a new recurring job — highlights the quietest slots on the chart above"
                >
                    {suggestLoading ? '…'
                        : suggestSlots === null ? 'Find best slot'
                        : suggestSlots.length === 0 ? 'No open slots found'
                        : 'Clear suggestions'}
                </button>
            </div>
        </div>
    );
})()}
```

**Step 3: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -8
```

**Step 4: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Plan.jsx
git commit -m "feat(spa): replace ρ text badge with visual progress bar

Filled bar with 0.80 dashed reference line is more accurately judged
than a number + label (Cleveland & McGill: position/length > text).
Color steps green→amber→red as load grows past 0.60→0.80.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Mobile full-screen Gantt expand + wire new GanttChart props

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/Plan.jsx`

**Context:**
On 375px mobile a 10-minute job bar is ~2.5px wide — unreadable. A full-screen overlay at
6h window makes bars 4× wider. Also wires `onRunJob` and `onScrollToJob` props to GanttChart
(added in Stream A Task 3).

**PREREQUISITE:** Stream A Task 3 must be complete and merged before this task.

**Step 1: Add `ganttExpanded` state**

In the state declarations at the top of `Plan()`, add:
```js
const [ganttExpanded, setGanttExpanded] = useState(false);
```

**Step 2: Add ESC key handler**

After the existing `useEffect` that sets up tick/refresh intervals, add:

```js
// Close expanded Gantt on ESC
useEffect(() => {
    if (!ganttExpanded) return;
    function onKey(evt) { if (evt.key === 'Escape') setGanttExpanded(false); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
}, [ganttExpanded]);
```

**Step 3: Detect mobile for windowHours**

Add a helper (outside the component, or inline):
```js
const isMobileScreen = () => typeof window !== 'undefined' && window.innerWidth <= 640;
```

**Step 4: Update the Gantt `t-frame` section**

Find the Gantt section in the render (~line 1050):
```jsx
{/* Gantt timeline — ... */}
<div class="t-frame" data-label="Next 24 hours">
    <p style={{...}}>
        Each bar is a scheduled job...
    </p>
    <GanttChart jobs={jobs} tick={tick} windowHours={24} loadMapSlots={...} suggestSlots={...} />
</div>
```

Replace with:

```jsx
{/* Gantt timeline — each bar is one scheduled job; width = expected run time; color = source program */}
{/* ⤢ button opens full-screen view on mobile so bars are 4× wider (6h window vs 24h) */}
<div class="t-frame" data-label="Next 24 hours">
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
                  marginBottom: '0.5rem' }}>
        <p style={{
            margin: 0,
            fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
            color: 'var(--text-tertiary)', lineHeight: 1.5, flex: 1,
        }}>
            Each bar is a scheduled job. Bar width shows how long it&apos;s expected to run.
            Color shows which program runs it. Tap or hover any bar for details.
        </p>
        <button
            title="Expand to full screen for better mobile readability"
            onClick={() => setGanttExpanded(true)}
            style={{
                background: 'none', border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius)', cursor: 'pointer',
                color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)',
                fontSize: 'var(--type-label)', padding: '2px 7px', marginLeft: '0.5rem',
                flexShrink: 0,
            }}
        >⤢</button>
    </div>
    <GanttChart
        jobs={jobs}
        tick={tick}
        windowHours={24}
        loadMapSlots={loadMap.value?.slots || []}
        suggestSlots={suggestSlots || []}
        onRunJob={id => { const rj = jobs.find(j => j.id === id); if (rj) handleRunNow(rj); }}
        onScrollToJob={handleScrollToJob}
    />
</div>

{/* Full-screen Gantt overlay — activated by ⤢ button, especially useful on mobile */}
{ganttExpanded && (
    <div
        style={{
            position: 'fixed', inset: 0, zIndex: 50,
            background: 'var(--bg-base)', overflowY: 'auto',
            padding: '1rem',
        }}
        onClick={evt => { if (evt.target === evt.currentTarget) setGanttExpanded(false); }}
    >
        <div style={{ display: 'flex', justifyContent: 'space-between',
                      alignItems: 'center', marginBottom: '0.75rem' }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                           fontSize: 'var(--type-headline)', color: 'var(--text-primary)' }}>
                Schedule {isMobileScreen() ? '(next 6h)' : '(next 24h)'}
            </span>
            <button
                onClick={() => setGanttExpanded(false)}
                style={{
                    background: 'none', border: '1px solid var(--border-subtle)',
                    borderRadius: 'var(--radius)', cursor: 'pointer',
                    color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)',
                    fontSize: 'var(--type-body)', padding: '3px 10px',
                }}
            >✕ close</button>
        </div>
        <GanttChart
            jobs={jobs}
            tick={tick}
            windowHours={isMobileScreen() ? 6 : 24}
            loadMapSlots={loadMap.value?.slots || []}
            suggestSlots={suggestSlots || []}
            onRunJob={id => { const rj = jobs.find(j => j.id === id); if (rj) handleRunNow(rj); }}
            onScrollToJob={id => { setGanttExpanded(false); handleScrollToJob(id); }}
        />
    </div>
)}
```

**Step 5: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -8
```

Expected: clean build.

**Step 6: Commit**

```bash
git add ollama_queue/dashboard/spa/src/pages/Plan.jsx
git commit -m "feat(spa): mobile full-screen Gantt expand + wire detail card callbacks

⤢ button opens full-screen overlay. On mobile (≤640px) uses 6h window
so bars are 4× wider than the default 24h view — fixes the 2.5px bar
problem at 375px. Wires onRunJob/onScrollToJob into GanttChart so the
detail card Run now and → job buttons work from within the chart.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Final: Push and verify

**Step 1: Final build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -5
```

Expected: `⚡ Done in XXms` with no errors.

**Step 2: Push**

```bash
git push
```

**Step 3: Visual verification checklist**

Open the dashboard at `http://localhost:7683/queue/ui/` (or the Tailscale URL).

On desktop:
- [ ] Hover a Gantt bar → `title` tooltip shows
- [ ] Click a Gantt bar → detail card floats above the bar with name, description, model, start time, run history dots, Run now and → job buttons
- [ ] Click Run now → job queues; click → job → table scrolls and row highlights briefly
- [ ] Click a density bucket → unrelated bars dim; label shows `HH:MM – HH:MM · N jobs`; click again → restores
- [ ] ρ bar fills proportionally; 0.80 dashed line visible; color changes at 0.60/0.80
- [ ] ⤢ button opens full-screen overlay; ESC closes it
- [ ] Column headers show `cursor: help` and tooltip on hover
- [ ] Legend shows `◆ aria ● telegram ▲ notion · other`

On mobile (or DevTools at 375px):
- [ ] Tap a Gantt bar → bottom sheet slides up from bottom
- [ ] Bottom sheet shows all info and Run now / → job / ✕ buttons
- [ ] ⤢ opens full-screen at 6h window — bars visibly wider
- [ ] Tap outside sheet → dismisses

When a job is late:
- [ ] Health callout strip appears above table with job names as links
- [ ] Clicking a name scrolls to and highlights the row
