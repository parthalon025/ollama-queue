# Schedule Timeline Redesign — Design Doc

**Date:** 2026-03-01
**Status:** Approved

---

## Goal

Replace the current resource-profile-colored Gantt with a chart that answers **who is doing what and why** at a glance — and correctly represents that overlap is normal (only heavy + heavy is a conflict).

---

## Mental Model

| Dimension | Mapped To | Visual Element |
|-----------|-----------|----------------|
| **Who** | Model name (qwen2.5, llama3, etc.) | Pill chip inside bar |
| **What** | Job name (aria-morning, notion-sync) | Bar label |
| **Why** | Source system (aria, telegram, notion) | Bar color |

---

## Visual Design

### Color Palette (replaces resource-profile colors)

| Source | Color Token |
|--------|-------------|
| aria | `var(--accent)` (blue) |
| telegram | `#f97316` (orange) |
| notion | `#a78bfa` (purple) |
| other / unknown | `var(--text-tertiary)` (gray) |

### Bar Anatomy

```
┌──────────────────────────────┐
│ aria-morning  [qwen2.5]      │  ← label + model chip
└──────────────────────────────┘
     ↑
     3px left border in amber if heavy model
```

- When bar is too narrow to show chip, chip hides; label truncates with ellipsis
- Heavy jobs: `border-left: 3px solid var(--status-warning)`

### Heavy + Heavy Conflict

When two heavy-profile bars overlap on any lane:
- Both bars get a red outline: `outline: 1px solid var(--status-error)`
- A small badge renders between them: `⚠ conflict — one will queue`
- Tooltip on the badge: "Two heavy models are scheduled at the same time. The second one will wait in queue until the first finishes."

### Standard Overlap

No change — existing `⟡ concurrent` badge on lane > 0 bars is sufficient. Overlap is normal and expected.

### Load Density Strip

- Thin horizontal strip (16px tall) pinned to the top of the chart container, above the lane rows
- Divided into 24 hourly buckets
- Each bucket colored by job count:
  - 0 jobs → transparent
  - 1 job → `rgba(accent, 0.2)`
  - 2 jobs → `rgba(accent, 0.5)`
  - 3+ jobs → `rgba(accent, 0.85)` (hot)
- No labels — purely a density heatmap

### History Tick

- Small dot (6px circle) rendered below each bar, positioned at the time the job last actually ran
- Color: green if on time (within ±5% of interval), amber if late (>5% drift), gray if no history
- If never run: no dot
- Tooltip on dot: "Last ran [time] — [Xm early / Xm late / on time]"

### Rebalance Button

- Rename label: **"Spread run times"**
- Add a `ⓘ` icon next to the label that shows tooltip on hover:
  *"Adjusts next-run times so jobs don't pile up in the same hour. Run once after adding or changing jobs. Does not change intervals or priorities."*
- Behavior: unchanged (manual only, calls `POST /schedule/rebalance`)

---

## Data Requirements

All data is already available in the recurring jobs response. No new API endpoints needed.

| Field needed | Source | Already present? |
|---|---|---|
| `model` or `model_profile` | recurring_jobs | ✅ `model_profile` |
| `source` | recurring_jobs | ✅ |
| `last_run_at` | recurring_jobs | Check — may need to add |
| `estimated_duration` | recurring_jobs | ✅ |
| `next_run` | recurring_jobs | ✅ |

**`last_run_at`**: Check if the recurring_jobs API response includes this. If not, add it from the `jobs` table (last completed job matching `recurring_job_id`).

---

## Source → Color Mapping

Derive source from `job.source` field (already populated). Map in a helper:

```js
const SOURCE_COLORS = {
  aria: 'var(--accent)',
  telegram: '#f97316',
  notion: '#a78bfa',
};
function sourceColor(source) {
  return SOURCE_COLORS[source?.toLowerCase()] ?? 'var(--text-tertiary)';
}
```

---

## Heavy Conflict Detection

In the lane-assignment pass (already runs in `GanttChart.jsx`), after assigning lanes, do a secondary pass:

```js
function findHeavyConflicts(jobs) {
  const heavy = jobs.filter(j => j.model_profile === 'heavy');
  const conflicts = [];
  for (let i = 0; i < heavy.length; i++) {
    for (let j = i + 1; j < heavy.length; j++) {
      const a = heavy[i], b = heavy[j];
      const aEnd = a.next_run + (a.estimated_duration || 0);
      const bEnd = b.next_run + (b.estimated_duration || 0);
      if (a.next_run < bEnd && b.next_run < aEnd) {
        conflicts.push([a.id, b.id]);
      }
    }
  }
  return conflicts;
}
```

Jobs in a conflict pair get a `_conflict: true` flag → red outline + badge.

---

## Files to Change

| File | Change |
|------|--------|
| `GanttChart.jsx` | Color by source, model chip in bar, heavy border, conflict detection + badge, history tick, load density strip |
| `ScheduleTab.jsx` | Pass `last_run_at` data if added; rename Rebalance button |
| `api.py` | Add `last_run_at` to recurring jobs response if missing |
| `store.js` | No changes expected |

---

## Out of Scope

- Changing the lane-stacking algorithm (keep greedy interval scheduling)
- Adding interactivity beyond existing tooltip/expand
- Historical run log view (that's the History tab)
- Auto-rebalance trigger
