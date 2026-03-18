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

Sidebar nav (desktop) + bottom tab bar (mobile). 9 views: **Now** (host-first command center: `HostCard` list [one per GPU backend — shows current job/eval/model, VRAM/CPU gauges; resource bars use gradient color ramp — full-width gradient track + mask; load_avg to % via `load_avg / cpu_count * 100`; CPU pause/resume thresholds `multiplier × 100` not `× 50`; `data-mood` on wrapper div — NOT on `.t-frame` — cascades superhot-ui mood selectors; `ShGlitch` fires only on `healthy → false` edge transition; offline backend wraps card in `ShThreatPulse active persistent`; stall elapsed shown via `ShFrozen`; expand card → VRAM % 24h `ShTimeChart` (local backends, from `healthHistory` prop) + full-width SYSTEM HEALTH gauges]; `ShStatsGrid` KPI section — 3 stats: queue depth, 24h jobs, RAM; `ShFrozen` wraps KPIs and HeroCards with 30s/2m/5m thresholds; DLQ dismiss button wired to `useShatter('earned')` with 7-fragment shatter; alert strip — amber badge for auto-disabled recurring jobs count linking to Plan tab; `LoadHeatmap` at bottom — 24h×7d GPU activity grid) + **Plan** (health summary strip showing active/failing/disabled/overdue/skip counts; 24h Gantt timeline with "now" needle dynamically positioned by wall-clock within zoom window, `visibleJobs` filter prevents past-due jobs piling at left edge in zoom mode; disabled jobs render at 40% opacity + dashed outline + ⏸ prefix; skip badge ↻N shows when job was skipped N times in last 24h; bar detail card shows load/run/unload segment breakdown; warmup cap = `min(rawWarmup, floor(estDur * 0.4))` prevents body collapse; 48-bucket load-map density strip with DLQ/deferral slot markers, ρ traffic intensity badge, "Suggest slot" button highlighting top-3 low-load windows; tag-grouped recurring jobs wrapped in `ShCollapsible` [uncontrolled, defaultOpen=true, job count in summary], bulk actions, expandable detail panels) + **History** (DLQ entries with reschedule status badges/reasoning, deferred jobs panel, duration trends, activity heatmap, `ShDataTable` for searchable/sortable job history) + **Models** (`ShDataTable` for searchable/sortable model list) + **Perf** (model performance table, cross-model performance curve chart [log-linear regression on `params.eval_slope`/`params.eval_intercept`/`params.residual_std` from `/api/metrics/performance-curve`; was broken using `curve.tok_slope` — fixed], `ShTimeChart` RAM % trend — last 24h, per-backend throughput table) + **Settings** (thresholds, defaults, retention, DLQ auto-reschedule, proactive deferral, daemon controls, CRT scanline display preference, audio toggle for procedural SFX) + **Consumers** (scan button, consumer cards with status badges and include/ignore/revert actions, intercept toggle with status banner).

**Atmosphere system** — `stores/atmosphere.js` drives global health-mode escalation (operational/degraded/critical) and effect density budgeting (max 3 simultaneous effects via `trackEffect()`/`isOverBudget()`). `app.jsx` subscribes to backend health data and updates `healthMode`/`escalation` signals. Escalation-driven mantra (`applyMantra`/`removeMantra`) activates when `escalation.value >= 2`. All 9 pages use `sh-stagger-children` class for entry choreography. `ShEmptyState` replaces all custom empty states with terminal-voice mantras ("NOTHING TO REPORT", "ALL SYSTEMS NOMINAL", etc.). `ShFrozen` wraps time-sensitive data across all pages (DLQ, deferrals, eval runs, consumer scan times, Gantt bars) with configurable staleness thresholds.

**Terminal voice** — all inline SPA copy uses piOS UPPERCASE voice style. `useActionFeedback` labels ("CANCELLING...", "QUEUED"), TAB_CONFIG subtitles, toast messages, button labels ("DISMISS", "RETRY", "SCAN"), empty state messages, and onboarding overlay text are all terminal-voiced.

**Tiered button shatter** — `useShatter` hook (`src/hooks/useShatter.js`) provides 3 tiers: `earned` (7 fragments — DLQ dismiss, eval cancel), `complete` (6 — form submits, promote), `routine` (3 — toggles, navigation). All action buttons across all 9 pages are wired. The hook integrates with the atmosphere effect density budget.

**Glitch/ThreatPulse deepening** — `ShGlitch` fires on connection loss transitions (`connectionStatus` signal), eval failure edges, and backend health transitions. `ShThreatPulse` wraps VRAM/RAM breach warnings, stuck eval runs, circuit breaker activation, and offline backends (with `persistent` flag).

All 9 pages use `ShPageBanner` (namespace/page/subtitle pixel-art header, TAB_CONFIG-driven) — replaces the old `PageBanner` component. Eval tab uses `ShPipeline` in `ActiveRunProgress.jsx` (replaces `EvalPipelineSwimline`). Tab metadata (id, icon, label, tooltip, namespace, page, subtitle) is the single source of truth in `src/config/tabs.js` (TAB_CONFIG) — eliminates duplicate NAV_ITEMS constants. Column configs for ShDataTable: `src/config/historyColumns.js` (History tab), `src/config/modelColumns.js` (Models tab).

Route IDs: `now` | `plan` | `history` | `models` | `settings` | `eval` | `consumers` | `performance`. Sidebar: 200px desktop, 64px icon-only (768–1023px), hidden on mobile. CSS classes: `layout-root`, `layout-sidebar`, `layout-main`, `now-grid`, `history-top-grid`, `mobile-bottom-nav`.

**Eval tab** (4 sub-views): Runs (run list + active progress + repeat + judge-rerun + per-run analysis panel with `simpleRenderMd()` + Analyze/Re-analyze button; winner label shows variant label + model name; L2 shows bootstrap CI inline in metrics + per-item breakdown panel sorted worst-first + Compute/Re-analyze button for `analysis_json`; L3 ResultsTable has TP/TN/FP/FN filter tabs with classification from `eval.positive_threshold`), Variants (prompt variant CRUD + stability table with cross-run F1 stdev/stable badge from `/api/eval/variants/stability` + ConfigDiffPanel for side-by-side comparison via `/api/eval/variants/{a}/diff/{b}`; `latest_f1` score shown inline; two-click inline delete replaces `confirm()` dialog; `description` field shown per row), Trends (F1 line chart + trend summary), Settings (judge defaults + data source + scheduling mode + setup checklist [2 gates: data source connected + first run exists] + `eval.analysis_model` — empty string means use judge model; judge mode inline description; "1–20" range hint on Lessons per Group; variant descriptions shown in checkbox list; alert()-based tooltips replaced with inline reveal). `eval_variants` rows have a `description TEXT` column; `GET /api/eval/variants` includes `description` in response; live DB migration backfills pre-existing rows via `UPDATE WHERE description IS NULL`. Eval state: `evalActiveRun`, `evalSubTab`, `fetchEvalRuns` in `stores/eval.js`. Key invariants: `repeat` starts a background thread (not just a DB row); `judge-rerun` copies gen_results from source run before judging; cancel sets `completed_at`; all fetch calls check `res.ok`; `generate_eval_analysis()` runs automatically after each eval run completes and stores markdown to `eval_runs.analysis_md`; `compute_run_analysis()` stores structured `analysis_json` (per-item breakdown, failure cases, bootstrap CI) per run. `eval/analysis.py` is the pure analysis module (no DB/HTTP) — 5 public functions. Graceful no-cluster degradation: returns `{"status": "no_cluster_data"}` for projects without cluster labels.

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

