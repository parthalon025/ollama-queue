# ollama-queue

[![Python](https://img.shields.io/badge/python-3.12+-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Security](https://github.com/parthalon025/ollama-queue/actions/workflows/security.yml/badge.svg)](https://github.com/parthalon025/ollama-queue/actions/workflows/security.yml)
[![CodeQL](https://github.com/parthalon025/ollama-queue/actions/workflows/codeql.yml/badge.svg)](https://github.com/parthalon025/ollama-queue/actions/workflows/codeql.yml)

**Priority job queue and smart scheduler for local Ollama LLM inference — with a full web dashboard, failure recovery, and an A/B prompt evaluation pipeline.**

Running multiple services against a local Ollama instance creates a resource contention problem: two jobs starting simultaneously both try to load a 5 GB model into RAM, causing swapping, OOM kills, or 10x slowdowns. ollama-queue solves this by routing all Ollama work through a single daemon that enforces health-gated, priority-ordered execution — and surfaces everything in a Preact SPA dashboard.

## Who This Is For

- **Self-hosters running Ollama** who have multiple services competing for local LLM inference and hitting OOM kills, slowdowns, or model thrash
- **Developers** who want a production-grade job queue with health gating, DLQ, retry backoff, and smart scheduling — without running a full distributed system like Celery or Redis
- **AI experimenters** who want to A/B test prompt variants against real output quality using an LLM judge and F1-gated auto-promotion
- **Home lab operators** who want a web dashboard showing exactly what's in the queue, what's running, and what failed — with click-to-retry DLQ entries

**Not for:** cloud-hosted Ollama (use provider-side queuing). Works best on Linux with systemd; the service manager and health monitor assume a local Linux environment.

## Prerequisites

- Linux with systemd (macOS works but without service management)
- [Ollama](https://ollama.ai) installed and running
- Python 3.12+
- The web dashboard requires Node.js 18+ (for building the Preact SPA)

---

## Features

| Category | What it does |
|---|---|
| **Priority queue** | Jobs run highest-priority-first (integer priority; higher = sooner). SJF dequeue with Age-of-Information weighting prevents starvation. |
| **Health gating** | Checks RAM, VRAM, and system load before each job. Pauses at high threshold, resumes below a lower threshold (hysteresis). Rejects submissions with 429 when queue depth exceeds limit. |
| **Recurring jobs** | Cron or interval scheduling. 48-slot load map with pin slots, automatic rebalancing, and skip-on-busy logic. CLI suggests low-load windows. |
| **Dead-letter queue** | Failed jobs retry with exponential backoff up to `max_retries`, then move to the DLQ. Retry or clear from CLI or dashboard. |
| **Stall detection** | Bayesian detection of jobs that started but stopped producing output. |
| **Circuit breaker** | Isolates Ollama failures automatically; exponential backoff before retry; prevents cascading failures. |
| **Burst detection** | Classifies traffic regime (burst / steady / trough) and adapts dequeue rate accordingly. |
| **Ollama proxy** | Drop-in `/api/generate` and `/api/embed` proxy — point existing apps at `localhost:7683` and they queue automatically. |
| **Consumer detection** | 4-phase scanner finds every service calling Ollama directly. Config patcher rewrites them to route through the queue. Optional iptables REDIRECT intercept catches unpatched callers at the network layer. |
| **Eval pipeline** | Run A/B–E prompt variant evaluations with an LLM judge (F1/recall/precision). Auto-promote the winning config when quality gates pass. Thompson Sampling routes production traffic to the recommended variant. |
| **Web dashboard** | 7-view Preact SPA: Now, Plan, History, Models, Settings, Eval, Consumers. |
| **REST API** | 70+ endpoints covering all features. |

---

## Quick Start

### Install

```bash
git clone https://github.com/parthalon025/ollama-queue
cd ollama-queue
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
ln -sf "$(pwd)/.venv/bin/ollama-queue" ~/.local/bin/ollama-queue
```

Build the dashboard (optional — required for the web UI):

```bash
cd ollama_queue/dashboard/spa
npm install
npm run build
```

### Start the server

```bash
ollama-queue serve --port 7683
```

The dashboard is at `http://localhost:7683/ui/`.

### Submit a job

```bash
# One-off job (priority 3, 120s timeout)
ollama-queue submit --source myapp --model qwen2.5:7b --priority 3 --timeout 120 -- python3 run_analysis.py

# Check queue status
ollama-queue status
ollama-queue queue
ollama-queue history
```

### Recurring jobs

```bash
ollama-queue schedule add --name daily-report --cron "0 23 * * *" -- python3 generate_report.py
ollama-queue schedule list
ollama-queue schedule remove daily-report
```

### Dead-letter queue

```bash
ollama-queue dlq list
ollama-queue dlq retry <id>
ollama-queue dlq clear
```

### Pause / resume processing

```bash
ollama-queue pause
ollama-queue resume
```

### Settings

```bash
ollama-queue settings get
ollama-queue settings set ram_threshold_high 85
```

---

## Ollama Proxy

The server exposes `/api/generate` and `/api/embed` as a drop-in Ollama proxy. Redirect your apps from `localhost:11434` to `localhost:7683` and they queue automatically — no other code changes required.

Pass priority metadata in the JSON body (stripped before forwarding to Ollama):

```json
{"model": "qwen2.5:7b", "prompt": "...", "_priority": 5, "_source": "myapp", "_timeout": 300}
```

`/api/embed` accepts both single-string and array inputs:

```json
{"model": "nomic-embed-text", "input": "text string"}
{"model": "nomic-embed-text", "input": ["text1", "text2"]}
```

---

## Systemd Integration

```ini
# Wrap any timer's ExecStart to route through the queue:
ExecStart=/bin/bash -c '. ~/.env && ollama-queue submit --source %n --model qwen2.5:7b --priority 2 -- python3 /path/to/script.py'
```

A migration script is included to import existing systemd timers as recurring jobs:

```bash
python3 scripts/migrate_timers.py --dry-run
python3 scripts/migrate_timers.py --execute
```

---

## Consumer Detection and Onboarding

The **Consumers** tab discovers every service on your machine that calls Ollama on port 11434 and walks you through routing them through the queue.

**4-phase scanner** (`POST /api/consumers/scan`):
1. **Live** — `ss`/`lsof`/`netstat` finds processes with active connections to port 11434
2. **Static** — walks `~/.config`, `~/.local`, `/etc` for config files (`.env`, `.yaml`, `.toml`, `.service`) referencing `localhost:11434`
3. **Stream** — inspects source code for `stream=true` calls (deadlock risk with the proxy)
4. **Deadlock** — checks whether the process is a queue job calling back through the proxy

**Per-consumer actions:**
- **Include** — rewrites the config file to point at `127.0.0.1:7683` and restarts the service
- **Ignore** — marks the consumer so it won't appear in future scan results
- **Revert** — restores the original config from backup

**Intercept mode** (Linux only) installs an iptables `REDIRECT` rule that transparently catches any process that still connects directly to port 11434 — no config change required:

```bash
curl -X POST http://localhost:7683/api/consumers/intercept/enable \
  -H 'Content-Type: application/json' -d '{"included_consumer_ids": [1, 2]}'
curl http://localhost:7683/api/consumers/intercept/status
curl -X POST http://localhost:7683/api/consumers/intercept/disable
```

---

## Eval Pipeline

The eval pipeline lets you A/B test prompt variants against a sample dataset, score them with an LLM judge, and promote the winner to production.

**Concepts:**
- **Variants** — named prompt templates (A through E) stored in the DB. Edit in the dashboard or via API.
- **Eval runs** — execute all variants against the dataset; the judge scores each response (F1/recall/precision).
- **LLM judge** — any locally-available Ollama model. Default: `deepseek-r1:8b`. Configurable per eval session.
- **Auto-promote** — when enabled, the winning variant is promoted automatically if it clears three gates: F1 ≥ threshold, F1 improvement over current production ≥ minimum, and error budget not exceeded.
- **Thompson Sampling** — production traffic is routed to the recommended variant using Thompson Sampling, balancing exploitation of the best known variant with exploration.

**CLI:**
```bash
# Generate eval results (all variants × dataset)
lessons-db meta eval-generate --priority 5

# Score results with the judge (F1 report)
lessons-db meta eval-judge

# Promote a specific run manually
curl -X POST http://localhost:7683/api/eval/runs/<id>/promote
```

**Dashboard (Eval tab):**
- **Runs** — run list, active progress, repeat, judge-rerun, per-run analysis panel
- **Variants** — prompt variant CRUD, stability table, production/recommended badges
- **Trends** — F1 line chart over time, trend summary
- **Settings** — judge model, data source, scheduling mode, auto-promote thresholds

---

## Web Dashboard

Seven views served from `http://localhost:7683/ui/`:

| View | Description |
|---|---|
| **Now** | Running job, queue, resource gauges (RAM/VRAM), KPI cards, burst regime badge, alert strip |
| **Plan** | 24h Gantt timeline with "now" needle, 48-bucket load-map density strip, traffic intensity badge, "Suggest slot" button, tag-grouped recurring jobs |
| **History** | Job history, DLQ entries, duration trends, activity heatmap, preemption indicators |
| **Models** | Model table with active model tracking |
| **Settings** | Thresholds, defaults, retention policy, daemon controls (12+ tunable parameters) |
| **Eval** | Runs, Variants, Trends, Settings sub-views for the prompt eval pipeline |
| **Consumers** | Scan results, consumer cards with status badges, include/ignore/revert actions, intercept toggle |

---

## API Reference

The REST API runs at `http://localhost:7683/api/`. Key endpoint groups:

| Group | Endpoints |
|---|---|
| **Queue** | `GET /api/queue`, `GET /api/history`, `POST /api/jobs/{id}/cancel` |
| **Health** | `GET /api/health`, `GET /api/health/detail` |
| **Schedule** | `GET/POST /api/schedule`, `DELETE /api/schedule/{id}`, `GET /api/schedule/load-map`, `POST /api/schedule/suggest` |
| **DLQ** | `GET /api/dlq`, `POST /api/dlq/{id}/retry`, `DELETE /api/dlq` |
| **Settings** | `GET/PUT /api/settings` |
| **Eval** | `GET/POST /api/eval/runs`, `POST /api/eval/runs/{id}/promote`, `GET/POST /api/eval/variants`, `GET /api/eval/trends`, `GET/PUT /api/eval/settings` |
| **Consumers** | `POST /api/consumers/scan`, `POST /api/consumers/{id}/include`, `POST /api/consumers/intercept/enable` |
| **Proxy** | `POST /api/generate`, `POST /api/embed` |

Full interactive docs are at `http://localhost:7683/docs` (FastAPI/Swagger).

---

## Architecture

```
systemd timers / apps / proxy clients
        │
        ▼
  ollama-queue submit --priority N -- COMMAND
        │
        ▼
┌─────────────────────────────────────────────┐
│  Daemon (5s poll loop)                      │
│                                             │
│  health check → RAM/VRAM/load OK?           │
│       │              pause if not           │
│       ↓                                     │
│  circuit breaker open?  → back off          │
│       ↓                                     │
│  entropy spike? → suspend low-priority      │
│       ↓                                     │
│  promote recurring jobs due now             │
│       ↓                                     │
│  SJF + AoI dequeue (admission gate)         │
│       ↓                                     │
│  preempt lower-priority if needed           │
│       ↓                                     │
│  subprocess.Popen → capture stdout/stderr   │
│       ↓                                     │
│  record result → estimator → DLQ routing   │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  FastAPI (70+ endpoints)                    │
│  /api/generate  /api/embed  (proxy)         │
│  /api/queue     /api/history  /api/health   │
│  /api/schedule  /api/dlq     /api/settings  │
│  /api/eval      /api/consumers              │
│                                             │
│  Static: /ui/ → Preact SPA                  │
└─────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.12+, FastAPI, Uvicorn |
| **Storage** | SQLite (WAL mode, `threading.RLock`) |
| **Scheduling** | croniter, custom 48-slot load map |
| **Dashboard** | Preact 10, @preact/signals, Tailwind v4, uPlot |
| **CLI** | Click |
| **Tests** | pytest, pytest-xdist (638 tests) |

---

## Project Structure

```
ollama_queue/
  cli.py              # Click CLI entry point
  db.py               # SQLite schema + CRUD (threading.RLock, WAL mode)
  daemon.py           # 5s poll loop: health → scheduler → dequeue → subprocess → DLQ
  health.py           # RAM/VRAM/load/swap metrics with hysteresis
  estimator.py        # Duration prediction: rolling avg + model-based defaults
  scheduler.py        # Recurring job promotion, rebalance, cron pin slots
  dlq.py              # Dead-letter queue: retry with backoff, max_retries, move_to_dlq
  scanner.py          # 4-phase consumer detection (live/static/stream/deadlock)
  patcher.py          # Config rewriter + health checker (systemd/env/yaml/toml)
  intercept.py        # iptables REDIRECT intercept mode (Linux only)
  api.py              # FastAPI REST API + Ollama proxy + static SPA serving
  eval_engine.py      # Eval session runner, LLM judge, auto-promote logic
  dashboard/spa/      # Preact SPA (build with npm run build)
scripts/
  migrate_timers.py              # Migrate systemd timers to recurring jobs
  migrate_dlq_max_retries.py     # Schema migration (idempotent)
tests/                           # 638 tests (pytest-xdist parallel)
```

---

## Data

- **Queue DB:** `~/.local/share/ollama-queue/queue.db` (SQLite, WAL mode)
- **Service:** `ollama-queue.service` (user systemd, `MemoryMax=512M`)

---

## Requirements

```
Python 3.12+
click>=8.1.0
croniter>=1.4.0
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
```

Dev/test: `pip install -r requirements-dev.txt`

---

## Running Tests

```bash
source .venv/bin/activate
pytest  # 638 tests, parallel by default
```

---

## Design Docs

Implementation plans and design decisions are in [`docs/plans/`](docs/plans/):

| Doc | Topic |
|---|---|
| [v2 design](docs/plans/archive/2026-02-27-ollama-queue-v2-design.md) | Recurring jobs, DLQ, retry architecture |
| [Smart scheduling](docs/plans/archive/2026-02-27-smart-scheduling-design.md) | Cron pin slots, load balancing, rebalancer |
| [Bayesian stall detection](docs/plans/2026-02-28-bayesian-stall-detection-design.md) | Stall detection algorithm design |
| [Queue optimization](docs/plans/2026-03-04-queue-optimization-design.md) | Overall optimization roadmap |
| [Eval pipeline UI](docs/plans/2026-03-05-eval-pipeline-ui-design.md) | Eval tab architecture, engine, SPA components |
| [Promote & auto-promote](docs/plans/2026-03-07-promote-auto-promote-design.md) | Winner promotion + three-gate auto-promote logic |
| [Consumer detection](docs/plans/2026-03-08-consumer-detection-design.md) | 4-phase scanner, config patcher, iptables intercept |

---

## License

MIT
