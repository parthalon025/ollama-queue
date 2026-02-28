# Design: Model Concurrency, Gantt Scheduler, and Model Management UI
**Date:** 2026-02-28
**Status:** Approved
**Scope Tags:** language:python, framework:preact, domain:ollama

---

## Problem Statement

The ollama-queue daemon serializes all Ollama access to prevent model-loading
contention — one job runs at a time. This causes two concrete problems:

1. **Slot waste:** Embedding jobs (`nomic-embed-text`, ~300MB VRAM) and
   Telegram brief jobs (fast, small model) occupy the same full serial slot as
   heavy analysis runs (`deepseek-r1:8b`, ~5GB VRAM), even though they would
   never compete for the same resource.
2. **Priority starvation:** High-priority Telegram jobs bump heavy jobs to
   second position instead of running alongside them.

**Goal:** Adaptive concurrency keyed on resource profile, with full model
management UI and a Gantt-style schedule visualizer.

---

## Feature Summary

| # | Feature | Risk |
|---|---------|------|
| 1 | Gantt schedule visualizer (blocks + stacking) | Low |
| 2 | Adaptive multi-model concurrency | High — mitigated |
| 3 | VRAM / memory display per task | Low |
| 4 | Model inventory + assignment + recommendations | Medium |
| 5 | Model download from UI (curated + search) | Medium |
| 6 | Production reliability hardening | Low |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  New: models.py — OllamaModels                              │
│  list_local() · get_loaded() · pull() · classify()          │
│  estimate_vram() · get_pull_status() · cancel_pull()         │
└──────────────────────┬──────────────────────────────────────┘
                       │ used by
          ┌────────────┴──────────────┐
          ▼                           ▼
   daemon.py (execution)         api.py (7 new endpoints)
   - ThreadPoolExecutor           - GET /api/models
   - Admission gate               - POST /api/models/pull
   - PID tracking                 - GET /api/models/pull/{id}
   - Same-model serialization     - DELETE /api/models/pull/{id}
   - Multi-job stall detect       - GET /api/models/catalog
                                  - GET /api/queue/etas
                                  - /api/schedule enriched with
                                    estimated_duration
          │
          ▼
   db.py (2 new tables, 1 new column)
   - jobs.pid  (crash recovery)
   - model_registry  (observed VRAM, profile, type)
   - model_pulls  (download tracking)
          │
          ▼
   SPA: new Models tab + Schedule Gantt
```

---

## 1. Resource Profiles — Primary Concurrency Axis

Resource profile is the first-class lever for concurrency decisions, not raw
VRAM math alone.

| Profile | VRAM footprint | Concurrency behavior |
|---------|---------------|---------------------|
| `embed` | ≤ 500MB | Always concurrent — skip VRAM gate entirely. Max 4 slots. |
| `ollama` | 500MB–8GB | Concurrent if three-factor admission passes. |
| `heavy` | > 8GB | Serialize. Max 1 active at a time. |

Profile is set in `model_registry.resource_profile` and derived by
`OllamaModels.classify()` based on model name heuristics:

```python
PROFILE_RULES = [
    (["embed", "nomic", "mxbai", "bge-m3"], "embed"),
    (["70b", "34b", "deepseek-r1:14", "deepseek-r1:32"], "heavy"),
]
# default: "ollama"
```

Type tag (separate from profile, used for recommendations):

```python
TYPE_TAGS = [
    (["coder", "code", "qwen2.5-coder", "deepseek-coder"], "coding"),
    (["embed", "nomic", "mxbai"], "embed"),
    (["r1", "o1", "think"], "reasoning"),
]
# default: "general"
```

---

## 2. New Python Module: `models.py`

```python
class OllamaModels:
    def list_local(self) -> list[dict]:
        """Parse `ollama list` → [{name, size_bytes, modified}]"""

    def get_loaded(self) -> list[dict]:
        """Parse `ollama ps` → [{name, size_bytes, vram_pct, cpu_pct, until}]
        Handles multiple rows (multi-model concurrency). Upgrades
        get_ollama_active_model() in health.py which only reads row 0."""

    def classify(self, model_name: str) -> dict:
        """Returns {resource_profile, type_tag} from name heuristics."""

    def estimate_vram_mb(self, model_name: str, db: Database) -> float:
        """Observed value from model_registry if available.
        Falls back to disk_size_bytes / 1e6 * 1.3 safety factor."""

    def pull(self, model_name: str) -> int:
        """Start `ollama pull <model>` subprocess. Returns pull_id (DB row).
        Streams stdout to parse progress lines."""

    def get_pull_status(self, pull_id: int, db: Database) -> dict:
        """Returns {status, progress_pct, model, started_at}"""

    def cancel_pull(self, pull_id: int, db: Database) -> bool:
        """SIGTERM the pull subprocess. Updates DB status to cancelled."""

    def search_catalog(self, query: str) -> list[dict]:
        """Search ollama.com/library API. Returns [{name, description, pulls}]"""
