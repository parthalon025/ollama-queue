# ollama-queue

Ollama job queue scheduler with priority, health monitoring, and web dashboard.

## Structure

```
ollama_queue/
  __init__.py
  cli.py              # Click CLI entry point
  config.py            # Configuration and defaults
  db.py                # SQLite schema and access (aiosqlite)
  scheduler.py         # Polling daemon: dequeue, dispatch, retry
  models.py            # Job, ScheduleEntry, HealthStatus dataclasses
  health.py            # Ollama health checks and model inventory
  api/
    __init__.py
    app.py             # FastAPI application factory
    routes.py          # REST endpoints (jobs, schedule, health)
    ws.py              # WebSocket for live dashboard updates
  dashboard/
    __init__.py
    spa/               # Preact SPA (built separately, served as static)
      dist/            # Production build output (gitignored)
tests/
  __init__.py
  test_db.py
  test_scheduler.py
  test_api.py
  test_health.py
  conftest.py
docs/
  plans/
```

## How to Run

```bash
# Activate venv (MUST use python3.12, not python3)
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate

# Run tests
pytest

# Start the server (API + scheduler + dashboard)
ollama-queue serve

# Submit a job
ollama-queue submit --model qwen2.5:7b --prompt "Summarize this" --priority 5

# Check queue status
ollama-queue status
```

## Key Decisions

- **SQLite** for job queue and schedule state (aiosqlite, single-file, no external DB)
- **FastAPI** for the REST API and WebSocket layer
- **Preact SPA** for the web dashboard (lightweight, built separately into `spa/dist/`)
- **Polling daemon** pattern: scheduler loop dequeues jobs by priority, dispatches to Ollama, handles retries
- **Click** for the CLI interface

## Gotchas

- **Use python3.12 for venv** (not `python3` which is 3.14 on this system and breaks some deps). Always: `/usr/bin/python3.12 -m venv .venv`
- Entry point `ollama_queue.cli:main` won't work until `cli.py` is created (Task 6)
- Dashboard SPA build output (`spa/dist/`) is gitignored -- build separately with npm

## Design Doc

Full design: `~/Documents/docs/plans/2026-02-14-ollama-queue-scheduler-design.md`
