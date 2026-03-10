# config/ LLM Guide

## What You Must Know

Three components form a pipeline from discovery to enforcement: `scanner.py` detects services calling Ollama on port 11434, `patcher.py` rewrites their config to route through the queue on port 7683, and `intercept.py` manages iptables REDIRECT rules as a network-layer fallback.

## Scanner Phases (scanner.py)

`run_scan(db)` executes 4 phases:

1. **Live scan** (`_live_scan_*`): `ss` -> `lsof` -> `netstat` fallback chain. Finds active connections to :11434.
2. **Static scan**: Searches systemd units and config files for port 11434 references.
3. **Streaming detection**: Regex for `stream=True` patterns in source files.
4. **Deadlock check**: Flags consumers whose process is a managed queue job (`is_managed_job=True`).

Results are upserted into the `consumers` table. Only columns in `_DB_CONSUMER_COLS` are written to the DB; transient scanner fields are stripped.

### Live scan subprocess handling

```python
# Check returncode, not just exception
result = subprocess.run(["ss", ...], capture_output=True, ...)
if result.returncode != 0:
    _log.warning("ss failed: %s", result.stderr)
    return []
```

`subprocess.run()` does NOT raise on non-zero exit. A missing tool returns empty list, not an error.

### Deadlock prevention

`deadlock_check()` calls `db._connect()` -- it must be wrapped in `with db._lock:`:

```python
with db._lock:
    conn = db._connect()
    # check if consumer PID matches a running queue job
```

Including a managed queue job as a consumer causes deadlock: the job calls through the proxy, which tries to claim the sentinel, but the daemon already holds it.

## Patcher (patcher.py)

`patch_consumer(consumer)` rewrites config files:

- **Backup**: Copies to `<path>.ollama-queue.bak` before modification
- **Revert**: `revert_consumer()` restores from backup
- **Formats**: systemd units (Environment=), env files (KEY=), YAML, TOML
- **Restart**: Immediate (`_reload_systemd()` + `_restart_service()`) or deferred

### Guard: empty patch_path

`revert_consumer()` must check `patch_path` before calling `Path(patch_path).exists()`. An empty string gives a false-positive hit on the current directory.

### Subprocess return values

`_reload_systemd()` and `_restart_service()` return `bool`. Callers must check the return value -- silent failures mask systemd errors.

## Intercept (intercept.py)

iptables NAT REDIRECT: redirects outbound traffic from a specific UID to port 11434 to the queue port (7683). Linux-only.

- `enable_intercept(uid, queue_port)` -- adds rule, persists with `iptables-save`
- `disable_intercept(uid, queue_port)` -- removes rule
- `get_intercept_status(uid, queue_port)` -- checks if rule exists

### Failure returns

`disable_intercept()` returns `{"enabled": True, ...}` on iptables failure (rule still active). The API endpoint raises HTTP 500 in this case.

`enable_intercept()` requires at least 1 included consumer (API guard, not intercept guard).

## Subprocess Security

`scanner.py`, `patcher.py`, and `intercept.py` all use `subprocess` with known system binaries. Bandit/ruff rules `S603`/`S607` are suppressed via `per-file-ignores` in `ruff.toml`. Do NOT add inline `# noqa` comments -- they trigger RUF100 (redundant noqa).

## Adding a New Config Format

1. Add detection logic in `scanner.py:static_scan()` (file pattern + regex)
2. Add patch function in `patcher.py` (e.g., `_patch_ini(path)`)
3. Add the format case to `patch_consumer()` dispatch
4. Ensure backup is created via `_backup(path)` before modification
5. Test revert restores the original content exactly

## Testing

```bash
pytest tests/test_scanner.py -x     # scanner phases
pytest tests/test_patcher.py -x     # patch + revert + health check
pytest tests/test_intercept.py -x   # iptables rule management
pytest tests/test_consumers.py -x   # consumer DB operations
pytest tests/test_consumers_api.py -x  # API endpoints
```

Scanner tests mock `subprocess.run` for `ss`/`lsof`/`netstat`. Intercept tests mock `subprocess.run` for `iptables`. Patcher tests use `tmp_path` for file operations.

## Dependencies

- **Depends on**: db/ (consumers table CRUD)
- **Depended on by**: api/consumers.py (scan, patch, revert, intercept endpoints), app.py (startup scan)
