# Schedule Tab Progressive Disclosure Design

**Date:** 2026-03-07
**Status:** Approved
**Project:** ollama-queue SPA
**Research basis:** `~/Documents/research/2026-03-02-data-visualization-design-research.md`

---

## Goals

Make the Schedule tab informative and verbose on demand without cluttering the default view.
Apply Shneiderman's "Overview first, zoom and filter, then details on demand" mantra across
all three schedule visualizations (Gantt chart, density strip, ρ indicator).

## Non-Goals

- No backend API changes
- No new data fetching (all data already in `scheduleJobs.value` and `loadMap.value`)
- No changes to other tabs

## Research Foundations

From `2026-03-02-data-visualization-design-research.md`:

| Principle | Source | Application |
|-----------|--------|-------------|
| Position/length > text for quantitative judgment | Cleveland & McGill §2.1 | ρ filled bar |
| Brushing & linking — selection propagates across charts | Shneiderman §10.2 | Bucket click → dim unrelated bars |
| Tap for tooltip (bottom sheet on mobile) | §9.3 Mobile patterns | Bar tap → detail card |
| Touch target ≥ 44px | WCAG 2.5.8, §9.3 | 8px touch slop on bars |
| Color alone insufficient — redundant encoding | §6.3 Colorblind | Shape symbols in legend |
| Overview first → details on demand | Shneiderman §10.1 | All features off by default |

---

## Feature 1: Gantt Bar Tap → Detail Card

### Behavior

- Click/tap any bar → `selectedBarId` state (in `GanttChart`)
- Click same bar again, or click outside the card → dismiss
- `title` tooltip retained as fallback for mouse users

### Detail Card Contents

```
[job name]                              [on schedule ●] / [running late ●]
[description — if set]

program   [source]        model     [model name or profile]
starts    [HH:MM]         runs      ~[estimated duration]
last ran  [relative]      interval  [formatted interval]

[⚠ large model — needs ≥16GB VRAM — if heavy]
[⟡ runs at the same time as another job — if concurrent]

history  ✓ ✓ ✗ ✓ ✓        [Run now ▶]    [→ job]
```

**"Run now"** calls `onRunJob(job.id)` prop (wired to existing `handleRunNow` in Plan.jsx).
**"→ job"** calls `onScrollToJob(job.id)` prop — scrolls to and briefly highlights the row.

### Positioning

- **Mobile (≤640px):** `position: fixed; bottom: 0; left: 0; right: 0` — slides up
- **Desktop (>640px):** Floating card near click point; flips above/below based on viewport space

### Touch Target

8px invisible touch slop around each bar via `padding` on the hit area (WCAG 2.5.8 minimum
44px touch target). `cursor: 'pointer'` on all bars.

### Run History Strip

Last 5 runs from the `jobRuns[id]` data already fetched when the detail panel expands.
Dots: ✓ green (`var(--status-healthy)`) / ✗ red (`var(--status-error)`) / `–` grey (no data).
If no run data available, show `no history yet`.

---

## Feature 2: Density Bucket → Brushing & Linking

### Behavior

Click a density bucket in the load strip →
- All Gantt bars **not** overlapping that 30-min window dim to `opacity: 0.15`
- Overlapping bars remain at full opacity
- Clicked bucket gets `outline: 2px solid var(--accent)`
- A label appears above the density strip: `HH:MM – HH:MM · N jobs`
- Click the same bucket again (or anywhere outside the strip) → clear

### Interaction Tokens Used

```
opacity 0.15  = --chart-dim-opacity    (non-selected bars)
opacity 1.0   = --chart-highlight-opacity  (selected bars)
outline: 2px solid var(--accent)  (selected bucket)
```

### Helper: buildBucketJobIds

```js
// Returns array of Set<job.id> — one Set per density bucket
function buildBucketJobIds(jobs, now, windowSecs, bucketCount) {
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

---

## Feature 3: ρ Visual Bar

### Current

Text badge: `load 0.23 — light load`

### New

```
Daily load  [████░░░░░░░░░░] 0.23  safe
            ←─────────────↑────→
                          0.80 (dashed reference line, labeled "warn")
