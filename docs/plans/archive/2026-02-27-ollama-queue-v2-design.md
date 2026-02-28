# ollama-queue v2 Design

**Date:** 2026-02-27
**Status:** Approved
**Scope:** Ollama-specific task queue with integrated scheduler, DLQ, retry, stall detection, job tagging, and full 6-tab dashboard

---

## Problem

The current ollama-queue serializes Ollama jobs but relies on 10 systemd timer units as an external scheduler. This creates:

- Split source of truth (schedule in unit files, execution in queue DB)
- No slot coordination — timers can fire simultaneously, causing queue pile-ups
- No retry logic — failed jobs are lost
- No dead letter queue — failures require manual log inspection
- No stall detection — hung jobs block the queue silently
- Dashboard has no visibility into schedule, failures, or retry state

## Solution

Integrate the scheduler directly into the queue. Eliminate all systemd timer units. Add DLQ, retry-with-backoff, stall detection, job tagging, and a full 6-tab dashboard. Leave a resource profile extension point for future non-Ollama job types.

---

## Architecture

### Approach: In-daemon promotion

A new `Scheduler` class handles recurring job promotion and schedule rebalancing. The daemon's `poll_once` gains three new steps before the existing health gate:

```
step 0:  promote_due_recurring_jobs()
step 0b: check_stalled_jobs()
step 0c: check_retryable_jobs()
steps 1-12: existing logic (unchanged)
step 13: on_job_complete() — update next_run, route failures to DLQ
```

No new process. The daemon already polls at 5s cadence with full DB access.

### Files Added

```
ollama_queue/
  scheduler.py       # Scheduler class: promote, rebalance, slot-finder
  dlq.py             # DLQ operations: move, retry, dismiss
tests/
  test_scheduler.py
  test_dlq.py
scripts/
  migrate_timers.py  # One-time: read unit files → recurring_jobs → delete units
```

### Files Modified

```
ollama_queue/
  db.py              # New tables + columns
  daemon.py          # Steps 0, 0b, 0c, 13
  cli.py             # schedule + dlq subcommands
  api.py             # New endpoints for schedule + DLQ
  dashboard/spa/src/ # 6-tab UI
```

---

## Database Schema

### New Tables

```sql
CREATE TABLE recurring_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    command TEXT NOT NULL,
    model TEXT,
    priority INTEGER DEFAULT 5,
    timeout INTEGER DEFAULT 600,
    source TEXT,
    tag TEXT,
    resource_profile TEXT DEFAULT 'ollama',
    interval_seconds INTEGER NOT NULL,
    next_run REAL,
    last_run REAL,
    last_job_id INTEGER REFERENCES jobs(id),
    max_retries INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE schedule_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    -- 'promoted' | 'rebalanced' | 'skipped_duplicate' | 'stall_detected' | 'dlq_moved' | 'retried'
    recurring_job_id INTEGER REFERENCES recurring_jobs(id),
    job_id INTEGER REFERENCES jobs(id),
    details TEXT  -- JSON: old_next_run, new_next_run, reason, affected_jobs, etc.
);

CREATE TABLE dlq (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_job_id INTEGER NOT NULL,
    command TEXT NOT NULL,
    model TEXT,
    source TEXT,
    tag TEXT,
    priority INTEGER,
    resource_profile TEXT DEFAULT 'ollama',
    failure_reason TEXT,
    stdout_tail TEXT,
    stderr_tail TEXT,
    retry_count INTEGER DEFAULT 0,
    moved_at REAL NOT NULL,
    resolved_at REAL,
    resolution TEXT  -- 'retried' | 'dismissed'
);
```

### Additions to `jobs` Table

```sql
ALTER TABLE jobs ADD COLUMN tag TEXT;
ALTER TABLE jobs ADD COLUMN max_retries INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN retry_count INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN retry_after REAL;
ALTER TABLE jobs ADD COLUMN stall_detected_at REAL;
ALTER TABLE jobs ADD COLUMN recurring_job_id INTEGER REFERENCES recurring_jobs(id);
ALTER TABLE jobs ADD COLUMN resource_profile TEXT DEFAULT 'ollama';
```

### Settings Additions

```json
{
  "priority_categories": {
    "critical":   [1, 2],
    "high":       [3, 4],
    "normal":     [5, 6],
    "low":        [7, 8],
    "background": [9, 10]
  },
  "priority_category_colors": {
    "critical":   "#ef4444",
    "high":       "#f97316",
    "normal":     "#3b82f6",
    "low":        "#6b7280",
    "background": "#374151"
  },
  "default_max_retries": 0,
  "retry_backoff_base_seconds": 60,
  "retry_backoff_multiplier": 2.0,
  "stall_multiplier": 2.0,
  "resource_profiles": {
    "ollama": { "check_vram": true,  "check_ram": true,  "check_load": true  },
    "any":    { "check_vram": false, "check_ram": false, "check_load": false }
  }
}
```

