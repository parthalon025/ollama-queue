# ollama-queue

Ollama job queue scheduler with priority, health monitoring, and web dashboard. Serializes all Ollama-using systemd tasks to prevent model loading contention.

**Repo:** https://github.com/parthalon025/ollama-queue

## Structure

```
ollama_queue/
  __init__.py
  app.py              # FastAPI app factory: create_app(db) → mounts all routers + static SPA
  cli.py              # Click CLI: submit, status, queue, history, pause, resume, cancel, serve, schedule, dlq, defer, metrics, settings
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
    health.py         # /api/health endpoint
    jobs.py           # Job submit, status, queue, history, cancel, batch
    models.py         # Model stats, catalog search, performance curve
    proxy.py          # /api/generate + /api/embed Ollama proxy (priority, streaming)
    schedule.py       # Recurring jobs, load-map, suggest, rebalance
    settings.py       # Settings CRUD endpoints

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

  eval/               # Eval pipeline — prompt evaluation with A/B variants + LLM judge
    __init__.py       # Re-exports public names from engine, judge, metrics, promote, analysis
    engine.py         # Session orchestration, run CRUD, scheduling modes, seed/reproducibility
    generate.py       # run_eval_generate: variant-based generation with cooperative cancellation
    judge.py          # run_eval_judge: LLM-based scoring with agreement tracking
    promote.py        # do_promote_eval_run, check_auto_promote (3-gate auto-promote logic)
    analysis.py       # Pure analysis (no DB/HTTP): per-item breakdown, bootstrap CI, stability, config diff

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
        hooks/        # Shared Preact hooks (useActionFeedback)
        pages/        # Page-level components (Now, Plan/, History, Models, etc.)
        stores/       # Signal stores by domain (eval, health, models, queue, schedule, settings)
        views/        # Eval sub-views (Runs, Variants, Trends, Settings)
      dist/           # Production build output (gitignored)

scripts/
  migrate_timers.py            # Migrate 8 of 10 systemd timers to recurring jobs
  migrate_dlq_max_retries.py   # Add max_retries column to existing dlq table (idempotent)

tests/                           # 1,588 tests, 100% line coverage
```

## How to Run

