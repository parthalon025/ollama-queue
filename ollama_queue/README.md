# ollama_queue — Package Architecture

The `ollama_queue` package is organized into 8 domain subpackages plus a handful of
top-level modules that wire them together. Each domain owns one vertical slice of
functionality and exposes its public API through `__init__.py` re-exports.

## Domain Map

| Domain | Purpose | Pattern |
|--------|---------|---------|
| [`api/`](api/README.md) | FastAPI REST endpoints (90+ routes) | APIRouter per concern, closure-captured `db` |
| [`daemon/`](daemon/README.md) | Polling loop + job executor | Mixin classes composing a single `Daemon` |
| [`db/`](db/README.md) | SQLite persistence layer | Mixin classes composing a single `Database` |
| [`eval/`](eval/README.md) | Prompt evaluation pipeline | Engine orchestrator + phase modules |
| [`scheduling/`](scheduling/README.md) | Time-based job orchestration | Scheduler + slot scoring + sweep schedulers |
| [`sensing/`](sensing/README.md) | System monitoring + anomaly detection | Stateless snapshot + stateful detectors |
| [`models/`](models/README.md) | Ollama model management + estimation | Client + hierarchical estimators |
| [`config/`](config/README.md) | Consumer detection + traffic intercept | Scanner/patcher pipeline + iptables |

## Top-Level Modules

| Module | Role |
|--------|------|
| `app.py` | FastAPI application factory: `create_app(db)` mounts all routers, static SPA, and CORS |
| `cli.py` | Click CLI: `ollama-queue serve`, `submit`, `status`, `schedule`, `dlq`, etc. |
| `dlq.py` | `DLQManager`: failure routing logic (retry with backoff or move to DLQ) |
| `intelligence.py` | `LoadPatterns`: hourly/daily load profiles from health log history |
| `metrics_parser.py` | Parses Ollama JSON response metrics (tok/s, eval duration, model size) |

## Assembly

`app.py:create_app(db)` is the entry point. It calls `api.register_routes(app, db)`,
which sets the module-level `api.db` reference and includes all 12 APIRouter modules.
The `cli.py:serve` command constructs a `Database`, calls `create_app`, starts the
`Daemon` in a background thread, and runs uvicorn.

## Cross-Domain Dependencies

```
cli.py ──> app.py ──> api/* ──> db/*
                          \──> eval/*
                          \──> models/*
                          \──> config/*
                          \──> sensing/*
                          \──> scheduling/*

daemon/* ──> db/*
         \──> models/*
         \──> sensing/*
         \──> scheduling/*
         \──> dlq.py

eval/* ──> db/* (via engine.py helpers)
       \──> api/proxy (HTTP calls to self for Ollama access)

scheduling/* ──> db/*
             \──> models/* (VRAM estimation for slot scoring)
             \──> sensing/* (failure classification)
```

## Key Patterns

- **Mixin composition**: `db/` and `daemon/` split large classes across files using
  mixins. Python MRO assembles them into a single `Database` or `Daemon` class.
  All mixins access `self._lock` and `self._connect()` (db) or `self.db` (daemon).

- **Closure-captured db**: API route modules access the database through the
  module-level `ollama_queue.api.db` reference, set once at startup by
  `register_routes()`. Each handler calls `db = _api.db` at the top.

- **Threading model**: SQLite is synchronous with `check_same_thread=False` and WAL
  mode. All writes go through `self._lock` (an `RLock`). FastAPI runs handlers in
  worker threads. The daemon poll loop is single-threaded; job execution uses a
  `ThreadPoolExecutor`.