```

**All DB methods use `contextlib.closing()`** — Lesson #34.

---

## 3. Database Changes

### New column: `jobs.pid`

```sql
ALTER TABLE jobs ADD COLUMN pid INTEGER;
```

Written when subprocess starts. Used on daemon restart to kill orphaned
processes.

### New table: `model_registry`

```sql
CREATE TABLE IF NOT EXISTS model_registry (
    name           TEXT PRIMARY KEY,
    size_bytes     INTEGER,
    vram_observed_mb REAL,          -- actual measured; NULL until first run
    resource_profile TEXT DEFAULT 'ollama',
    type_tag       TEXT DEFAULT 'general',
    last_seen      REAL             -- unix timestamp from last ollama list
);
```

Populated on daemon start and every `/api/models` call. `vram_observed_mb`
updated after each job completes (delta from nvidia-smi before/after).

### New table: `model_pulls`

```sql
CREATE TABLE IF NOT EXISTS model_pulls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model        TEXT NOT NULL,
    status       TEXT DEFAULT 'pulling',  -- pulling|completed|failed|cancelled
    progress_pct REAL DEFAULT 0,
    pid          INTEGER,
    started_at   REAL,
    completed_at REAL,
    error        TEXT
);
```

---

## 4. Daemon Changes

### 4a. ThreadPoolExecutor for concurrent execution

```python
# daemon.py
from concurrent.futures import ThreadPoolExecutor

class Daemon:
    def __init__(self, db, ...):
        self._executor = ThreadPoolExecutor(max_workers=self._max_slots())
        self._running: dict[int, Future] = {}   # job_id → future
        self._running_models: dict[int, str] = {}  # job_id → model name
        self._lock = threading.Lock()  # guards _running + _running_models

    def _max_slots(self) -> int:
        return int(db.get_setting("max_concurrent_jobs") or 1)
```

The polling loop stays single-threaded. Only `subprocess.Popen` execution is
offloaded to the pool.

### 4b. Three-factor admission gate

```python
def _can_admit(self, job: dict) -> bool:
    profile = self._model_profile(job["model"])

    # embed: always concurrent, no gate
    if profile == "embed":
        return len(self._running) < 4

    # heavy: serialize
    if profile == "heavy":
        return len(self._running) == 0

    # Same model already loading → serialize
    if job.get("model") and job["model"] in self._running_models.values():
        return False

    # Model pull in progress → block
    if self._model_pull_in_progress(job["model"]):
        return False

    # Pre-flight model presence check
    if not self._model_exists(job["model"]):
        self._route_to_dlq(job, reason="model_missing")
        return False

    # Three-factor VRAM+RAM check
    snap = self._health.check()
    health_eval = self._health.evaluate(snap, self._settings(), currently_paused=False)
    if health_eval["should_pause"]:
        return False

    model_vram = OllamaModels().estimate_vram_mb(job["model"], self._db)
    free_vram_mb = self._free_vram_mb()  # from nvidia-smi
    free_ram_mb = self._free_ram_mb()    # from /proc/meminfo

    if free_vram_mb is not None and model_vram > free_vram_mb * 0.8:
        return False
    if model_vram * 0.5 > free_ram_mb * 0.8:  # worst-case CPU spill
        return False

    return len(self._running) < self._max_slots()
