# ollama-queue LLM Guide

Read this first when working on any part of ollama-queue.

## What This System Does

ollama-queue serializes all Ollama LLM tasks on a Linux workstation to prevent model-loading contention. A polling daemon (5s interval) checks system health, dequeues jobs by priority, and runs them as subprocesses. A FastAPI server exposes 90+ REST endpoints and serves a Preact SPA dashboard. A single SQLite file (`~/.local/share/ollama-queue/queue.db`) holds all persistent state.

## Request Flow

```
CLI submit / API POST / recurring job promotion
  -> jobs table (status='pending')
  -> daemon poll_once() dequeues by priority (SJF + aging)
  -> _can_admit() checks health, VRAM budget, concurrency
  -> _run_job() runs subprocess.Popen, drains pipes via select()
  -> complete_job() stores result, triggers DLQ sweep + deferral sweep
  -> metrics_parser extracts tok/s if Ollama JSON detected in stdout

Proxy path (interactive):
  POST /api/generate -> try_claim_for_proxy() polls for sentinel (job_id=-1)
  -> forward to Ollama at :11434 -> release_proxy_claim() on done
```

## Critical Invariants

1. **Lock ordering: `_lock` before `_connect()`** -- Every DB write follows `with self._lock: conn = self._connect()`. Reversing causes TOCTOU races.

2. **`_lock` is `threading.RLock`** -- Changing to `Lock` deadlocks because callers hold the lock while calling `_connect()`.

3. **Proxy sentinel: `current_job_id = -1`** -- The proxy sets this to serialize Ollama access. `poll_once()` must never clear it when a proxy request is in flight. Guard all "set idle" transitions with `if current_job_id == -1: skip`.

4. **Falsy-zero antipattern** -- `int(x) or default` treats 0 as falsy. Use `int(x) if x is not None else default` for any numeric setting or score that can be 0 (error_budget, poll_interval, score_transfer).

5. **Proxy sentinel in recovery** -- `_recover_orphans()` must skip `command LIKE 'proxy:%'` sentinels. Never reset them to pending or the daemon will shell-execute them (exit 127).

6. **Eval runs: status='queued' not 'pending'** -- `_recover_orphans()` kills pending runs on restart. Use `'queued'` for new eval runs.

7. **Eval analysis.py is pure** -- No DB, no HTTP, no imports from `db` or `api`. Takes dicts, returns dicts.

8. **completed_at on every terminal transition** -- `failed`, `cancelled`, `complete` must all set `completed_at`. Missing it breaks `prune_old_data()` and trend queries.

9. **API route registration order** -- Fixed paths (`/stability`, `/retry-all`) before parameterized (`/{id}`). FastAPI matches first.

10. **Shell scripts must exit 0 for "nothing to do"** -- Non-zero exits count as failures. 3 consecutive failures open the circuit breaker.

## Common Tasks

### Run tests
```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
pytest                          # all 1,587 tests
pytest tests/test_daemon.py -x  # one file, stop on first failure
pytest -k "test_proxy" -x       # by name pattern
```

### Lint and format
```bash
make lint       # ruff + SPA lint + shellcheck + pip-audit
make format     # ruff format + prettier
```

### Add a new API endpoint
1. Add route function to the appropriate `api/<domain>.py` file
2. Use `import ollama_queue.api as _api` then `db = _api.db` at handler top
3. If adding a new file, create router and add `app.include_router()` in `api/__init__.py`
4. Fixed paths before parameterized paths in registration order
5. Write tests in `tests/test_api*.py` using `from fastapi.testclient import TestClient`

### Add a new DB column
1. Add `self._add_column_if_missing(conn, "table", "col", "TYPE DEFAULT val")` in `db/schema.py:_run_migrations()`
2. Update all INSERT/SELECT queries that touch that table
3. If table has seeded rows (e.g. `eval_variants`), add `UPDATE table SET col = val WHERE col IS NULL` after the migration to backfill
4. Add the column to `CREATE TABLE IF NOT EXISTS` in `initialize()` for fresh DBs
5. Document in CLAUDE.md Gotchas if the column breaks existing DBs