```

**Implementation:**
- Outer container: `position: relative; height: 8px; background: var(--bg-inset); border-radius: var(--radius)`
- Fill div: `width: ${Math.min(rho, 1) * 100}%; height: 100%; border-radius: var(--radius)`
- Fill color: green below 0.60, amber 0.60–0.80, red above 0.80
- Reference line: `position: absolute; left: 80%; top: -4px; bottom: -4px; width: 1px; border-left: 1px dashed var(--status-warning)`
- Reference label: `0.80` in `var(--type-micro)` above the line
- Numeric value + status label remain as text to the right
- "Find best slot" button remains right-aligned below the bar

---

## Feature 4: Colorblind Redundant Encoding in Legend

Color alone is insufficient per §6.3 (affects ~8% of males). Add shape symbols to the legend
swatches as redundant encoding. Bars themselves are too small for per-bar shape encoding;
the legend is the correct fix at this scale.

**Legend update:**

```
color:  ◆ aria   ● telegram   ▲ notion   · other
        │  large model   ○ on schedule  ⚠ running late
bar width = expected run time · hover or tap for details
```

Symbols: `◆` (diamond) for aria, `●` (circle) for telegram, `▲` (triangle) for notion.
These are preattentive shape features distinct from color (Ware 2004, §2.3).

---

## Feature 5: Mobile Full-Screen Gantt Expand

### Behavior

- `⤢` icon button (top-right of the Gantt `t-frame` header)
- Click → `ganttExpanded` state in `Plan.jsx`
- Expanded overlay: `position: fixed; inset: 0; z-index: 50; background: var(--bg-base);
  overflow: auto; padding: 1rem`
- `windowHours`: 6h on ≤640px (makes bars 4× wider), 24h on desktop
- `✕` close button in top-right; ESC key also dismisses
- Density strip, legend, and detail card all work inside the expanded view

---

## Feature 6: Column Header Tooltips

Add `title` to each `<th>` in the recurring jobs table:

| Column | Title copy |
|--------|-----------|
| Name | Job name — set when the recurring job was created |
| Model | Ollama model this job uses (overrides the system default) |
| GPU Mem | Memory profile: light · standard · heavy. Heavy needs ≥16GB VRAM and cannot overlap another heavy job |
| Repeats | How often this job runs — interval (e.g. 4h) or cron expression |
| Priority | 1=highest, 10=lowest. Lower number dequeues first when multiple jobs are waiting |
| Due In | Time until the next scheduled run |
| Est. Time | Estimated run duration based on recent history |
| ✓ | Number of completed successful runs |
| Limit | Max retry attempts before the job is moved to the Dead Letter Queue (DLQ) |
| 📌 | Pinned slot — the rebalancer will not move this job's scheduled time |
| On | Enable or disable this recurring job |

---

## Feature 7: Schedule Health Callout

Show a warning strip **above the job table** when any enabled jobs are running behind schedule.

**Condition:** `runStatus(job.last_run, job.interval_seconds).label === 'running behind'`
for at least one enabled job.

**Display:**
```
⚠  2 jobs running behind schedule — aria-organic-discovery, notion-vector-sync
```

- Job names are clickable — scrolls to and briefly highlights the row
- Strip is absent when all jobs are on schedule (zero noise for the common case)
- Uses existing `runStatus()` helper — no new logic

---

## New GanttChart Props

| Prop | Type | Purpose |
|------|------|---------|
| `onRunJob` | `(id) => void` | "Run now" in detail card → `handleRunNow` in Plan |
| `onScrollToJob` | `(id) => void` | "→ job" in detail card → scroll/highlight row |

Both default to `() => {}` so GanttChart is safe to render without Plan wiring.

---

## Files Changed

| File | Features |
|------|---------|
| `spa/src/components/GanttChart.jsx` | 1 (detail card), 2 (brushing), 4 (legend symbols) |
| `spa/src/pages/Plan.jsx` | 3 (ρ bar), 5 (expand), 6 (column tooltips), 7 (health callout) + new props to GanttChart |

No other files changed. No backend changes.

---

## Acceptance Criteria

1. Tapping a Gantt bar on mobile shows the bottom-sheet detail card
2. Detail card shows description, program, model, start time, last ran, interval, history dots
3. "Run now" in detail card submits the job; "→ job" scrolls to the table row
4. Clicking a density bucket dims unrelated bars; clicking again restores all
5. ρ bar fills proportionally; 0.80 reference line visible; color steps green→amber→red
6. `⤢` expand button opens full-screen overlay at 6h window on mobile
7. All column headers show tooltip on hover
8. Health callout appears when any enabled job is running behind; absent otherwise
9. Legend shows shape symbols alongside color swatches
10. `npm run build` produces no errors after changes
