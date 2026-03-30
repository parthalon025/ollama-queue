# ollama-queue

Ollama job queue scheduler with priority, health monitoring, and web dashboard. Serializes all Ollama-using systemd tasks to prevent model loading contention.

**Repo:** https://github.com/parthalon025/ollama-queue

## Structure

```
ollama_queue/
  __init__.py
  app.py              # FastAPI app factory: create_app(db) → mounts all routers + static SPA
  cli.py              # Click CLI: submit, status, queue, history, pause, resume, cancel, serve, schedule, dlq, defer, metrics, settings, backend
  cli_backend.py      # Click subcommand group: backend status, sync-models, update-ollama
  dlq.py              # DLQManager: handle_failure routes to retry (backoff) or DLQ
  intelligence.py     # LoadPatterns: hourly/daily load profiles from health log history
  metrics_parser.py   # Ollama response metrics parser (tok/min, eval duration)

  api/                # FastAPI REST API (90+ endpoints, APIRouter per domain)
    __init__.py       # register_routes() — sets module db ref, includes all APIRouters
    consumers.py      # Consumer management endpoints (scan, patch, revert, health)
    dlq.py            # DLQ list/retry/clear/schedule endpoints
    eval_runs.py      # Eval run CRUD, progress, results, analysis, promote
    eval_settings.py  # Eval settings + data source + setup checklist
    eval_trends.py    # Eval trend aggregation endpoints
    eval_variants.py  # Eval variant CRUD, stability, config diff
    forge_runs.py     # Forge run CRUD, progress, cancel, results endpoints
    forge_settings.py # Forge settings + autonomy endpoints
    forge_archive.py  # GET /api/forge/archive, /heatmap, /cell — MAP-Elites grid endpoints
    health.py         # /api/health endpoint
    jobs.py           # Job submit, status, queue, history, cancel, batch
    models.py         # Model stats, catalog search, performance curve
    proxy.py          # /api/generate + /api/embed Ollama proxy (priority, streaming)
    schedule.py       # Recurring jobs, load-map, suggest, rebalance
    settings.py       # Settings CRUD endpoints
    backends.py       # Backend management: status, register, weight, heartbeat, command dispatch
    backend_router.py # 6-tier multi-backend routing: health → model → warm → VRAM → CPU/RAM → weighted
    required_models.py # GET /api/required-models — hardware-filtered model list for backend agents

  daemon/             # Polling loop + job executor (mixin pattern → single Daemon class)
    __init__.py       # Daemon class: assembles LoopMixin + ExecutorMixin, holds all state
    executor.py       # ExecutorMixin: _run_job, _can_admit, preemption, stall checks, resource helpers
    loop.py           # LoopMixin: poll_once, run, shutdown, circuit breaker, entropy, orphan recovery

  db/                 # SQLite persistence (mixin pattern → single Database class)
    __init__.py       # Database class: assembles all mixins, holds _conn + _lock
    schema.py         # SchemaMixin: CREATE TABLE, migrations, seed data, initialize()
    jobs.py           # JobsMixin: CRUD for jobs table (submit, claim, complete, cancel, retry)
    schedule.py       # ScheduleMixin: recurring_jobs CRUD, promote_due, next_run, load_map
    dlq.py            # DLQMixin: dlq table CRUD, move_to_dlq, retry, reschedule tracking
    health.py         # HealthMixin: health_log, daemon_state, prune_old_data
    settings.py       # SettingsMixin: key-value settings table
    eval.py           # EvalMixin: eval_runs, eval_results, eval_variants tables
    forge.py          # ForgeMixin: CRUD for forge_runs, forge_results, forge_embeddings, forge_archive, forge_thompson_state tables

  eval/               # Eval pipeline — prompt evaluation with A/B variants + LLM judge
    __init__.py       # Re-exports public names from engine, judge, metrics, promote, analysis
    engine.py         # Session orchestration, run CRUD, scheduling modes, seed/reproducibility
    generate.py       # run_eval_generate: variant-based generation with cooperative cancellation
    judge.py          # run_eval_judge: LLM-based scoring with agreement tracking
    promote.py        # do_promote_eval_run, check_auto_promote (3-gate auto-promote logic)
    analysis.py       # Pure analysis (no DB/HTTP): per-item breakdown, bootstrap CI, stability, config diff
    metrics.py        # Pure metric computation: F1/precision/recall, tournament/Bayesian aggregates, report rendering

  forge/              # Forge v2 — oracle-calibrated eval engine (replaces cluster-based eval logic)
    __init__.py       # Re-exports public API (run_forge_cycle, ForgeDataSource, AutonomyLevel, etc.)
    types.py          # ForgeDataSource Protocol, AutonomyLevel/ForgeRunStatus enums, ForgeResult dataclass, PairQuartile
    settings.py       # FORGE_DEFAULTS dict + get_forge_setting() typed accessor
    embedder.py       # embed_items() via nomic-embed-text + content_hash() for cache keying
    pairs.py          # build_similarity_matrix(), select_stratified_pairs() (4-quartile cosine-sim sampling)
    judge.py          # build_judge_prompt(), parse_judge_response() (1–5 transfer score extraction)
    oracle.py         # select_oracle_sample(), compute_kappa(), compute_per_group_kappa()
    calibrator.py     # fit_calibration() (isotonic regression), apply_calibration() (judge → calibrated score)
    metrics.py        # compute_forge_metrics() (oracle-ground-truth F1), spearman_rank_correlation(), score_variance()
    engine.py         # run_forge_cycle() — orchestrates embed→pair→judge→oracle→calibrate→metrics; never raises
    splits.py         # deterministic train/val/test split (60/20/20) via SHA-256(seed, item_id); stable across item additions
    descriptors.py    # behavior descriptor computation: output_length × vocabulary_diversity (default axes for MAP-Elites)
    archive.py        # MAP-Elites quality-diversity grid: ArchiveCell, try_insert, QD-score, coverage, heatmap
    thompson.py       # ThompsonBudget: Beta-posterior oracle budget allocation per category; adapted from ARIA shadow_engine.py
    evolver.py        # tournament_select, crossover_prompts, mutate_prompt, evolve_generation — variant creation from archive
    goodhart.py       # composite monitoring score (display-only, NEVER optimizer target); divergence + staleness detection
    engine_evolve.py  # run_evolve_phase() — Phase 2 orchestration: splits → descriptors → archive → Thompson → evolution

  scheduling/         # Time-based job orchestration
    __init__.py       # Re-exports: Scheduler
    scheduler.py      # Recurring job promotion, rebalance, load_map_extended, pin enforcement
    slot_scoring.py   # find_fitting_slot: score-ranked slot selection for DLQ/deferral
    deferral.py       # DeferralScheduler: two-phase sweep (resume past-scheduled + find slots)
    dlq_scheduler.py  # DLQScheduler: failure classification, slot fitting, chronic skip

  sensing/            # System monitoring + anomaly detection
    __init__.py       # Re-exports: HealthMonitor
    health.py         # HealthMonitor: RAM/VRAM/load/swap/ollama-ps with hysteresis
    stall.py          # StallDetector: stdout silence + CPU usage tracking
    burst.py          # BurstDetector: submission rate regime detection (calm/burst/storm)
    system_snapshot.py # SystemSnapshot: 10-factor slot scoring with VRAM hard gates

  models/             # Ollama model management + performance estimation
    __init__.py       # Re-exports: OllamaModels
    client.py         # OllamaModels: list_local, model_info, VRAM estimation, cache
    estimator.py      # DurationEstimator: rolling avg + model-based duration defaults
    runtime_estimator.py # RuntimeEstimator: Bayesian log-normal (4-tier: job → model → family → global)
    performance_curve.py # PerformanceCurve: log-linear regression (tok/min vs param count)

  config/             # Consumer configuration + traffic intercept
    __init__.py
    scanner.py        # 4-phase consumer detection: live (ss/lsof/netstat), static, stream, deadlock
    patcher.py        # Config rewriter (systemd/env/yaml/toml) + health checker + backup/revert
    intercept.py      # iptables REDIRECT intercept mode (Linux only)

  dashboard/
    spa/              # Preact SPA (built separately, served as static)
      src/
        components/   # UI components (eval/, consumers/, SettingsForm/, RunRow/, Plan/)
        hooks/        # Shared Preact hooks (useActionFeedback, useShatter)
        pages/        # Page-level components (Now, Plan/, History, Models, etc.)
        stores/       # Signal stores by domain (atmosphere, eval, health, models, queue, schedule, settings)
        views/        # Eval sub-views (Runs, Variants, Trends, Settings)
      dist/           # Production build output (gitignored)

sidecar/
  backend_agent.py   # Backend agent: reconciliation loop, heartbeat, command endpoints (port 11435)
  Dockerfile         # Docker image → ghcr.io/parthalon025/ollama-backend-agent:latest
  tests/             # Agent endpoint tests (10 tests)

scripts/
  bootstrap-backend.sh         # One-command setup for new GPU hosts (Ollama + agent containers)
  backend-onboard.sh           # Pull all required Ollama models on any backend URL (legacy, use bootstrap-backend.sh)
  migrate_timers.py            # Migrate 8 of 10 systemd timers to recurring jobs
  migrate_dlq_max_retries.py   # Add max_retries column to existing dlq table (idempotent)

tests/                           # 2,139 tests, 100% line coverage
```

