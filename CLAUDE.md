# ollama-queue

Ollama job queue scheduler with priority, health monitoring, and web dashboard. Serializes all Ollama-using systemd tasks to prevent model loading contention.

**Repo:** https://github.com/parthalon025/ollama-queue (private)

## Structure

```
ollama_queue/
  __init__.py
  cli.py              # Click CLI: submit, status, queue, history, pause, resume, cancel, serve, schedule, dlq, settings
  db.py               # SQLite schema and CRUD (synchronous sqlite3, threading.RLock)
  daemon.py           # Polling loop: health check → scheduler → dequeue → subprocess → DLQ routing
  health.py           # System metrics: RAM/VRAM/load/swap/ollama-ps with hysteresis
  estimator.py        # Duration prediction: rolling avg + model-based defaults
  scheduler.py        # Recurring job promotion: promote_due_jobs, update_next_run, rebalance
  dlq.py              # DLQManager: handle_failure routes to retry (backoff) or DLQ
  api.py              # FastAPI REST API (23 endpoints including /api/generate proxy) + static SPA serving
  dashboard/
    spa/              # Preact SPA (built separately, served as static)
      src/            # Source: JSX components, signals store, CSS tokens
      dist/           # Production build output (gitignored)
scripts/
  migrate_timers.py            # Migrate 8 of 10 systemd timers to recurring jobs (--dry-run / --execute)
  migrate_dlq_max_retries.py   # Add max_retries column to existing dlq table (idempotent)
tests/
  test_db.py          # 22 tests
  test_health.py      # 12 tests
  test_daemon.py      # 9 tests (incl. scheduler + stall integration)
  test_estimator.py   # 5 tests
  test_cli.py         # 13 tests
  test_api.py         # 12 tests
  test_scheduler.py   # scheduler unit tests
  test_dlq.py         # DLQ unit tests
```

## How to Run

```bash
# Activate venv (MUST use python3.12, not python3)
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate

# Run tests (195 total)
pytest

# Start the server (daemon + API + dashboard)
ollama-queue serve --port 7683

# Submit a job
ollama-queue submit --source test --model qwen2.5:7b --priority 3 --timeout 120 -- echo hello

# Check queue
ollama-queue status
ollama-queue queue
ollama-queue history

# Recurring jobs (v2)
ollama-queue schedule add --name daily-aria --interval 3600 -- aria run
ollama-queue schedule list
ollama-queue schedule remove daily-aria

# DLQ
ollama-queue dlq list
ollama-queue dlq retry <id>
ollama-queue dlq clear
```

## Deployment

- **Service:** `ollama-queue.service` (user systemd, MemoryMax=512M)
- **Symlink:** `~/.local/bin/ollama-queue` → `.venv/bin/ollama-queue`
- **DB:** `~/.local/share/ollama-queue/queue.db`
- **Tailscale:** `https://<your-machine>.<your-tailnet>.ts.net/queue/` → `http://127.0.0.1:7683`
- **Dashboard:** `/queue/ui/` (Preact SPA served by FastAPI)

## Key Decisions

- **Synchronous SQLite** (not aiosqlite) — daemon is single-threaded, FastAPI uses `check_same_thread=False` with WAL mode
- **FastAPI** for REST API, serves static SPA from `dashboard/spa/dist/`
- **Preact 10** + @preact/signals + Tailwind v4 + uPlot — ARIA design language
- **Polling daemon** (5s): health check → evaluate → dequeue by priority → subprocess.Popen → record result
- **Health hysteresis**: pause at high threshold, resume only below lower threshold (prevents flapping)
- **Click CLI** with `--db` option for testability

## Dashboard SPA

```bash
cd ollama_queue/dashboard/spa
npm install
npm run build        # Production
npm run dev          # Watch mode
```

4 tabs: Dashboard (status, queue, KPIs, resource trends, duration trends, heatmap, history) + Schedule (24h timeline, recurring job list) + DLQ (dead letter queue with retry/dismiss) + Settings (thresholds, defaults, retention, daemon controls).

## Pipeline Verification

**Horizontal:** All 23 API endpoints + static files. **Vertical:** `ollama-queue submit` → DB row → daemon dequeue → subprocess → DB completed → API endpoints reflect → dashboard renders. Recurring: `schedule add` → `promote_due_jobs` → queue → run → `update_next_run`. DLQ: job fails max_retries → `move_to_dlq` → `dlq list` reflects. Full method: `projects/CLAUDE.md` § Pipeline Verification.

