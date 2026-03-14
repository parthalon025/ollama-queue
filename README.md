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
| **Health gating** | Checks RAM, VRAM, and system load before each job. Pauses at high threshold, resumes below a lower threshold (hysteresis). Configurable max-pause escape hatch prevents indefinite stalls. Rejects submissions with 429 when queue depth exceeds limit. |
| **Recurring jobs** | Cron or interval scheduling. 48-slot load map with pin slots, automatic rebalancing, and skip-on-busy logic. CLI suggests low-load windows. |
| **Dead-letter queue** | Failed jobs retry with exponential backoff up to `max_retries`, then move to the DLQ. Auto-reschedule classifies failures, finds optimal time slots, and creates new jobs — with chronic failure detection to prevent infinite loops. Retry or clear from CLI or dashboard. |
| **Proactive deferral** | Defer jobs when resources are tight; two-phase sweep resumes them when conditions improve or scheduled times pass. |
| **Stall detection** | Bayesian detection of jobs that started but stopped producing output. |
| **Circuit breaker** | Isolates Ollama failures automatically; exponential backoff before retry; prevents cascading failures. |
| **Burst detection** | Classifies traffic regime (burst / steady / trough) and adapts dequeue rate accordingly. |
| **Ollama proxy** | Drop-in `/api/generate` and `/api/embed` proxy — point existing apps at `localhost:7683` and they queue automatically. Routes requests across multiple Ollama backends and captures per-backend throughput metrics. |
| **Consumer detection** | 4-phase scanner finds every service calling Ollama directly. Config patcher rewrites them to route through the queue. Optional iptables REDIRECT intercept catches unpatched callers at the network layer. |
| **Eval pipeline** | Run A/B–E prompt variant evaluations with an LLM judge (F1/recall/precision). Auto-promote the winning config when quality gates pass. Thompson Sampling routes production traffic to the recommended variant. |
| **Intelligence layer** | Bayesian log-normal runtime estimation (4-tier hierarchy), log-linear cross-model performance curves, 10-factor slot scoring with VRAM hard gates, hourly/daily load pattern learning. |
| **Web dashboard** | 8-view Preact SPA: Now, Plan, History, Models, Performance, Settings, Eval, Consumers. SUPERHOT terminal aesthetic — CRT page banners, VT323 pixel font, glitch/shatter effects on state transitions, ThreatPulse gauges with three-state ambient animation, KPI degradation glitch on warning transitions. |
| **Accessible, science-backed UI** | Non-color priority discriminators (Treisman multi-channel encoding), progressive disclosure on queue row hover (Shneiderman), sparklines on every KPI card (Tufte), semantic `data-chroma` tokens, three-tier animation system with `prefers-reduced-motion` opt-in, `@starting-style` tab entrance animations. |
| **REST API** | 90+ endpoints covering all features. |

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
ollama-queue dlq schedule-preview    # Preview what auto-reschedule would do
ollama-queue dlq reschedule <id>     # Manually reschedule as a new job
```

### Deferral

```bash
ollama-queue defer <job_id> --reason manual   # Defer a pending/queued job
```

### Metrics

```bash
ollama-queue metrics models          # Per-model stats (runs, tok/min, warmup, size)
ollama-queue metrics curve           # Fitted cross-model performance curve
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
- **Variants** — named prompt templates (9 system variants: A–H + M) stored in the DB, each with a plain-English description. Edit in the dashboard or via API.
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

Eight views served from `http://localhost:7683/ui/`:

