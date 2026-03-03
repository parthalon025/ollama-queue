# check_command + max_runs — Design Doc

**Date:** 2026-03-03
**Status:** Approved
**Feature:** Conditional job promotion for recurring jobs

## Problem

Recurring jobs currently run on every scheduled interval regardless of whether there's meaningful work to do. The three lessons-db meta-learning jobs (extract-principles, generate-meta-lessons, eval-generate) each embed short-circuit logic in their wrapper scripts — SQLite queries that check whether work remains before invoking the LLM CLI. This logic is invisible to the queue, produces meaningless "completed with exit 0" job records when there's nothing to do, and can't signal auto-disable when a job has fully converged.

## Solution

Add two optional fields to `recurring_jobs`:

- **`check_command TEXT`** — a shell command that runs before the job body. Its exit code controls promotion.
- **`max_runs INTEGER`** — a countdown that auto-disables the job after N successful completions.

These are independent: a job can have either, both, or neither.

---

## 1. Schema

```sql
ALTER TABLE recurring_jobs ADD COLUMN check_command TEXT;
ALTER TABLE recurring_jobs ADD COLUMN max_runs INTEGER;
```

Both are nullable. Existing jobs are unaffected.

Migration path: added as part of `_ensure_schema()` / `MIGRATIONS` list in `db.py` (the existing pattern).

---

## 2. Execution Model

**Execution location: `_run_job()` (executor thread)**

The check_command runs inside the executor thread, not during `promote_due_jobs()` on the poll thread. This is critical — blocking the poll thread would stall all scheduling for the subprocess duration.

Sequence inside `_run_job()` when `check_command` is set:

```
1. Spawn check_command via subprocess.run(shell=True, timeout=30)
2. Inspect exit code:
   - 0  → proceed to main job command (normal run)
   - 1  → skip this run; advance next_run by interval; log reason; return (no failure recorded)
   - 2  → auto-disable job (set enabled=False); log reason; return
   - other → treat as check failure; log warning; proceed anyway (fail-open)
3. If main command runs and succeeds (exit 0):
   - Decrement max_runs if set (max_runs = max_runs - 1)
   - If max_runs reaches 0: auto-disable job
4. Record job history as normal
```

**Fail-open rationale:** Unknown exit codes from `check_command` should not silently suppress work. If the check script has a bug (non-0, non-1, non-2), we run the main job anyway and log a warning. This prevents a buggy check from indefinitely suppressing legitimate work.

**Timeout:** 30s hard limit on `check_command`. If it times out, fail-open (proceed with main job) and log a warning.

---

## 3. Stopping Conditions

| Condition | Trigger | Action |
|-----------|---------|--------|
| `check_command` exits 2 | Work permanently done | `enabled=False`, `outcome_reason="check_command signaled complete"` |
| `max_runs` reaches 0 | N successful runs completed | `enabled=False`, `outcome_reason="max_runs exhausted"` |
| Both set | Either condition triggers first | First to trigger wins; both log to `outcome_reason` |

**Failed runs do NOT count toward `max_runs`.** Only main-command exits with code 0 decrement the counter. This ensures jobs that converge after N *successful* passes (e.g., eval-generate hitting all 180 pairs) auto-disable after those N runs, not N attempts including retries.

---

## 4. Exit Code Contract (documented)

```
check_command exit codes:
  0  → work available → promote to queue (run now)
  1  → no work → skip this interval, advance next_run
  2  → permanently done → auto-disable this job
  other → warning logged → fail-open (run anyway)
```

This is the contract the wrapper scripts must honor. The three lessons-db scripts will be updated to use this contract instead of their current short-circuit `exit 0` patterns.

---

## 5. CLI Changes

### Create / Update

```bash
ollama-queue schedule add \
  --name "lessons-db-extract-principles" \
  --command "..." \
  --interval 30m \
  --check-command "..." \
  --max-runs 10

ollama-queue schedule update lessons-db-extract-principles \
  --check-command "..." \
  --max-runs 10
```

