# PRD: Full Domain Reorganization

**Design doc:** `docs/plans/2026-03-10-full-domain-reorganization-design.md`
**Tasks:** 13 | **Phases:** 3 (Python split → JS split → cleanup)

## Task Dependency Graph

```
Phase 1 — Python:
  [1] api/ ──┐
             ├──[2] eval/ ──┐
             │              ├──[3] db/ ──[4] daemon/ ──[5] small modules
             │              │
             └──────────────┘
  [5] ──[6] cli imports ──[7] test imports ──[11] clean re-exports

Phase 2 — JavaScript (independent of Python):
  [8] stores/ ──┬──[9] Plan/
                └──[10] component splits

Phase 3 — Cleanup:
  [11] + [12] docs ──[13] final quality gate
```

## Tasks

### 1. Create api/ subpackage from api.py
Split 2,826-line monolith into 13 domain route files using FastAPI APIRouter. Create app.py for app assembly. Each route file handles one domain (jobs, proxy, schedule, consumers, models, health, settings, dlq, eval_runs, eval_variants, eval_settings, eval_trends).

### 2. Create eval/ subpackage from eval_engine.py
Split 2,620-line monolith into 5 phase modules: engine.py (lifecycle), generate.py (generation), judge.py (judging + agreement), promote.py (auto-promote), analysis.py (pure analysis, moved from eval_analysis.py).

### 3. Create db/ subpackage from db.py using mixin pattern
Split 1,940-line single-class module into 7 domain mixin files. Database class assembles via multiple inheritance. Each mixin uses self._conn and self._lock from the base.

### 4. Create daemon/ subpackage from daemon.py
Split 1,347-line module into loop.py (polling) + executor.py (subprocess management).

### 5. Move small modules to domain subpackages
16 existing modules → 4 domain packages: scheduling/ (4 files), sensing/ (4 files), models/ (4 files), config/ (3 files). 3 tiny files stay flat.

### 6. Update cli.py imports
Point all cli.py imports to new subpackage paths.

### 7. Update all test imports and mock paths
Update imports and `mock.patch()` target strings across 40 test files. Critical: patch paths must match where the name is looked up, not where it's defined.

### 8. Split store.js into domain stores
816-line store → 6 domain stores + index.js re-export. Update component imports.

### 9. Split Plan.jsx into section components
1,318-line page → Plan/ directory with 3 section components.

### 10. Split oversized JS components into directories
4 components → directory components with index.jsx entry points.

### 11. Remove backward-compatibility re-exports
Clean up temporary `__init__.py` re-exports and verify no stale import paths remain.

### 12. Update CLAUDE.md and ruff.toml
Reflect new structure in docs. Update per-file-ignores for renamed paths.

### 13. Full quality gate
All 1,587 tests, 100% coverage, lint clean, format clean, SPA builds, no files over 900 lines.
