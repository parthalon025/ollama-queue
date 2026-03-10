# Full Domain Reorganization Design

**Date:** 2026-03-10
**Status:** Approved
**Goal:** Reorganize ollama-queue into domain subpackages with ~300-line modules, optimized for Claude Code AI readability and top-company engineering practices.

## Context

The codebase has grown to 13,037 lines of Python (28 modules) and 13,264 lines of JavaScript (75+ components). Four Python files exceed 1,300 lines; six JS files exceed 400 lines. The primary optimization target is **minimal read surface per AI-assisted task** — Claude Code should read 1-2 targeted files per change, not scan through 2,800-line monoliths.

### Current Pain Points

| File | Lines | Problem |
|------|-------|---------|
| `api.py` | 2,826 | 90+ endpoints in one file — any API change requires reading all 2,826 lines |
| `eval_engine.py` | 2,620 | Generate, judge, analyze, promote all in one file |
| `db.py` | 1,940 | Single Database class with ~80 methods, one lock, one connection |
| `daemon.py` | 1,347 | Orchestrator loop + subprocess execution + recovery |
| `store.js` | 816 | All Preact signals + all API fetchers for every domain |
| `Plan.jsx` | 1,318 | 3 distinct visual sections crammed into one component |
| `SettingsForm.jsx` | 774 | All system settings in one form |
| `RunRow.jsx` | 555 | Summary + expanded detail in one component |
| `AddRecurringJobModal.jsx` | 500 | Modal + cron builder + job config in one file |
| `GeneralSettings.jsx` | 465 | Judge config + thresholds + auto-promote |

### Reference

- **FlightVin/automated-refactoring** — LLM-driven per-file refactoring with code smell metrics (cyclomatic complexity, fan-out, coupling). Approach: detect smells → LLM refactor → auto-PR.
- **FastAPI best practices** — APIRouter per domain, thin routes, service layer for logic.
- **Google** — Clear package boundaries, public API via `__init__.py`.
- **Stripe/Netflix** — Layered architecture: API → Service → Repository. One-directional dependency flow.

## Design Principles

1. **Layered architecture** — API (thin routes) → Business logic → Persistence. Dependencies flow downward only.
2. **Domain grouping** — Files grouped by business domain, not technical layer.
3. **~300 lines per file** — Each file fits in a single Claude Code Read with room to spare.
4. **Backward-compatible imports** — `__init__.py` re-exports preserve existing import paths during migration.
5. **Mixin pattern for Database** — Single class API, split implementation across files.
6. **Don't force splits** — Single-purpose modules (GanttChart, intelligence, metrics_parser) stay intact even if slightly over 300 lines.
7. **Safety first** — Full test suite (1,587 tests, 100% coverage) runs between each file split.

## Section 1: Python Target Architecture