New flags on both `schedule add` and `schedule update`:
- `--check-command TEXT` — optional shell command
- `--max-runs INTEGER` — optional countdown (NULL = unlimited)

### Re-enable disabled jobs

```bash
ollama-queue schedule enable lessons-db-extract-principles
```

Clears `enabled=False` + resets `outcome_reason`. Useful when a job auto-disabled via exit-2 but conditions have changed (e.g., new lessons added, want to re-run eval-generate from scratch).

---

## 6. API Changes

`RecurringJobCreate` and `RecurringJobUpdate` Pydantic models gain:

```python
check_command: Optional[str] = None
max_runs: Optional[int] = None
```

New endpoint:
```
POST /api/schedule/jobs/{name}/enable
```
Clears `enabled=False` and `outcome_reason`.

---

## 7. Dashboard Changes

The Schedule tab recurring-jobs table gains two new columns:

| Column | Content |
|--------|---------|
| **Check** | `✓` if `check_command` is set, blank otherwise |
| **Runs** | `{max_runs} left` if set, blank if NULL |

Both are read-only display fields. Editing check_command / max_runs happens via the existing row-edit modal (add the two fields there).

The enable/disable toggle already exists. When a job auto-disables via max_runs or check_command exit-2, the toggle reflects `enabled=False` — clicking it re-enables (same as `schedule enable` CLI).

---

## 8. Wrapper Script Updates (post-implementation)

Once the feature lands, the three lessons-db wrapper scripts are simplified:

- **Remove** the `python3 -c "SELECT COUNT(*) ..."` short-circuit blocks
- **Add** a separate `check_*.sh` companion or inline the check logic as the `check_command` value
- **Exit code contract** replaces the current `exit 0` when nothing to do

This is a follow-on task — the wrapper scripts work correctly today. The check_command feature makes them cleaner, not mandatory to refactor immediately.

---

## 9. Tests

| Test | Description |
|------|-------------|
| `test_check_command_exit0_runs_job` | Exit 0 → main job promoted |
| `test_check_command_exit1_skips` | Exit 1 → job skipped, next_run advanced |
| `test_check_command_exit2_disables` | Exit 2 → job auto-disabled |
| `test_check_command_unknown_exit_failopen` | Exit 99 → warning + runs anyway |
| `test_check_command_timeout_failopen` | Timeout → warning + runs anyway |
| `test_max_runs_decrements_on_success` | Successful run → max_runs decremented |
| `test_max_runs_no_decrement_on_failure` | Failed run → max_runs unchanged |
| `test_max_runs_zero_disables_job` | max_runs → 0 → auto-disabled |
| `test_schedule_enable_clears_disabled` | `enable` endpoint re-enables |
| `test_cli_check_command_flag` | CLI flags parsed correctly |
| `test_api_check_command_field` | API model accepts/returns fields |
| `test_dashboard_check_column_renders` | Dashboard column present |

---

## 10. Files to Modify

| File | Change |
|------|--------|
| `ollama_queue/db.py` | Add `check_command`, `max_runs` columns to schema/migrations |
| `ollama_queue/daemon.py` | `_run_job()`: check_command execution + max_runs decrement logic |
| `ollama_queue/api.py` | `RecurringJobCreate/Update` models + `/enable` endpoint |
| `ollama_queue/cli.py` | `--check-command`, `--max-runs` flags; `schedule enable` subcommand |
| `frontend/src/` | Schedule tab: two new columns + enable endpoint wire-up |
| `tests/test_daemon.py` | Executor thread tests |
| `tests/test_api.py` | API model + endpoint tests |
| `tests/test_cli.py` | CLI flag tests |

---

## Key Decisions

- **Executor thread, not poll thread** — avoids blocking the scheduling loop
- **Fail-open on unknown exit codes** — buggy checks don't suppress work silently
- **Failed runs don't count toward max_runs** — counts measure convergence, not attempts
- **max_runs and check_command are independent** — either, both, or neither
- **enable endpoint** — provides an explicit escape hatch when auto-disabled conditions change
