# ollama-queue

Ollama job queue scheduler with priority, health monitoring, and web dashboard. Serializes all Ollama-using systemd tasks to prevent model loading contention.

**Repo:** https://github.com/parthalon025/ollama-queue (private)

## Structure

```
ollama_queue/
  __init__.py
  cli.py              # Click CLI: submit, status, queue, history, pause, resume, cancel, serve
  db.py                # SQLite schema and CRUD (synchronous sqlite3)
  daemon.py            # Polling loop: health check → dequeue → subprocess → record
  health.py            # System metrics: RAM/VRAM/load/swap/ollama-ps with hysteresis
  estimator.py         # Duration prediction: rolling avg + model-based defaults
  api.py               # FastAPI REST API (12 endpoints) + static SPA serving
  dashboard/
    spa/               # Preact SPA (built separately, served as static)
      src/             # Source: JSX components, signals store, CSS tokens
      dist/            # Production build output (gitignored)
tests/
  test_db.py           # 22 tests
  test_health.py       # 12 tests
  test_daemon.py       # 7 tests
  test_estimator.py    # 5 tests
  test_cli.py          # 13 tests
  test_api.py          # 12 tests
```

## How to Run

```bash
# Activate venv (MUST use python3.12, not python3)
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate

# Run tests (71 total)
pytest

# Start the server (daemon + API + dashboard)
ollama-queue serve --port 7683

# Submit a job
ollama-queue submit --source test --model qwen2.5:7b --priority 3 --timeout 120 -- echo hello

# Check queue
ollama-queue status
ollama-queue queue
ollama-queue history
```

## Deployment

- **Service:** `ollama-queue.service` (user systemd, MemoryMax=512M)
- **Symlink:** `~/.local/bin/ollama-queue` → `.venv/bin/ollama-queue`
- **DB:** `~/.local/share/ollama-queue/queue.db`
- **Tailscale:** `https://justin-linux.tail828051.ts.net/queue/` → `http://127.0.0.1:7683`
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

2 tabs: Dashboard (status, queue, KPIs, resource trends, duration trends, heatmap, history) + Settings (thresholds, defaults, retention, daemon controls).

## Pipeline Verification

After deployment or feature changes, run dual-axis tests (this project is where the pattern was validated):

**Horizontal:** Hit all 12 API endpoints (GET status/queue/history/health/durations/heatmap/settings, PUT settings, POST submit/cancel/pause/resume) + static files (/ui/, bundle.js, bundle.css). Confirm status codes and response shapes.

**Vertical:** Submit one job via CLI, trace through full stack:
```
ollama-queue submit --source e2e-test --model none --priority 1 --timeout 10 -- echo test →
  DB: job row created →
    Daemon: dequeues within 5s poll →
      subprocess: executes, captures stdout →
        DB: status=completed, duration recorded →
          API /history: job visible →
            API /durations: record present →
              API /heatmap: aggregated →
                API /status: KPIs + daily counters updated →
                  Dashboard: renders all sections
```

Full method: `~/Documents/docs/lessons/2026-02-15-horizontal-vertical-pipeline-testing.md`

## Gotchas

- **Use python3.12 for venv** — `python3` is 3.14 on this system and breaks deps
- **SPA dist/ is gitignored** — must `npm run build` after cloning
- **check_same_thread=False** on SQLite — required for FastAPI worker threads, safe with WAL mode
- **httpx** must be installed for API tests — `pip install httpx`
- **Never use `h` or `Fragment` as callback parameter names in JSX files.** esbuild injects `h` as the JSX factory via `preact-shim.js`. Arrow function parameters like `.map(h => (<div>...))` shadow it, causing silent render crashes that cascade through the entire component tree. Use descriptive names (`hr`, `item`, `row`). See `~/Documents/docs/lessons/2026-02-15-esbuild-jsx-factory-shadowing.md`.

## Design Doc

Full design: `~/Documents/docs/plans/2026-02-14-ollama-queue-scheduler-design.md`
Implementation plan: `~/Documents/docs/plans/2026-02-14-ollama-queue-implementation.md`