## How to Run

```bash
# Activate venv (MUST use python3.12, not python3)
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate

# Run tests (2,139 tests, 100% line coverage)
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
ollama-queue dlq schedule-preview    # Show unscheduled DLQ entries with failure classification
ollama-queue dlq reschedule <id>     # Manually reschedule a DLQ entry as a new job

# Deferral
ollama-queue defer <job_id> --reason manual   # Defer a pending/queued job

# Metrics
ollama-queue metrics models          # Per-model stats (runs, tok/min, warmup, size)
ollama-queue metrics curve           # Fitted cross-model performance curve parameters

# Backend management
ollama-queue backend status                     # Show all backend agents
ollama-queue backend status http://100.1.2.3:11434  # Specific backend
ollama-queue backend sync-models                # Trigger model sync on all backends
ollama-queue backend update-ollama http://...   # Update Ollama on specific backend
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
- **Preact 10** + @preact/signals + Tailwind v4 + uPlot + superhot-ui design system (`file:` dependency)
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

9-view SPA: Now (command center), Plan (Gantt + recurring jobs), History (DLQ + trends), Models, Perf, Settings, Consumers, Eval (4 sub-views). Full view-by-view reference in `docs/llm-guide-design-system.md`.

### UI Layman Comments (always required)

Every JSX component and every significant data transformation in the SPA **must** include a brief comment block in plain English explaining:
1. **What it shows** — what data/state this component displays to the user
2. **What decision/action it drives** — what the user can do or understand because of it

Format (JSX file-level or component-level):
```jsx
// What it shows: The currently-running job's name, model, elapsed time, and stdout tail.
// Decision it drives: Lets the user know whether the queue is working and what it's doing,
//   so they can decide to cancel, wait, or submit more work.
```

This applies to: component files, store transformations in `stores/`, computed values, and any non-obvious data shaping. Skip for pure layout/styling helpers with self-evident names.

## Forge (Eval Engine v2)

Forge is the oracle-calibrated evaluation engine that replaces the cluster-based eval pipeline. It evaluates AI-generated content (currently: lessons-db lessons) by pairing items via embedding similarity, having a cheap judge LLM score them, and using a strong oracle LLM (Claude Sonnet) to validate the judge's reliability.

**Design doc:** `docs/plans/2026-03-17-forge-v2-design.md`
**Research:** `~/Documents/research/2026-03-17-eval-oracle-darwinism-range-research.md`

### Architecture

```
ForgeDataSource (HTTP: GET /eval/items, GET /eval/embeddings, GET /eval/groups)
    |
