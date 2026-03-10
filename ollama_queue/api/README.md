# api/ — FastAPI REST API

## Purpose

Exposes 90+ HTTP endpoints organized by domain. Each module defines an `APIRouter`
with related routes; `register_routes()` assembles them into the FastAPI app at
startup.

## Architecture

All route modules access the database through a **closure-captured module-level
reference**:

```python
import ollama_queue.api as _api
db = _api.db  # set once by register_routes()
```

This avoids dependency injection boilerplate while keeping the db instance
configurable (test injection via `create_app(db)`). Routes are plain functions
(not class methods) registered on per-module `APIRouter` instances.

**Route registration order matters**: fixed-path routes (e.g. `/api/dlq/retry-all`,
`/api/eval/variants/stability`) must be registered before parameterized routes
(`/api/dlq/{dlq_id}/retry`, `/api/eval/variants/{variant_id}`) to prevent shadowing.

## Modules

| File | Routes | Key Exports |
|------|--------|-------------|
| `__init__.py` | -- | `register_routes(app, db)`, module-level `db` reference |
| `jobs.py` | Status, queue, submit, cancel, priority, ETAs, history, heatmap, durations, deferrals | `SubmitJobRequest` |
| `health.py` | `/api/health` — health log + burst regime | `router` |
| `proxy.py` | `/api/generate`, `/api/embed` — Ollama proxy with queue serialization | `_proxy_ollama_request`, `_iter_ndjson` |
| `settings.py` | Settings CRUD + daemon pause/resume | `router` |
| `schedule.py` | Recurring jobs CRUD, rebalance, load-map, suggest, batch ops, run-now | `RecurringJobCreate`, `RecurringJobUpdate` |
| `dlq.py` | DLQ list, retry, dismiss, clear, schedule-preview, reschedule | `router` |
| `models.py` | Model list, catalog search, pull lifecycle, metrics, performance curve | `router` |
| `consumers.py` | Consumer scan, include/ignore/revert, health check, intercept mode | `ConsumerIncludeRequest` |
| `eval_runs.py` | Eval run CRUD, progress, results, cancel, repeat, judge-rerun, promote, analysis, confusion | `router` |
| `eval_settings.py` | Eval settings, datasource test/prime, eval scheduling | `router` |
| `eval_trends.py` | Eval trend aggregation | `router` |
| `eval_variants.py` | Eval variant CRUD, stability, config diff | `router` |

## Key Patterns

- **Proxy serialization**: `/api/generate` and `/api/embed` poll `try_claim_for_proxy()`
  until the daemon releases the sentinel (`current_job_id = -1`). The proxy creates a
  real jobs-table entry for tracking, then forwards the request to Ollama. Queue-specific
  fields (`_priority`, `_source`, `_timeout`) are popped from the body before forwarding.

- **Streaming**: `/api/generate` with `stream=True` uses `_iter_ndjson()` to yield
  line-aligned NDJSON chunks and releases the proxy claim on `done=true`.

- **Background threads**: Long operations (eval sessions, description generation,
  analysis) spawn daemon threads to avoid blocking the HTTP response. The daemon does
  NOT poll `eval_runs` for pending work — the API endpoint must start the thread.

- **Token masking**: `GET /api/eval/settings` replaces `eval.data_source_token` with
  `"***"`. `PUT /api/eval/settings` validates `data_source_url` targets localhost only
  (SSRF protection).

## Dependencies

**Depends on**: `db/`, `eval/`, `models/`, `config/`, `sensing/`, `scheduling/`
**Depended on by**: `app.py` (route registration), `eval/engine.py` (HTTP proxy calls to self)
