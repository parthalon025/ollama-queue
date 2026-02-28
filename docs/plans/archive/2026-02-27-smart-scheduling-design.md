# Smart Scheduling Design: Pin, Auto-Suggest, Priority-Aware Rebalance

**Date:** 2026-02-27
**Status:** Approved
**Scope:** ollama-queue scheduler

## Problem

Recurring jobs need three modes beyond simple interval/cron:

1. **Pin** — a job claims a specific time; other jobs must rebalance around it
2. **Auto-suggest** — given a job's priority, the scheduler picks the best available time slot
3. **Priority-aware rebalance** — interval jobs spread across the day avoiding pinned windows
4. **Priority adjustment** — change a job's priority via CLI or dashboard; rebalance immediately reflects it

## Data Model

One new column added to `recurring_jobs`:

```sql
pinned INTEGER DEFAULT 0   -- 1 = protected; rebalancer treats this time as impassable
```

`priority` (1–10) already exists. No other schema changes needed.

## Core Algorithm: Load Map

`Scheduler.load_map(now)` returns a 48-slot array (30-min resolution, 24h window):

- **Pinned cron job**: slots at fire_time ±1 slot (±15 min) get score `999` (hard block — impassable)
- **Non-pinned cron job**: slot at fire_time gets score `11 - priority` (priority 3 → 8, priority 8 → 3)
- **Interval job**: distribute expected fire times across 24h, score each hit slot the same way
- Slot scores accumulate (multiple jobs in same slot sum their scores)

Score meaning: higher = busier/protected. `999` = hard block for pinned.

## Auto-Suggest (`--at auto`)

CLI:
```bash
ollama-queue schedule add --name my-job --at auto --priority 5 -- cmd
```

Algorithm:
1. Build load map
2. Find the 5 lowest-scoring 30-min slots
3. Select best slot for the job's priority (tie → earlier in day wins)
4. Set `cron_expression = "M H * * *"` for the chosen slot
5. Print: `Suggested 03:30 (score=2) — placed at cron='30 3 * * *'`

Standalone suggestion (no job creation):
```bash
ollama-queue schedule suggest --priority 5    # prints top 3 candidates
```

## Rebalance Enforcement

Updated `rebalance()` flow:

1. Build load map from **pinned + cron** jobs only (interval jobs are what's being placed)
2. For each interval job being rebalanced: compute candidate `next_run` offsets within the interval window
3. Skip any candidate offset whose slot has score ≥ 999 (pinned block)
4. If no conflict-free slot exists, place as close to the nearest gap as possible and log a warning
5. Cron jobs continue to be excluded from rebalance (they have pinned or explicitly set times)

## Priority Adjustment

**CLI:**
```bash
ollama-queue schedule edit <name> --priority 3   # also supports --interval, --command, etc.
```
Wraps `db.update_recurring_job()` (already supports `priority` in allowed fields).
Triggers rebalance after update so the new priority is reflected immediately.

**Dashboard:**
- Each job row on the Schedule tab gains:
  - A **★ pin toggle** (click → `PUT /api/schedule/{id}` with `{"pinned": true/false}`)
  - A **priority input** (number 1–10 + save → `PUT /api/schedule/{id}` with `{"priority": N}`)
- Both trigger a rebalance on save

## CLI Surface Summary

| Command | Description |
|---------|-------------|
| `schedule add --pin` | Mark cron job as pinned (mutually exclusive with interval) |
| `schedule add --at auto` | Auto-select optimal time based on priority and load map |
| `schedule suggest [--priority N]` | Print top 3 time suggestions without adding a job |
| `schedule edit <name> [--priority N] [--interval X] [--command CMD]` | Edit a recurring job's fields |
| `schedule list` | Shows `★` for pinned jobs; SCHEDULE column shows cron or interval |

## Dashboard Additions

- Schedule tab: pin toggle (★) + priority input per job row
- New API: `GET /api/schedule/load-map` → returns 48-slot array for visualization
- Optional: render load map as a 24h heatmap row on the Schedule tab

## New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/schedule/load-map` | 48-slot priority-weighted load array |

`PUT /api/schedule/{id}` already exists and accepts `pinned` once added to `RecurringJobUpdate`.

## Implementation Order

1. DB: add `pinned` column + migration
2. `Scheduler.load_map()` method
3. `Scheduler.suggest_time()` method (uses load map)
4. `rebalance()` update (enforce pinned slots)
5. CLI: `schedule suggest`, `schedule edit`, `--pin` flag, `--at auto` mode
6. API: add `pinned` to models, add `/api/schedule/load-map` endpoint
7. Dashboard: pin toggle + priority input on Schedule tab
8. Tests

## Constraints

- Buffer around pinned time: ±15 minutes (1 slot at 30-min resolution)
- Priority scale: 1 (highest) → 10 (lowest), score = `11 - priority`
- Pinned flag only valid on cron jobs (not interval jobs — interval placement is managed by rebalance)
- `suggest` returns times in 30-min increments (HH:00 or HH:30)