---

## Scheduler

### `scheduler.py` — `Scheduler` class

```python
class Scheduler:
    def promote_due_jobs(self, now: float) -> list[int]
        # recurring_jobs where next_run <= now AND no pending/running instance
        # → insert into jobs, log schedule_events('promoted')

    def update_next_run(self, recurring_job_id: int, completed_at: float) -> None
        # next_run = completed_at + interval_seconds
        # log schedule_events('next_run_updated')

    def rebalance(self) -> list[dict]
        # Called on: add, enable, priority change
        # 1. Collect enabled recurring_jobs sorted by priority (asc = highest first)
        # 2. window = min(all interval_seconds)
        # 3. offset_i = window * i / N per priority group
        # 4. Update next_run for all affected jobs
        # 5. Log each change to schedule_events('rebalanced') with old/new values

    def find_slot(self, interval_seconds: int, priority: int) -> float
        # Returns optimal next_run for a new job before rebalance
```

### Rebalance Algorithm

1. Sort all enabled recurring jobs by priority ascending (1 = highest = earliest slot)
2. Compute `window = min(interval_seconds)` across all jobs
3. For each job at index `i` of `N` total: `offset = window * i / N`
4. Jobs with longer intervals get proportional distribution within their own window
5. Write `next_run = now + offset` for each
6. Log to `schedule_events` with `details = {job_name, old_next_run, new_next_run, reason}`

Rebalancer is triggered automatically on: `schedule add`, `schedule enable`, priority category change in Settings.

---

## Dead Letter Queue

### Routing logic (in `daemon.py` `on_job_complete`)

```
if exit_code != 0:
    if job.retry_count < job.max_retries:
        # schedule retry
        retry_after = now + backoff_base * (backoff_multiplier ** retry_count)
        update job: status='pending', retry_count+1, retry_after=retry_after
        log schedule_events('retried')
    else:
        # move to DLQ
        insert into dlq (from job record)
        update job: status='dead'
        log schedule_events('dlq_moved')
```

### DLQ retry

Re-inserts original command as a new `jobs` row with `retry_count=0`. Marks DLQ entry as `resolution='retried'`.

---

## Stall Detection

In `poll_once` step 0b:

```python
for job in running_jobs:
    if job.estimated_duration and job.stall_detected_at is None:
        elapsed = now - job.started_at
        if elapsed > stall_multiplier * job.estimated_duration:
            db.mark_stall(job.id, now)
            log schedule_events('stall_detected', details={elapsed, estimated})
```

Stalled jobs are **flagged, not killed**. Dashboard shows alert banner. User decides whether to cancel.

---

## Retry with Exponential Backoff

```
retry_after = now + backoff_base_seconds * (backoff_multiplier ** retry_count)

Example (base=60s, multiplier=2.0):
  retry 1: now + 60s
  retry 2: now + 120s
  retry 3: now + 240s
```

`check_retryable_jobs()` in step 0c:
```python
jobs where status='pending' AND retry_after IS NOT NULL AND retry_after <= now
→ eligible for dequeue on next cycle
```

---

## CLI

### New `schedule` subcommand

```bash
ollama-queue schedule add \
  --name NAME \
  --interval 6h \          # Xs, Xm, Xh, Xd
  --model MODEL \
  --priority N \
  --tag TAG \
  --max-retries N \
  --source SOURCE \
  -- COMMAND

ollama-queue schedule list           # table: name, interval, next_run, last_run, tag, priority, enabled
ollama-queue schedule enable NAME
ollama-queue schedule disable NAME
ollama-queue schedule remove NAME
ollama-queue schedule rebalance      # manual rebalance trigger
```

### New `dlq` subcommand

```bash
ollama-queue dlq list
ollama-queue dlq retry ID
ollama-queue dlq retry-all
ollama-queue dlq dismiss ID
ollama-queue dlq clear
```

### Updated `submit`

```bash
ollama-queue submit \
  --tag TAG \
  --max-retries N \
  --profile ollama|any \
  ...existing flags...
```

---

## API Endpoints (additions to existing 13)

```
GET  /api/schedule              # list recurring jobs
POST /api/schedule              # add recurring job
PUT  /api/schedule/{id}         # update (enable/disable/priority/interval)
DELETE /api/schedule/{id}       # remove
POST /api/schedule/rebalance    # trigger rebalance
GET  /api/schedule/events       # schedule_events log (paginated)

GET  /api/dlq                   # list DLQ entries
POST /api/dlq/{id}/retry        # retry one
POST /api/dlq/retry-all         # retry all
POST /api/dlq/{id}/dismiss      # dismiss one
DELETE /api/dlq                 # clear all resolved
```

---

## Dashboard — 6 Tabs

