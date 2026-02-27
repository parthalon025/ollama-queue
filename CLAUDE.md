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
  api.py               # FastAPI REST API (13 endpoints including /api/generate proxy) + static SPA serving
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

2 tabs: Dashboard (status, queue, KPIs, resource trends, duration trends, heatmap, history) + Settings (thresholds, defaults, retention, daemon controls).

## Pipeline Verification

**Horizontal:** All 13 API endpoints + static files. **Vertical:** `ollama-queue submit` → DB row → daemon dequeue → subprocess → DB completed → API endpoints reflect → dashboard renders. Full method: `projects/CLAUDE.md` § Pipeline Verification.

## Gotchas

- **SPA dist/ is gitignored** — must `npm run build` after cloning
- **check_same_thread=False** on SQLite — required for FastAPI worker threads, safe with WAL mode
- **httpx** must be installed for API tests — `pip install httpx`
- **Proxy endpoint uses sentinel job_id=-1** — `try_claim_for_proxy()` sets `current_job_id=-1` to distinguish proxy claims from real job execution. `release_proxy_claim()` only releases claims with `current_job_id=-1` to avoid accidentally releasing real jobs.
- **Deploy proxy before ARIA restart** — ARIA routes Ollama calls through port 7683. If ollama-queue is down, ARIA's activity predictions and organic naming fail with connection refused.
- esbuild JSX `h` shadowing — see `projects/CLAUDE.md` § Shared Gotchas.

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