```
ollama_queue/
├── __init__.py                    # Package version (unchanged)
├── app.py                         # NEW: FastAPI app creation + router assembly
│
├── api/                           # HTTP layer — thin routes, delegates to services
│   ├── __init__.py                # Assembles all routers into one
│   ├── jobs.py                    # Queue/job CRUD endpoints
│   ├── proxy.py                   # Ollama proxy + streaming
│   ├── schedule.py                # Recurring jobs, load map, rebalance
│   ├── consumers.py               # Consumer detection + intercept toggle
│   ├── models.py                  # Model registry endpoints
│   ├── health.py                  # Health/metrics/dashboard/SPA endpoints
│   ├── settings.py                # Settings CRUD
│   ├── dlq.py                     # DLQ endpoints
│   ├── eval_runs.py               # Eval run lifecycle endpoints
│   ├── eval_variants.py           # Variant CRUD endpoints
│   ├── eval_settings.py           # Eval settings endpoints
│   └── eval_trends.py             # Trends + analysis endpoints
│
├── db/                            # Persistence layer — SQL + CRUD only
│   ├── __init__.py                # Database class (assembly via mixins) + connection
│   ├── schema.py                  # Table DDL + migrations + _ensure_schema()
│   ├── jobs.py                    # JobsMixin: submit, dequeue, complete, history, metrics
│   ├── schedule.py                # ScheduleMixin: recurring jobs + load map CRUD
│   ├── settings.py                # SettingsMixin: get/set settings
│   ├── health.py                  # HealthMixin: health log + daemon state CRUD
│   ├── dlq.py                     # DLQMixin: DLQ CRUD + retry
│   └── eval.py                    # EvalMixin: eval runs/variants/results/trends CRUD
│
├── eval/                          # Eval business logic — no HTTP, no SQL direct access
│   ├── __init__.py                # Re-exports engine, analysis
│   ├── engine.py                  # Run lifecycle orchestration (~300 lines)
│   ├── generate.py                # Generation phase (fetch data, queue jobs, collect)
│   ├── judge.py                   # Judging phase + agreement scoring
│   ├── analysis.py                # Pure analysis (from eval_analysis.py, unchanged)
│   └── promote.py                 # Auto-promote gate logic + do_promote
│
├── daemon/                        # Runtime — polling loop + execution
│   ├── __init__.py                # Re-exports DaemonLoop
│   ├── loop.py                    # Main 5s polling loop (~900 lines — natural unit)
│   └── executor.py                # Subprocess management + metrics capture (~450 lines)
│
├── scheduling/                    # Time-based orchestration
│   ├── __init__.py                # Re-exports
│   ├── scheduler.py               # Recurring job promotion (443 lines)
│   ├── slot_scoring.py            # Smart slot selection (131 lines)
│   ├── deferral.py                # Proactive deferral (128 lines)
│   └── dlq_scheduler.py           # DLQ auto-reschedule (174 lines)
│
├── sensing/                       # Health monitoring + anomaly detection
│   ├── __init__.py                # Re-exports
│   ├── health.py                  # RAM/VRAM/load/swap with hysteresis (241 lines)
│   ├── stall.py                   # Bayesian stall detection (197 lines)
│   ├── burst.py                   # Traffic regime classification (115 lines)
│   └── system_snapshot.py         # 10-factor scoring (169 lines)
│
├── models/                        # Ollama model management
│   ├── __init__.py                # Re-exports OllamaModels
│   ├── client.py                  # OllamaModels API client (402 lines)
│   ├── estimator.py               # Rolling-average duration prediction (125 lines)
│   ├── runtime_estimator.py       # Bayesian log-normal estimation (161 lines)
│   └── performance_curve.py       # Cross-model regression (131 lines)
│
├── config/                        # Discovery + configuration
│   ├── __init__.py                # Re-exports
│   ├── patcher.py                 # Config rewriter (231 lines)
│   ├── scanner.py                 # 4-phase consumer detection (306 lines)
│   └── intercept.py               # iptables REDIRECT mode (108 lines)
│
├── cli.py                         # Click CLI (~712 lines — may split later if grows)
├── dlq.py                         # DLQManager routing logic (73 lines)
├── intelligence.py                # LoadPatterns (71 lines)
└── metrics_parser.py              # Ollama response parser (59 lines)
```

### Database Mixin Pattern

```python
# db/__init__.py
from .schema import SchemaMixin
from .jobs import JobsMixin
from .schedule import ScheduleMixin
from .settings import SettingsMixin
from .health import HealthMixin
from .dlq import DLQMixin
from .eval import EvalMixin

class Database(SchemaMixin, JobsMixin, ScheduleMixin, SettingsMixin,
               HealthMixin, DLQMixin, EvalMixin):
    """Single Database instance with methods split across domain mixins.

    All mixins use self._conn (sqlite3.Connection) and self._lock (threading.RLock).
    These are initialized in __init__ defined here.
    """

    def __init__(self, db_path=None):
        self._db_path = db_path or self._default_path()
        self._conn = None
        self._lock = threading.RLock()
        self._connect()
        self._ensure_schema()

    def _connect(self):
        with self._lock:
            self._conn = sqlite3.connect(
                self._db_path, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
```

### API Router Assembly

