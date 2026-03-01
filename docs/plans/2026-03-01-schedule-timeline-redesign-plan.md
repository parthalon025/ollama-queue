# Schedule Timeline Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign the Schedule tab's Gantt chart to answer "who is doing what and why" — source-colored bars with model chips, load density, heavy-conflict flagging, on-time status indicators, and a renamed Rebalance button with plain-English explanation.

**Architecture:** All changes are in `GanttChart.jsx` (component logic + rendering) and `ScheduleTab.jsx` (button label/tooltip). No API or DB changes — `last_run`, `source`, `model_profile`, and `estimated_duration` are already returned by `GET /api/schedule`. Pure frontend work.

**Tech Stack:** Preact 10, @preact/signals, esbuild JSX (h injected globally — never name .map() callbacks `h`)

---

## Context for Implementer

The current `GanttChart.jsx` lives at:
`ollama_queue/dashboard/spa/src/components/GanttChart.jsx`

It receives `{ jobs, tick, windowHours=24 }` from `ScheduleTab.jsx` line ~308:
```jsx
<GanttChart jobs={jobs} tick={tick} windowHours={24} />
```

`jobs` is an array of recurring job objects. Each has:
- `id`, `name`, `source` (e.g. "aria", "telegram", "notion")
- `model_profile` ("embed" | "ollama" | "heavy")
- `model` (e.g. "qwen2.5:14b") — may be null
- `next_run` (unix timestamp seconds)
- `estimated_duration` (seconds, may be null → default 600)
- `last_run` (unix timestamp seconds, null if never run)
- `enabled` (boolean)

The current chart colors bars by `model_profile`. After this plan, bars will be colored by `source`.

Tests for this are visual (no existing test file for GanttChart). We test the pure logic helpers with unit tests, and verify the visual output by running `npm run build` and checking the dashboard manually.

---

### Task 1: Source color mapping + model chip + enriched tooltip

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`
- Test: `ollama_queue/dashboard/spa/src/components/GanttChart.test.js` (create)

**What this does:** Replace resource-profile color coding with source-system colors (aria=blue, telegram=orange, notion=purple, other=gray). Add a small model chip inside each bar. Update tooltip to show source, model, next run time, last run, duration.

**Step 1: Create the test file**

```bash
touch ollama_queue/dashboard/spa/src/components/GanttChart.test.js
```

```js
// ollama_queue/dashboard/spa/src/components/GanttChart.test.js
import { sourceColor, formatDuration } from './GanttChart.jsx';

describe('sourceColor', () => {
    it('returns accent for aria', () => {
        expect(sourceColor('aria')).toBe('var(--accent)');
    });
    it('returns orange for telegram', () => {
        expect(sourceColor('telegram')).toBe('#f97316');
    });
    it('returns purple for notion', () => {
        expect(sourceColor('notion')).toBe('#a78bfa');
    });
    it('returns tertiary for unknown source', () => {
        expect(sourceColor('unknown')).toBe('var(--text-tertiary)');
    });
    it('is case-insensitive', () => {
        expect(sourceColor('Aria')).toBe('var(--accent)');
    });
    it('handles null/undefined', () => {
        expect(sourceColor(null)).toBe('var(--text-tertiary)');
        expect(sourceColor(undefined)).toBe('var(--text-tertiary)');
    });
});

describe('formatDuration', () => {
    it('formats seconds under 60 as Xs', () => {
        expect(formatDuration(45)).toBe('45s');
    });
    it('formats seconds as Xm for >= 60', () => {
        expect(formatDuration(90)).toBe('1m 30s');
    });
    it('formats 600 as 10m', () => {
        expect(formatDuration(600)).toBe('10m');
    });
    it('handles null with default', () => {
        expect(formatDuration(null)).toBe('~10m');
    });
});
```

**Step 2: Run tests to confirm they fail**

```bash
cd ollama_queue/dashboard/spa
npx jest GanttChart.test.js 2>&1 | tail -20
```

Expected: FAIL — `sourceColor` and `formatDuration` not exported.

**Step 3: Rewrite GanttChart.jsx with source colors, model chip, enriched tooltip**

Replace the entire file:

```jsx
// ollama_queue/dashboard/spa/src/components/GanttChart.jsx
import { h } from 'preact';

