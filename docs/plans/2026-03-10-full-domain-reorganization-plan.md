# Full Domain Reorganization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reorganize ollama-queue from 4 monolithic files into domain subpackages with ~300-line modules, optimized for Claude Code AI readability.

**Architecture:** Domain-driven subpackages (api/, db/, eval/, daemon/, scheduling/, sensing/, models/, config/) with layered architecture (API → Business Logic → Persistence). Database class uses mixin pattern to split implementation while preserving single-class API. JS store splits by domain with re-export index.

**Tech Stack:** Python 3.12, FastAPI/APIRouter, SQLite, Preact/signals, esbuild

**Design doc:** `docs/plans/2026-03-10-full-domain-reorganization-design.md`
**PRD:** `tasks/prd.json` (13 tasks)

---

## Critical Context

### Import Patterns (read before ANY task)
- **Tests use `patch.object()`**, not string-based `patch('module.path')`. This means mock patches are refactoring-safe — they follow the object reference, not the import path.
- **`api.py` uses closure-captured `db`** — `create_app(db)` captures the Database instance in closure. Route handlers access `db` directly (not via `app.state`).
- **`eval_engine.py` uses `TYPE_CHECKING` guard** — `from ollama_queue.db import Database` is only for type hints, not runtime.
- **`db.py` is a single class** — all methods use `self._conn` and `self._lock`. Mixin pattern preserves this.

### Safety Protocol
1. One file split at a time
2. `pytest --timeout=120 -x -q` after EVERY step that touches Python
3. `npm run build` after EVERY step that touches JavaScript
4. Commit after each passing step
5. Never delete old file until new structure passes all tests

### ruff.toml Updates Required
When files move, per-file-ignores must be updated:
- `ollama_queue/api.py` → `ollama_queue/api/*.py`
- `ollama_queue/eval_engine.py` → `ollama_queue/eval/*.py`
- `ollama_queue/daemon.py` → `ollama_queue/daemon/*.py`
- `ollama_queue/health.py` → `ollama_queue/sensing/health.py`
- `ollama_queue/models.py` → `ollama_queue/models/client.py`
- `ollama_queue/intercept.py` → `ollama_queue/config/intercept.py`
- `ollama_queue/scanner.py` → `ollama_queue/config/scanner.py`
- `ollama_queue/patcher.py` → `ollama_queue/config/patcher.py`

## Quality Gates

Run between EVERY batch:
```bash
# Python tests
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q

# SPA build (after JS changes)
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build

# Lint
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m ruff check ollama_queue/

# Coverage (final gate only)
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --cov=ollama_queue --cov-fail-under=99 --timeout=120 -q
```

---

## Batch 1: api.py → api/ subpackage (PRD Task 1)

This is the highest-ROI split and establishes the pattern for all subsequent splits. FastAPI's `APIRouter` was designed for exactly this use case.

### Task 1.1: Create api/ package skeleton with shared state

