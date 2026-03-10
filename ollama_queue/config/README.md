# config/ — Consumer Configuration + Traffic Intercept

## Purpose

Discovers services that call Ollama directly on port 11434, patches their
configuration to route through the queue on port 7683, and optionally intercepts
traffic at the network layer using iptables.

## Architecture

Three components form a pipeline from discovery to enforcement:

```
scanner.py    -- Detect: find processes and config files pointing at :11434
patcher.py    -- Patch: rewrite config files to point at :7683 + backup/revert
intercept.py  -- Enforce: iptables REDIRECT rule at the network layer (Linux only)
```

The scanner runs automatically on startup (`app.py`) and on-demand via
`POST /api/consumers/scan`. Patching and intercept are user-initiated through
the Consumers tab in the dashboard.

## Modules

| File | Key Exports | Role |
|------|-------------|------|
| `__init__.py` | -- | Docstring only |
| `scanner.py` | `run_scan(db)`, `live_scan()`, `static_scan()` | 4-phase consumer detection: (1) live scan via `ss`/`lsof`/`netstat` for active connections, (2) static scan of systemd unit files and config files for port 11434 references, (3) streaming detection via regex for `stream=True` patterns, (4) deadlock check to flag consumers that are managed queue jobs (patching these causes deadlock). Results are upserted into the `consumers` table. |
| `patcher.py` | `patch_consumer(consumer)`, `revert_consumer(consumer)`, `check_health(consumer, db)` | Config file rewriter supporting systemd units, env files, YAML, and TOML. Creates `.ollama-queue.bak` backups before patching. `revert_consumer()` restores from backup. `check_health()` verifies the patched service responds through the queue proxy. Supports immediate or deferred restart policies. |
| `intercept.py` | `enable_intercept(uid, queue_port)`, `disable_intercept(uid, queue_port)`, `get_intercept_status(uid, queue_port)` | iptables NAT REDIRECT rule management. Redirects outbound traffic from uid to port 11434 to the queue port (7683). Linux-only. Persists rules via `iptables-save`. Returns status dicts with `enabled` boolean and optional `error`. |

## Key Patterns

- **Deadlock prevention**: The scanner's `deadlock_check()` flags consumers whose
  process is a managed queue job (`is_managed_job=True`). Including these would
  cause a deadlock: the queue job would call through the proxy, which tries to
  claim the proxy sentinel, but the daemon already holds it for the running job.

- **Backup before patch**: `patcher.py` copies the original file to
  `<path>.ollama-queue.bak` before any modification. `revert_consumer()` restores
  from this backup. The backup path is deterministic -- no versioning.

- **Live scan fallback chain**: Tries `ss` first, falls back to `lsof`, then
  `netstat`. Each tool may not be installed; non-zero exit codes are logged as
  warnings and return empty lists.

- **subprocess security**: `scanner.py` and `patcher.py` invoke system binaries
  (`ss`, `lsof`, `iptables`, `systemctl`). Bandit/ruff `S603`/`S607` rules are
  suppressed via `per-file-ignores` in `ruff.toml` (not inline `# noqa`).

- **UID-scoped intercept**: The iptables rule uses `-m owner --uid-owner` to only
  redirect traffic from the queue user's UID. Other users' Ollama traffic is
  unaffected.

## Dependencies

**Depends on**: `db/` (consumers table CRUD)
**Depended on by**: `api/consumers.py` (scan, patch, revert, intercept endpoints), `app.py` (startup scan)
