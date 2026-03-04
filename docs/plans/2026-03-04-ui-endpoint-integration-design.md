# UI Endpoint Integration Design

**Date:** 2026-03-04
**Status:** Approved
**Scope:** ollama-queue dashboard SPA

---

## Problem

Six backend API endpoints have no frontend surface:

| Endpoint | Gap |
|---|---|
| `POST /api/queue/submit` | Jobs can only be submitted via CLI |
| `POST /api/generate` | Proxy calls visible in queue but no metrics |
| `POST /api/embed` | Same |
| `GET /api/schedule/load-map` | Smart scheduling data computed but never shown |
| `POST /api/schedule` | Recurring jobs can only be created via CLI |
| `POST /api/schedule/jobs/{name}/enable` | Auto-disabled jobs have no re-enable path in UI |

---

## Design Decisions

### `/api/generate` + `/api/embed` → Proxy Mini-Stat

**Decision:** Read-only count derived from the existing `history` signal. No new endpoint calls.

**Implementation:** Filter `history.value` for jobs where `source.startsWith("proxy:")`, count by endpoint suffix (`/api/generate` vs `/api/embed`). Display as a small `.data-mono` stat row below the KPI grid in the Now tab right column:

```
proxy  4 generate · 2 embed  (last 24h)
```

Shown only when count > 0. Updates with the existing 60s history poll. Zero backend work.

**Design rationale (Treisman):** Inline monospace text is preattentive for scanning — no visual hierarchy needed for a secondary stat that is rarely acted upon.

---

### `POST /api/queue/submit` → Submit Job Modal (Now tab)

**Decision:** Floating action button (FAB) bottom-right of Now content area → native `<dialog>` modal.

**FAB:** `.t-status` pill, `var(--accent)` color, 44×44px touch target (iOS HIG minimum).

**Modal:** `<dialog>` styled with `.t-frame data-label="Submit Job"`. Five fields:

| Field | Input | Default |
|---|---|---|
| Command | `<textarea>` 2 rows | — (required) |
| Source | `<input>` | `"dashboard"` (required) |
| Model | `<input>` | blank (optional) |
| Priority | `<input type="number">` 0–10 | `settings.default_priority` |
| Timeout | `<input type="number">` seconds | `settings.default_timeout_seconds` |

**Submit flow:**
1. Validate: Command non-empty, Source non-empty, Priority 0–10 integer
2. POST to `/api/queue/submit`
3. Success: close modal, flash `.t-status-healthy` toast "Job #N queued" for 2s, call `fetchStatus()` immediately
4. Error: show inline error text inside modal, stay open

**Modal accessibility:** Native `<dialog>` via `showModal()` provides built-in focus trapping, Escape key handling, and ARIA `role="dialog"` — WCAG 2.1 AA modal pattern without a library.

---

### `GET /api/schedule/load-map` → Density Strip + Suggested Times (Plan tab)

**Decision:** Two surfaces — a persistent density strip above the Gantt chart, and top-3 suggested times in the Add Recurring Job modal.

#### Density Strip

**Visual:** 48 bars (one per 30-min slot) in a `flex` row, 32px total height. Each bar: fixed height, opacity scaled 0.15→1.0 proportional to max load in any slot. Color: `var(--accent)`.

**Rationale (Cleveland & McGill + Treisman):** Opacity rather than height variation for load encoding. When bars share a fixed baseline, opacity is a preattentive attribute — users perceive "dark = busy" without counting bar heights in a dense 48-element display.

**Layout:**
```
Load  ▁▁▂▄█▅▁▁▁▂▄█▅▁▁  24h
──────────────────────────────
[Gantt chart — existing]
```

X-axis ticks at 00:00, 06:00, 12:00, 18:00, 24:00. Label `"Load"` left, `"24h"` right in `.data-mono` small.

**Data fetch:** `fetchLoadMap()` on Plan tab mount. Not on the 60s cycle — load distribution is stable. Re-fetched after `addRecurringJob()` succeeds.

#### Suggested Times in Add Job Modal

Compute the 3 lightest contiguous 2-slot windows (1h blocks) from load map data. Display as clickable chips above the Schedule field:

```
Suggested: [02:00 ★] [14:30] [19:00]
```

