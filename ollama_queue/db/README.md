# db/ — SQLite Persistence Layer

## Purpose

All persistent state lives in a single SQLite file
(`~/.local/share/ollama-queue/queue.db`). Every other module reads and writes
through this layer -- nothing else touches disk directly.

## Architecture

Uses the **mixin pattern** to split a large class across domain files while
presenting a single `Database` API. All mixins access `self._conn` and `self._lock`:

```python
class Database(SchemaMixin, JobsMixin, ScheduleMixin, SettingsMixin,
               HealthMixin, DLQMixin, EvalMixin):
    def __init__(self, db_path):
        self._conn = None
        self._lock = threading.RLock()
```

The `_connect()` method lazily creates the connection with WAL mode, foreign keys,
and performance pragmas (mmap, cache, busy_timeout).

## Modules

| File | Mixin | Tables Owned | Key Methods |
|------|-------|--------------|-------------|
| `schema.py` | `SchemaMixin` | All (CREATE TABLE) | `initialize()`, `_run_migrations()`, `seed_eval_defaults()` |
| `jobs.py` | `JobsMixin` | `jobs`, `duration_history`, `job_metrics`, `model_registry`, `model_pulls` | `submit_job`, `start_job`, `complete_job`, `cancel_job`, `get_pending_jobs`, `estimate_duration`, `store_job_metrics`, `get_model_stats` |
| `schedule.py` | `ScheduleMixin` | `recurring_jobs`, `schedule_events` | `add_recurring_job`, `list_recurring_jobs`, `update_recurring_job`, `promote_due_jobs_batch`, `get_schedule_events` |
| `dlq.py` | `DLQMixin` | `dlq` | `move_to_dlq`, `retry_dlq_entry`, `dismiss_dlq_entry`, `list_dlq`, `update_dlq_reschedule` |
| `health.py` | `HealthMixin` | `health_log`, `daemon_state` | `log_health`, `get_daemon_state`, `update_daemon_state`, `try_claim_for_proxy`, `release_proxy_claim`, `prune_old_data` |
| `settings.py` | `SettingsMixin` | `settings` | `get_setting`, `set_setting`, `get_all_settings` |
| `eval.py` | `EvalMixin` | -- (placeholder) | Currently empty; eval CRUD lives in `eval/engine.py` |

## Key Patterns

- **RLock, not Lock**: `self._lock` is a `threading.RLock` because existing callers
  hold the lock while calling `_connect()`. Changing to `Lock` would deadlock.

- **Lock-before-connect**: Every write method follows `with self._lock:` then
  `conn = self._connect()`. Reversing this order creates a TOCTOU race.

- **WAL mode**: Enables concurrent readers alongside a single writer. Combined with
  `check_same_thread=False`, this lets FastAPI worker threads read while the daemon
  writes.

- **Idempotent migrations**: `_add_column_if_missing()` wraps `ALTER TABLE ADD COLUMN`
  and catches "duplicate column" errors. `_run_migrations()` calls this for every
  column added after the initial schema.

- **Proxy sentinel**: `try_claim_for_proxy()` sets `current_job_id = -1` to
  distinguish proxy claims from real job execution. `release_proxy_claim()` only
  clears claims with `current_job_id = -1`.

- **Settings are JSON-encoded**: `get_setting()` calls `json.loads()` on the stored
  value. The falsy-zero antipattern applies: use `int(x) if x is not None else default`
  for numeric settings that can legitimately be 0.

## Dependencies

**Depends on**: Nothing (leaf module)
**Depended on by**: Every other domain