- **`run_forge_cycle` never raises** — wraps `_run_forge_cycle_inner` in `try/except Exception` and marks run `failed` with the error string. Callers in background threads must not depend on exception propagation.
- **`forge.judge_model` empty string = inherit** — `get_forge_setting()` returns empty string default. Engine code must fall back to the `eval.judge_model` setting when `forge.judge_model` is empty. Don't pass an empty string to the LLM provider.
- **`forge_embeddings` table caches by `content_hash`** — `embed_items()` skips items whose hash already exists in `forge_embeddings`. If item content changes, the old embedding is NOT invalidated automatically. Manually delete rows or the embedder will use stale vectors.
- **`calibrator.fit_calibration()` returns `None` when < 10 oracle pairs** — `apply_calibration(None, score)` returns the raw score unchanged. Raw scores are used with a WARNING logged. Don't assume calibration always runs.
- **`forge.oracle_fraction` × total_pairs must yield ≥ 1 pair** — `select_oracle_sample()` returns `[]` when rounding produces zero pairs. The oracle phase is then a no-op, kappa = None, and calibration falls back to raw scores. Guard in config: at 80 pairs default, fraction=0.2 → 16 pairs (fine).
- **`update_forge_result_oracle()` only updates rows with oracle data** — `_run_oracle_phase` iterates a *sample* of results and updates them in-place. The remaining judge-only rows retain `oracle_score = NULL`. `compute_kappa` filters to `oracle_score IS NOT NULL` rows.
- **`forge_results.calibrated_score` is written with direct `conn.execute`** — `_run_calibrate_and_metrics()` writes calibrated scores via a direct SQL UPDATE inside `with db._lock:`. It does not go through a `ForgeMixin` method. Do not add a redundant `update_forge_result_calibrated` mixin method without removing this path.
- **DB schema migration required for forge tables** — `db/schema.py` SchemaMixin creates `forge_runs`, `forge_results`, `forge_embeddings`, `forge_archive`, `forge_thompson_state` tables. If upgrading a live queue.db that predates Forge, restart the service — `initialize()` uses `CREATE TABLE IF NOT EXISTS` and handles it automatically (no manual ALTER needed).
- **`forge_archive` and `forge_thompson_state` are Phase 2 tables** — only populated when `forge.evolution_enabled = true`. Both are created at startup regardless; the archive is simply empty until an evolve phase runs.
- **`run_evolve_phase()` is a pure computation entry point** — takes calibrated results and settings, returns offspring variant specs. No DB writes inside `engine_evolve.py`. The caller (`engine.py`) handles persistence.
- **`goodhart.py` composite score is display-only** — `compute_composite()` output must never be passed to the evolver or used as a gate condition. It is only written to the Trends sub-view for human observation.
- **`splits.py` uses SHA-256(seed, item_id) for deterministic assignment** — the split is stable: adding new items does not reshuffle existing ones. Changing `seed` reshuffles everything. Default ratios: 60/20/20 (train/val/test).

## Pipeline Verification

**Horizontal:** All 90+ API endpoints + static files (includes `/api/generate` and `/api/embed` proxies). **Vertical:** `ollama-queue submit` → DB row → daemon dequeue → subprocess → DB completed → API endpoints reflect → dashboard renders. Recurring: `schedule add` → `promote_due_jobs` → queue → run → `update_next_run`. DLQ: job fails max_retries → `move_to_dlq` → `dlq list` reflects. Full method: `projects/CLAUDE.md` § Pipeline Verification.

## Gotchas

- **Multi-backend setup** — configured via `OLLAMA_BACKENDS` in `~/.env` (sourced by the systemd service). **Current topology (3 backends):**
  - `http://127.0.0.1:11434` — GTX 1650, local, weight=2
  - `http://100.114.197.57:11434` — RTX 5080 (`desktop-fbl9e0c`), weight=1
  - `http://100.87.66.25:11434` — RTX 2070 Max-Q (`razer-docker-desktop`, Docker Desktop WSL2 node), weight=1, 8GB VRAM; Ollama runs in Docker with `--gpus all`; ollama-queue also runs in Docker on port 7683 for GPU name + VRAM reporting
  Default weights in `OLLAMA_BACKEND_WEIGHTS`. **DB weights take precedence** — use `PUT /api/backends/{url}/weight` to override per-backend routing weight at runtime without restarting the service. The remote Windows PC (razer) runs ollama-queue in Docker (`docker run -d --name ollama-queue -p 7683:7683 -e OLLAMA_URL=http://host.docker.internal:11434 --restart unless-stopped ollama-queue:latest`) for VRAM-aware routing. `Dockerfile` is in the project root. **When adding a new backend**, run `scripts/bootstrap-backend.sh http://<tailscale-ip>:11434 http://<queue-ip>:7683` — this installs Ollama + the backend agent container, which auto-registers with the queue via heartbeat push. The agent reconciles models automatically from `required_models` setting. Legacy `scripts/backend-onboard.sh` still works for manual model pulls. Note: `bitnet:10b` is excluded (not an Ollama model — served by `bitnet-server.service` on port 11435). **Keep this topology section up to date when backends are added or removed.**
