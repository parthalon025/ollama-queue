# ollama-queue

Ollama job queue scheduler with priority, health monitoring, and web dashboard.

**Repository:** https://github.com/parthalon025/ollama-queue

## In Plain English

When multiple background tasks all need the same AI model on the same machine, they fight over memory and slow each other down. This tool makes them take turns, running jobs one at a time in order of importance, and gives you a dashboard to see what's running and what's waiting.

## Why This Exists

This workstation runs 10 different systemd timers that all use Ollama for local AI inference — daily intelligence reports, automation suggestions, meta-learning reviews, email analysis, and more. Without coordination, two tasks starting at the same time would each try to load a 5GB model into memory simultaneously, causing swapping, OOM kills, or 10x slower execution. Previously this was managed by carefully spacing timer schedules 45 minutes apart, which was fragile and wasted time. ollama-queue serializes all Ollama work through a single daemon that checks system health before starting each job, runs them by priority, and tracks duration estimates so you know when the queue will clear.

## Structure

```
ollama_queue/
  __init__.py
  cli.py              # Click CLI: submit, status, queue, history, pause, resume, cancel, serve
  db.py                # SQLite schema and CRUD (synchronous sqlite3)
  daemon.py            # Polling loop: health check -> dequeue -> subprocess -> record
  health.py            # System metrics: RAM/VRAM/load/swap/ollama-ps with hysteresis
  estimator.py         # Duration prediction: rolling avg + model-based defaults
  api.py               # FastAPI REST API (12 endpoints) + static SPA serving
  dashboard/
    spa/               # Preact SPA (esbuild-bundled, Tailwind v4, uPlot charts)
      src/             # JSX components, signals store, CSS tokens
      dist/            # Production build output (gitignored)
tests/
  test_db.py           # 22 tests
  test_health.py       # 12 tests
  test_daemon.py       # 7 tests
  test_estimator.py    # 5 tests
  test_cli.py          # 13 tests
  test_api.py          # 12 tests
```

## Architecture

```
systemd timers (10 tasks)
        │
        ▼
  ollama-queue submit --source NAME --model MODEL --priority N -- COMMAND
        │
        ▼
┌─────────────────────────────────────────────────┐
│  Daemon (5s poll loop)                          │
│                                                 │
│  health check ──→ RAM/VRAM/load/swap OK?        │
│       │              │ no: pause (hysteresis)    │
│       │ yes          │                          │
│       ▼              │                          │
│  dequeue by priority                            │
│       │                                         │
│       ▼                                         │
│  subprocess.Popen ──→ capture stdout/stderr     │
│       │                                         │
│       ▼                                         │
│  record result + duration to SQLite             │
└─────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│  FastAPI (12 endpoints)                         │
│  GET  /status /queue /history /health           │
│       /durations /heatmap /settings             │
│  PUT  /settings                                 │
│  POST /submit /cancel /pause /resume            │
│                                                 │
│  Static: /ui/ ──→ Preact SPA                    │
│  (KPIs, resource trends, duration trends,       │
│   heatmap, history, settings)                   │
└─────────────────────────────────────────────────┘
```

## Running

```bash
# Start the server (daemon + API + dashboard)
ollama-queue serve --port 7683

# Submit a job
ollama-queue submit --source test --model qwen2.5:7b --priority 3 --timeout 120 -- echo hello

# Check status
ollama-queue status
ollama-queue queue
ollama-queue history

# Pause/resume processing
ollama-queue pause
ollama-queue resume
```

## Data

- **Queue DB:** `~/.local/share/ollama-queue/queue.db` (SQLite, WAL mode)
- **Symlink:** `~/.local/bin/ollama-queue` -> `.venv/bin/ollama-queue`

## Services

- `ollama-queue.service` (user systemd, MemoryMax=512M)
- **Tailscale Serve:** `https://<your-machine>.<your-tailnet>.ts.net/queue/` -> `http://127.0.0.1:7683`
- **Dashboard:** `/queue/ui/`

## Dependencies

- Python 3.12 (Click, FastAPI, uvicorn, sqlite3)
- Preact 10 + @preact/signals + Tailwind v4 + uPlot (dashboard)
- Ollama (the thing being queued, not a direct dependency)

## Tests

```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
pytest  # 195 tests
```
