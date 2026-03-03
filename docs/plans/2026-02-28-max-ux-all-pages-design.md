# Max UX — All Pages Design

**Date:** 2026-02-28
**Scope:** ollama-queue dashboard SPA — ScheduleTab, ModelsTab, DLQTab (minor)
**Status:** Approved

## Goal

Bring every dashboard page to the same quality bar as Dashboard after the 17-improvement pass: correct data, legible on mobile, actionable feedback, no dead ends.

## Pages In Scope

### Dashboard — no changes
Already improved this session (17 of 20 improvements). Only remaining gap is relative timestamp ticking in history rows — low priority, skip.

### DLQ Tab — minor
Already has empty state, expand, retry/dismiss. Add "Retry All" bulk action when item count > 3.

### Settings Tab — no changes
SettingsForm already improved. Thin wrapper is correct.

---

## ScheduleTab — 8 improvements

**1. Horizontal scroll with sticky Name column**
Dense 11-column table overflows viewport on mobile and tablet. Wrap table in `overflow-x: auto` container. Apply `position: sticky; left: 0` to Name `<td>` with solid bg to prevent bleed-through. No column removal — all data is meaningful.

**2. Humanize interval display**
Raw seconds (`3600`) unreadable. Convert: `< 60 → Ns`, `< 3600 → Nm`, `< 86400 → Nh`, `else → Nd`. Show in Schedule column.

**3. Relative "Next Run" with absolute on hover**
Current absolute datetime is hard to parse at a glance. Show relative (`in 47m`, `in 2h`, `overdue`) as primary text. Wrap in `<span title="2026-02-28 14:30:00">` for hover tooltip.

**4. Rebalance button feedback**
Currently fire-and-forget with no visual response. Add: loading spinner while request in flight, success flash (green glow, 1s), error banner if API returns non-2xx.

**5. Rebalance log relative timestamps**
Log shows `14:23:01` — useless after midnight or cross-day. Replace with relative (`3h ago`, `yesterday 14:23`). Use same `relativeTime()` helper pattern from HistoryList.

**6. Overdue badge**
Overdue jobs currently only get text color change (easy to miss). Add amber pill `OVERDUE` badge in Next Run cell. Turn red if > 2× interval past due.

**7. Live debounced search**
No way to find a job by name in a long list. Add search input above table, debounced 300ms, filters by name substring (case-insensitive). Show "No jobs match '...'" empty state.

**8. Run Now confirmation**
"Run Now" fires immediately with no confirmation. Add `confirm()` dialog if job has `estimated_duration > 300` (5 min). Always allow immediate run for short jobs.

---

## ModelsTab — 6 improvements

**1. Live debounced catalog search**
Current search requires button click — friction on every keystroke. Remove button, bind `onInput` with 300ms debounce. Add "No models match '...'" empty state with clear button.

**2. Sortable installed table**
No way to sort by size to find large models. Add click-to-sort on Name and Size columns. Chevron indicator (↑/↓) on active sort column. Default: size descending.

**3. VRAM badge on catalog cards**
Catalog cards show name, description, tags — but not VRAM requirement. Add VRAM badge from catalog metadata if present, `—` if absent. Use same badge style as installed table.

**4. Pull progress: elapsed time**
Progress bar shows `%` only. Add elapsed time (`23s`) next to percentage. Speed (`MB/s`) is not available from Ollama's pull API without streaming — skip.

**5. Remove "Assign to Job" select**
This control assigns a downloaded model to a queued job — awkward and disconnected from the job-submit workflow. Remove entirely. Assignment belongs in `ollama-queue submit --model`.

**6. Empty state for catalog search**
When search returns no results, show centered: `No models match "llama"` + X button to clear. Currently shows empty grid with no explanation.

---

## Architecture

- All changes are pure frontend (JSX/CSS only)
- No new API endpoints required
- `relativeTime()` helper: already exists in HistoryList — duplicate inline (don't import cross-component)
- Debounce: implement inline `useDebounce` hook (3 lines) — no library needed
- Sort state: `useState({ col: 'size', dir: 'desc' })`
- Rebalance loading: `useState(false)` with try/finally

## Files Changed

| File | Changes |
|------|---------|
| `src/pages/ScheduleTab.jsx` | Items 1–8 above |
| `src/pages/ModelsTab.jsx` | Items 1–6 above |
| `src/pages/DLQTab.jsx` | Retry All button |

## Success Criteria

- [ ] `npm run build` succeeds, bundle < 200KB
- [ ] `pytest --timeout=120 -x -q` 226 passed (no backend changes)
- [ ] Schedule table scrolls horizontally on narrow viewport without layout break
- [ ] Interval column shows human-readable text (not raw seconds)
- [ ] Next Run shows relative time
- [ ] Rebalance shows loading/success/error states
- [ ] Catalog search filters on input (no button needed)
- [ ] Installed table sortable by name and size
- [ ] Pull progress shows elapsed time
- [ ] "Assign to Job" removed