## Gotchas

- **SPA dist/ is gitignored** — must `npm run build` after cloning
- **check_same_thread=False** on SQLite — required for FastAPI worker threads, safe with WAL mode
- **httpx** must be installed for API tests — `pip install httpx`
- **Proxy endpoint uses sentinel job_id=-1** — `try_claim_for_proxy()` sets `current_job_id=-1` to distinguish proxy claims from real job execution. `release_proxy_claim()` only releases claims with `current_job_id=-1` to avoid accidentally releasing real jobs.
- **Deploy proxy before ARIA restart** — ARIA routes Ollama calls through port 7683. If ollama-queue is down, ARIA's activity predictions and organic naming fail with connection refused.
- esbuild JSX `h` shadowing — see `projects/CLAUDE.md` § Shared Gotchas.
- **`db._lock` is `threading.RLock`** (not `Lock`) — existing callers hold the lock while calling `_connect()`. Do NOT change to `Lock` or nested acquisition will deadlock.
- **DLQ `timeout` column** — added in v2. If restoring from a pre-v2 backup, run `ALTER TABLE dlq ADD COLUMN timeout INTEGER NOT NULL DEFAULT 600` before restarting.
- **DLQ `max_retries` column** — added post-v2. If upgrading a live DB that predates this fix, run `python3 scripts/migrate_dlq_max_retries.py` (idempotent, safe to re-run).
- **migrate_timers.py** skips `telegram-brief-midday` (weekday-only) and `lessons-review` (monthly 14th) — migrated manually as 24h and 30d interval jobs respectively. `telegram-brief-midday` will now run 7 days/week (acceptable until cron scheduling lands). `lessons-review` next run: 2026-03-14 10:00.
- **Deployment sequence:** `cp queue.db queue.db.pre-v2` → stop service → `python3 scripts/migrate_timers.py --execute` → start service → verify `schedule list`
- **v2 schema migration on pre-existing DB** — `initialize()` uses `CREATE TABLE IF NOT EXISTS`, which skips if table exists. A pre-v1 `jobs` table needs 7 manual `ALTER TABLE ADD COLUMN` statements: `tag TEXT`, `max_retries INTEGER DEFAULT 0`, `retry_count INTEGER DEFAULT 0`, `retry_after REAL`, `stall_detected_at REAL`, `recurring_job_id INTEGER REFERENCES recurring_jobs(id)`, `resource_profile TEXT DEFAULT 'ollama'`. Run these before starting the service after upgrade.
- **Recurring job next_run after migration** — rebalancer sets `next_run` relative to now, not to original timer times. After running `migrate_timers.py`, manually set `next_run` values in the DB (use journal history to recover original times: `journalctl --user -u <name>.service`). Scheduled times: aria-full=23:30, morning=07:00, evening=21:00, aria-meta-learn=Mon 01:30, aria-suggest-automations=Sun 04:30, aria-organic-discovery=Sun 05:30, notion-vector-sync=+6h from last run.

## Design Doc

Full design: `~/Documents/docs/plans/2026-02-14-ollama-queue-scheduler-design.md`
Implementation plan: `~/Documents/docs/plans/2026-02-14-ollama-queue-implementation.md`

## Code Factory

## Scope Tags
language:python, framework:preact, domain:ollama

Quality gates for agent-driven development (auto-triggered via superpowers integration in `~/Documents/CLAUDE.md`):
- **Quality checks**: `python3 -m pytest --timeout=120 -x -q; npm run build`
- **PRD artifacts**: `tasks/prd.json`, `tasks/prd-<feature>.md`
- **Progress log**: `progress.txt` (append-only during execution)

## Code Quality
- Lint: `make lint`
- Format: `make format`

## Quality Gates
- Before committing: `/verify`
- Before PRs: `lessons-db scan --target . --baseline HEAD`

## Lessons
- Check before planning: `/check-lessons`
- Capture after bugs: `/capture-lesson`
- Lessons location: `docs/lessons/`

## Local AI Review
- Code review: `ollama-code-review .`

## Semantic Search
- Generate: `bash scripts/generate-embeddings.sh`
- Storage: `.embeddings/` (gitignored)