**Files:**
- Create: `ollama_queue/api/__init__.py`
- Create: `ollama_queue/app.py`
- Keep: `ollama_queue/api.py` (don't delete yet)

**Step 1: Create `ollama_queue/app.py`**

This is the new application factory. It creates the FastAPI app and mounts all routers.

```python
"""FastAPI application factory for ollama-queue."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from ollama_queue.db import Database


def create_app(db: Database) -> FastAPI:
    """Application factory. Takes a Database instance for test injection."""
    app = FastAPI(title="Ollama Queue")

    class _NoCacheSPA(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/ui"):
                response.headers["cache-control"] = "no-store"
            return response

    app.add_middleware(_NoCacheSPA)

    # Import here to avoid circular imports — route modules import from api package
    from ollama_queue.api import register_routes

    register_routes(app, db)
    return app
```

**Step 2: Create `ollama_queue/api/__init__.py`**

```python
"""API route package — assembles all domain routers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)

# Module-level references — set by register_routes(), used by route modules
db: Database | None = None


def register_routes(app: FastAPI, db_instance: Database) -> None:
    """Register all API routers and set shared state."""
    import ollama_queue.api as _self

    _self.db = db_instance

    # Import route modules (each defines a `router` and registers endpoints)
    from ollama_queue.api import (
        consumers,
        dlq,
        eval_runs,
        eval_settings,
        eval_trends,
        eval_variants,
        health,
        jobs,
        models,
        proxy,
        schedule,
        settings,
    )

    # Include all routers
    for mod in [
        jobs,
        proxy,
        schedule,
        consumers,
        models,
        health,
        settings,
        dlq,
        eval_runs,
        eval_variants,
        eval_settings,
        eval_trends,
    ]:
        app.include_router(mod.router)
```

**Step 3: Verify the package imports without errors**

Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -c "import ollama_queue.api"`
Expected: No import error (route modules don't exist yet, but the package loads)

Note: This will fail because register_routes tries to import route modules. That's expected — we'll add them next. For now, just verify the files compile:

Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m py_compile ollama_queue/app.py && .venv/bin/python -m py_compile ollama_queue/api/__init__.py`

**Step 4: Commit skeleton**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/app.py ollama_queue/api/__init__.py
git commit -m "WIP: add api/ package skeleton and app.py factory"
```

### Task 1.2: Extract route modules from api.py

**Strategy:** For each domain, create a route file that:
1. Imports `router = APIRouter()` from FastAPI
2. Imports `db` from `ollama_queue.api` (the module-level reference)
3. Copies the route handlers verbatim from api.py
4. Copies any domain-specific helper functions

**Files to create (one at a time, in this order):**

Each file follows this template:
```python
"""<Domain> API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

import ollama_queue.api as _api

_log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/endpoint")
def endpoint_handler():
    db = _api.db
    # ... handler body from api.py ...
```

**Order of extraction** (extract, test, commit each one):

1. **`api/jobs.py`** — Lines 225-315 of api.py: `/api/status`, `/api/queue`, `/api/queue/submit`, `/api/cancel`, `/api/priority`, `/api/queue/etas`. Plus `_compute_kpis()` and `_compute_kpis_locked()` helper functions (lines 2764+). Also includes `/api/history`, `/api/heatmap`, `/api/durations`.

2. **`api/health.py`** — Line 321 of api.py: `/api/health`. Plus SPA static file serving (line 2734+) and the `/ui` routes.

3. **`api/settings.py`** — Lines 366-380 of api.py: `GET/PUT /api/settings`, daemon pause/resume.

4. **`api/proxy.py`** — Lines 535-648 of api.py: `/api/generate`, `/api/embed` proxy endpoints. Include `_hop_by_hop` frozenset, `OLLAMA_URL`, `PROXY_WAIT_TIMEOUT`, `PROXY_POLL_INTERVAL` constants. Also `_call_generate_description()` helper.

5. **`api/schedule.py`** — Lines 681-856 of api.py: All 15 schedule/recurring job endpoints, load map, rebalance, suggest time.

6. **`api/dlq.py`** — Lines 866-925 of api.py: DLQ list, retry, dismiss, clear, reschedule, preview.

7. **`api/models.py`** — Lines 961-1080 of api.py: Models list, catalog, pull, performance metrics, deferral endpoints.

8. **`api/consumers.py`** — Lines 2597-2724 of api.py: Consumer scan, include, ignore, revert, health check, intercept enable/disable.

9. **`api/eval_variants.py`** — Lines 1112-1540 of api.py: Variant and template CRUD, clone, generate, export/import, stability, diff.

10. **`api/eval_runs.py`** — Lines 1910-2496 of api.py: Eval run CRUD, progress, results, cancel, repeat, judge-rerun, promote, export, analysis, schedule.

11. **`api/eval_settings.py`** — Lines 1758-1772 of api.py: Eval settings GET/PUT, datasource test/prime.

12. **`api/eval_trends.py`** — Line 1577 of api.py: Eval trends endpoint.

**For each file:**

Step 1: Create the file, copy relevant routes from api.py
Step 2: Change `db` references from closure variable to `_api.db`
Step 3: Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m py_compile ollama_queue/api/<file>.py`
Step 4: After ALL 12 files are created, run tests (they still use old api.py's `create_app`)

### Task 1.3: Wire up new routes and redirect create_app

**Step 1: Update `ollama_queue/app.py`** to be the real `create_app`

The old `api.py:create_app()` must now delegate to `app.py:create_app()`. Initially, keep old `api.py` as a shim:

```python
# In ollama_queue/api.py — temporary shim
from ollama_queue.app import create_app  # noqa: F401 — re-export for backward compat
```

**Step 2: Update all test imports**

Every test that does `from ollama_queue.api import create_app` needs to change to `from ollama_queue.app import create_app`. Count: ~15 files.

Run: `cd ~/Documents/projects/ollama-queue && grep -rn 'from ollama_queue.api import create_app' tests/`

Update each one.

**Step 3: Run full test suite**

Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q`

**Step 4: Remove old api.py**

Only after all tests pass with the new structure.

**Step 5: Commit**

```bash
git add ollama_queue/api/ ollama_queue/app.py tests/
git rm ollama_queue/api.py
git commit -m "refactor: split api.py into api/ subpackage (13 route files)"
```

### Task 1.4: Update ruff.toml for api/ paths

**Step 1: Edit ruff.toml**

Change:
```toml
"ollama_queue/api.py" = ["B008", "B904", "C901", "PLR0912", "PLR0915"]
```
To:
```toml
"ollama_queue/api/*.py" = ["B008", "B904", "C901", "PLR0912", "PLR0915"]
```

**Step 2: Run lint**

Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m ruff check ollama_queue/`

Fix any issues.

**Step 3: Commit**

```bash
git add ruff.toml
git commit -m "chore: update ruff.toml per-file-ignores for api/ subpackage"
```

---

## Batch 2: eval_engine.py → eval/ subpackage (PRD Task 2)

### Task 2.1: Create eval/ package skeleton

**Files:**
- Create: `ollama_queue/eval/__init__.py`
- Create: `ollama_queue/eval/engine.py`
- Create: `ollama_queue/eval/generate.py`
- Create: `ollama_queue/eval/judge.py`
- Create: `ollama_queue/eval/promote.py`
- Move: `ollama_queue/eval_analysis.py` → `ollama_queue/eval/analysis.py`

**Step 1: Create `ollama_queue/eval/__init__.py`**

```python
"""Eval pipeline business logic — generate, judge, analyze, promote."""

from ollama_queue.eval.analysis import compute_run_analysis
from ollama_queue.eval.engine import run_eval_session

__all__ = ["run_eval_session", "compute_run_analysis"]
```

**Step 2: Split eval_engine.py by phase**

Using the line ranges from the exploration:

- **`eval/engine.py`** (~300 lines): DB helper functions (lines 41-158), `run_eval_session()` (line 2591+), data fetch functions (lines 1751-1795), shared constants (`_RETRYABLE_CODES`, `_MAX_RETRIES`, `_ProxyDownError`), and the `_call_proxy()` helper used by all phases.

- **`eval/generate.py`** (~350 lines): Prompt building (lines 467-692), `run_eval_generate()` (lines 1804-2152). Imports helpers from `eval.engine`.

- **`eval/judge.py`** (~400 lines): Judge prompt construction (lines 777-1115), analysis signal computation (lines 1115-1252), `run_eval_judge()` (lines 2186-2591). Imports helpers from `eval.engine`.

- **`eval/promote.py`** (~200 lines): Auto-promote logic (lines 214-419). Imports helpers from `eval.engine`.

- **`eval/analysis.py`**: Copy `eval_analysis.py` verbatim (already pure — no DB, no HTTP).

**Step 3: Update imports in each file**

Each eval submodule imports shared functions from `eval.engine`:
```python
from ollama_queue.eval.engine import (
    get_eval_run,
    update_eval_run,
    create_eval_run,
    _call_proxy,
    _ProxyDownError,
    _RETRYABLE_CODES,
    _MAX_RETRIES,
    _RETRY_BASE_DELAY,
)
```

**Step 4: Update api/eval_*.py imports**

Change `from ollama_queue.eval_engine import ...` to `from ollama_queue.eval.engine import ...` (or `from ollama_queue.eval import ...` if re-exported).

**Step 5: Update test imports**

Run: `cd ~/Documents/projects/ollama-queue && grep -rn 'from ollama_queue.eval_engine import' tests/`

Update each occurrence. There are ~91 import statements referencing `eval_engine`.

**Step 6: Run tests**

Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q`

**Step 7: Remove old files and commit**

```bash
git rm ollama_queue/eval_engine.py ollama_queue/eval_analysis.py
git add ollama_queue/eval/
git commit -m "refactor: split eval_engine.py into eval/ subpackage (5 modules)"
```

### Task 2.2: Update ruff.toml for eval/ paths

Change `"ollama_queue/eval_engine.py"` to `"ollama_queue/eval/*.py"`.

---

## Batch 3: db.py → db/ subpackage with mixins (PRD Task 3)

### Task 3.1: Create db/ package with mixin pattern

**Files:**
- Create: `ollama_queue/db/__init__.py` — Database class assembly + `__init__`, `_connect`, `close`, `_add_column_if_missing`
- Create: `ollama_queue/db/schema.py` — `SchemaMixin` with `initialize()`, `_run_migrations()`, `seed_eval_defaults()`
- Create: `ollama_queue/db/jobs.py` — `JobsMixin` with all job CRUD (~lines 770-957)
- Create: `ollama_queue/db/schedule.py` — `ScheduleMixin` with recurring job CRUD (~lines 1245-1534)
- Create: `ollama_queue/db/settings.py` — `SettingsMixin` with get/set settings (~lines 991-1014)
- Create: `ollama_queue/db/health.py` — `HealthMixin` with health log, daemon state, proxy sentinel (~lines 1117-1245)
- Create: `ollama_queue/db/dlq.py` — `DLQMixin` with DLQ CRUD (~lines 1553-1693)
- Create: `ollama_queue/db/eval.py` — `EvalMixin` with eval tables CRUD (remainder)

**Step 1: Create `ollama_queue/db/__init__.py`**

```python
"""SQLite database layer for ollama-queue.

The queue's filing cabinet. All modules read and write through this one class.
Uses mixin pattern to split implementation across domain files while preserving
a single Database class API.
"""

import logging
import sqlite3
import threading

from ollama_queue.db.dlq import DLQMixin
from ollama_queue.db.eval import EvalMixin
from ollama_queue.db.health import HealthMixin
from ollama_queue.db.jobs import JobsMixin
from ollama_queue.db.schedule import ScheduleMixin
from ollama_queue.db.schema import SchemaMixin
from ollama_queue.db.settings import SettingsMixin

_log = logging.getLogger(__name__)

# Re-export DEFAULTS for backward compat
from ollama_queue.db.schema import DEFAULTS, EVAL_SETTINGS_DEFAULTS  # noqa: E402


class Database(
    SchemaMixin,
    JobsMixin,
    ScheduleMixin,
    SettingsMixin,
    HealthMixin,
    DLQMixin,
    EvalMixin,
):
    """Synchronous SQLite database for the ollama-queue daemon.

    All mixins use self._conn (sqlite3.Connection) and self._lock (threading.RLock).
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._conn.execute("PRAGMA synchronous = NORMAL")
                self._conn.execute("PRAGMA temp_store = MEMORY")
                self._conn.execute("PRAGMA mmap_size = 536870912")
                self._conn.execute("PRAGMA cache_size = -64000")
                self._conn.execute("PRAGMA wal_autocheckpoint = 1000")
                self._conn.execute("PRAGMA busy_timeout = 5000")
        return self._conn

    def _add_column_if_missing(self, conn, table, col, defn):
        """ALTER TABLE … ADD COLUMN, ignoring duplicate-column errors."""
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                _log.debug("%s.%s already exists — skipping migration", table, col)
            else:
                raise

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
```

**Step 2: Create each mixin file**

Each mixin is a plain class with methods that use `self._conn`, `self._lock`, `self._connect()`, `self._add_column_if_missing()`:

```python
# ollama_queue/db/jobs.py
"""Job CRUD operations mixin."""


class JobsMixin:
    """Methods for job submission, dequeue, completion, and history."""

    def submit_job(self, command, source="manual", ...):
        with self._lock:
            conn = self._connect()
            # ... exact code from db.py ...
```

**Critical:** Each mixin references `self._connect()`, `self._lock`, etc. These are defined on `Database` (in `__init__.py`). Python's MRO resolves them correctly because `Database` inherits from all mixins AND defines these attributes.

**Step 3: Move DEFAULTS and EVAL_SETTINGS_DEFAULTS to schema.py**

These constants are currently at the top of db.py. Move them to `db/schema.py` and re-export from `db/__init__.py`.

**Step 4: Verify MRO**

Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -c "from ollama_queue.db import Database; print(len(Database.__mro__)); print(Database.__mro__)"`

Expected: MRO has ≥8 entries (Database + 7 mixins + object)

**Step 5: Run tests**

Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q`

All 1,587 tests must pass. No test changes needed — they all import `from ollama_queue.db import Database` which still works via the `__init__.py`.

**Step 6: Remove old db.py and commit**

```bash
git rm ollama_queue/db.py
git add ollama_queue/db/
git commit -m "refactor: split db.py into db/ subpackage with mixin pattern (7 mixins)"
```

---

## Batch 4: daemon.py → daemon/ subpackage (PRD Task 4)

### Task 4.1: Split daemon into loop + executor

**Files:**
- Create: `ollama_queue/daemon/__init__.py`
- Create: `ollama_queue/daemon/loop.py` — Main polling: `poll_once()`, `run()`, `shutdown()`, health/entropy/recovery/dequeue logic
- Create: `ollama_queue/daemon/executor.py` — Job execution: `_run_job()`, `_drain_pipes_with_tracking()`, `_Executor` class, stall checking, check command

**Step 1: Create `ollama_queue/daemon/__init__.py`**

```python
"""Daemon polling loop and job executor."""

from ollama_queue.daemon.loop import DaemonLoop  # noqa: F401 — re-export
```

**Step 2: Create `ollama_queue/daemon/executor.py`**

Extract from daemon.py:
- `_drain_pipes_with_tracking()` (lines 46-130)
- `_Executor` class or the `_can_admit()` logic (lines 133-395)
- `_run_job()` (lines 810-1032) — subprocess spawning, stdout capture, DLQ routing
- `_check_stalled_jobs()`, `_check_retryable_jobs()` (lines 1033-1105)
- `_run_check_command()` (lines 1231-1307)

**Step 3: Create `ollama_queue/daemon/loop.py`**

Keep the rest:
- All imports (update paths for executor references)
- `poll_once()` (line 596) — calls executor functions
- `_dequeue_next_job()` (line 1105)
- `_recover_orphans()` (line 466)
- Entropy/circuit breaker logic (lines 396-550)
- `run()`, `shutdown()` (lines 1308-1347)

**Step 4: Update cross-references**

`loop.py` imports from `executor.py`:
```python
from ollama_queue.daemon.executor import run_job, drain_pipes, check_stalled_jobs
```

**Step 5: Update test imports**

Run: `cd ~/Documents/projects/ollama-queue && grep -rn 'from ollama_queue.daemon import' tests/`

Update ~25 import statements.

**Step 6: Run tests, commit**

```bash
git rm ollama_queue/daemon.py
git add ollama_queue/daemon/
git commit -m "refactor: split daemon.py into daemon/ subpackage (loop + executor)"
```

### Task 4.2: Update ruff.toml for daemon/ paths

Change `"ollama_queue/daemon.py"` to `"ollama_queue/daemon/*.py"`.

---

## Batch 5: Move small modules to domain packages (PRD Task 5)

### Task 5.1: Create scheduling/ package

**Files:**
- Create: `ollama_queue/scheduling/__init__.py`
- Move: `ollama_queue/scheduler.py` → `ollama_queue/scheduling/scheduler.py`
- Move: `ollama_queue/slot_scoring.py` → `ollama_queue/scheduling/slot_scoring.py`
- Move: `ollama_queue/deferral_scheduler.py` → `ollama_queue/scheduling/deferral.py`
- Move: `ollama_queue/dlq_scheduler.py` → `ollama_queue/scheduling/dlq_scheduler.py`

**Step 1: Create directory and __init__.py**

```python
"""Time-based job orchestration — scheduling, slot scoring, deferral."""

from ollama_queue.scheduling.scheduler import Scheduler as RecurringJobScheduler  # noqa: F401

__all__ = ["RecurringJobScheduler"]
```

**Step 2: Move files**

```bash
mkdir -p ollama_queue/scheduling
cp ollama_queue/scheduler.py ollama_queue/scheduling/scheduler.py
cp ollama_queue/slot_scoring.py ollama_queue/scheduling/slot_scoring.py
cp ollama_queue/deferral_scheduler.py ollama_queue/scheduling/deferral.py
cp ollama_queue/dlq_scheduler.py ollama_queue/scheduling/dlq_scheduler.py
```

**Step 3: Update internal imports within moved files**

In `scheduling/scheduler.py`: change `from ollama_queue.slot_scoring import` to `from ollama_queue.scheduling.slot_scoring import`
In `scheduling/deferral.py`: update similarly
In `scheduling/dlq_scheduler.py`: update similarly

**Step 4: Update imports in daemon/, api/, cli.py**

Search and replace:
- `from ollama_queue.scheduler import` → `from ollama_queue.scheduling.scheduler import`
- `from ollama_queue.deferral_scheduler import` → `from ollama_queue.scheduling.deferral import`
- `from ollama_queue.dlq_scheduler import` → `from ollama_queue.scheduling.dlq_scheduler import`
- `from ollama_queue.slot_scoring import` → `from ollama_queue.scheduling.slot_scoring import`

**Step 5: Update test imports**

**Step 6: Run tests, remove old files, commit**

```bash
git rm ollama_queue/scheduler.py ollama_queue/slot_scoring.py ollama_queue/deferral_scheduler.py ollama_queue/dlq_scheduler.py
git add ollama_queue/scheduling/
git commit -m "refactor: move scheduling modules to scheduling/ subpackage"
```

### Task 5.2: Create sensing/ package

Same pattern as 5.1 for: `health.py`, `stall.py`, `burst.py`, `system_snapshot.py`

```python
# sensing/__init__.py
from ollama_queue.sensing.health import HealthMonitor  # noqa: F401
```

Update ruff.toml: `"ollama_queue/health.py"` → `"ollama_queue/sensing/health.py"`

### Task 5.3: Create models/ package

Same pattern for: `models.py` → `models/client.py`, `estimator.py`, `runtime_estimator.py`, `performance_curve.py`

```python
# models/__init__.py
from ollama_queue.models.client import OllamaModels  # noqa: F401
```

**Note:** `models.py` renames to `client.py` to avoid collision with the package name.

Update ruff.toml: `"ollama_queue/models.py"` → `"ollama_queue/models/client.py"`

### Task 5.4: Create config/ package

Same pattern for: `patcher.py`, `scanner.py`, `intercept.py`

```python
# config/__init__.py
from ollama_queue.config.patcher import patch_consumer, revert_consumer, check_health  # noqa: F401
```

Update ruff.toml for all three files.

### Task 5.5: Update all remaining imports, run tests, commit

```bash
git commit -m "refactor: move small modules to domain subpackages (scheduling, sensing, models, config)"
```

---

## Batch 6: Update cli.py + all test imports (PRD Tasks 6-7)

### Task 6.1: Update cli.py imports

**File:** `ollama_queue/cli.py`

Find and replace all old module paths with new subpackage paths. Key changes:
- `from ollama_queue.db import` → stays the same (db/__init__.py re-exports)
- `from ollama_queue.scheduler import` → `from ollama_queue.scheduling.scheduler import`
- `from ollama_queue.health import` → `from ollama_queue.sensing.health import`
- `from ollama_queue.models import` → `from ollama_queue.models import` (stays same via __init__)
- etc.

Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest tests/test_cli.py --timeout=120 -x -q`

### Task 6.2: Update all remaining test imports

Systematic search across all test files:

```bash
cd ~/Documents/projects/ollama-queue
grep -rn 'from ollama_queue.scheduler import' tests/
grep -rn 'from ollama_queue.health import' tests/
grep -rn 'from ollama_queue.stall import' tests/
grep -rn 'from ollama_queue.burst import' tests/
grep -rn 'from ollama_queue.patcher import' tests/
grep -rn 'from ollama_queue.scanner import' tests/
grep -rn 'from ollama_queue.models import' tests/
grep -rn 'from ollama_queue.estimator import' tests/
grep -rn 'from ollama_queue.runtime_estimator import' tests/
grep -rn 'from ollama_queue.performance_curve import' tests/
grep -rn 'from ollama_queue.intercept import' tests/
grep -rn 'from ollama_queue.system_snapshot import' tests/
grep -rn 'from ollama_queue.slot_scoring import' tests/
grep -rn 'from ollama_queue.deferral_scheduler import' tests/
grep -rn 'from ollama_queue.dlq_scheduler import' tests/
```

Update each import to the new path.

Run: `cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q`

Commit: `git commit -m "refactor: update all imports to new subpackage paths"`

---

## Batch 7: JavaScript store + component splits (PRD Tasks 8-10)

### Task 7.1: Split store.js → stores/ (PRD Task 8)

**Files:**
- Create: `src/stores/index.js` — re-exports everything
- Create: `src/stores/queue.js` — queue/job signals + fetchers (lines 9-15, 294-368, 651-708 of store.js)
- Create: `src/stores/eval.js` — eval signals + fetchers (lines 33-63, 70-246 of store.js)
- Create: `src/stores/schedule.js` — schedule signals + fetchers (lines 17-18, 26, 372-462 of store.js)
- Create: `src/stores/models.js` — model signals + fetchers (lines 21-23, 583-642 of store.js)
- Create: `src/stores/settings.js` — settings signals + fetchers (line 15 of store.js)
- Create: `src/stores/health.js` — health/dashboard signals + fetchers (lines 12-14, 19-20, 28-31 of store.js)
- Remove: `src/store.js`

**Cross-domain dependencies to handle:**
- `stores/queue.js` defines `status`, `connectionStatus` — used by polling logic
- `stores/eval.js` references `evalActiveRun` persistence — self-contained
- `stores/schedule.js` and `stores/health.js` both fetched in `_fetchNonRealtime` — put the batch fetcher in `stores/index.js`

**Step 1: Create each store file, group signals + their fetchers together**

**Step 2: Create `stores/index.js`**

```javascript
// Backward-compatible re-export — components can import from '../stores' or '../stores/index'
export * from './queue.js';
export * from './eval.js';
export * from './schedule.js';
export * from './models.js';
export * from './settings.js';
export * from './health.js';

// Cross-domain polling orchestrator stays here
// (startPolling/stopPolling call fetchers from multiple domains)
```

**Step 3: Update component imports**

Global find-replace: `from '../store'` → `from '../stores'` (or `from '../stores/index'`)
Also: `from '../../store'` → `from '../../stores'` etc.

**Step 4: Build and verify**

Run: `cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa && npm run build`

**Step 5: Remove old store.js, commit**

```bash
git rm src/store.js
git add src/stores/
git commit -m "refactor: split store.js into stores/ by domain (6 domain stores)"
```

### Task 7.2: Split Plan.jsx → Plan/ directory (PRD Task 9)

**Files:**
- Create: `src/pages/Plan/index.jsx` — page shell + state + tab routing
- Create: `src/pages/Plan/GanttSection.jsx` — GanttChart wrapper + controls
- Create: `src/pages/Plan/LoadMapSection.jsx` — load map + density strip
- Create: `src/pages/Plan/RecurringJobsSection.jsx` — recurring jobs table + actions
- Remove: `src/pages/Plan.jsx`

**Step 1: Identify the 3 visual sections in Plan.jsx**

Look for section boundaries — likely JSX with headers or collapsible wrappers.

**Step 2: Extract each section into its own component file**

Each receives props or imports signals directly from stores.

**Step 3: Update `app.jsx` import** (should auto-resolve `Plan/index.jsx`)

**Step 4: Build, commit**

### Task 7.3: Split oversized components (PRD Task 10)

Same directory-component pattern for each:

1. **SettingsForm/** — Split by setting category (queue, daemon, advanced)
2. **AddRecurringJobModal/** — Split modal chrome from CronBuilder
3. **eval/RunRow/** — Split summary from expanded details
4. **eval/GeneralSettings/** — Split judge config from thresholds

Each follows the pattern: `index.jsx` (container) + extracted sub-components.

Build after each split. Commit after all 4 pass.

---

## Batch 8: Cleanup + quality gate (PRD Tasks 11-13)

### Task 8.1: Remove backward-compatibility re-exports (PRD Task 11)

**Step 1: Check for any remaining old-path imports**

```bash
cd ~/Documents/projects/ollama-queue
grep -rn 'from ollama_queue.scheduler import' .
grep -rn 'from ollama_queue.health import' .
grep -rn 'from ollama_queue.stall import' .
# ... etc for all old paths
```

**Step 2: Remove any temporary re-exports in `__init__.py` files**

**Step 3: Run tests, commit**

### Task 8.2: Update CLAUDE.md (PRD Task 12)

**Step 1: Rewrite the `## Structure` section** to reflect the new domain subpackage layout

**Step 2: Update any gotchas that reference old file paths** (e.g., "eval_analysis.py is pure" → "eval/analysis.py is pure")

**Step 3: Update ruff.toml** — verify all per-file-ignores use new paths

**Step 4: Commit**

### Task 8.3: Final quality gate (PRD Task 13)

Run all checks:

```bash
# 1. All tests pass
cd ~/Documents/projects/ollama-queue && .venv/bin/python -m pytest --timeout=120 -x -q

# 2. Coverage ≥ 99%
.venv/bin/python -m pytest --cov=ollama_queue --cov-fail-under=99 --timeout=120 -q

# 3. Lint clean
.venv/bin/python -m ruff check ollama_queue/

# 4. Format clean
.venv/bin/python -m ruff format --check ollama_queue/

# 5. SPA builds
cd ollama_queue/dashboard/spa && npm run build

# 6. No files over 900 lines
find ollama_queue/ -name '*.py' -not -path '*/__pycache__/*' -exec sh -c 'lines=$(wc -l < "$1"); if [ "$lines" -gt 900 ]; then echo "OVER: $1 ($lines lines)"; fi' _ {} \;

# 7. No old flat modules remain at root
ls ollama_queue/*.py | grep -v '__init__\|cli\|dlq\|intelligence\|metrics_parser\|app'
```

All must pass. Then:

```bash
git commit -m "refactor: complete domain reorganization — all quality gates pass"
```

---

## Summary

| Batch | PRD Tasks | Files Created | Files Removed | Risk |
|-------|-----------|--------------|---------------|------|
| 1: api/ | 1 | 14 (12 routes + __init__ + app.py) | 1 (api.py) | Medium — most endpoints |
| 2: eval/ | 2 | 6 (5 modules + __init__) | 2 (eval_engine, eval_analysis) | Medium — phase boundaries |
| 3: db/ | 3 | 8 (7 mixins + __init__) | 1 (db.py) | High — mixin MRO |
| 4: daemon/ | 4 | 3 (loop + executor + __init__) | 1 (daemon.py) | Low — clear boundary |
| 5: small modules | 5 | 4 __init__.py files | 16 flat files | Low — simple moves |
| 6: imports | 6-7 | 0 | 0 | Medium — 40 test files |
| 7: JavaScript | 8-10 | ~25 files | ~8 files | Low — SPA build verifies |
| 8: cleanup | 11-13 | 0 | 0 | Low — verification only |