// NOTE: all .map() callbacks use descriptive names (job, slot, laneIdx) — never 'h'
// as that shadows the JSX factory esbuild injects.

// --- Pure helpers (exported for testing) ---

export const SOURCE_COLORS = {
    aria:     'var(--accent)',
    telegram: '#f97316',
    notion:   '#a78bfa',
};

export function sourceColor(source) {
    if (!source) return 'var(--text-tertiary)';
    return SOURCE_COLORS[source.toLowerCase()] ?? 'var(--text-tertiary)';
}

export function formatDuration(seconds) {
    if (seconds == null) return '~10m';
    const s = Math.round(seconds);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return rem === 0 ? `${m}m` : `${m}m ${rem}s`;
}

function assignLanes(jobs) {
    const sorted = [...jobs].sort((a, b) => a.next_run - b.next_run);
    const laneEnds = [];
    return sorted.map(job => {
        const start = job.next_run;
        const end = start + (job.estimated_duration || 600);
        let laneIdx = laneEnds.findIndex(laneEnd => laneEnd <= start);
        if (laneIdx === -1) laneIdx = laneEnds.length;
        laneEnds[laneIdx] = end;
        return { ...job, _lane: laneIdx, _end: end };
    });
}

function buildTooltip(job, isConcurrent) {
    const nextRunStr = new Date(job.next_run * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const lastRunStr = job.last_run
        ? new Date(job.last_run * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
        : 'never';
    const modelStr = job.model || job.model_profile || 'ollama';
    const parts = [
        `${job.name}`,
        `via ${job.source || 'unknown'} · ${modelStr}`,
        `est. ${formatDuration(job.estimated_duration)} · next: ${nextRunStr}`,
        `last run: ${lastRunStr}`,
    ];
    if (isConcurrent) parts.push('⟡ runs concurrently');
    return parts.join('\n');
}

export function GanttChart({ jobs, tick, windowHours = 24 }) {
    void tick;
    const now = Date.now() / 1000;
    const windowSecs = windowHours * 3600;
    const windowEnd = now + windowSecs;

    const laneJobs = assignLanes(
        jobs.filter(job => job.next_run < windowEnd)
    );
    const laneCount = laneJobs.reduce((max, job) => Math.max(max, job._lane + 1), 1);
    const laneHeight = 44;
    const chartHeight = laneCount * laneHeight + 8;

    return (
        <div style={{ position: 'relative', width: '100%' }}>
            {/* Time axis labels */}
            <div style={{ display: 'flex', justifyContent: 'space-between',
                          fontSize: 'var(--type-label)', color: 'var(--text-tertiary)',
                          fontFamily: 'var(--font-mono)', marginBottom: '0.25rem' }}>
                {[0, 6, 12, 18, 24].map(offset => {
                    const t = new Date((now + offset * 3600) * 1000);
                    return (
                        <span key={offset}>
                            {offset === 0 ? 'now' : t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                    );
                })}
            </div>

            {/* Chart area */}
            <div style={{
                position: 'relative',
                height: chartHeight,
                background: 'var(--bg-inset)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius)',
                overflow: 'hidden',
            }}>
                {/* Lane dividers */}
                {Array.from({ length: laneCount }, (_, laneIdx) => (
                    <div key={laneIdx} style={{
                        position: 'absolute',
                        top: laneIdx * laneHeight,
                        left: 0, right: 0,
                        height: laneHeight,
                        borderBottom: laneIdx < laneCount - 1
                            ? '1px solid var(--border-subtle)' : 'none',
                    }} />
                ))}

                {/* Job blocks */}
                {laneJobs.map(job => {
                    const startOffset = Math.max(0, job.next_run - now);
                    const duration = job.estimated_duration || 600;
                    const leftPct = (startOffset / windowSecs) * 100;
                    const widthPct = Math.max(0.5, (duration / windowSecs) * 100);
                    const color = sourceColor(job.source);
                    const isHeavy = job.model_profile === 'heavy';
                    const isConcurrent = job._lane > 0;
                    const modelLabel = job.model
                        ? job.model.split(':')[0]   // trim tag: "qwen2.5:14b" → "qwen2.5"
                        : (job.model_profile || null);
                    const barWidth = Math.min(widthPct, 100 - leftPct);
                    const showChip = barWidth > 8; // only show chip if bar is wide enough

                    return (
                        <div
                            key={job.id}
                            title={buildTooltip(job, isConcurrent)}
                            style={{
                                position: 'absolute',
                                left: `${Math.min(leftPct, 99.5)}%`,
                                width: `${barWidth}%`,
                                top: job._lane * laneHeight + 4,
                                height: laneHeight - 8,
                                background: color,
                                opacity: 0.85,
                                borderRadius: 'var(--radius)',
                                borderLeft: isHeavy ? '3px solid var(--status-warning)' : undefined,
                                overflow: 'hidden',
                                display: 'flex',
                                alignItems: 'center',
                                paddingLeft: isHeavy ? '0.3rem' : '0.4rem',
                                gap: '0.3rem',
                                cursor: 'default',
                            }}
                        >
                            <span style={{
                                fontFamily: 'var(--font-mono)',
                                fontSize: 'var(--type-label)',
                                color: 'var(--accent-text)',
                                fontWeight: 600,
                                whiteSpace: 'nowrap',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                flexShrink: 1,
                            }}>
                                {isConcurrent && '⟡ '}{job.name}
                            </span>
                            {showChip && modelLabel && (
                                <span style={{
                                    fontFamily: 'var(--font-mono)',
                                    fontSize: 'var(--type-micro)',
                                    color: 'rgba(255,255,255,0.7)',
                                    background: 'rgba(0,0,0,0.25)',
                                    borderRadius: 3,
                                    padding: '1px 4px',
                                    whiteSpace: 'nowrap',
                                    flexShrink: 0,
                                }}>
                                    {modelLabel}
                                </span>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
```

**Step 4: Run tests**

```bash
cd ollama_queue/dashboard/spa
npx jest GanttChart.test.js 2>&1 | tail -20
```

Expected: All tests PASS.

**Step 5: Build and visually verify**

```bash
npm run build 2>&1 | tail -4
```

Expected: Clean build, no errors. Open `/queue/ui/` → Schedule tab → bars should now be blue (aria), orange (telegram), purple (notion). Heavy jobs have amber left border. Model chip visible on wide bars.

**Step 6: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/GanttChart.jsx \
        ollama_queue/dashboard/spa/src/components/GanttChart.test.js
git commit -m "feat(gantt): source-color bars, model chip, enriched tooltip"
```

---

### Task 2: Load density strip

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`
- Test: `ollama_queue/dashboard/spa/src/components/GanttChart.test.js` (add tests)

**What this does:** Adds a thin 16px heat strip above the chart. Divides the 24h window into 24 hourly buckets, counts how many jobs are active in each bucket (job's time range overlaps with the hour), and colors each bucket by density.

**Step 1: Add unit test for buildDensityBuckets**

Append to `GanttChart.test.js`:

```js
import { buildDensityBuckets } from './GanttChart.jsx';

describe('buildDensityBuckets', () => {
    const now = 1000000; // fixed reference time
    const windowSecs = 24 * 3600;

    it('returns 24 buckets', () => {
        expect(buildDensityBuckets([], now, windowSecs)).toHaveLength(24);
    });

    it('counts a job that spans the first bucket', () => {
        const jobs = [{ next_run: now, estimated_duration: 3600 }];
        const buckets = buildDensityBuckets(jobs, now, windowSecs);
        expect(buckets[0]).toBe(1);
        expect(buckets[1]).toBe(0);
    });

    it('counts a job spanning multiple buckets', () => {
        const jobs = [{ next_run: now, estimated_duration: 7200 }]; // 2 hours
        const buckets = buildDensityBuckets(jobs, now, windowSecs);
        expect(buckets[0]).toBe(1);
        expect(buckets[1]).toBe(1);
        expect(buckets[2]).toBe(0);
    });

    it('counts two concurrent jobs in same bucket', () => {
        const jobs = [
            { next_run: now, estimated_duration: 1800 },
            { next_run: now + 900, estimated_duration: 1800 },
        ];
        const buckets = buildDensityBuckets(jobs, now, windowSecs);
        expect(buckets[0]).toBe(2);
    });
});
```

**Step 2: Run tests to confirm they fail**

```bash
cd ollama_queue/dashboard/spa
npx jest GanttChart.test.js 2>&1 | tail -10
```

Expected: FAIL — `buildDensityBuckets` not exported.

**Step 3: Add buildDensityBuckets to GanttChart.jsx and render the strip**

Add this function after `buildTooltip` (before the `GanttChart` component):

```js
export function buildDensityBuckets(jobs, now, windowSecs) {
    const bucketCount = 24;
    const bucketSecs = windowSecs / bucketCount;
    const buckets = Array(bucketCount).fill(0);
    for (const job of jobs) {
        const jobStart = job.next_run;
        const jobEnd = jobStart + (job.estimated_duration || 600);
        for (let i = 0; i < bucketCount; i++) {
            const bucketStart = now + i * bucketSecs;
            const bucketEnd = bucketStart + bucketSecs;
            if (jobStart < bucketEnd && jobEnd > bucketStart) {
                buckets[i]++;
            }
        }
    }
    return buckets;
}

function densityColor(count) {
    if (count === 0) return 'transparent';
    if (count === 1) return 'rgba(var(--accent-rgb, 99,179,237), 0.2)';
    if (count === 2) return 'rgba(var(--accent-rgb, 99,179,237), 0.5)';
    return 'rgba(var(--accent-rgb, 99,179,237), 0.85)';
}
```

Add the strip inside the `GanttChart` render, just before the time axis labels `<div>`:

```jsx
{/* Load density strip */}
{(() => {
    const buckets = buildDensityBuckets(
        jobs.filter(job => job.next_run < windowEnd),
        now,
        windowSecs
    );
    return (
        <div style={{
            display: 'flex',
            height: 10,
            borderRadius: 'var(--radius)',
            overflow: 'hidden',
            marginBottom: '0.2rem',
            border: '1px solid var(--border-subtle)',
        }}
            title="Job density: darker = more jobs active in that hour"
        >
            {buckets.map((count, bucketIdx) => (
                <div
                    key={bucketIdx}
                    style={{
                        flex: 1,
                        background: count === 0
                            ? 'var(--bg-inset)'
                            : count === 1
                                ? 'rgba(99,179,237,0.25)'
                                : count === 2
                                    ? 'rgba(99,179,237,0.55)'
                                    : 'rgba(99,179,237,0.9)',
                        borderRight: bucketIdx < 23 ? '1px solid var(--border-subtle)' : 'none',
                    }}
                    title={count > 0 ? `${count} job${count > 1 ? 's' : ''} active` : undefined}
                />
            ))}
        </div>
    );
})()}
```

**Step 4: Run tests**

```bash
cd ollama_queue/dashboard/spa
npx jest GanttChart.test.js 2>&1 | tail -10
```

Expected: All tests PASS.

**Step 5: Build and verify**

```bash
npm run build 2>&1 | tail -4
```

Visually: a thin heat strip appears above the time axis. Darker segments = hours with more overlapping jobs.

**Step 6: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/GanttChart.jsx \
        ollama_queue/dashboard/spa/src/components/GanttChart.test.js
git commit -m "feat(gantt): load density strip above chart"
```

---

### Task 3: Heavy conflict detection + visual flag

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`
- Test: `ollama_queue/dashboard/spa/src/components/GanttChart.test.js` (add tests)

**What this does:** Detects when two heavy-profile jobs overlap in time. Flags both bars with a red outline and renders a small warning badge between them.

**Step 1: Add unit test for findHeavyConflicts**

Append to `GanttChart.test.js`:

```js
import { findHeavyConflicts } from './GanttChart.jsx';

describe('findHeavyConflicts', () => {
    it('returns empty for no jobs', () => {
        expect(findHeavyConflicts([])).toEqual(new Set());
    });

    it('returns empty when heavy jobs do not overlap', () => {
        const jobs = [
            { id: 1, model_profile: 'heavy', next_run: 1000, estimated_duration: 600 },
            { id: 2, model_profile: 'heavy', next_run: 2000, estimated_duration: 600 },
        ];
        expect(findHeavyConflicts(jobs).size).toBe(0);
    });

    it('flags both jobs when two heavy jobs overlap', () => {
        const jobs = [
            { id: 1, model_profile: 'heavy', next_run: 1000, estimated_duration: 600 },
            { id: 2, model_profile: 'heavy', next_run: 1300, estimated_duration: 600 },
        ];
        const conflicts = findHeavyConflicts(jobs);
        expect(conflicts.has(1)).toBe(true);
        expect(conflicts.has(2)).toBe(true);
    });

    it('does not flag non-heavy jobs that overlap', () => {
        const jobs = [
            { id: 1, model_profile: 'ollama', next_run: 1000, estimated_duration: 600 },
            { id: 2, model_profile: 'ollama', next_run: 1300, estimated_duration: 600 },
        ];
        expect(findHeavyConflicts(jobs).size).toBe(0);
    });

    it('does not flag a single heavy job', () => {
        const jobs = [
            { id: 1, model_profile: 'heavy', next_run: 1000, estimated_duration: 600 },
        ];
        expect(findHeavyConflicts(jobs).size).toBe(0);
    });
});
```

**Step 2: Run tests to confirm they fail**

```bash
cd ollama_queue/dashboard/spa
npx jest GanttChart.test.js 2>&1 | tail -10
```

Expected: FAIL — `findHeavyConflicts` not exported.

**Step 3: Add findHeavyConflicts to GanttChart.jsx and apply conflict styling**

Add after `buildDensityBuckets`:

```js
export function findHeavyConflicts(jobs) {
    const heavy = jobs.filter(j => j.model_profile === 'heavy');
    const conflictIds = new Set();
    for (let i = 0; i < heavy.length; i++) {
        for (let j = i + 1; j < heavy.length; j++) {
            const a = heavy[i], b = heavy[j];
            const aEnd = a.next_run + (a.estimated_duration || 600);
            const bEnd = b.next_run + (b.estimated_duration || 600);
            if (a.next_run < bEnd && b.next_run < aEnd) {
                conflictIds.add(a.id);
                conflictIds.add(b.id);
            }
        }
    }
    return conflictIds;
}
```

In the `GanttChart` component, compute conflicts before render:

```js
const conflictIds = findHeavyConflicts(laneJobs);
```

In the job block `div`, add conflict styling:

```jsx
outline: conflictIds.has(job.id) ? '2px solid var(--status-error)' : undefined,
outlineOffset: conflictIds.has(job.id) ? '-2px' : undefined,
```

After the job blocks, add the conflict badge(s) — one per conflicting heavy job pair:

```jsx
{/* Heavy conflict badges */}
{conflictIds.size > 0 && (() => {
    const conflictingJobs = laneJobs.filter(j => conflictIds.has(j.id));
    // Find overlap midpoints and render one badge per conflicting pair
    const heavy = conflictingJobs.filter(j => j.model_profile === 'heavy');
    const badges = [];
    for (let i = 0; i < heavy.length; i++) {
        for (let j2 = i + 1; j2 < heavy.length; j2++) {
            const a = heavy[i], b = heavy[j2];
            const aEnd = a.next_run + (a.estimated_duration || 600);
            const bEnd = b.next_run + (b.estimated_duration || 600);
            if (a.next_run < bEnd && b.next_run < aEnd) {
                const midStart = Math.max(a.next_run, b.next_run);
                const midEnd = Math.min(aEnd, bEnd);
                const midPoint = (midStart + midEnd) / 2;
                const leftPct = ((midPoint - now) / windowSecs) * 100;
                badges.push(
                    <div key={`conflict-${a.id}-${b.id}`}
                         title="Two heavy models overlap — one will queue behind the other"
                         style={{
                             position: 'absolute',
                             left: `${Math.max(1, Math.min(leftPct - 4, 90))}%`,
                             top: Math.max(a._lane, b._lane) * laneHeight + laneHeight / 4,
                             background: 'var(--status-error)',
                             color: '#fff',
                             fontSize: 'var(--type-micro)',
                             fontFamily: 'var(--font-mono)',
                             padding: '1px 5px',
                             borderRadius: 3,
                             pointerEvents: 'none',
                             zIndex: 10,
                             whiteSpace: 'nowrap',
                         }}>
                        ⚠ conflict
                    </div>
                );
            }
        }
    }
    return badges;
})()}
```

**Step 4: Run tests**

```bash
cd ollama_queue/dashboard/spa
npx jest GanttChart.test.js 2>&1 | tail -10
```

Expected: All tests PASS.

**Step 5: Build**

```bash
npm run build 2>&1 | tail -4
```

**Step 6: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/GanttChart.jsx \
        ollama_queue/dashboard/spa/src/components/GanttChart.test.js
git commit -m "feat(gantt): heavy conflict detection — red outline + warning badge"
```

---

### Task 4: On-time status dot per job bar

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`
- Test: `ollama_queue/dashboard/spa/src/components/GanttChart.test.js` (add tests)

**What this does:** Renders a small 7px colored dot at the right edge of each job bar. Green = ran within 5% of its interval (on time). Amber = ran late (>5% drift). Gray = never run. Tooltip on the dot shows the actual last run time and drift.

The dot is positioned at the RIGHT edge of the bar (not the positional last_run time — that would be off-screen since `last_run` is in the past). It's a status indicator, not a positional one.

**Step 1: Add unit tests for runStatus**

Append to `GanttChart.test.js`:

```js
import { runStatus } from './GanttChart.jsx';

describe('runStatus', () => {
    it('returns never for null last_run', () => {
        expect(runStatus(null, 3600)).toEqual({ label: 'never', color: 'var(--text-tertiary)' });
    });

    it('returns on-time when drift < 5% of interval', () => {
        const interval = 3600;
        const lastRun = Date.now() / 1000 - interval - interval * 0.03; // 3% late
        expect(runStatus(lastRun, interval).label).toBe('on time');
    });

    it('returns late when drift >= 5% of interval', () => {
        const interval = 3600;
        const lastRun = Date.now() / 1000 - interval - interval * 0.10; // 10% late
        expect(runStatus(lastRun, interval).label).toBe('late');
    });
});
```

**Step 2: Run tests to confirm they fail**

```bash
cd ollama_queue/dashboard/spa
npx jest GanttChart.test.js 2>&1 | tail -10
```

Expected: FAIL — `runStatus` not exported.

**Step 3: Add runStatus helper and dot to GanttChart.jsx**

Add after `findHeavyConflicts`:

```js
export function runStatus(lastRun, intervalSeconds) {
    if (!lastRun) return { label: 'never', color: 'var(--text-tertiary)' };
    const now = Date.now() / 1000;
    const elapsed = now - lastRun;
    const drift = elapsed - (intervalSeconds || 0);
    const threshold = (intervalSeconds || 3600) * 0.05;
    if (drift <= threshold) return { label: 'on time', color: 'var(--status-healthy)' };
    return { label: 'late', color: 'var(--status-warning)' };
}
```

Inside the job block render, after the model chip span, add the status dot. The dot sits at the right edge of the bar via absolute positioning:

```jsx
{/* On-time status dot — right edge of bar */}
{(() => {
    const { label, color } = runStatus(job.last_run, job.interval_seconds);
    const lastRunStr = job.last_run
        ? new Date(job.last_run * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
        : 'never';
    return (
        <span
            title={`Last run: ${lastRunStr} (${label})`}
            style={{
                position: 'absolute',
                right: 4,
                top: '50%',
                transform: 'translateY(-50%)',
                width: 7,
                height: 7,
                borderRadius: '50%',
                background: color,
                border: '1px solid rgba(0,0,0,0.3)',
                flexShrink: 0,
            }}
        />
    );
})()}
```

Note: the bar `div` already has `overflow: hidden` — change it to `overflow: visible` and rely on `borderRadius` + clipping from the chart container instead. Or keep `overflow: hidden` and add the dot outside the bar as a sibling positioned absolute within the chart. Use the simpler approach: keep `overflow: hidden` on the bar but add `position: relative` so the absolute dot still clips correctly within the bar bounds (rightmost 11px of the bar).

**Step 4: Run tests**

```bash
cd ollama_queue/dashboard/spa
npx jest GanttChart.test.js 2>&1 | tail -10
```

Expected: All tests PASS.

**Step 5: Build**

```bash
npm run build 2>&1 | tail -4
```

Visually: each job bar has a small colored dot at its right edge. Green = ran on time, amber = ran late, gray = never run.

**Step 6: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/GanttChart.jsx \
        ollama_queue/dashboard/spa/src/components/GanttChart.test.js
git commit -m "feat(gantt): on-time status dot per job bar (green/amber/gray)"
```

---

### Task 5: Rebalance button rename + explanation tooltip

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx`

**What this does:** Renames the "Rebalance Now" button to "Spread run times" and adds a small ⓘ icon that shows a plain-English tooltip explaining what it does and that it's manual-only.

**Step 1: Find the button in ScheduleTab.jsx**

```bash
grep -n "Rebalance\|rebalance" ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
```

Note the line numbers for the button element.

**Step 2: Update the button label and add info tooltip**

Find this in `ScheduleTab.jsx` (around line 255-263):

```jsx
{rebalancing ? '…' : rebalanceFlash === 'ok' ? '✓ Done' : 'Rebalance Now'}
```

Replace with:

```jsx
{rebalancing ? '…' : rebalanceFlash === 'ok' ? '✓ Done' : 'Spread run times'}
```

Find the button's surrounding `<div>` or the button itself and add an info icon as a sibling span. The info icon is a plain `title`-tooltip span placed right after the button:

```jsx
<span
    title="Adjusts next-run times so jobs don't pile up in the same hour. Run once after adding or changing jobs. Does not change intervals or priorities."
    style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 'var(--type-label)',
        color: 'var(--text-tertiary)',
        cursor: 'help',
        userSelect: 'none',
    }}
>
    ⓘ
</span>
```

The ⓘ and button should sit in a flex row. Wrap both in a `<div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>` if not already.

**Step 3: Build and verify**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -4
```

Visually: button now reads "Spread run times". Hovering ⓘ shows the tooltip.

**Step 4: Commit**

```bash
cd /home/justin/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
git commit -m "feat(schedule): rename Rebalance button, add plain-English explanation tooltip"
```

---

### Task 6: Final verification

**Step 1: Run full test suite**

```bash
cd /home/justin/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q 2>&1 | tail -10
```

Expected: All tests pass (195+).

**Step 2: Run SPA build one final time**

```bash
cd ollama_queue/dashboard/spa
npm run build 2>&1 | tail -4
```

Expected: Clean build.

**Step 3: Restart service and smoke test**

```bash
systemctl --user restart ollama-queue.service
systemctl --user is-active ollama-queue.service
```

Open `/queue/ui/` → Schedule tab. Verify:
- Bars colored by source (blue/orange/purple/gray)
- Model chip visible on wide bars
- Load density strip above the time axis
- Heavy jobs have amber left border
- On-time status dot at right edge of each bar
- Button reads "Spread run times" with ⓘ tooltip

**Step 4: Push**

```bash
git push
```