### Tab 1: Dashboard (existing + additions)
- Status card, KPI row, resource trends, duration trends, activity heatmap
- **NEW:** Stall alert banner (if any job stalled)
- **NEW:** DLQ badge count on DLQ tab header
- **NEW:** Retry count badge on active job card

### Tab 2: Queue (existing + additions)
- Active job card, pending jobs list with drag-to-reorder
- **NEW:** Tag filter chips above list
- **NEW:** Priority category color bands on job rows (red/orange/blue/grey/dim)
- **NEW:** Retry badge on retrying jobs (`retry 2/3`)
- **NEW:** Stall indicator on stalled job card

### Tab 3: Schedule (NEW)
- Recurring jobs grouped by priority category (drag between categories → triggers rebalance)
- Per-job row: name, tag, interval, next_run countdown, last_run status, enabled toggle
- **24h timeline view:** horizontal bar chart showing when each job fires, collision highlighting
- Rebalance event log: table of recent schedule_events with old/new times and reason
- "Add Recurring Job" slide-out form
- "Rebalance Now" button

### Tab 4: History (existing + additions)
- Filter by: tag, source, model, status, date range
- **NEW:** Retry chain grouping — original + retries displayed as expandable group
- **NEW:** Stall indicator on affected rows
- **NEW:** DLQ indicator on failed rows with link to DLQ tab

### Tab 5: DLQ (NEW)
- Failed jobs table: name, command, source, tag, failure reason, retry count, moved_at
- Expandable row: stdout/stderr tail
- Per-row actions: Retry, Dismiss
- Bulk actions: Retry All, Clear All
- Empty state: "No failed jobs" illustration

### Tab 6: Settings (existing + additions)
- **Existing:** health thresholds, defaults, retention, daemon controls
- **NEW: Priority Categories**
  - 5 rows: name (editable), color picker, priority range (editable min/max)
  - Changes trigger automatic rebalance
- **NEW: Retry Defaults**
  - max_retries, backoff_base_seconds, backoff_multiplier sliders
- **NEW: Stall Detection**
  - stall_multiplier slider (default 2.0×)
- **NEW: Resource Profiles** (read-only display for now, extension point for future)

---

## Migration Script

`scripts/migrate_timers.py`:

1. Read all `*.timer` unit files in `~/.config/systemd/user/`
2. For each Ollama-using timer: parse `ExecStart`, `OnCalendar`, infer `model` from command, infer `priority` from existing schedule position
3. Register via `ollama-queue schedule add` with derived values
4. Run `ollama-queue schedule rebalance` to distribute slots
5. `systemctl --user disable NAME.timer` for each
6. Delete unit files
7. Write summary to `schedule_events` with `event_type='migration'`

---

## Resource Profile Extension Point

Jobs carry `resource_profile` (default: `ollama`). Daemon health evaluation routes by profile:

```python
profile = settings["resource_profiles"][job["resource_profile"]]
if profile["check_vram"] and snap["vram_pct"] > vram_pause_threshold:
    should_pause = True
# etc.
```

Adding a new profile (e.g., `cpu`) requires only a settings JSON update — no code change. Non-Ollama jobs from other projects submit with `--profile any` and bypass all resource gates.

---

## Priority Categories

1-10 integer priority maps to named categories stored in settings:

| Category | Default Range | Color |
|---|---|---|
| Critical | 1-2 | Red `#ef4444` |
| High | 3-4 | Orange `#f97316` |
| Normal | 5-6 | Blue `#3b82f6` |
| Low | 7-8 | Grey `#6b7280` |
| Background | 9-10 | Dim `#374151` |

- Ranges and colors adjustable in Settings tab
- Drag job between categories in Schedule tab → updates priority → triggers rebalance
- Category filter in Queue and History tabs

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scheduler location | In-daemon (step 0) | No new process; daemon already has full DB access at 5s cadence |
| Schedule format | Interval only (Xs/Xm/Xh/Xd) | All 10 current jobs are interval-based; cron is unnecessary complexity |
| Overrun handling | Coalesce + completion-anchored | One instance per job; next_run = completed_at + interval prevents backlog accumulation |
| Preemption | None | Ollama jobs aren't idempotent; killing mid-run wastes work |
| Rebalance trigger | On add/enable/priority change | Automatic; also manual via CLI and dashboard button |
| Stall response | Flag only, no kill | User decides; daemon can't know if a long job is stuck vs just slow |
| DLQ routing | After max_retries exhausted | Retries happen first; DLQ is the final fallback |
| Timer migration | Delete unit files | Clean break; queue is sole source of truth |
| Non-Ollama jobs | resource_profile='any' (extension point) | Leave room without building it out now |

---

## Out of Scope

- Multi-node / load balancer (single Ollama instance)
- Non-Ollama health profiles (extension point only)
- Job dependencies / pipelines
- Webhook notifications
- External queue protocol (Redis, AMQP)