```python
# api/__init__.py
from fastapi import APIRouter
from .jobs import router as jobs_router
from .proxy import router as proxy_router
from .schedule import router as schedule_router
from .consumers import router as consumers_router
from .models import router as models_router
from .health import router as health_router
from .settings import router as settings_router
from .dlq import router as dlq_router
from .eval_runs import router as eval_runs_router
from .eval_variants import router as eval_variants_router
from .eval_settings import router as eval_settings_router
from .eval_trends import router as eval_trends_router

api_router = APIRouter()
api_router.include_router(jobs_router)
api_router.include_router(proxy_router)
api_router.include_router(schedule_router)
# ... etc

# app.py
from fastapi import FastAPI
from .api import api_router

def create_app(db, models_client, daemon):
    app = FastAPI()
    app.include_router(api_router)
    return app
```

### Import Migration Strategy

To preserve backward compatibility during migration:

```python
# Top-level ollama_queue/__init__.py additions (temporary)
# These let old imports work while tests are migrated
from ollama_queue.db import Database
from ollama_queue.models.client import OllamaModels
from ollama_queue.sensing.health import HealthMonitor
# ... etc
```

These re-exports are removed after all consumers are updated.

## Section 2: JavaScript Target Architecture

```
src/
├── index.jsx                          # Entry point (unchanged)
├── app.jsx                            # Router + layout (unchanged)
├── preact-shim.js                     # JSX factory shim (unchanged)
├── index.css                          # Tailwind input (unchanged)
│
├── stores/                            # State management — split by domain
│   ├── index.js                       # Re-exports all signals (backward compat)
│   ├── queue.js                       # Job queue signals + fetchers
│   ├── eval.js                        # Eval signals + fetchers
│   ├── schedule.js                    # Recurring jobs + load map signals
│   ├── models.js                      # Model registry signals + fetchers
│   ├── settings.js                    # Settings signals + fetchers
│   └── health.js                      # Health/dashboard signals + fetchers
│
├── pages/
│   ├── Now.jsx                        # 323 lines (unchanged)
│   ├── Plan/                          # Split 1,318 lines → 3 sections
│   │   ├── index.jsx                  # Page shell + tab routing
│   │   ├── GanttSection.jsx           # Gantt chart + controls
│   │   ├── LoadMapSection.jsx         # Load map visualization
│   │   └── RecurringJobsSection.jsx   # Recurring jobs table + actions
│   ├── History.jsx                    # 331 lines (unchanged)
│   ├── ModelsTab.jsx                  # 326 lines (unchanged)
│   ├── Performance.jsx                # 114 lines (unchanged)
│   ├── Eval.jsx                       # 44 lines (unchanged)
│   ├── Settings.jsx                   # 84 lines (unchanged)
│   └── Consumers.jsx                  # 119 lines (unchanged)
│
├── components/
│   ├── GanttChart.jsx                 # 700 lines — KEEP AS-IS (single visualization)
│   ├── SettingsForm/                  # Split 774 lines → 3 groups
│   │   ├── index.jsx                  # Form shell + tabs
│   │   ├── QueueSettings.jsx          # Queue config section
│   │   ├── DaemonSettings.jsx         # Daemon config section
│   │   └── AdvancedSettings.jsx       # Advanced options
│   ├── AddRecurringJobModal/          # Split 500 lines → 2 parts
│   │   ├── index.jsx                  # Modal shell + submit logic
│   │   └── CronBuilder.jsx            # Cron expression builder
│   ├── eval/
│   │   ├── RunRow/                    # Split 555 lines → 2 parts
│   │   │   ├── index.jsx              # Row container + expand/collapse
│   │   │   └── RunDetails.jsx         # Expanded detail panel
│   │   ├── GeneralSettings/           # Split 465 lines → 2 parts
│   │   │   ├── index.jsx              # Settings shell + judge config
│   │   │   └── ThresholdConfig.jsx    # Thresholds + auto-promote
│   │   ├── RunTriggerPanel.jsx        # 403 lines — KEEP AS-IS (borderline)
│   │   └── [remaining eval components unchanged]
│   └── [remaining components unchanged]
│
├── views/                             # (unchanged)
├── hooks/                             # (unchanged)
└── __mocks__/                         # (unchanged)
```

### Store Split Strategy

