# ollama-queue

[![Python](https://img.shields.io/badge/python-3.12+-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Security](https://github.com/parthalon025/ollama-queue/actions/workflows/security.yml/badge.svg)](https://github.com/parthalon025/ollama-queue/actions/workflows/security.yml)
[![CodeQL](https://github.com/parthalon025/ollama-queue/actions/workflows/codeql.yml/badge.svg)](https://github.com/parthalon025/ollama-queue/actions/workflows/codeql.yml)

Priority job queue for Ollama — serializes local AI inference tasks, monitors system health, and serves a web dashboard.

## Why

Running 10+ systemd timers that all use Ollama creates a contention problem. Two tasks starting simultaneously both try to load a 5GB model into RAM, causing swapping, OOM kills, or 10x slowdowns. The naive fix — spacing timer schedules 45 minutes apart — is fragile and wastes time.

ollama-queue puts all Ollama work through a single daemon. It checks RAM/VRAM before each job, runs tasks by priority, tracks duration history, and handles failures with automatic retry and a dead-letter queue. The dashboard shows what's running, what's waiting, and how long the queue will take to clear.

## Install

```bash
git clone https://github.com/parthalon025/ollama-queue
cd ollama-queue
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Link the binary to your PATH:
```bash
ln -sf "$(pwd)/.venv/bin/ollama-queue" ~/.local/bin/ollama-queue
```

Install dashboard dependencies (optional, required for the web UI):
```bash
cd ollama_queue/dashboard/spa
npm install
npm run build
```

## Use

```bash
# Start the server (daemon + API + dashboard)
ollama-queue serve --port 7683

# Submit a job
ollama-queue submit --source myapp --model qwen2.5:7b --priority 3 --timeout 120 -- python3 run_analysis.py

# Check queue and status
ollama-queue status
ollama-queue queue
ollama-queue history

# Recurring jobs
ollama-queue schedule add --name daily-report --cron "0 23 * * *" -- python3 generate_report.py
ollama-queue schedule list
ollama-queue schedule remove daily-report

# Pause/resume processing
ollama-queue pause
ollama-queue resume

# Dead-letter queue
ollama-queue dlq list
ollama-queue dlq retry <id>
ollama-queue dlq clear

# Settings
ollama-queue settings get
ollama-queue settings set ram_threshold_high 85
```

The dashboard is at `http://localhost:7683/ui/` once the server is running.

### Consumer detection and onboarding

The Consumers tab discovers every service on your machine that calls Ollama on port 11434 and guides you through routing them through the queue.

**4-phase scanner** (`POST /api/consumers/scan`):
1. **Live** — `ss`/`lsof`/`netstat` finds processes with active connections to port 11434
2. **Static** — walks `~/.config`, `~/.local`, `/etc` for config files (`.env`, `.yaml`, `.toml`, `.service`) referencing `localhost:11434`
3. **Stream** — inspects source code for `stream=true` calls (deadlock risk with the proxy)
4. **Deadlock** — checks whether the process is a queue-submitted job calling back through the proxy

**Per-consumer actions:**
- **Include** — rewrites the config file to point at `127.0.0.1:7683` and restarts the service
- **Ignore** — marks the consumer so it won't show up in future scan results
- **Revert** — restores the original config from backup

**Intercept mode** (Linux only): installs an iptables `REDIRECT` rule that transparently catches any process that still connects directly to port 11434 — no config change required:

```bash
# Via the dashboard Consumers tab, or directly via API:
curl -X POST http://127.0.0.1:7683/api/consumers/intercept/enable \
  -H 'Content-Type: application/json' -d '{"included_consumer_ids": [1, 2]}'
curl http://127.0.0.1:7683/api/consumers/intercept/status
curl -X POST http://127.0.0.1:7683/api/consumers/intercept/disable
```

Intercept requires at least one included consumer (to prevent accidentally capturing unrelated traffic). The iptables rule is persisted via `iptables-save` and restored on reboot.

### Ollama proxy

The server exposes `/api/generate` and `/api/embed` as a drop-in proxy for the Ollama API. Redirect Ollama calls from your apps to `http://127.0.0.1:7683` and they'll queue automatically.

Pass priority metadata in the JSON body (stripped before forwarding to Ollama):
```json
{"model": "qwen2.5:7b", "prompt": "...", "_priority": 5, "_source": "myapp", "_timeout": 300}
```

`/api/embed` accepts both single-string and array input and sets `resource_profile=embed` for proper concurrency handling (4 concurrent slots, no VRAM gate):
```json
{"model": "nomic-embed-text", "input": "text string"}
{"model": "nomic-embed-text", "input": ["text1", "text2"]}
```

### Systemd integration

```ini
# Run jobs through the queue by adding to each timer's ExecStart:
ExecStart=/bin/bash -c '. ~/.env && ollama-queue submit --source %n --model qwen2.5:7b --priority 2 -- python3 /path/to/script.py'
```

## What It Does

| Feature | Description |
|---------|-------------|
| Priority queue | Jobs run highest-priority first (int, higher = sooner) |
| Health gating | Checks RAM/VRAM/load before each job; pauses at high threshold, resumes below lower threshold (hysteresis) |
| Admission control | Rejects submissions with 429 when queue depth exceeds `max_queue_depth`; VRAM gate checks model size before dequeue |
| SJF scheduling | Shortest-Job-First dequeue with Age-of-Information freshness weighting to prevent starvation |
| Preemption | Optionally interrupts a low-priority running job so a critical job can start immediately |
| Circuit breaker | Isolates Ollama failures automatically; exponential backoff before retry; prevents cascading failures |
| Burst detection | Classifies current traffic regime (burst / steady / trough) so the scheduler can adapt dequeue rate |
| Entropy alerting | Detects anomalous submission spikes (σ-threshold) and optionally suspends low-priority jobs during the surge |
| Recurring jobs | Cron or interval scheduling with pin slots, rebalancing, and skip-on-busy logic |
| Retry + DLQ | Exponential backoff retries; failed jobs move to dead-letter queue with configurable max_retries |
| Duration estimates | Rolling average + model-based defaults; predicts queue drain time |
| Stall detection | Bayesian detection of jobs that started but stopped producing output |
| Ollama proxy | `/api/generate` and `/api/embed` endpoints with priority injection; scheduler prefers embed-profile jobs at equal priority to keep embed models warm |
| Job descriptions | Recurring jobs carry a plain-English "what it does" field — auto-generated by local Ollama (qwen3:8b), editable in the dashboard, regenerated on demand via ↻ |
| Eval pipeline | Run A/B–E prompt variant evaluations with an LLM judge (F1/recall/precision), view trends, and promote the winning config to production |
| Auto-promote | Automatically promote the winner when all quality gates pass: F1 threshold, improvement over current production, and error budget. Off by default (`eval.auto_promote`) |
| Consumer detection | 4-phase scanner finds every service calling Ollama directly; patcher rewrites config to route through the queue; optional iptables REDIRECT intercept catches unpatched callers at the network layer |
| REST API | 70+ endpoints covering queue management, scheduling, health, settings, eval pipeline, consumer management |
| Web dashboard | 7-view Preact SPA: Now (burst regime badge), Plan (24h Gantt), History (preemption indicators), Models, Settings (12+ tunable parameters), Eval (runs, variants, trends, settings), Consumers (onboarding flow) |

## Architecture

```
systemd timers / apps
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
│  /api/consumers /api/consumers/intercept    │
│                                             │
│  Static: /ui/ → Preact SPA                  │
└─────────────────────────────────────────────┘
```

## Structure

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
  dashboard/spa/      # Preact SPA (build with npm run build)
scripts/
  migrate_timers.py            # Migrate systemd timers to recurring jobs (--dry-run / --execute)
  migrate_dlq_max_retries.py   # Schema migration for max_retries column (idempotent)
tests/                         # 638 tests (pytest-xdist parallel)
```

## Data

- **Queue DB:** `~/.local/share/ollama-queue/queue.db` (SQLite, WAL mode)
- **Service:** `ollama-queue.service` (user systemd, `MemoryMax=512M`)

## Requirements

```
Python 3.12+
click>=8.1.0
croniter>=1.4.0
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
```

Dev/test: `pip install -r requirements-dev.txt`

## Tests

```bash
source .venv/bin/activate
pytest  # 638 tests, parallel by default
```

## Design Docs

Implementation plans and design decisions are in [`docs/plans/`](docs/plans/):

| Doc | Topic |
|-----|-------|
| [v2 design](docs/plans/archive/2026-02-27-ollama-queue-v2-design.md) | Recurring jobs, DLQ, retry architecture |
| [Smart scheduling](docs/plans/archive/2026-02-27-smart-scheduling-design.md) | Cron pin slots, load balancing, rebalancer |
| [Bayesian stall detection](docs/plans/2026-02-28-bayesian-stall-detection-design.md) | Stall detection algorithm design |
| [Model concurrency](docs/plans/2026-02-28-model-concurrency-ui-design.md) | Multi-model scheduling + affinity |
| [Dashboard redesign](docs/plans/2026-03-01-dashboard-sidebar-redesign-design.md) | Layout, navigation, UX decisions |
| [Queue optimization](docs/plans/2026-03-04-queue-optimization-design.md) | Overall optimization roadmap (PR1–PR4) |
| [PR2: Admission + reliability](docs/plans/2026-03-04-pr2-admission-reliability.md) | Circuit breaker, VRAM admission, CPU offload |
| [PR3: SJF scheduling](docs/plans/2026-03-04-pr3-scheduling-intelligence.md) | Shortest-Job-First + AoI + burst detection |
| [PR4: Observability](docs/plans/2026-03-04-pr4-observability-strategic.md) | Entropy alerting, preemption |
| [Job descriptions](docs/plans/2026-03-05-job-descriptions-design.md) | AI-generated layman descriptions for recurring jobs |
| [LLM design system guide](docs/llm-guide-design-system.md) | Full reference for the dashboard design language |
| [Eval pipeline UI](docs/plans/2026-03-05-eval-pipeline-ui-design.md) | Eval tab architecture, engine, SPA components |
| [Promote & auto-promote](docs/plans/2026-03-07-promote-auto-promote-design.md) | Winner promotion + three-gate auto-promote logic |
| [Consumer detection](docs/plans/2026-03-08-consumer-detection-design.md) | 4-phase scanner, config patcher, iptables intercept |

## License

MIT
