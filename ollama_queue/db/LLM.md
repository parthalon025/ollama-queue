# db/ LLM Guide

## What You Must Know

All persistent state lives in a single SQLite file. The `Database` class is assembled from 7 mixins via Python MRO. Every other module reads and writes through this layer.

## Mixin Pattern

```python
class Database(SchemaMixin, JobsMixin, ScheduleMixin, SettingsMixin,
               HealthMixin, DLQMixin, EvalMixin):
    def __init__(self, db_path):
        self._conn = None
        self._lock = threading.RLock()
```

Each mixin owns a set of tables. All mixins access `self._lock` and `self._connect()`.

## The Two Absolute Rules

### 1. Lock before connect -- always

```python
# CORRECT
def my_method(self, ...):
    with self._lock:
        conn = self._connect()
        conn.execute(...)
        conn.commit()

# WRONG -- TOCTOU race
def my_method(self, ...):
    conn = self._connect()  # race window here
    with self._lock:
        conn.execute(...)
```

### 2. RLock, not Lock

`self._lock` is `threading.RLock` because `_connect()` itself acquires `self._lock` internally. If you change to `Lock`, nested acquisition deadlocks.

## Adding a New Column

1. Add to `CREATE TABLE IF NOT EXISTS` in `schema.py:initialize()` (for fresh DBs)
2. Add `self._add_column_if_missing(conn, "table", "col", "TYPE DEFAULT val")` in `_run_migrations()`
3. If table has seeded rows (e.g. `eval_variants`), add backfill after migration:
   ```python
   conn.execute("UPDATE eval_variants SET col = ? WHERE col IS NULL", (value,))
   ```
   `INSERT OR IGNORE` skips pre-existing rows, leaving the new column NULL.
4. Update all INSERT/SELECT queries in the corresponding mixin
5. Document upgrade path in CLAUDE.md Gotchas for live DBs

## Adding a New Setting

1. Add to `DEFAULTS` or `EVAL_SETTINGS_DEFAULTS` in `schema.py`
2. Read with `db.get_setting("key")` -- returns JSON-decoded value or `None`
3. Write with `db.set_setting("key", value)` -- JSON-encodes
4. **Falsy-zero guard**: `int(x) if x is not None else default`, never `int(x) or default`

## Proxy Sentinel

`try_claim_for_proxy()` sets `current_job_id = -1` in `daemon_state`. `release_proxy_claim()` only clears claims where `current_job_id = -1`. This distinguishes proxy claims from real job execution.

## Key Method Inventory

| Mixin | Critical Methods |
|-------|-----------------|
| SchemaMixin | `initialize()`, `_run_migrations()`, `seed_eval_defaults()` |
| JobsMixin | `submit_job()`, `get_pending_jobs(exclude_sentinel=True)`, `start_job()`, `complete_job()`, `cancel_job()`, `_set_job_retry()` |
| ScheduleMixin | `add_recurring_job()`, `promote_due_jobs_batch()`, `update_recurring_job()` |
| DLQMixin | `move_to_dlq()`, `retry_dlq_entry()`, `mark_dlq_scheduling()`, `update_dlq_reschedule()` |
| HealthMixin | `log_health()`, `update_daemon_state()`, `try_claim_for_proxy()`, `release_proxy_claim()`, `prune_old_data()` |
| SettingsMixin | `get_setting()`, `set_setting()`, `get_all_settings()` |
| EvalMixin | Currently empty -- eval CRUD lives in `eval/engine.py` |

## Gotchas

- `move_to_dlq()` must set `completed_at` on the job row, or `prune_old_data()` never cleans it up
- `_set_job_retry()` must clear `completed_at = NULL`, or the retried job gets pruned before re-running
- `get_pending_jobs()` defaults to `exclude_sentinel=True` -- pass `False` only if you need proxy sentinel jobs
- Settings are JSON-encoded strings. `get_setting()` calls `json.loads()`. A setting value of `"0"` is truthy as a string but `int("0")` is falsy as an int
- `_add_column_if_missing()` catches "duplicate column" errors -- idempotent and safe to re-run

## WAL Mode and Threading

SQLite connects with `check_same_thread=False` and WAL mode. This allows FastAPI worker threads to read while the daemon writes. The `_lock` serializes all writes. `busy_timeout=5000` handles cross-process contention (e.g., sqlite3 CLI, migration scripts).

## Testing

```bash
pytest tests/test_db.py -x           # core DB tests
pytest tests/test_dlq.py -x          # DLQ operations
pytest tests/test_job_metrics.py -x  # metrics storage
```

Test fixture: `db(tmp_path)` in `conftest.py` creates a fresh `Database` per test with `initialize()` called.

## Dependencies

- **Depends on**: Nothing (leaf module)
- **Depended on by**: Every other domain