### Add a new setting
1. Add to `DEFAULTS` or `EVAL_SETTINGS_DEFAULTS` in `db/schema.py`
2. Read via `db.get_setting("key")` -- returns JSON-decoded value or None
3. Expose in `api/settings.py` if user-configurable
4. Use explicit None check for numeric values: `int(x) if x is not None else default`

### Build the SPA
```bash
cd ollama_queue/dashboard/spa
npm install
npm run build   # production
npm run dev     # watch mode
```

## File Size Budget

Target ~300 lines per module. When a file exceeds ~500 lines, split using the mixin pattern (db, daemon) or phase modules (eval). Current largest files: `eval/judge.py` (801), `daemon/executor.py` (784), `eval/engine.py` (663).

## Architecture Patterns

- **Mixin composition** (db/, daemon/): Split large classes across files. Python MRO assembles them into `Database` or `Daemon`. All mixins access `self._lock`/`self._connect()` (db) or `self.db`/`self.health` (daemon).
- **Closure-captured db** (api/): `import ollama_queue.api as _api; db = _api.db`. Set once by `register_routes()`.
- **Phase modules** (eval/): `engine.py` orchestrates; `generate.py`, `judge.py`, `promote.py`, `analysis.py` each own one phase.
- **Background threads for long ops**: Eval sessions, description generation, analysis. The daemon does NOT poll eval_runs -- the API endpoint must start the thread.

## Gotcha Quick-Reference

| Domain | Gotcha |
|--------|--------|
| db | `_lock` is RLock, not Lock. Lock-before-connect always. |
| db | `INSERT OR IGNORE` skips existing rows. Pair with `UPDATE WHERE col IS NULL`. |
| daemon | Proxy sentinel guard on every "set idle" path in `poll_once()`. |
| daemon | Circuit breaker: 3 consecutive Ollama failures. Check shell script exit codes. |
| daemon | 128KB stdout sliding window. Do not accumulate unbounded output. |
| eval | Cooperative cancellation: re-check run status every loop iteration. |
| eval | `repeat_eval_run` must start background thread. Row alone does nothing. |
| eval | `judge_rerun` must copy gen_results before judging. |
| eval | `score=0` is valid. Use `s if s is not None`, not `s or fallback`. |
| api | Eval variant routes: register `/stability` before `/{variant_id}`. |
| api | Token masking: `eval.data_source_token` returns `"***"`. SSRF guard on URL. |
| api | `useActionFeedback` hook for all action buttons. Hook calls before `return null`. |
| proxy | Never submit a queue job that calls back through the proxy (deadlock). |
| proxy | `_priority`, `_source`, `_timeout` stripped from body before forwarding. |
| models | `_list_local_cache` is class-level. Tests must call `_invalidate_list_cache()`. |
| sensing | `BurstDetector` needs `threading.Lock`. Deque mutates during iteration. |
| sensing | `StallDetector._last_stdout` needs `_stdout_lock`. Cross-thread access. |
| config | `deadlock_check()` needs `with db._lock:`. Calls `_connect()` directly. |
| config | Check subprocess `returncode`, not just exceptions. |
| scheduling | Deferral sweep is two-phase: all entries first, then unscheduled-only. |
| scheduling | DLQ priority sort is ascending (1=critical, 10=background). |

## Cross-Domain References

| If working in... | Also read... |
|-------------------|-------------|
| api/ | db/ (data layer), eval/ (background sessions) |
| daemon/ | db/, models/, sensing/, scheduling/, dlq.py |
| eval/ | engine.py (all DB helpers), api/proxy (HTTP calls to self) |
| scheduling/ | db/, models/ (VRAM estimation), sensing/ (failure classification) |
| config/ | db/ (consumers table), api/consumers.py |

## Testing Patterns

- Fixture: `db(tmp_path)` in `conftest.py` creates fresh `Database` per test
- Autouse: `reset_burst_detector_singleton` clears EWMA state between tests
- API tests: `TestClient(create_app(db))` with the `db` fixture
- Daemon tests: Mock `subprocess.Popen`, patch `ollama_queue.daemon.executor.subprocess`
- Always call `OllamaModels._invalidate_list_cache()` in teardown if mocking `list_local()`