```

### 4c. PID tracking and crash recovery

```python
# On daemon startup:
def _recover_orphans(self):
    """Kill subprocesses for jobs stuck in 'running' state."""
    orphans = self._db.get_jobs_by_status("running")
    for job in orphans:
        if job.get("pid"):
            try:
                os.kill(job["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass  # already gone
        self._db.reset_job_to_pending(job["id"])
```

### 4d. Multi-job stall detection

```python
# Stall detection iterates all running jobs, not just current_job_id
def _check_stalls(self):
    for job_id, future in list(self._running.items()):
        job = self._db.get_job(job_id)
        elapsed = time.time() - (job["started_at"] or time.time())
        if elapsed > job["timeout"] * 1.5:
            future.cancel()
            self._db.mark_stalled(job_id)
```

### 4e. Shadow mode

When `max_concurrent_jobs` changes from 1 → N, the daemon logs
`SHADOW: would admit concurrent job [name]` for 24h before acting. Controlled
by `concurrent_shadow_hours` setting (default: 24, set to 0 to disable).

### 4f. Observed VRAM delta tracking

After each job completes, sample `nvidia-smi` VRAM before/after and update
`model_registry.vram_observed_mb` with an exponential moving average (α=0.3).

---

## 5. Proxy Endpoint Changes (`api.py`)

`try_claim_for_proxy()` must be slot-aware. Replace the sentinel `job_id=-1`
approach with a dedicated proxy semaphore that respects `max_concurrent_jobs`:

```python
# Claim succeeds only if a slot is available across all concurrent jobs
# Proxy claims count as one slot against the concurrent limit
```

---

## 6. New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/models` | List local models with profile, type, vram_mb, avg_duration |
| `POST` | `/api/models/pull` | Start pull `{model: str}` → `{pull_id}` |
| `GET` | `/api/models/pull/{pull_id}` | Poll pull progress |
| `DELETE` | `/api/models/pull/{pull_id}` | Cancel pull |
| `GET` | `/api/models/catalog` | `{curated: [...], search_results: [...]}` |
| `GET` | `/api/queue/etas` | Queue ETAs with concurrency awareness |

### Enrich `GET /api/schedule`

Each recurring job row gains:
```json
{
  "estimated_duration": 847.3,
  "model_profile": "ollama",
  "model_type": "coding",
  "model_vram_mb": 4200,
  "model_recommendation": "qwen2.5-coder:14b"
}
```

`estimated_duration` from `DurationEstimator.estimate(rj["name"], rj["model"])`.
`model_recommendation` from type-tag matching against local model registry.

---

## 7. Frontend Changes

### 7a. New `Models` tab (5th tab)

Sections:
1. **Installed Models** — table: Name | Type | Profile | VRAM | Avg Duration | Assigned Jobs | Actions
2. **Assign Model** — dropdown per recurring job with recommendations highlighted
3. **Download Panel** — curated grid + search box → pull progress bar per model

### 7b. `ScheduleTab.jsx` — Gantt chart

Replace `TimelineBar` with `GanttChart`:

```
┌────────────────────────────────────────────────────────┐
│ now         6h          12h         18h          24h   │
│ ┌──────────────────┐                                   │ ← lane 0
│ │ aria-full (4.5h) │                                   │
│ └──────────────────┘ ┌──────────┐ ┌───────────────┐   │ ← lane 0 (cont.)
│ ┌────┐               │telegram  │ │ aria-meta     │   │ ← lane 1 (concurrent)
│ │embed│              │ (12m)    │ │ (2.5h)        │   │
│ └────┘               └──────────┘ └───────────────┘   │
└────────────────────────────────────────────────────────┘
```

**Lane assignment (greedy interval scheduling):**
```javascript
function assignLanes(jobs) {
    // sort by next_run asc
    const sorted = [...jobs].sort((a, b) => a.next_run - b.next_run);
    const lanes = [];   // each lane: array of {start, end}
    return sorted.map(job => {
        const start = job.next_run;
        const end = start + (job.estimated_duration || 600);
        const laneIdx = lanes.findIndex(lane =>
            lane.length === 0 || lane[lane.length - 1].end <= start
        );
        const idx = laneIdx === -1 ? lanes.length : laneIdx;
        if (!lanes[idx]) lanes[idx] = [];
        lanes[idx].push({ start, end });
        return { ...job, lane: idx };
    });
}
```

**Block rendering:**
- X: `(next_run - windowStart) / windowSeconds * 100%`
- Width: `(estimated_duration / windowSeconds) * 100%`
- Y: `lane * 44px`
- Height: `36px`
- Color: resource profile (embed=teal, ollama=accent, heavy=warning)
- Label: job name (truncated), VRAM badge

**Concurrent badge:** blocks in lane > 0 show a `⟡ concurrent` tag.

**Auto-refresh:** 10s polling interval. Uses `useRef` for guard state —
not render-time snapshot (Lesson #97). Cleanup returned from `useEffect`.

**JSX naming:** all `.map()` callbacks use descriptive names (`job`, `lane`,
`slot`) — never `h`, `j`, `l` (Lesson #13).

### 7c. Schedule table new columns

| Column | Content |
|--------|---------|
| Model | Editable dropdown (assigned model) with type badge |
| VRAM | Model VRAM in GB or `—` if unknown |
| ETA | Estimated start wall-clock time (from queue ETAs endpoint) |

### 7d. Settings tab additions

| Setting | Type | Default | Validation |
|---------|------|---------|------------|
| `max_concurrent_jobs` | int 1–8 | 1 | Warn if VRAM budget < 2× largest model |
| `concurrent_shadow_hours` | int 0–168 | 24 | 0 = disable shadow mode |
| `vram_safety_factor` | float 1.0–2.0 | 1.3 | Applied to disk size estimates |

UI warns if `max_concurrent_jobs > 1` and `vram_pause_pct` would be hit by
running 2 concurrent jobs simultaneously.

---

## 8. Production Reliability Hardening

| Risk | Mitigation |
|------|-----------|
| Orphaned PIDs on crash | `jobs.pid` column + `_recover_orphans()` on startup |
| Same-model concurrent collision | `_running_models` set checked in admission gate |
| Proxy sentinel collision | Slot-aware proxy semaphore replaces `job_id=-1` |
| `ollama ps` single-model assumption | `get_loaded()` returns full list |
| VRAM disk-size vs loaded-size gap | Observed delta EMA in `model_registry` |
| Model not found on job start | Pre-flight check → DLQ with `reason=model_missing` |
| Pull blocks job start | `_model_pull_in_progress()` check in admission gate |
| Stall detector assumes one job | Iterate `self._running` dict |
| Gantt data staleness | 10s auto-refresh + status-triggered refresh |
| `max_concurrent_jobs` too high | Settings UI warns + API validates bounds 1–8 |
| Multi-statement migration try/except | Each ALTER TABLE in own try/except (Lesson #107) |
| Schema drift frontend/backend | Vertical trace test after every new API field (Lesson #4) |
| `useEffect` stale snapshot | `useRef` for refresh guard (Lesson #97) |
| `contextlib.closing()` | All new DB methods (Lesson #34) |
| Async await discipline | Call-site `await` audit before PR (Lessons #31, #22) |

---

## 9. Testing Strategy

| Test | File | Coverage |
|------|------|---------|
| Admission gate unit tests (mock VRAM readings) | `test_daemon.py` | embed always passes, heavy serializes, same-model blocks |
| Concurrent job execution (mock ThreadPoolExecutor) | `test_daemon.py` | 2 jobs run, PID tracked, stall detected |
| Orphan recovery on startup | `test_daemon.py` | SIGTERM sent, jobs reset to pending |
| `OllamaModels.get_loaded()` multi-model parse | `test_models.py` | 0, 1, 3 loaded models; CPU/GPU split |
| `OllamaModels.classify()` profile heuristics | `test_models.py` | embed, heavy, ollama, coding, general |
| Pull lifecycle (start, progress, cancel) | `test_models.py` | status transitions, SIGTERM on cancel |
| Lane assignment algorithm | `test_schedule.py` | no overlap, 2-way stack, 3-way stack |
| `/api/models` endpoint | `test_api.py` | returns profile + type + vram fields |
| `/api/queue/etas` endpoint | `test_api.py` | cumulative offsets, concurrency-aware |
| Schema migration (each ALTER TABLE) | `test_db.py` | isolated try/except, partial failure safe |
| Vertical trace: submit → concurrent run → Gantt ETA | integration | one real job through full stack |

---

## 10. Concurrency Model Diagram

```
Daemon polling loop (single thread, 5s)
│
├── _recover_orphans()  [on startup only]
│
└── every tick:
    ├── health_check()
    ├── promote_due_jobs()
    ├── for each pending job (priority order):
    │   └── if _can_admit(job):
    │       ├── write job.pid to DB
    │       └── executor.submit(_run_job, job)  → background thread
    │
    └── _check_stalls()  [iterates self._running]

Background thread (per concurrent job):
└── subprocess.Popen(job.command)
    ├── on start: db.start_job(id), record VRAM before
    └── on complete: db.complete_job(id), record VRAM after → update model_registry
```

---

## 11. File Inventory

### New files
| File | Purpose |
|------|---------|
| `ollama_queue/models.py` | OllamaModels class |
| `tests/test_models.py` | OllamaModels unit tests |
| `ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx` | New Models tab |
| `ollama_queue/dashboard/spa/src/components/GanttChart.jsx` | Gantt visualizer |
| `ollama_queue/dashboard/spa/src/components/ModelBadge.jsx` | Type/profile badge |

### Modified files
| File | Change |
|------|--------|
| `ollama_queue/db.py` | `model_registry`, `model_pulls` tables; `jobs.pid`; CRUD methods |
| `ollama_queue/daemon.py` | ThreadPoolExecutor, admission gate, PID tracking, stall multi-job |
| `ollama_queue/health.py` | `get_loaded_models()` multi-model parse |
| `ollama_queue/estimator.py` | Concurrency-aware `queue_etas()` |
| `ollama_queue/api.py` | 6 new endpoints, enriched `/api/schedule`, proxy semaphore |
| `ollama_queue/dashboard/spa/src/app.jsx` | Add Models tab |
| `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx` | GanttChart, Model/VRAM/ETA columns |
| `ollama_queue/dashboard/spa/src/pages/Settings.jsx` | Concurrency settings |
| `ollama_queue/dashboard/spa/src/store.js` | New signals: models, pulls, etas |
| `tests/test_daemon.py` | Concurrency + admission tests |
| `tests/test_api.py` | New endpoint tests |
| `tests/test_db.py` | Schema migration tests |

---

## Open Questions (resolved)

- **Concurrency model:** Adaptive (parallel when VRAM allows) ✓
- **Model catalog source:** Curated + search ✓
- **Recommendations:** Type-based badge + historical timing ✓
- **Derisking:** Feature flag `max_concurrent_jobs=1`, shadow mode 24h, mock-testable ✓
- **Ollama RAM spillover:** Three-factor gate (VRAM + RAM + health evaluate) ✓
- **Timeline visualization:** Gantt blocks + lane stacking ✓