| View | Description |
|---|---|
| **Now** | Running job, queue, ThreatPulse resource gauges (RAM/VRAM with three-state ambient animation), KPI cards with sparkline trends and chroma-coded severity, burst regime badge, alert strip |
| **Plan** | 24h Gantt timeline with "now" needle, 48-bucket load-map strip with DLQ/deferral slot markers, traffic intensity badge, "Suggest slot" button, tag-grouped recurring jobs |
| **History** | Job history, DLQ entries with reschedule status badges and reasoning, deferred jobs panel, duration trends, activity heatmap |
| **Models** | Model table with active model tracking |
| **Perf** | Per-model performance table, cross-model performance curve chart (SVG scatter, log-scale), 24h×7d load heatmap, system health gauges, per-backend throughput table showing tok/min per GPU |
| **Settings** | Thresholds, defaults, retention, DLQ auto-reschedule, proactive deferral, daemon controls (14+ tunable parameters) |
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
| **DLQ** | `GET /api/dlq`, `POST /api/dlq/{id}/retry`, `DELETE /api/dlq`, `GET /api/dlq/schedule-preview`, `POST /api/dlq/{id}/reschedule` |
| **Deferral** | `POST /api/defer/{job_id}`, `GET /api/deferred`, `POST /api/deferred/{id}/resume` |
| **Metrics** | `GET /api/metrics/models`, `GET /api/metrics/performance-curve`, `GET /api/metrics/backends` |
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
│       ↓                                     │
│  DLQ auto-reschedule (failure classify →    │
│       slot scoring → new job)               │
│  Deferral scheduler (two-phase sweep →      │
│       resume when conditions improve)       │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│  FastAPI (90+ endpoints)                    │
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
| **Tests** | pytest, pytest-xdist (1,870 tests, 100% line coverage) |

---

## Project Structure

```
ollama_queue/
  cli.py              # Click CLI entry point
  dlq.py              # DLQManager: handle_failure routes to retry or DLQ
  intelligence.py     # LoadPatterns: hourly/daily load profiles
  metrics_parser.py   # Ollama response metrics parser
  app.py              # FastAPI app factory: create_app(db)

  api/                # FastAPI REST API (90+ endpoints, APIRouter per domain)
  db/                 # SQLite persistence (mixin pattern → single Database class)
  daemon/             # Polling loop + job executor (mixin pattern → single Daemon class)
  eval/               # Eval pipeline: generate, judge, promote, analysis, metrics
  scheduling/         # Scheduler, slot scoring, deferral, DLQ scheduling
  sensing/            # Health monitoring, stall/burst detection, system snapshots
  models/             # Ollama model management, duration/runtime estimation, perf curve
  config/             # Consumer detection (scanner), config rewriting (patcher), intercept

  dashboard/spa/      # Preact SPA (build with npm run build)
scripts/
  migrate_timers.py              # Migrate systemd timers to recurring jobs
  migrate_dlq_max_retries.py     # Schema migration (idempotent)
tests/                           # 1,870 tests, 100% line coverage (pytest-xdist parallel)
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
pytest  # 1,870 tests, 100% line coverage, parallel by default
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
| [DLQ auto-reschedule](docs/plans/2026-03-09-dlq-auto-reschedule-design.md) | Failure classification, slot scoring, proactive deferral, runtime estimation |
| [Eval analysis panel](docs/plans/2026-03-09-eval-analysis-panel-design.md) | Per-item breakdown, bootstrap CI, stability, config diff |
| [Eval UX improvements](docs/plans/2026-03-09-eval-ux-design.md) | Dead button removal, inline tooltips, variant descriptions |
| [UX design philosophy](docs/plans/2026-03-11-ux-design-philosophy-improvements-design.md) | SUPERHOT aesthetic, animation discipline, interaction depth strategy |
| [UX Phase 3 — SUPERHOT effects](docs/plans/2026-03-11-ux-phase3-superhot-philosophy-plan.md) | ThreatPulse gauges, KPI glitch, StatusBadge beat, typed cursor states |
| [UX Phase 4 — Visualization Science](docs/plans/2026-03-11-ux-phase4-viz-science-plan.md) | Treisman priority encoding, Shneiderman disclosure, Tufte sparklines, animation tiers |
| [Edge case audit & fixes](docs/plans/2026-03-13-edge-case-fixes.md) | 27 edge cases across 6 subsystems — proxy deadlock, SQLITE_BUSY retry, health pause escape hatch, eval safety gates |
| [Bug audit fixes](docs/plans/2026-03-13-bug-audit-fixes.md) | API input validation: offset bounds, limit caps, settings type enforcement |

---

## License

MIT