```bash
# Activate venv (MUST use python3.12, not python3)
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate

# Run tests (1,587 total, 100% line coverage)
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

Sidebar nav (desktop) + bottom tab bar (mobile). 8 views: **Now** (2-column command center: running job, queue, resource gauges, KPI cards, alert strip) + **Plan** (24h Gantt timeline with "now" needle, 48-bucket load-map density strip with DLQ/deferral slot markers, ρ traffic intensity badge, "Suggest slot" button highlighting top-3 low-load windows; tag-grouped recurring jobs with collapsible sections, bulk actions, expandable detail panels) + **History** (DLQ entries with reschedule status badges/reasoning, deferred jobs panel, duration trends, activity heatmap, job list) + **Models** (model table) + **Perf** (model performance table, cross-model performance curve chart, 24h×7d load heatmap, system health gauges) + **Settings** (thresholds, defaults, retention, DLQ auto-reschedule, proactive deferral, daemon controls) + **Consumers** (scan button, consumer cards with status badges and include/ignore/revert actions, intercept toggle with status banner).

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

## Pipeline Verification

**Horizontal:** All 90+ API endpoints + static files (includes `/api/generate` and `/api/embed` proxies). **Vertical:** `ollama-queue submit` → DB row → daemon dequeue → subprocess → DB completed → API endpoints reflect → dashboard renders. Recurring: `schedule add` → `promote_due_jobs` → queue → run → `update_next_run`. DLQ: job fails max_retries → `move_to_dlq` → `dlq list` reflects. Full method: `projects/CLAUDE.md` § Pipeline Verification.

## Gotchas

- **SPA dist/ is gitignored** — must `npm run build` after cloning
- **Worktree + `expedition33-ui` `file:` dep** — `npm install` in a worktree creates a relative symlink for the `file:` local dep. The path is valid from the main repo depth but silently broken from `.worktrees/<branch>/`. Fix: `rm node_modules/expedition33-ui && ln -s /home/justin/Documents/projects/expedition33-ui node_modules/expedition33-ui` in the worktree's spa dir. Permanent fix: run this in `postinstall`. See global `~/CLAUDE.md` gotcha (Lesson #1461).
- **check_same_thread=False** on SQLite — required for FastAPI worker threads, safe with WAL mode
- **httpx** must be installed for API tests — `pip install httpx`
- **Proxy endpoint uses sentinel job_id=-1** — `try_claim_for_proxy()` sets `current_job_id=-1` to distinguish proxy claims from real job execution. `release_proxy_claim()` only releases claims with `current_job_id=-1` to avoid accidentally releasing real jobs.
- **Proxy priority fields** — `/api/generate` and `/api/embed` accept `_priority` (int), `_source` (str), `_timeout` (int) in the JSON body. These are extracted before forwarding to Ollama, so they never reach the model server. Defaults: priority=0, source="proxy", timeout=120. Used by lessons-db eval pipeline to set job priority.
- **Deploy proxy before ARIA restart** — ARIA routes Ollama calls through port 7683. If ollama-queue is down, ARIA's activity predictions and organic naming fail with connection refused.
- esbuild JSX `h` shadowing — see `projects/CLAUDE.md` § Shared Gotchas.
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
- **`evalActiveRun` sessionStorage staleness** — on store init, if `evalActiveRun` is loaded from sessionStorage (service restart), immediately verify via `GET /api/eval/runs/{id}/progress`. If status is terminal or fetch fails, clear `evalActiveRun.value` and remove the sessionStorage key. Use an identity guard (`run_id !== _storedId`) to prevent the async `.then()` from clobbering a new run started during the fetch window. See `stores/eval.js` after `API` declaration.
- **`promote_eval_run` auto-resolves winner from DB** — the promote endpoint accepts an empty body `{}` and resolves model/prompt_template_id/temperature/num_ctx from `run.winner_variant` → `eval_variants` row. The shared core is `do_promote_eval_run()` in `eval/promote.py`; both the API endpoint and `check_auto_promote()` call it. Error routing: run-not-found → 404; not-complete/no-winner/variant-not-in-db → 400; lessons-db unreachable → 502.
- **`check_auto_promote` never raises** — wraps `_check_auto_promote_inner` in `try/except Exception` and logs on any error. Same pattern as `generate_eval_analysis`. Called from `run_eval_session` after `generate_eval_analysis` completes. Three gates: winner F1 ≥ `eval.f1_threshold`; winner F1 > production F1 + `eval.auto_promote_min_improvement`; `error_budget_used ≤ eval.error_budget`. Auto-promote is off by default (`eval.auto_promote = false`) — must be explicitly enabled. Stability window gate: if `eval.stability_window > 0`, winner must have passed threshold in the last N completed runs.
- **`is_production`/`is_recommended` cleared on all variants at promote time** — `do_promote_eval_run` sets winner to `is_recommended=1, is_production=1` then clears all other variants to 0 in the same DB transaction. The VariantRow badges (`★ Recommended`, `Production`) update automatically on the next `fetchEvalVariants()` call.
- **`repeat_eval_run` must insert `status='queued'` not `'pending'`** — `_recover_orphans()` queries `status IN ('generating', 'judging', 'pending')` on daemon restart and marks all matches `failed`. A repeat run inserted as `'pending'` will be killed on the next restart before it does any work. `create_eval_run()` always uses `'queued'`; any raw INSERT bypass must match. Issue #41.
- **`db._connect()` must always be called INSIDE `with self._lock:`** — every write method in `db/` follows the pattern `with self._lock: conn = self._connect()`. Reversing the order (`conn = self._connect()` before the lock) creates a race window between connection acquisition and lock protection. The three recurring-job methods (`delete_recurring_job`, `update_recurring_job`, `delete_recurring_job_by_id`) had this reversed — see issue #39.
- **`move_to_dlq` must set `completed_at` on the job row** — `prune_old_data()` filters `WHERE completed_at IS NOT NULL`. Jobs marked `status='dead'` without `completed_at` are invisible to the pruner and accumulate indefinitely. Issue #49.
- **`_call_generate_description` must go through the queue proxy** — background description generation must call `POST http://127.0.0.1:7683/api/generate`, not `localhost:11434` directly. Direct Ollama calls bypass concurrency serialization and can collide with running queue jobs. Issue #54.
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
- **`poll_once()` must not clobber the proxy sentinel** — the daemon's "set idle" transitions (`job is None`, `cannot admit`) must guard against `current_job_id == -1`. If a proxy is in-flight, omit `current_job_id` from the `update_daemon_state()` call; only the proxy's own `release_proxy_claim()` should clear it. Without this guard, the daemon clears the sentinel every 5s poll cycle, allowing multiple concurrent proxy requests that leave jobs permanently stuck in `status='running'`. Fix: `daemon/loop.py` guard at every `update_daemon_state(state='idle', current_job_id=None)` call site. (#67)
- **`OllamaModels._list_local_cache` is class-level** — tests that mock `list_local()` must call `OllamaModels._invalidate_list_cache()` in teardown, or the 60s cached result bleeds into subsequent tests and causes false positives.
- **`HealthMonitor` now has `__init__`** — if subclassing or constructing directly in tests, call `super().__init__()` to initialise `_vram_cache`. Missing this raises `AttributeError` on the first `get_vram_pct()` call.
- **`_reload_systemd()` and `_restart_service()` return `bool`** — callers that previously ignored the return value should check it and log or raise on `False`. Silent failures from these calls were masking systemd and service restart errors.
- **`get_pending_jobs()` defaults to `exclude_sentinel=True`** — pass `exclude_sentinel=False` at any call site that intentionally needs proxy sentinel jobs (command LIKE `'proxy:%'`) included in the result. The default is safe for all normal dequeue paths.
- **`deferral_scheduler._do_sweep()` is two-phase** — Phase 1 fetches ALL deferred entries and resumes any whose `scheduled_for` has passed. Phase 2 fetches unscheduled-only entries and finds fitting slots. The original single-call design (`list_deferred(unscheduled_only=True)`) filtered out entries WITH `scheduled_for`, making scheduled resumptions impossible. Tests must assert `list_deferred` is called twice: once with no args (phase 1), once with `unscheduled_only=True` (phase 2).
- **`_estimate_model_vram(model)` regex extracts param count from model name** — parses patterns like `7b`, `14b`, `0.5b` from the model string. The `_PARAM_TO_VRAM` lookup table maps common sizes to Q4-quantized VRAM estimates (e.g. `7b` → 4.5GB). Models without a recognizable size pattern default to 4.0GB. This is a heuristic — actual VRAM depends on quantization level and context size.
- **`job_metrics` table** — stores per-job Ollama response metrics (tokens/sec, eval duration, model). Populated by `metrics_parser.py` which extracts metrics from job stdout if it contains Ollama JSON. The `model_performance` API endpoint aggregates these into per-model stats.
- **`deferrals` table** — tracks job deferral lifecycle. Jobs move `pending → deferred` via `db.defer_job()` and back to `pending` via `db.resume_deferred_job()`. The `scheduled_for` column is set by `update_deferral_schedule()` when the deferral scheduler finds a fitting slot.
- **DLQ auto-reschedule columns** — `auto_reschedule_count`, `rescheduled_job_id`, `reschedule_reasoning`, `last_reschedule_at` on the `dlq` table. `set_setting("dlq.auto_reschedule", True)` enables automatic sweep. `dlq.chronic_failure_threshold` (default 3) prevents infinite reschedule loops.
- **`mark_dlq_scheduling` is the crash-safety marker** — does NOT increment `auto_reschedule_count`. Only `update_dlq_reschedule` increments the count and sets resolution. The two-step pattern (mark → submit job → finalize) prevents double-counting if the process crashes between submit and finalize.
- **`RuntimeEstimator` uses precision-weighted posterior** — `post_precision = prior_precision + sample_precision`, `post_std = sqrt(1/post_precision)`. The old pseudo-count formula (`(n0 * prior_var + n * sample_var) / (n0 + n)`) was inconsistent with the mean formula and over-estimated uncertainty when sample data was abundant.
- **`performance_curve.fit()` must reset state at entry** — `_warmup_slope`, `_warmup_intercept`, `_eval_slope`, `_eval_intercept`, `_residual_std` must all be set to `None` at the top of `fit()`. Without this, stale parameters from a previous fit survive if the new fit has insufficient data, causing `predict_tok_per_min()` and `predict_warmup()` to return results based on obsolete data.
- **`_set_job_retry` must clear `completed_at`** — the UPDATE sets `completed_at = NULL` alongside `status = 'pending'`. Without this, `prune_old_data()` considers the retried job eligible for pruning (it has a non-NULL `completed_at`) and may delete it before it re-runs.
- **Daemon stdout capture has a 128KB sliding window** — `_MAX_STDOUT_BYTES = 128 * 1024`. The `_append_stdout` callback pops oldest chunks when total exceeds the cap. Without this, a chatty Ollama job (e.g. full response streaming) accumulates unbounded stdout in memory, which can exceed the service's `MemoryMax=512M` and trigger OOM kill.
- **Falsy-zero antipattern in settings** — `db.get_setting()` returns strings or `None`. `x or default` treats `"0"` as truthy (correct) but `int(x) or default` treats `0` as falsy (wrong). Always use `int(x) if x is not None else default` for numeric settings that can legitimately be 0 (e.g. `error_budget=0` meaning zero tolerance, `poll_interval=0`).
- **DLQ priority sort is ascending** — lower number = higher importance (1=critical, 10=background). `sorted(entries, key=lambda e: e.get("priority", 0))` is correct. The inverted sort (descending) processed background jobs before critical ones.

## Design Doc

See `docs/` for implementation notes and design decisions.

## Design System Usage

**Full guide:** `docs/llm-guide-design-system.md` (~700 lines) — LLM reference for applying the design system to the queue dashboard.

**Before building any UI:** Read `docs/llm-guide-design-system.md`. Follow §1.5 Strategy Stack (Outcome-Driven + Friction Reduction + Trust & Predictability + Action-Oriented + Feedback-Rich). Behavioral target: fire-and-forget confidence.

Pipeline: ui-template (base) → expedition33-ui (theme) → ollama-queue (consumer). Key mappings:
- **Running job** → BattlePanel + StatBar progress (gustave/active)
- **Queued job** → TurnSlot in TurnQueue (verso/waiting)
- **Failed/DLQ** → HUDFrame + InkSplatter (maelle/dread)
- **Priority** → GlyphBadge (critical=maelle, high=enemy, normal=gustave, low=verso)
- **Resources** → StatBar (HP=RAM, AP=VRAM) with threshold markers
- **Daemon state** → GlyphBadge (running=gustave/dawn, paused=sciel/nostalgic, offline=maelle/dread)
- **Tabs:** Now=lumiere/dawn, Plan=continent/wonder, History=wasteland/dread, Models=continent/nostalgic
- **Battle metaphor:** Job queued=unit enters turn queue, running=active turn, complete=victory, fail=unit falls

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
