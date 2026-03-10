# 100% Test Coverage Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Achieve 100% line coverage across all `ollama_queue/` modules (currently 81%, 1,143 uncovered lines).

**Architecture:** Add unit tests with mocking for I/O-heavy paths (subprocess, HTTP, filesystem, systemd). No production code changes — tests only.

**Tech Stack:** pytest, pytest-cov, unittest.mock, httpx (test client)

---

## Coverage Summary (baseline: 2026-03-10)

| Tier | Modules | Current | Lines Missing | Strategy |
|------|---------|---------|---------------|----------|
| **T1: Near-100%** | estimator, intelligence, slot_scoring, runtime_estimator, deferral_scheduler, dlq_scheduler, metrics_parser, performance_curve, eval_analysis, dlq, db, burst | 92-100% | ~57 lines | 1-2 edge-case tests per module |
| **T2: 80-90%** | scheduler, system_snapshot, health, scanner, stall | 81-90% | ~95 lines | Mock system calls, test error branches |
| **T3: 69-81%** | api, eval_engine, cli, daemon, intercept, models, patcher | 55-81% | ~991 lines | Significant mocking of subprocess, httpx, systemd, iptables |

---

## Batch 1: T1 Modules (92-100% → 100%)

### Task 1: Close `db.py` gaps (37 lines → 0)

**Files:**
- Test: `tests/test_db.py`

**Step 1: Read uncovered lines**
Run: `.venv/bin/python -m pytest -o "addopts=" --cov=ollama_queue/db --cov-report=term-missing tests/test_db.py -q`
Lines: 129, 558-559, 929-936, 1002, 1167, 1170, 1183, 1342-1343, 1390-1403, 1428, 1558, 1637, 1660, 1664, 1736, 1816-1822, 1847, 1933

**Step 2: Write tests for each uncovered branch**
Focus on: error paths in CRUD methods, edge cases in schema migration, settings fallbacks.

**Step 3: Run and verify**
Run: `.venv/bin/python -m pytest -o "addopts=" --cov=ollama_queue/db --cov-report=term-missing tests/test_db.py -q`
Expected: 100% coverage on db.py

**Step 4: Commit**
```bash
git add tests/test_db.py
git commit -m "test: achieve 100% coverage on db.py"
```

### Task 2: Close `burst.py` gaps (3 lines → 0)

**Files:**
- Test: `tests/test_burst.py`

**Step 1: Identify uncovered lines**
Lines 73, 103, 109 — likely edge cases in regime detection.

**Step 2: Write tests targeting those branches**

**Step 3: Verify 100% coverage**

**Step 4: Commit**

### Task 3: Close `scheduler.py` gaps (21 lines → 0)

**Files:**
- Test: `tests/test_scheduler.py`

Lines: 65, 78-83, 101, 162-163, 274-276, 351, 356-366, 442

### Task 4: Close remaining T1 gaps

Cover remaining 1-8 line gaps in: `deferral_scheduler.py` (1 line), `dlq_scheduler.py` (1 line), `dlq.py` (2 lines), `metrics_parser.py` (1 line), `performance_curve.py` (3 lines), `eval_analysis.py` (8 lines), `runtime_estimator.py` (1 line).

**Step 1: Write one test file with all edge cases**
Create: `tests/test_coverage_t1.py` — one test per uncovered line/branch

**Step 2: Run and verify**

**Step 3: Commit**

---

## Batch 2: T2 Modules (80-90% → 100%)

### Task 5: Close `system_snapshot.py` gaps (8 lines)

Lines 53-54, 57-58, 61-62, 67-68 — VRAM estimation branches for different model sizes.

### Task 6: Close `health.py` gaps (18 lines)

Lines: real system metric paths (psutil/nvidia-smi). Mock `psutil.virtual_memory()`, `subprocess.run("nvidia-smi")`.

### Task 7: Close `scanner.py` gaps (28 lines)

Lines: live scan subprocess calls (`ss`, `lsof`, `netstat`). Mock `subprocess.run()` with various returncode/stdout combinations.

### Task 8: Close `stall.py` gaps (20 lines)

Lines: CPU monitoring paths, edge cases in Bayesian detection.

---

## Batch 3: T3 Modules — Heavy Mocking (55-81% → 100%)

### Task 9: Close `patcher.py` gaps (65 lines — lowest coverage)

Mock: `Path.read_text()`, `Path.write_text()`, `subprocess.run()` for systemd reload/restart. Test: all config formats (env, yaml, toml, systemd unit), backup/revert paths, error branches.

### Task 10: Close `models.py` gaps (69 lines)

Mock: `httpx.get("http://localhost:11434/api/tags")`, `httpx.get("http://localhost:11434/api/ps")`. Test: cache invalidation, model size parsing, VRAM estimation.

### Task 11: Close `intercept.py` gaps (17 lines)

Mock: `subprocess.run("iptables ...")`. Test: enable/disable/status with various iptables outputs and failures.

### Task 12: Close `cli.py` gaps (122 lines)

Use Click's `CliRunner` to test all subcommands. Mock `db` and `requests` for API calls. Cover: settings get/set, metrics output formatting, schedule suggest, dlq commands.

### Task 13: Close `daemon.py` gaps (224 lines)

This is the hardest module. Mock: `subprocess.Popen`, `health.get_metrics()`, `db` methods. Test:
- `serve()` startup and signal handling
- `_run_job()` subprocess lifecycle (success, failure, timeout, stall)
- `_can_admit()` all branches
- `poll_once()` all state transitions
- `_recover_orphans()` with various DB states

### Task 14: Close `eval_engine.py` gaps (231 lines)

Mock: Ollama proxy calls, DB methods. Test:
- `run_eval_generate()` — full loop with mock responses, cancellation mid-loop
- `run_eval_judge()` — judge scoring with various response formats
- `check_auto_promote()` — all three gates, edge cases
- `generate_eval_analysis()` — error paths
- Session lifecycle: create → generate → judge → complete

### Task 15: Close `api.py` gaps (263 lines)

Use FastAPI `TestClient`. Mock: `db`, `daemon`, `eval_engine`. Test:
- Streaming proxy error paths
- All eval endpoints error responses
- Consumer scan/include/revert/intercept endpoints
- Settings validation
- SSRF protection on data_source_url

---

## Batch 4: Verification

### Task 16: Full coverage run

Run: `.venv/bin/python -m pytest -o "addopts=" --cov=ollama_queue --cov-report=term-missing --cov-fail-under=100 -q`

Expected: 100% coverage, 0 uncovered lines.

### Task 17: Commit and PR

```bash
git add tests/
git commit -m "test: achieve 100% line coverage across all modules"
```