- **`_gpu_name_cache` is populated lazily with a 600s TTL** — if the remote ollama-queue container wasn't up when the first `/api/backends` request fired, `gpu_name` will be cached as `null` for 10 minutes. Restart the `ollama-queue.service` to flush all in-process caches immediately.
- **`gpu_name: null` from Docker container = WSL2 GPU name quirk, not missing data** — `nvidia-smi --query-gpu=memory.used,memory.total` works inside Docker Desktop (VRAM % correct), but `--query-gpu=name` may return null. VRAM pressure routing works correctly; only the label in HostCard falls back to hostname.
- **Stall detector queries all OLLAMA_BACKENDS** — `sensing/stall.py:get_ollama_ps_models()` unions `/api/ps` from every configured backend. Before this fix it hardcoded `localhost:11434`, causing remote-backend jobs to always get a false-positive `+1.61` stall penalty (model "not loaded" on wrong host).
- **`HostCard` replaces `CurrentJob` + `InfrastructurePanel`** — the Now page uses a vertical `HostCard` list (one card per GPU backend) instead of a 2-column split. Each card shows: current job name/model/elapsed time, active eval, loaded models, VRAM+CPU gauges, and health status. Removed components: `CurrentJob.jsx`, `InfrastructurePanel.jsx` (+ its `BackendsPanel.jsx` sub-component). `fetchBackends` 15s interval is now owned by `Now.jsx` — not by the deleted `InfrastructurePanel`.
- **SPA dist/ is gitignored** — must `npm run build` after cloning
- **Worktree + `superhot-ui` `file:` dep** — `npm install` in a worktree creates a relative symlink for the `file:` local dep. The path is valid from the main repo depth but silently broken from `.worktrees/<branch>/`. Fix: `rm node_modules/superhot-ui && ln -s /home/justin/Documents/projects/superhot-ui node_modules/superhot-ui` in the worktree's spa dir. Permanent fix: run this in `postinstall`. See global `~/CLAUDE.md` gotcha (Lesson #1461).
- **check_same_thread=False** on SQLite — required for FastAPI worker threads, safe with WAL mode
- **httpx** must be installed for API tests — `pip install httpx`
- **Proxy endpoint uses sentinel job_id=-1** — `try_claim_for_proxy()` sets `current_job_id=-1` to distinguish proxy claims from real job execution. `release_proxy_claim()` only releases claims with `current_job_id=-1` to avoid accidentally releasing real jobs.
- **Proxy priority fields** — `/api/generate` and `/api/embed` accept `_priority` (int), `_source` (str), `_timeout` (int) in the JSON body. These are extracted before forwarding to Ollama, so they never reach the model server. Defaults: priority=0, source="proxy", timeout=120. Used by lessons-db eval pipeline to set job priority.
- **Deploy proxy before ARIA restart** — ARIA routes Ollama calls through port 7683. If ollama-queue is down, ARIA's activity predictions and organic naming fail with connection refused.
- esbuild JSX `h` shadowing — see `projects/CLAUDE.md` § Shared Gotchas.
- **esbuild dual-Preact crash** — `file:` deps (superhot-ui) with their own `node_modules/preact` cause two Preact instances. esbuild resolves imports via symlink real-path, so `preact/hooks` inside `superhot-ui/dist/` resolves to superhot-ui's preact — a different instance than the SPA's. Hooks from the wrong instance can't find `currentComponent` → `TypeError: Cannot read properties of undefined (reading '__H')`. Fix: the `alias` block in `esbuild.config.mjs` pins all Preact imports to `spa/node_modules/preact`. Don't remove it.
- **`dict.get(key, default)` does not guard against explicit `None` values** — if the key exists but is `None`, `.get(key, "")` returns `None`. Use `.get(key) or ""` to handle both missing keys and None values. Applies to DLQ entry model/command fields.
- **`dist/index.html` missing = `/ui/` returns 404** — `npm run dev` (watch mode) skips `injectVersionHash()`, so JS/CSS build but HTML is never written. Always run `npm run build` for a full production build after cloning or switching branches.
- **`db._lock` is `threading.RLock`** (not `Lock`) — existing callers hold the lock while calling `_connect()`. Do NOT change to `Lock` or nested acquisition will deadlock.
- **DLQ `timeout` column** — added in v2. If restoring from a pre-v2 backup, run `ALTER TABLE dlq ADD COLUMN timeout INTEGER NOT NULL DEFAULT 600` before restarting.
- **DLQ `max_retries` column** — added post-v2. If upgrading a live DB that predates this fix, run `python3 scripts/migrate_dlq_max_retries.py` (idempotent, safe to re-run).
- **migrate_timers.py** skips `telegram-brief-midday` (weekday-only) and `lessons-review` (monthly 14th) — migrated manually as 24h and 30d interval jobs respectively. `telegram-brief-midday` will now run 7 days/week (acceptable until cron scheduling lands). `lessons-review` next run: 2026-03-14 10:00.
- **Deployment sequence:** `cp queue.db queue.db.pre-v2` → stop service → `python3 scripts/migrate_timers.py --execute` → start service → verify `schedule list`
- **v2 schema migration on pre-existing DB** — `initialize()` uses `CREATE TABLE IF NOT EXISTS`, which skips if table exists. A pre-v1 `jobs` table needs 7 manual `ALTER TABLE ADD COLUMN` statements: `tag TEXT`, `max_retries INTEGER DEFAULT 0`, `retry_count INTEGER DEFAULT 0`, `retry_after REAL`, `stall_detected_at REAL`, `recurring_job_id INTEGER REFERENCES recurring_jobs(id)`, `resource_profile TEXT DEFAULT 'ollama'`. Run these before starting the service after upgrade.
- **Recurring job next_run after migration** — rebalancer sets `next_run` relative to now, not to original timer times. After running `migrate_timers.py`, manually set `next_run` values in the DB (use journal history to recover original times: `journalctl --user -u <name>.service`). Scheduled times: aria-full=23:30, morning=07:00, evening=21:00, aria-meta-learn=Mon 01:30, aria-suggest-automations=Sun 04:30, aria-organic-discovery=Sun 05:30, notion-vector-sync=+6h from last run.
- **`burst_regime` column** — added post-v2. If upgrading a live DB, run `ALTER TABLE daemon_state ADD COLUMN burst_regime TEXT DEFAULT 'unknown'` before restarting. Missing column causes `Burst regime check failed` error every poll cycle.
- **`analysis_md` column** — added post-v2. If upgrading a live DB, run `ALTER TABLE eval_runs ADD COLUMN analysis_md TEXT` before restarting.
- **`analysis_json` column** — added for structured analysis. If upgrading a live DB, run `ALTER TABLE eval_runs ADD COLUMN analysis_json TEXT` before restarting. Also add title columns: `ALTER TABLE eval_results ADD COLUMN source_item_title TEXT; ALTER TABLE eval_results ADD COLUMN target_item_title TEXT`. Index: `CREATE INDEX IF NOT EXISTS idx_eval_results_run_variant ON eval_results(run_id, variant)`.
- **`eval/analysis.py` is pure** — no DB, no HTTP, no side effects. Takes lists of dicts, returns lists of dicts. All DB access happens in `eval/engine.py:compute_run_analysis()`. Never import `db` or `api` into `eval/analysis`.
- **`score_transfer=0` is valid, not missing** — `_get_score()` in `eval/analysis.py` uses `s if s is not None else fallback` (explicit None check). The `x or fallback` pattern treats 0 as falsy and silently drops valid zero scores. Same applies to any numeric field that can legitimately be 0.
- **`eval.positive_threshold` setting** — integer 1-5, default 3. Controls TP/FP/FN classification in analysis and results API. Stored in settings table, read by `compute_run_analysis()` and `GET /api/eval/runs/{id}/results?classification=...`.
- **`/api/eval/variants/stability` must register before `/{variant_id}`** — FastAPI matches routes in registration order. If the parameterized route `/{variant_id}` is registered first, `GET /stability` will match it with `variant_id="stability"` and return 404. Literal paths always go before parameterized paths.
- **Shell scripts must exit 0 for "nothing to do"** — any non-zero exit code from a queued job is treated as failure. 3 consecutive failures open the circuit breaker, blocking all jobs. Scripts that check preconditions and bail early (e.g. "all work already done") must exit 0, not 1 or 2.
- **Never submit a queue job that calls back through the proxy** — if a queue job calls `_call_proxy()` → `POST /api/generate`, it will deadlock because the daemon holds `current_job_id` for the running job, blocking `try_claim_for_proxy()`. Use `threading.Thread` for work that needs the proxy. Lesson #1733.
- **`_recover_orphans()` must skip `proxy:` command sentinels** — proxy endpoints use sentinel jobs (`command LIKE 'proxy:%'`) to serialize Ollama access. On restart, these must be marked failed directly, not reset to pending, or the daemon will try to shell-execute them (exit 127 → DLQ). `get_pending_jobs()` also filters them out. Lessons #1734.
- **`schedule add` with `bash -c` requires the full script as one quoted arg** — CLI tokenizes `COMMAND...` args; `shlex.quote` is applied at join time. `bash -c source /path...` stores `source` as the script and `/path` as `$0`. Use `--command 'bash -c '"'"'source ...'"'"''` or pass a single-token arg. Lesson #1735.
- **Eval cooperative cancellation: re-check run status inside every loop iteration** — `run_eval_generate` and `run_eval_judge` run in background threads. `_recover_orphans()` marks the DB row `failed`/`cancelled` on daemon restart, but the thread is still alive. Each loop iteration must re-fetch the run row and return immediately if status is `failed`, `cancelled`, or the row is deleted. Without this, a restarted daemon produces a second overlapping execution while the zombie thread continues writing results.
- **`completed_at` is required on every terminal eval status transition** — `failed`, `cancelled`, and `completed` must all set `completed_at = time.time()` in the DB update. Missing it leaves the run open-ended in trend queries and the Runs list never shows elapsed time correctly. The `cancel_eval_run` endpoint was missing this; it is now fixed.
- **`repeat_eval_run` must start a background thread** — the endpoint creates a new DB run row and then must call `threading.Thread(target=run_eval_session, ...).start()`. The row alone does nothing; the daemon does not poll `eval_runs` for pending sessions. Previously the row was created but execution never started, producing a permanently-pending run.
- **`judge_rerun_eval_run` must copy gen_results from the source run** — the judge-rerun endpoint creates a new run row and calls the judge phase directly, bypassing generation. If `gen_results` is not copied from the original run to the new row before judging, the judge has nothing to score and returns empty metrics (precision=0, recall=0, F1=0).
- **`db._lock` must wrap every `db._connect()` call in eval endpoints** — `get_eval_trends` and any other eval read endpoint that calls `db._connect()` directly (outside the standard CRUD helpers) must do so inside `with db._lock:`. The RLock is reentrant, so nested acquisition is safe, but unguarded reads race against concurrent writes from background eval threads.
- **SPA fetch errors must be checked explicitly** — `fetch()` resolves (does not throw) on 4xx/5xx responses; only network failures reject. Always check `res.ok` and throw on failure, otherwise the UI silently ignores HTTP errors and shows stale state. `cancelEvalRun` in `stores/eval.js` was missing this check.
- **Action button feedback: use `useActionFeedback` hook** — all non-immediate action buttons (cancel, submit, pause, retry, etc.) use `src/hooks/useActionFeedback.js`. Pattern: `const [fb, act] = useActionFeedback(); <button disabled={fb.phase==='loading'} onClick={() => act('Loading…', fn, result => `Done: ${result.id}`)}>`; render `{fb.msg && <div class={`action-fb action-fb--${fb.phase}`}>{fb.msg}</div>}` below the button. Success labels must be specific (e.g. `"Run #12 started"`, `"Job #6350 queued"`), not generic "Done". Hook lives in `src/hooks/useActionFeedback.js` — one instance per button.
- **`useActionFeedback` double-click guard** — `run()` returns early if `state.phase === 'loading'`. Place this check as the first line to prevent concurrent executions from the same button.
- **Rules of Hooks in action buttons** — all `useActionFeedback()` calls must appear before any conditional `return null` in the component. If an early guard precedes the hooks, React will throw "rendered fewer hooks than previous render" on re-render. Move hook calls to the top of the function body.
- **`evalActiveRun` auto-detection + sessionStorage staleness** — `fetchEvalRuns()` auto-detects active runs (`generating`/`judging`) started outside the SPA (curl, cron, API) and calls `startEvalPoll()` to begin live progress updates. On store init, if `evalActiveRun` is loaded from sessionStorage, verify via `GET /api/eval/runs/{id}/progress` — clear if terminal, resume polling if still live. Identity guard (`run_id !== _storedId`) prevents async `.then()` from clobbering a new run. See `stores/eval.js`.
- **`promote_eval_run` auto-resolves winner from DB** — the promote endpoint accepts an empty body `{}` and resolves model/prompt_template_id/temperature/num_ctx from `run.winner_variant` → `eval_variants` row. The shared core is `do_promote_eval_run()` in `eval/promote.py`; both the API endpoint and `check_auto_promote()` call it. Error routing: run-not-found → 404; not-complete/no-winner/variant-not-in-db → 400; lessons-db unreachable → 502.
- **`check_auto_promote` never raises** — wraps `_check_auto_promote_inner` in `try/except Exception` and logs on any error. Same pattern as `generate_eval_analysis`. Called from `run_eval_session` after `generate_eval_analysis` completes. Three gates: winner F1 ≥ `eval.f1_threshold`; winner F1 > production F1 + `eval.auto_promote_min_improvement`; `error_budget_used ≤ eval.error_budget`. Auto-promote is off by default (`eval.auto_promote = false`) — must be explicitly enabled. Stability window gate: if `eval.stability_window > 0`, winner must have passed threshold in the last N completed runs.
- **`is_production`/`is_recommended` cleared on all variants at promote time** — `do_promote_eval_run` sets winner to `is_recommended=1, is_production=1` then clears all other variants to 0 in the same DB transaction. The VariantRow badges (`★ Recommended`, `Production`) update automatically on the next `fetchEvalVariants()` call.
- **`repeat_eval_run` must insert `status='queued'` not `'pending'`** — `_recover_orphans()` queries `status IN ('generating', 'judging', 'pending')` on daemon restart and marks all matches `failed`. A repeat run inserted as `'pending'` will be killed on the next restart before it does any work. `create_eval_run()` always uses `'queued'`; any raw INSERT bypass must match. Issue #41.
- **`db._connect()` must always be called INSIDE `with self._lock:`** — every write method in `db/` follows the pattern `with self._lock: conn = self._connect()`. Reversing the order (`conn = self._connect()` before the lock) creates a race window between connection acquisition and lock protection. The three recurring-job methods (`delete_recurring_job`, `update_recurring_job`, `delete_recurring_job_by_id`) had this reversed — see issue #39.
- **`move_to_dlq` must set `completed_at` on the job row** — `prune_old_data()` filters `WHERE completed_at IS NOT NULL`. Jobs marked `status='dead'` without `completed_at` are invisible to the pruner and accumulate indefinitely. Issue #49.
- **`_call_generate_description` must go through the queue proxy** — background description generation must call `POST http://127.0.0.1:7683/api/generate`, not `localhost:11434` directly. Direct Ollama calls bypass concurrency serialization and can collide with running queue jobs. Issue #54.
- **`POST /api/schedule/{id}/generate-description` returns immediately** — the endpoint spawns a background thread and returns `{"ok": True}` without waiting for the Ollama response. The description appears on the next `GET /api/schedule` poll (typically 5–15s). Do not expect the result synchronously.
- **`PUT /api/schedule/{id}` only rebalances on `_REBALANCE_FIELDS` changes** — `_REBALANCE_FIELDS = {"interval_seconds", "cron_expression", "priority", "pinned"}`. Cosmetic edits (description, tag, command, model, timeout) skip the O(N) rebalance write. Add new scheduling-affecting fields to this set; leave cosmetic ones out.
- **DLQ `_sweep_lock` also guards the `list_dlq` fetch** — `DLQScheduler._sweep()` fetches the unscheduled DLQ list inside `_sweep_lock`, not before acquiring it. This prevents a stale-list race where `on_job_completed` and `periodic_sweep` interleave: without the guard, one caller reads the list before the other marks an entry, causing double-reschedule.
- **Auto-promote gate 2 must never silently skip** — `_check_auto_promote_inner` must log and `return` (not `pass`) when production metrics are unparseable. A silent `pass` leaves `production_f1=None`, which causes gate 2 (`F1 > production + min_improvement`) to silently evaluate as skipped — allowing a regression to auto-promote. Issue #43.
- **`do_promote_eval_run` winner-set and clear-others must be in one lock** — setting `is_production=1` on the winner and clearing others to 0 are two DB writes. If done with separate lock acquisitions, a concurrent promote call can interleave and leave no production variant. Both UPDATEs must be inside a single `with db._lock:` block. Issue #50.
- **`BurstDetector` singleton needs `threading.Lock`** — `record_submission()` is called from FastAPI worker threads; `regime()` is called from the daemon poll thread. The `sorted(self._baseline_samples)` call in `regime()` iterates the deque while `record_submission()` may be appending, causing `RuntimeError: deque mutated during iteration`. Always acquire `self._lock` in both methods. Issue #45.
- **`StallDetector._last_stdout` needs `threading.Lock`** — `update_stdout_activity()` is written from worker threads (pipe-drain); `get_stdout_silence()` is read from the poll thread. Use `self._stdout_lock` (a plain `Lock`, not RLock) for both methods and `forget()`. `_cpu_prev` is single-threaded (poll only) and does not need a lock. Issue #52.
- **`prime_eval_datasource` returns HTTP 502 on upstream failure** — the endpoint raises `HTTPException(502)` when the lessons-db datasource is unreachable or returns non-2xx. Callers must handle 502, not just check `res.ok` for a 200 with `ok=False`. Issue #51.
- **`run_eval_generate` re-checks status after opportunistic throttle sleep** — after `_sleep_fn(_OPPORTUNISTIC_THROTTLE_SLEEP_S)` wakes, the run may have been cancelled during the sleep. Re-fetch the run row and return immediately if status is `failed` or `cancelled`. Also guard before the final `update_eval_run(status='judging')` at the end of the loop. Without this, a cancel during the last sleep overwrites the `cancelled` status with `judging`. Issue #42.
- **`GET /api/eval/settings` masks `eval.data_source_token`** — the endpoint returns `"***"` for the token. Never log or return the raw token value. `PUT /api/eval/settings` also rejects `data_source_url` that doesn't target `127.0.0.1` or `localhost` (SSRF protection). Issue #56.
- **Migration scripts need `sqlite3.connect(timeout=30)`** — without a timeout, if the daemon holds a write lock during migration, the script raises `OperationalError: database is locked` and exits non-zero. 3 consecutive non-zero exits open the circuit breaker, blocking all queue jobs. Always pass `timeout=30` and handle the `"locked"` case with `sys.exit(0)`. Issue #58.
- **`intercept/enable` requires ≥1 included consumer** — the endpoint raises 400 if `included_consumer_ids` is empty or missing. This guard prevents accidentally redirecting all port-11434 traffic before any consumers have been onboarded.
- **`disable_intercept` returns `enabled=True` on iptables failure** — if the `iptables -D` command fails, `intercept.py` catches the error, logs WARNING, and returns `{"enabled": True, "error": "..."}`. The API endpoint then raises HTTP 500. The caller should treat non-ok responses as "intercept still active".
- **Scanner `deadlock_check` needs `with db._lock:`** — `deadlock_check()` calls `db._connect()` to look up active jobs. Must always be wrapped in `with db._lock:` (same as all other `_connect()` calls). Missing the lock causes a race with daemon write transactions.
- **`_live_scan_*` functions: check returncode, not just exception** — `subprocess.run()` on `ss`/`lsof`/`netstat` does not raise on non-zero exit; it returns a `CompletedProcess` with `returncode != 0`. Log a WARNING and return `[]` on non-zero rather than silently returning an empty list on success. Same pattern in `_reload_systemd` and `_restart_service` in patcher.py.
- **Consumer `patch_path` may be empty** — `revert_consumer()` must check `patch_path` before calling `Path(patch_path).exists()`. An empty string produces a false-positive hit on the current directory.
- **config/scanner.py and config/patcher.py use `subprocess` with known system binaries** — `S603`/`S607` (bandit/ruff subprocess rules) are suppressed via `per-file-ignores` in `ruff.toml`, matching the same pattern as `daemon/`. Do not add inline `# noqa` comments — they will be flagged as RUF100 (redundant) if the per-file-ignore is already in effect.
- **`GET /api/eval/trends` returns `variants` as an object keyed by variant id, not an array** — SPA components must not iterate `variants` directly with `.map()`. Call `normalizeTrends()` in `stores/eval.js` before assigning to `evalTrends.value`; this converts the object to an array (with `id` attached), aggregates `trend_direction`/`completed_runs`/`judge_reliability`/`item_count_growing` at the top level, and normalises `started_at` ISO strings to unix `timestamp` fields. Any new Trends-tab component must consume the normalised shape, not the raw API response.
- **`INSERT OR IGNORE` skips pre-existing seeded rows — always pair with `UPDATE WHERE column IS NULL` backfill** — when adding a new column to a table that has seeded rows (e.g. `eval_variants` system variants A–H + M), `ALTER TABLE ADD COLUMN` sets the column to NULL on existing rows. The seed `INSERT OR IGNORE` then silently skips those rows, leaving the new column unpopulated. Always follow the migration with `UPDATE <table> SET <column> = <value> WHERE <column> IS NULL` to backfill pre-existing rows.
- **`next_run` advancement during skip is a poll-suppression sentinel, not the authoritative schedule** — when a duplicate is skipped, `next_run` advances past the trigger window to prevent repeated promotions during the same poll cycle. `update_recurring_next_run()` (called after job completion) always overwrites this with the real fixed-delay value. Never treat the sentinel value as the canonical next scheduled time.
- **`skip_count_24h` field on recurring job API response** — counts `skipped_duplicate` health-log events per job in the last 24h. Used by the Gantt skip badge (↻N). Added to `GET /api/schedule/list` via a batch query against `health_log` at response-build time.
- **`poll_once()` must not clobber the proxy sentinel** — the daemon's "set idle" transitions (`job is None`, `cannot admit`) must guard against `current_job_id == -1`. If a proxy is in-flight, omit `current_job_id` from the `update_daemon_state()` call; only the proxy's own `release_proxy_claim()` should clear it. Without this guard, the daemon clears the sentinel every 5s poll cycle, allowing multiple concurrent proxy requests that leave jobs permanently stuck in `status='running'`. Fix: `daemon/loop.py` guard at every `update_daemon_state(state='idle', current_job_id=None)` call site. (#67)
- **`OllamaModels._list_local_cache` is class-level** — tests that mock `list_local()` must call `OllamaModels._invalidate_list_cache()` in teardown, or the 60s cached result bleeds into subsequent tests and causes false positives.
- **`HealthMonitor` now has `__init__`** — if subclassing or constructing directly in tests, call `super().__init__()` to initialise `_vram_cache`. Missing this raises `AttributeError` on the first `get_vram_pct()` call.
- **`_reload_systemd()` and `_restart_service()` return `bool`** — callers that previously ignored the return value should check it and log or raise on `False`. Silent failures from these calls were masking systemd and service restart errors.
- **`get_pending_jobs()` defaults to `exclude_sentinel=True`** — pass `exclude_sentinel=False` at any call site that intentionally needs proxy sentinel jobs (command LIKE `'proxy:%'`) included in the result. The default is safe for all normal dequeue paths.
- **`deferral_scheduler._do_sweep()` is two-phase** — Phase 1 fetches ALL deferred entries and resumes any whose `scheduled_for` has passed. Phase 2 fetches unscheduled-only entries and finds fitting slots. The original single-call design (`list_deferred(unscheduled_only=True)`) filtered out entries WITH `scheduled_for`, making scheduled resumptions impossible. Tests must assert `list_deferred` is called twice: once with no args (phase 1), once with `unscheduled_only=True` (phase 2).
- **`_estimate_model_vram(model)` regex extracts param count from model name** — parses patterns like `7b`, `14b`, `0.5b` from the model string. The `_PARAM_TO_VRAM` lookup table maps common sizes to Q4-quantized VRAM estimates (e.g. `7b` → 4.5GB). Models without a recognizable size pattern default to 4.0GB. This is a heuristic — actual VRAM depends on quantization level and context size.
- **`job_metrics` table** — stores per-job Ollama response metrics (tokens/sec, eval duration, model). Populated by `metrics_parser.py` which extracts metrics from job stdout if it contains Ollama JSON. The `model_performance` API endpoint aggregates these into per-model stats.
- **`backend_metrics` table** — per-backend inference metrics captured from proxy responses (`backend_url, model, eval_count, eval_duration_ns, load_duration_ns, tok_per_min, recorded_at`). Only populated when `eval_count` is present (generate requests, not embed). `store_backend_metrics()` is called after `resp.json()` on the non-streaming path and via `metrics_fn` callback on the `done=true` chunk on the streaming path. `get_backend_stats()` aggregates by `(backend_url, model)`. `GET /api/metrics/backends` exposes it. The Performance tab's "Per-Backend Throughput" section is hidden until the first proxy generate request completes.
- **`deferrals` table** — tracks job deferral lifecycle. Jobs move `pending → deferred` via `db.defer_job()` and back to `pending` via `db.resume_deferred_job()`. The `scheduled_for` column is set by `update_deferral_schedule()` when the deferral scheduler finds a fitting slot.
- **DLQ auto-reschedule columns** — `auto_reschedule_count`, `rescheduled_job_id`, `reschedule_reasoning`, `last_reschedule_at` on the `dlq` table. `set_setting("dlq.auto_reschedule", True)` enables automatic sweep. `dlq.chronic_failure_threshold` (default 3) prevents infinite reschedule loops.
- **`mark_dlq_scheduling` is the crash-safety marker** — does NOT increment `auto_reschedule_count`. Only `update_dlq_reschedule` increments the count and sets resolution. The two-step pattern (mark → submit job → finalize) prevents double-counting if the process crashes between submit and finalize.
- **`RuntimeEstimator` uses precision-weighted posterior** — `post_precision = prior_precision + sample_precision`, `post_std = sqrt(1/post_precision)`. The old pseudo-count formula (`(n0 * prior_var + n * sample_var) / (n0 + n)`) was inconsistent with the mean formula and over-estimated uncertainty when sample data was abundant.
- **`performance_curve.fit()` must reset state at entry** — `_warmup_slope`, `_warmup_intercept`, `_eval_slope`, `_eval_intercept`, `_residual_std` must all be set to `None` at the top of `fit()`. Without this, stale parameters from a previous fit survive if the new fit has insufficient data, causing `predict_tok_per_min()` and `predict_warmup()` to return results based on obsolete data.
- **`_set_job_retry` must clear `completed_at`** — the UPDATE sets `completed_at = NULL` alongside `status = 'pending'`. Without this, `prune_old_data()` considers the retried job eligible for pruning (it has a non-NULL `completed_at`) and may delete it before it re-runs.
- **Daemon stdout capture has a 128KB sliding window** — `_MAX_STDOUT_BYTES = 128 * 1024`. The `_append_stdout` callback pops oldest chunks when total exceeds the cap. Without this, a chatty Ollama job (e.g. full response streaming) accumulates unbounded stdout in memory, which can exceed the service's `MemoryMax=512M` and trigger OOM kill.
- **Falsy-zero antipattern in settings** — `db.get_setting()` returns strings or `None`. `x or default` treats `"0"` as truthy (correct) but `int(x) or default` treats `0` as falsy (wrong). Always use `int(x) if x is not None else default` for numeric settings that can legitimately be 0 (e.g. `error_budget=0` meaning zero tolerance, `poll_interval=0`).
- **DLQ priority sort is ascending** — lower number = higher importance (1=critical, 10=background). `sorted(entries, key=lambda e: e.get("priority", 0))` is correct. The inverted sort (descending) processed background jobs before critical ones.
- **`GET /api/health` returns `cpu_count`** — read from `/proc/cpuinfo` via `sensing/health.py:get_cpu_count()` (not `os.cpu_count()`), cached at module level in `api/health.py` as `_CPU_COUNT`. SPA `stores/health.js` exposes a `cpuCount` signal; `Now.jsx` and `CurrentJob.jsx` use `load_avg / cpuCount * 100` to convert load average to a percentage before passing to `ResourceGauges`. CPU pause/resume thresholds are `multiplier × 100` (not `× 50`).
- **`OllamaModels.list_local` is a classmethod** — `_fetch_list_local` is also a classmethod; `_list_local_lock = threading.Lock()` is a class attribute. Call `OllamaModels.list_local()` directly (no instance needed). Existing `self.list_local()` calls still work. Tests that mock `list_local` must call `OllamaModels._invalidate_list_cache()` in teardown or the 60s cached result bleeds into subsequent tests.
- **`DLQManager` has two entry points for failure routing** — `handle_failure(job_id, reason)` acquires `db._lock` and is safe to call from outside any lock. `_handle_failure_locked(job_id, reason)` assumes the caller already holds `db._lock` and must be used from within `with self.db._lock:` blocks (e.g. `executor.py`). Using `handle_failure` from inside a lock causes RLock re-entry which silently succeeds but breaks the single-caller atomicity guarantee — a FastAPI reader can observe a `failed` job with no DLQ entry yet.
- **`_add_column_if_missing` and `_run_migrations` must NOT commit internally** — `initialize()` in `db/schema.py` is the sole commit owner for the full init flow. Any future helper added to the init chain must omit internal `conn.commit()` calls.
- **`cancel_job` 409 detail is `"Job is not in a cancellable state"`** — not `"already_terminal"`. Update any test or integration code that checks the exact 409 message string.
- **`health.evaluate()` returns a safe result on missing settings keys** — previously raised `KeyError` when a settings key was absent; now falls back to defaults. Callers no longer need to guard against `KeyError` from `evaluate()`.
- **`max_pause_duration_seconds` setting** — default 600 (10 min). If the health monitor stays paused longer than this, it force-resumes regardless of metric values. Prevents indefinite pause when a single metric sits in the hysteresis band. `evaluate()` accepts `paused_since` parameter.
- **`SystemSnapshot.vram_known` field** — `bool`, default `True`. Set to `False` when `get_vram_pct()` returns `None` or raises. When `vram_known=False`, slot scoring skips VRAM hard gates and resource headroom checks to avoid phantom-zero scheduling.
- **First-ever eval run requires manual promote** — `check_auto_promote` returns early (no promotion) when no production variant exists. The first eval run must be manually promoted to establish a baseline. This prevents auto-promoting a mediocre variant with no comparison data.
- **Model list cache TTL is 15s** — reduced from 60s. `_invalidate_list_cache()` forces fresh fetch on next call.
- **Priority bounds enforced: 0-10** — `set_priority` returns HTTP 400 for out-of-range values. `GET /api/schedule/events` limit capped at 1000. `suggest_schedule_time` top_n capped at 20.
- **Batch operations return 404 for unknown tags** — `batch_toggle_schedule` and `batch_run_schedule` return 404 when no recurring jobs match the tag, instead of 200 with `updated: 0`.
- **`data-mood` must be on the wrapper div, not `.t-frame`** — superhot-ui mood selectors are `[data-mood="X"] .t-frame {}`. Placing `data-mood` directly on the `.t-frame` element makes it a self-referencing selector that never matches. Always wrap: `<div data-mood="dread"><div class="t-frame">…</div></div>`.
- **`ShGlitch` is edge-triggered** — only fires on `healthy → false` transitions, not on every render where `healthy` is false. Use `useRef` to track the previous `healthy` value and set `glitchActive` only when it transitions from `true` to `false`. Persistent false-healthy state should show `ShThreatPulse`, not a continuous glitch.
- **`evalActiveRun` in `HostCard` comes as a prop, not a store import** — `Now.jsx` passes `evalActiveRun={evalActiveRun.value}` (the unwrapped value). `HostCard` must NOT import `evalActiveRun` from `stores/eval.js`; it receives a plain value. Importing the signal inside HostCard breaks when the component is tested in isolation and creates an indirect store dependency.
- **`fetchBackends` interval is owned by `Now.jsx`** — after `InfrastructurePanel` was deleted, its 15s `setInterval(fetchBackends, 15000)` was removed. `Now.jsx` must own this interval (in its own `useEffect`). Without it, `backendsData` only refreshes on mount and HostCards show stale backend state.
- **POJO vnode tests: use `mockClear`, not `mockReset`, on `useSignal`** — `mockReset` strips the default implementation function, causing `useSignal()` to return `undefined` and crashing any test that calls it via the default path. `mockClear` only clears call history while preserving the default implementation. Use `mockClear` for teardown on mocks that have meaningful defaults (e.g. `jest.fn().mockImplementation(...)`).
- **`useSignal` mock call order in POJO tests follows component initialization order** — `mockReturnValueOnce` queues are FIFO. If a component initializes `logLines` (1st call) then `expanded` (2nd call), the mock setup must match: `useSignal.mockReturnValueOnce({value: []}).mockReturnValueOnce({value: true})`. Swapping the order is a silent bug — tests may pass by accident if both values are truthy.
- **`judge_parse_failures` column on `eval_runs`** — `INTEGER DEFAULT 0`. Counts how many judge responses failed to parse during an eval run. Logged as WARNING when > 0.
- **`_retry_on_busy()` wraps high-frequency DB writes** — `log_health()`, `update_daemon_state()`, `submit_job()`, `complete_job()` retry up to 2 times on `SQLITE_BUSY` with exponential backoff (0.1s, 0.2s). Retries happen inside `_lock`.
- **`clear_stall_detected(job_id)`** — new DB method. Called when stall posterior drops below threshold, clearing `stall_detected_at` so any future spike gets a fresh grace period instead of inheriting an expired one.
- **RuntimeEstimator excludes negative durations** — non-positive durations (from clock skew) are excluded, not clamped to 0.1. Logs WARNING with count. Falls back to prior if all durations are invalid.
- **PerformanceCurve predictions capped at 100k tok/min** — prevents `math.exp(huge)` → `inf` on degenerate fits (nearly identical x-values). Falls back to single-point slope (-0.7) when `abs(slope) > 10`.
- **Consumer config TOCTOU guard** — `patch_consumer()` checks `scanned_mtime` against current mtime before patching. Raises `ValueError` if file was modified between scan and patch.
- **BurstDetector activates after 5 samples** — reduced from 10. On low-traffic systems (1-2 jobs/day), detection activates in 2-3 days instead of 5-10.
- **Cron scheduling is timezone-aware** — `_local_dt()` helper converts timestamps via `ZoneInfo("localtime")` with UTC fallback. Prevents DST-related double-fire or missed-fire for cron-scheduled jobs.
- **Cron expressions validated at submission time** — `add_recurring_job()` validates via `croniter()` before INSERT. Invalid cron returns HTTP 400, not a 500 at promotion time.
- **DLQ chronic threshold check is atomic** — re-reads `auto_reschedule_count` from DB inside `_sweep_lock` to prevent double-reschedule at the threshold boundary.
- **`has_healthy_remote_backend()`** — sync read from `_health_cache` in `backend_router.py`; returns True when any non-127.0.0.1/localhost backend has a cached healthy status within the 30s TTL. Used by `executor._can_admit`.
- **CPU gate bypass for remote GPU** — in `executor._can_admit`, when `health.evaluate()` says `should_pause` but the reason is CPU-load-only (no RAM/Swap/VRAM in reason string) and `has_healthy_remote_backend()` is True, the local CPU gate is bypassed and inference proxies to the remote GPU. Logs "bypassing local CPU load gate" at INFO.
- **`_backend_gpu_name` TTL split** — HTTP 200 response caches for 600s (hardware stable); network exception caches for 30s (backend may be restarting). Previously a single 600s TTL caused null gpu_name to persist 10min after a restarting backend came back.
- **DLQ `_do_sweep()` logs at DEBUG when `dlq.auto_reschedule` is disabled** — previously silent; now visible in debug logs without polluting INFO.
- **`_safeJson(resp)` in SPA stores** — checks `Content-Type` includes `application/json` before calling `.json()`; applied to all fetch calls in `stores/index.js`. Prevents JSON parse errors on unexpected HTML error responses.
- **`HostCard` derives serving state per-backend** — `matchesBackend(backend, currentJob)` cross-references `currentJob.model` against `backend.loaded_models`. The serving backend gets a highlighted state via `data-mood="dawn"`. All-unreachable state renders each card with `data-mood="dread"` + `ShThreatPulse active persistent` wrapper. `evalActiveRun` is passed as `.value` (raw) from `Now.jsx` — `HostCard` must NOT import the signal from stores directly.
- **`useShatter` hook integrates with atmosphere effect budget** — `trackEffect('shatter')` is called before firing; if `isOverBudget()` returns true, the shatter is suppressed. The `onComplete` callback passed to `shatterElement()` calls the cleanup function from `trackEffect()`. Three tiers: `earned` (7 fragments), `complete` (6), `routine` (3). The hook returns `[ref, fire]` — attach `ref` to the button element, call `fire()` in `onClick`.
- **`atmosphere.js` store signals are global singletons** — `healthMode`, `escalation`, `effectBudget` are module-level `@preact/signals` values. `app.jsx` drives `healthMode` from backend health data on each fetch cycle. Components read these signals reactively — do not import and cache `.value` in a closure.
- **`sh-stagger-children` CSS class for entry choreography** — applied to the root container of each page. superhot-ui's CSS handles the stagger timing via `nth-child` selectors. The class must be on the immediate parent of the elements to stagger, not on a wrapper div above it.
- **Terminal voice is ALL CAPS in all UI copy** — every string visible to the user (button labels, empty states, action feedback, toast messages, tab subtitles, onboarding text) uses UPPERCASE. Tests must assert uppercase strings. This is intentional piOS aesthetic, not a bug.
- **`ShEmptyState` replaces all custom empty states** — `EmptyState.jsx` and `ErrorState.jsx` were deleted. All pages now use `ShEmptyState` from `superhot-ui/preact` with terminal mantras. Import: `import { ShEmptyState } from 'superhot-ui/preact'`.
- **Audio toggle in Settings** — opt-in procedural SFX via `playSfx()` from superhot-ui. Controlled by a setting in the Settings page. Off by default — user must explicitly enable.
- **`ganttInteractingRef` in Plan/index.jsx** — suppresses the 10s load-map refresh while user hovers Gantt or LoadMapStrip, preventing mid-interaction repaints.
- **`normalizeTrends()` surfaces `no_cluster_data: true`** — F1LineChart shows a specific actionable message (not a generic empty state) when cluster labels are missing from the eval results.
- **`_get_weights()` reads DB first, env var fallback** — `backend_router._get_weights()` uses a deferred import (`import ollama_queue.api as _api` inside the function body) to read `db.list_backends()` weights at call time. Deferred import is required because `backend_router` is imported by `api/__init__.py` — a module-level import would create a circular dependency. DB weight wins; env var is the fallback; default is 1.0.
- **`BITNET_URL` and `OLLAMA_QUEUE_PORT` are read dynamically** — these are no longer module-level constants. Both are read via `os.environ.get(...)` at call time in `proxy.py` and `backend_router.py`. This means `PUT /api/backends/{url}/weight` and any env var change take effect on the next request without a service restart.
- **Entropy anomaly alert uses rising-edge dedup** — `_entropy_anomaly_active` in `Daemon.__init__` prevents WARNING spam. The anomaly log and suspension are triggered only on the first transition into anomalous state; subsequent poll cycles while anomalous are silent. The flag clears when the anomaly resolves. Also: `_compute_entropy()` uses `max(0.0, ...)` to collapse IEEE 754 negative-zero (`log2(1.0) = 0.0` → `-0.0`) which was printing as `-0.00` in schedule events.
- **`BACKENDS` (env var list) vs `db.list_backends()` (DB table)** — `BACKENDS` in `backend_router.py` is the canonical list of all registered backends, parsed from `OLLAMA_BACKENDS` env var. `db.list_backends()` only returns backends that have custom weight overrides in the `backends` DB table. Backend validation (proxy `_backend` param, eval settings) must use `BACKENDS` or the union of both — never `db.list_backends()` alone. Failure mode: a backend present in env var but not in DB gets rejected with 422, causing eval circuit breaker failures.
- **VRAM estimate warning dedup** — `OllamaModels._vram_warned` (`ClassVar[set[str]]`) ensures each unknown-model VRAM fallback warning logs once per process lifetime. Without this, the remote backend's ~30 unregistered models produce ~180 WARNING lines per dashboard refresh cycle, drowning out operational logs.
- **Daemon startup logging** — `_recover_orphans()` always logs a summary line: either "clean startup" or orphan counts (eval runs, jobs, proxy sentinels). `run()` logs `"Daemon started (poll_interval=Ns)"` after recovery completes. These are the first INFO lines after service start — useful for verifying the process is alive.
- **`get_models()` uses `asyncio.to_thread` for subprocess calls** — `api/models.py` is `async def` (needed for `await fetch_all_backend_models()`), but `om.get_loaded()` and `om.list_local()` call `subprocess.run()`. Both are wrapped in `asyncio.to_thread()` to avoid blocking the event loop for up to 15s.
- **Eval preflight queries all `BACKENDS` for model availability** — `engine.py` preflight checks `OllamaModels.list_local()` (local binary) AND queries `/api/tags` from all remote backends in `BACKENDS`. Without this, models only available on remote backends are rejected with "Models not installed" and the eval run fails immediately. Local/localhost backends are skipped (already covered by `list_local()`).
- **`GET /api/backends` returns `weight` and `checked_at`** — `weight` comes from DB, falls back to `OLLAMA_BACKEND_WEIGHTS` env var, then 1.0. `checked_at` is derived from `_health_cache[url][0]` (monotonic timestamp) converted to wall time via `time.time() - (time.monotonic() - cached_ts)`. `BackendsTab.BackendCard` uses `checked_at` for `ShFrozen` freshness indicator and `weight` for the editable weight display.
- **`PUT /api/backends/{url}/heartbeat` — remote push API** — a remote ollama-queue instance calls this periodically (recommended 30s) to push its own health state (`healthy`, `gpu_name`, `vram_pct`, `vram_total_gb`, `loaded_models`, `available_models`) directly into the primary's routing caches (`_health_cache`, `_hw_cache`, `_gpu_name_cache`, `_loaded_cache`, `_models_cache`). The primary no longer needs to poll the remote on each routing decision. Auto-registers the backend if absent — the act of pushing proves reachability. Uses `exclude_unset=True` on the Pydantic model so partial pushes (e.g. only `healthy`) don't overwrite other caches with default 0 values. Implemented in `backend_router.receive_heartbeat()`.
- **Remote host push implementation** — on the remote Docker container, add `curl -X PUT http://<primary-ip>:7683/api/backends/http://<remote-ip>:11434/heartbeat -H "Content-Type: application/json" -d '{"healthy":true,"gpu_name":"RTX 2070","vram_pct":45}' &` to a cron every 30s, or call it from the remote ollama-queue's own health loop. `scripts/backend-onboard.sh` should be extended to set up this cron.
- **Backend agent port 11435** — the backend agent runs on port 11435 (not 11434 which is Ollama). Queue-side command dispatch (`POST /api/backends/{url}/command`) derives the agent URL by replacing the port: `{scheme}://{hostname}:11435/{action}`. If the agent port changes, update `_AGENT_PORT` in `ollama_queue/api/backends.py`.
- **Backend agent heartbeat includes GPU fields** — the agent sends `gpu_name`, `vram_pct`, `vram_total_gb`, and `loaded_models` via nvidia-smi and Ollama `/api/ps`. These populate `_vram_total_cache` which the `required-models` endpoint uses for VRAM-based filtering. Without GPU fields, all agent-managed backends get only core models (safe fallback but defeats hardware-aware assignment).
- **`required_models` setting stores the canonical model list** — seeded in `db/schema.py` with 13 models across 3 tiers (core/standard/optional). Each entry has `name`, `vram_mb`, and `tier`. The setting is JSON-serialized; `required_models.py` handles both string and list formats from the DB.
- **Backend agent auto-registers via heartbeat** — when an agent pushes its first heartbeat, `backend_heartbeat()` in `backends.py` auto-registers it in the BACKENDS list and DB. No separate `POST /api/backends` call needed. The act of pushing proves reachability.
- **Command dispatch uses GET for read-only actions** — `_GET_ACTIONS = {"status"}`. All other actions (sync-models, update-ollama, restart-ollama) use POST. Sending POST to a GET-only agent endpoint returns 405.
- **CPU/RAM routing pressure is tier 5 in 6-tier router** — backends with >90% CPU or >90% RAM (from heartbeat data) are skipped. Fail-open: if all backends are overloaded, the filter returns the full list (better to route slowly than not at all). Cache TTL: CPU 60s, RAM 120s. Stale entries are ignored (treated as "no data").
- **`bootstrap-backend.sh` replaces `backend-onboard.sh` for new hosts** — the bootstrap script sets up Ollama container + backend agent container in one command. `backend-onboard.sh` is legacy (manual model pulls only, no agent). For existing hosts, the agent container can be added separately: `docker run -d --name ollama-backend-agent -p 11435:11435 -v /ollama:/ollama -e QUEUE_URL=http://<queue>:7683 -e BACKEND_URL=http://<self>:11434 ghcr.io/parthalon025/ollama-backend-agent:latest`.

## Design Doc

See `docs/` for implementation notes and design decisions.

## Design System Usage

**Full guide:** `docs/llm-guide-design-system.md` (~700 lines) — LLM reference for applying the design system to the queue dashboard.

**Before building any UI:** Read `docs/llm-guide-design-system.md`. Follow §1.5 Strategy Stack (Outcome-Driven + Friction Reduction + Trust & Predictability + Action-Oriented + Feedback-Rich). Behavioral target: fire-and-forget confidence.

Pipeline: ui-template (base) → superhot-ui (theme) → ollama-queue (consumer). Key mappings:
- **Host state** → `HostCard` (one per GPU backend — job/eval/model/gauges; `data-mood` wrapper cascades mood)
- **Running job** → `ShStatusBadge` (running) + `ShFrozen` elapsed timer
- **Queued job** → QueueList rows + `ShStatusBadge` (queued/waiting)
- **Failed/DLQ** → `ShShatter` dismiss (earned tier, 7 fragments) + `ShStatusBadge` (error); `dread` mood on page/card
- **Health degraded** → `ShGlitch` (edge-triggered on `healthy → false`, connection loss, eval failure) + `ShThreatPulse` (offline backends, VRAM/RAM breach, stuck eval, circuit breaker)
- **Resources** → `HostGaugeBar` + gradient color ramp (VRAM >90% error, >80% warning; CPU `multiplier × 100`)
- **KPI summary** → `ShStatsGrid` / `ShStatCard`; `ShFrozen` wraps time-sensitive KPIs (30s/2m/5m thresholds)
- **Page headers** → `ShPageBanner` (namespace/page/subtitle pixel-art header, TAB_CONFIG-driven)
- **Eval progress** → `ShPipeline` in `ActiveRunProgress.jsx`
- **Tables** → `ShDataTable` (History, Models tabs)
- **Loading** → `ShSkeleton`; **CRT scanline toggle** → `ShCrtToggle`
- **OFFLINE watermark** → `applyMantra` / `removeMantra` (escalation-driven, triggers at `escalation >= 2`)
- **Entry choreography** → `sh-stagger-children` class on all 9 page root containers
- **Empty states** → `ShEmptyState` with terminal mantras (replaces deleted `EmptyState.jsx` / `ErrorState.jsx`)
- **Button actions** → `useShatter` hook: earned (7 fragments) for DLQ/eval cancel, complete (6) for submits/promote, routine (3) for toggles
- **Atmosphere** → `stores/atmosphere.js`: `healthMode` (operational/degraded/critical), `escalation` (0–3), effect density budget (max 3 simultaneous)
- **Terminal voice** → ALL CAPS piOS style on all UI copy; **Audio** → opt-in procedural SFX via `playSfx()`
- **Tabs:** Now=dawn, Plan=wonder, History=dread, Models=nostalgic

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
- Lessons: `lessons-db search` to query, `lessons-db capture` to add. DB is authoritative — never write lesson .md files directly.

## Local AI Review
- Code review: `ollama-code-review .`

## Semantic Search
- Generate: `bash scripts/generate-embeddings.sh`
- Storage: `.embeddings/` (gitignored)