```javascript
// stores/queue.js
import { signal, computed } from '@preact/signals';

// Queue-domain signals
export const jobs = signal([]);
export const currentJob = signal(null);
export const queueStats = signal({});

// Queue-domain fetchers
export async function fetchJobs() { ... }
export async function submitJob(payload) { ... }
export async function cancelJob(id) { ... }

// stores/index.js — backward compatibility
export * from './queue.js';
export * from './eval.js';
export * from './schedule.js';
export * from './models.js';
export * from './settings.js';
export * from './health.js';
```

## Section 3: Test Migration Strategy

### Safety Protocol

1. **One source file at a time** — Extract one module, run full suite, commit.
2. **Re-exports first** — `__init__.py` re-exports ensure old imports work immediately.
3. **Test imports updated last** — Only after all source modules are stable.
4. **Test file follows source** — When `api.py` splits into `api/`, test files split similarly:
   - `test_api.py` → `test_api_jobs.py`, `test_api_proxy.py`, etc.
   - But only AFTER source split is stable and all 1,587 tests pass.

### Test File Mapping

| Current Test | Tests For | Migration |
|---|---|---|
| `test_api.py` (1,255) | api.py routes | Split by domain after api/ split |
| `test_api_eval_runs.py` (1,600) | eval run endpoints | → tests for api/eval_runs.py |
| `test_api_eval_variants.py` (848) | variant endpoints | → tests for api/eval_variants.py |
| `test_api_cov_{a,b,c,d}.py` | coverage gaps | Redistribute to domain test files |
| `test_eval_engine.py` (4,365) | eval_engine.py | Split by phase: test_eval_generate, test_eval_judge, etc. |
| `test_daemon.py` (3,197) | daemon.py | Split: test_daemon_loop + test_daemon_executor |
| `test_db.py` (1,433) | db.py | Split by domain mixin |
| `test_cli.py` (1,373) | cli.py | Keep as-is (CLI is one entry point) |
| Other test files | Small modules | Update import paths only |

### Execution Order

Phase 1 — Python source split (highest ROI, most complex):
1. `api.py` → `api/` (APIRouter — cleanest split, sets the pattern)
2. `eval_engine.py` → `eval/` (independent phases)
3. `db.py` → `db/` (mixin pattern)
4. `daemon.py` → `daemon/` (executor extraction)
5. Small modules → domain packages (sensing/, scheduling/, models/, config/)

Phase 2 — JavaScript source split:
6. `store.js` → `stores/` (domain split with re-export index)
7. `Plan.jsx` → `Plan/` (3 sections)
8. `SettingsForm.jsx` → `SettingsForm/` (3 groups)
9. Remaining oversized components

Phase 3 — Test migration:
10. Update test imports to use new paths
11. Split oversized test files by domain
12. Verify 100% coverage maintained

## Metrics

### Before

- **Python:** 4 files > 1,000 lines, largest 2,826
- **JavaScript:** 6 files > 400 lines, largest 1,318
- **Test read surface:** To work on eval endpoints, Claude Code reads api.py (2,826) + eval_engine.py (2,620) + db.py (1,940) = **7,386 lines**

### After (Target)

- **Python:** 0 files > 900 lines (daemon/loop.py is the exception — natural orchestrator)
- **JavaScript:** 1 file > 400 lines (GanttChart.jsx — single visualization, intentionally kept)
- **Test read surface:** To work on eval endpoints, Claude Code reads api/eval_runs.py (~250) + eval/engine.py (~300) + db/eval.py (~300) = **~850 lines** (8.7× reduction)

## Risks

1. **Import breakage** — Mitigated by `__init__.py` re-exports and running tests after each split.
2. **Mixin pattern complexity** — Mitigated by clear domain boundaries and type hints on self._conn/_lock.
3. **Test coverage regression** — Mitigated by running `pytest --cov` after each phase.
4. **Circular imports** — Risk when splitting api/ (routes may cross-reference). Mitigated by keeping shared state in app.py and passing via FastAPI dependency injection.
5. **Daemon loop size** — 900 lines exceeds 300-line target. Accepted because the loop is a natural atomic unit — forcing a split creates artificial boundaries.