Clicking a chip fills the Pin time field. Lightest slot gets a `★` marker.

---

### `POST /api/schedule` → Add Recurring Job Modal (Plan tab)

**Trigger:** `[+ Add Job]` button in the Plan tab header, alongside the existing Rebalance button.

**Modal:** `<dialog>` styled with `.t-frame data-label="Add Recurring Job"`.

**Essential fields (always shown — Sweller cognitive load ≤7±2):**

| Field | Input | Default |
|---|---|---|
| Name | `<input>` | — (required) |
| Command | `<textarea>` 2 rows | — (required) |
| Schedule | Radio: Interval `[4h]` / Cron `[0 3 * * *]` | Interval |
| Model | `<input>` | blank (optional) |
| Priority | `<input type="number">` | 5 |

**Advanced section (collapsed, `▶ Advanced options`):**
Timeout, Tag, Source, Max Retries, Resource Profile (select: ollama/embed/heavy), Pinned (checkbox), Check Command, Max Runs.

**Submit flow:**
1. Validate: Name and Command non-empty, interval > 0 or cron non-empty
2. POST to `/api/schedule`
3. Success: close modal, call `fetchSchedule()` + `fetchLoadMap()` (load distribution changed)
4. Error: inline error text, stay open

---

### `POST /api/schedule/jobs/{name}/enable` → Re-enable Button (Plan tab)

**Trigger:** Recurring job rows where `enabled: false` AND `outcome_reason` is set (auto-disabled by daemon — e.g., "max_runs reached", "check_command failed").

**Current behavior:** Regular toggle sets `enabled` via `PUT /api/schedule/{rj_id}` but does not clear `outcome_reason`.

**New behavior:**
- Detect `outcome_reason` present → replace toggle with `.t-status-warning` badge showing reason text + `[Re-enable]` button
- `[Re-enable]` calls `POST /api/schedule/jobs/{name}/enable` which clears `outcome_reason` and re-enables
- On success: `fetchSchedule()` re-fetches

**Rationale:** Semantically distinct from a manual disable toggle. The `outcome_reason` is a machine signal ("this job was stopped because X") — clearing it requires an intentional human action, not just flipping a boolean.

---

## Architecture Summary

### store.js additions

```js
// New signal
export const loadMap = signal(null);  // /api/schedule/load-map response

// New functions
export async function fetchLoadMap() { ... }
export async function submitJob(body) { ... }  // POST /api/queue/submit
export async function addRecurringJob(body) { ... }  // POST /api/schedule
export async function enableJobByName(name) { ... }  // POST /api/schedule/jobs/{name}/enable
```

### New components

| File | Used by |
|---|---|
| `components/SubmitJobModal.jsx` | `pages/Now.jsx` |
| `components/LoadMapStrip.jsx` | `pages/Plan.jsx` |
| `components/AddRecurringJobModal.jsx` | `pages/Plan.jsx` |

### Existing file modifications

| File | Change |
|---|---|
| `pages/Now.jsx` | Add FAB + `<SubmitJobModal>`, proxy mini-stat in right column |
| `pages/Plan.jsx` | Add `<LoadMapStrip>`, `[+ Add Job]` button + `<AddRecurringJobModal>`, re-enable logic in job rows |

---

## Research Principles Applied

| Principle | Application |
|---|---|
| Sweller cognitive load ≤7±2 | Add Job form: 5 essential fields, 9 behind Advanced toggle |
| Cleveland & McGill perceptual hierarchy | Load map: opacity encoding over height for dense 48-bar display |
| Treisman preattentive attributes | Opacity variation in density strip; inline mono text for proxy stat |
| Shneiderman progressive disclosure | Essential fields first, Advanced collapsed; proxy stat hidden when zero |
| iOS HIG 44×44px minimum touch target | FAB sized to 44×44px |
| WCAG 2.1 AA modal pattern | Native `<dialog>` with `showModal()` for built-in focus trap + Escape |

---

## Out of Scope

- `/api/generate` and `/api/embed` POST surfaces (machine-to-machine, no dashboard submit UI)
- Streaming support for proxy generate endpoint (non-blocking future enhancement)
- Cron expression validation/preview UI (can be added in a follow-up)