run_forge_cycle() [engine.py — never raises]
    |-> embedder.py:      embed_items() → nomic-embed-text via Ollama proxy
    |-> pairs.py:         build_similarity_matrix() → select_stratified_pairs() (4 quartiles, cosine sim)
    |-> judge.py:         build_judge_prompt() → LLM judge scores pairs 1–5 (blind, no similarity info)
    |-> oracle.py:        select_oracle_sample() → stronger LLM re-scores 20% (oracle IS ground truth)
    |-> calibrator.py:    fit_calibration() isotonic regression → apply_calibration() (judge → calibrated score)
    |-> metrics.py:       compute_forge_metrics() → oracle-ground-truth F1, Spearman, score variance
    '-> engine_evolve.py: run_evolve_phase() → splits → descriptors → archive → Thompson → evolution (Phase 2)
```

### Run Lifecycle Statuses

`queued` → `embedding` → `judging` → `oracle` → `calibrating` → `complete` (or `failed`/`cancelled`)

### Key Settings (FORGE_DEFAULTS)

| Setting | Default | Purpose |
|---------|---------|---------|
| `forge.oracle_provider` | `claude` | LLM provider for oracle (claude/openai/ollama) |
| `forge.oracle_model` | `claude-sonnet-4-20250514` | Oracle model |
| `forge.oracle_budget` | `20` | Max pairs oracle re-scores per run |
| `forge.oracle_fraction` | `0.2` | Fraction of judge pairs sent to oracle |
| `forge.oracle_min_kappa` | `0.6` | Kappa gate for auto-promote |
| `forge.judge_model` | `""` (inherit eval setting) | Local judge model |
| `forge.judge_temperature` | `0.1` | Judge LLM temperature |
| `forge.pairs_per_quartile` | `20` | Pairs sampled per similarity quartile (80 total) |
| `forge.positive_threshold` | `3` | Score >= threshold = positive class for F1 |
| `forge.embedding_model` | `nomic-embed-text` | Embedding model (via Ollama proxy) |
| `forge.autonomy_level` | `observer` | observer / advisor (auto-promote) / operator (+ feedback) |
| `forge.f1_threshold` | `0.7` | F1 gate for auto-promote |
| `forge.auto_promote_min_improvement` | `0.05` | F1 improvement gate over production |
| `forge.grid_size` | `10` | MAP-Elites grid dimension (N×N cells) |
| `forge.evolution_enabled` | `false` | Enable evolution operators after calibration |
| `forge.evolution_offspring` | `4` | New variants generated per evolve cycle |
| `forge.evolution_min_archive` | `3` | Minimum occupied cells before evolution runs |
| `forge.evolution_mutation_rate` | `0.15` | Probability of mutation vs pure crossover |
| `forge.thompson_enabled` | `true` | Adaptive oracle budget allocation via Thompson Sampling |
| `forge.thompson_discount` | `0.95` | Beta posterior discount factor per cycle |
| `forge.thompson_window` | `100` | Max oracle observations retained in Thompson state |

### Pair Selection (Why Not Clusters?)

4 similarity quartiles from cosine distance matrix:
- **Q1 (0.75–1.0):** likely applies — tests recall
- **Q2 (0.50–0.75):** might apply — tests nuance
- **Q3 (0.25–0.50):** probably doesn't — tests specificity (hard negatives)
- **Q4 (0.00–0.25):** definitely doesn't — tests baseline discrimination

Spearman(judge_scores, embedding_similarity) ≈ 0 → judge acquiescing (all same scores). Spearman > 0.4 → judge tracking correctly.

### Forge-Specific Gotchas

See `docs/gotchas.md` § Forge (Eval Engine v2) for the full list (11 entries). Key items: `run_forge_cycle` never raises (captured in top 15 above); `forge.judge_model` empty string = inherit eval setting; `forge_embeddings` caches by `content_hash` — stale embeddings not auto-invalidated.

## Pipeline Verification

**Horizontal:** All 90+ API endpoints + static files (includes `/api/generate` and `/api/embed` proxies). **Vertical:** `ollama-queue submit` → DB row → daemon dequeue → subprocess → DB completed → API endpoints reflect → dashboard renders. Recurring: `schedule add` → `promote_due_jobs` → queue → run → `update_next_run`. DLQ: job fails max_retries → `move_to_dlq` → `dlq list` reflects. Full method: `projects/CLAUDE.md` § Pipeline Verification.

## Gotchas

Full gotchas reference: `docs/gotchas.md` (162 entries)

Top 15 most critical — data loss, deadlocks, silent failures, and hard-to-debug integration issues:

- **`db._connect()` must always be called INSIDE `with self._lock:`** — reversing the order creates a race window between connection acquisition and lock protection. All three recurring-job methods (`delete_recurring_job`, `update_recurring_job`, `delete_recurring_job_by_id`) had this reversed. Issue #39.
- **`move_to_dlq` must set `completed_at` on the job row** — `prune_old_data()` filters `WHERE completed_at IS NOT NULL`. Jobs marked `status='dead'` without `completed_at` accumulate indefinitely. Issue #49.
- **`poll_once()` must not clobber the proxy sentinel** — the daemon's "set idle" transitions must guard `current_job_id == -1`. Without this guard, the daemon clears the sentinel every 5s poll cycle, allowing multiple concurrent proxy requests that leave jobs permanently stuck in `status='running'`. Fix: `daemon/loop.py` guard at every `update_daemon_state(state='idle', current_job_id=None)` call site. Issue #67.
- **`repeat_eval_run` must start a background thread** — the endpoint must call `threading.Thread(target=run_eval_session, ...).start()`. The DB row alone does nothing; the daemon does not poll `eval_runs` for pending sessions. Previously the row was created but execution never started, producing a permanently-pending run.
- **Never submit a queue job that calls back through the proxy** — if a queue job calls `_call_proxy()` → `POST /api/generate`, it will deadlock because the daemon holds `current_job_id`, blocking `try_claim_for_proxy()`. Use `threading.Thread` for work that needs the proxy. Lesson #1733.
- **`_recover_orphans()` must skip `proxy:` command sentinels** — on restart, proxy sentinels must be marked failed directly, not reset to pending, or the daemon will try to shell-execute them (exit 127 → DLQ). Lesson #1734.
- **Shell scripts must exit 0 for "nothing to do"** — any non-zero exit code from a queued job is treated as failure. 3 consecutive failures open the circuit breaker, blocking all jobs. Scripts that bail early must exit 0, not 1 or 2.
- **`do_promote_eval_run` winner-set and clear-others must be in one lock** — setting `is_production=1` on the winner and clearing others to 0 are two DB writes. Separate lock acquisitions allow a concurrent promote call to interleave and leave no production variant. Issue #50.
- **`BurstDetector` singleton needs `threading.Lock`** — `record_submission()` runs in FastAPI threads; `regime()` runs in the daemon poll thread. `sorted(self._baseline_samples)` iterates the deque while `record_submission()` may be appending → `RuntimeError: deque mutated during iteration`. Issue #45.
- **Auto-promote gate 2 must never silently skip** — `_check_auto_promote_inner` must log and `return` (not `pass`) when production metrics are unparseable. A silent `pass` leaves `production_f1=None`, allowing a regression to auto-promote. Issue #43.
- **esbuild dual-Preact crash** — `file:` deps (superhot-ui) with their own `node_modules/preact` cause two Preact instances. Hooks from the wrong instance → `TypeError: Cannot read properties of undefined (reading '__H')`. Fix: the `alias` block in `esbuild.config.mjs` pins all Preact imports to `spa/node_modules/preact`. Don't remove it.
- **Eval cooperative cancellation: re-check run status inside every loop iteration** — `run_eval_generate` and `run_eval_judge` run in background threads. Each iteration must re-fetch the run row and return immediately if status is `failed` or `cancelled`. Without this, a restarted daemon produces a second overlapping execution while the zombie thread continues writing results.
- **SPA fetch errors must be checked explicitly** — `fetch()` resolves (does not throw) on 4xx/5xx; only network failures reject. Always check `res.ok` and throw on failure, otherwise the UI silently ignores HTTP errors and shows stale state.
- **`_set_job_retry` must clear `completed_at`** — the UPDATE must set `completed_at = NULL` alongside `status = 'pending'`. Without this, `prune_old_data()` sees a non-NULL `completed_at` and may delete the retried job before it re-runs.
- **`run_forge_cycle` never raises** — wraps `_run_forge_cycle_inner` in `try/except Exception` and marks run `failed` with the error string. Callers in background threads must not depend on exception propagation.
- **BitNet backend** — `bitnet-local` uses a separate lock (`_BITNET_LOCK`) and port 11435 (not the standard 11434). See `api/backends.py` and `projects/bitnet-local/CLAUDE.md` for the integration. PR #113.

## Design Doc

See `docs/` for implementation notes and design decisions.

## Design System Usage

**Full guide:** `docs/llm-guide-design-system.md` (~700 lines) — LLM reference for applying the design system to the queue dashboard.

**Before building any UI:** Read `docs/llm-guide-design-system.md`. Follow §1.5 Strategy Stack (Outcome-Driven + Friction Reduction + Trust & Predictability + Action-Oriented + Feedback-Rich). Behavioral target: fire-and-forget confidence.

## Scope Tags
language:python, framework:preact, domain:ollama

Quality gates for agent-driven development (auto-triggered via superpowers integration in `~/Documents/CLAUDE.md`):
- **Quality checks**: `python3 -m pytest --timeout=120 -x -q; npm run build`
- **PRD artifacts**: `tasks/prd.json`, `tasks/prd-<feature>.md`
- **Progress log**: `progress.txt` (append-only during execution)
