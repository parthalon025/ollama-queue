# Consumer Detection & Onboarding Design

**Date:** 2026-03-08
**Status:** Approved
**Scope:** ollama-queue

## Overview

Detect services and processes calling Ollama directly on port 11434, surface them in the SPA dashboard, and allow users to onboard them through the queue proxy with one click — safely, with streaming and deadlock guards.

Designed for public use across machines. Full auto-patch support on Linux and macOS. Windows: detection + manual snippet only.

---

## Goals

- Detect both live (currently running) and dormant (scheduled/systemd) Ollama consumers
- Present discovered consumers in a new "Consumers" SPA tab
- Allow include/ignore per consumer with streaming and deadlock safety gates
- Auto-patch included consumers (env-var injection into systemd unit / .env / config file)
- Validate that the redirect actually worked post-patch (two-signal health check)
- Revertable: every patch backed up before writing

---

## Non-Goals

- iptables/network-layer transparent redirect (too blunt, breaks streaming globally)
- Windows auto-patch support (detection + snippet only)
- Automatic inclusion without user consent

---

## Architecture

```
Scanner (ollama_queue/scanner.py)
  ├── Phase 1: live_scan()     — ss / lsof / netstat by platform
  ├── Phase 2: static_scan()   — grep systemd units, .env, config files
  ├── Phase 3: stream_check()  — grep source for stream=True
  └── Phase 4: deadlock_check()— cross-ref recurring_jobs table

Patcher (ollama_queue/patcher.py)
  ├── systemd unit injection
  ├── .env file update
  ├── config.yaml / .toml update
  └── manual snippet generation (fallback)

Validator (ollama_queue/scanner.py)
  └── Two-signal health check post-patch:
      1. Old port (:11434) gone for process
      2. New port (:7683) active for process
      3. (Strongest) request_count increased after onboarded_at

API: /api/consumers/*
SPA: "Consumers" tab (6th tab)
DB:  consumers table
```

---

## Known Risks & Mitigations

### 1. Live scan misses scheduled services
- **Risk:** `ss` only catches running processes. Systemd timers running at 7am are invisible at 2pm.
- **Mitigation:** Phase 2 static scan catches dormant services via unit file grep. Mark live-only detections as `type=transient`. Persist scan history — a service seen once stays in the table with `last_live_seen` timestamp. UI shows "Scheduled — may not appear until next run" badge for transient consumers.

### 2. Streaming forced False — silent breaker
- **Risk:** Proxy hardcodes `stream=False`. Streaming consumers break silently after patch.
- **Mitigation:** Phase 3 stream check greps source for `stream=True|stream: true|streaming=True`.
  - `streaming_confirmed=True` (found in source): Include disabled until user ticks confirm checkbox
  - `streaming_suspect=True` (can't inspect: binary/3rd-party lib): Include shows confirm modal
  - Third-party libs (LangChain, LlamaIndex) may set streaming internally — treated as suspect by default
  - API hard-blocks unless `force_streaming_override=True` sent explicitly

### 3. Deadlock — queue job calling back through proxy
- **Risk:** A recurring queue job that calls Ollama, if redirected through :7683, holds a job slot that the proxy claim waits on → deadlock. See Lesson #1733.
- **Mitigation:** Phase 4 cross-references process name/cmdline against `recurring_jobs.command` and `recurring_jobs.name`. If match: `is_managed_job=True`. API returns 409 with no override. UI shows lock badge — cannot include.

### 4. Service restart drops in-flight requests
- **Risk:** Patching a live systemd service requires restart, killing any active Ollama call.
- **Mitigation:** `restart_policy` per consumer: `immediate` (restart now) or `deferred` (patch file, restart on next natural restart). UI exposes as dropdown on Include button. Status shows `pending_restart` until user or service restart applies the change.

### 5. .env patching is fragile across environments
- **Risk:** Not all projects use .env. Some use config.yaml, .toml, Docker env, or hardcode the URL.
- **Mitigation:** Patcher tries in order: systemd unit → .env → config.yaml → .toml → manual snippet.
  `patch_type` stored in DB. UI clearly distinguishes "Auto-patched (systemd)" from "Snippet generated (manual required)". Manual snippet shown inline with copy button.

### 6. Post-patch health validation timing gap
- **Risk:** A single ss check (old port gone) passes even if the consumer failed to restart.
- **Mitigation:** Two-signal validation — old port gone AND new port active. Third signal: `request_count` increase after `onboarded_at` (strongest — comes from queue's own data). Status states: `verifying → confirmed | partial | failed`. Timeout: 60s → auto-mark failed with UI alert.

### 7. Cross-platform (public use)
- **Risk:** `ss` is Linux-only. Public users may run macOS or Windows.
- **Mitigation:** Platform detection at scanner init.
  - Linux: `ss -tp | grep :11434`
  - macOS: `lsof -i :11434 -sTCP:ESTABLISHED`
  - Windows: `netstat -ano | findstr :11434` (detect only, no auto-patch)
  - Full patcher support: Linux (systemd + .env) + macOS (.env + config). Windows: detection + manual snippet.

---

## Data Model

```sql
CREATE TABLE consumers (
  id                  INTEGER PRIMARY KEY,
  name                TEXT NOT NULL,
  type                TEXT NOT NULL,          -- systemd|env_file|transient|managed_job|unknown
  platform            TEXT NOT NULL,          -- linux|macos|windows
  source_label        TEXT NOT NULL,          -- auto-generated _source value for queue

  status              TEXT NOT NULL DEFAULT 'discovered',
                                              -- discovered|included|ignored|
                                              --   pending_restart|patched|error

  -- Streaming safety
  streaming_confirmed INTEGER DEFAULT 0,      -- stream=True found in source
  streaming_suspect   INTEGER DEFAULT 0,      -- can't inspect (binary/3rd-party)

  -- Deadlock prevention
  is_managed_job      INTEGER DEFAULT 0,      -- matches recurring_jobs — hard block, no override

  -- Patch tracking
  patch_type          TEXT,                   -- systemd|env_file|config_yaml|config_toml|manual_snippet
  restart_policy      TEXT DEFAULT 'deferred',-- immediate|deferred
  patch_applied       INTEGER DEFAULT 0,
  patch_path          TEXT,                   -- file modified (backup at patch_path + .ollama-queue.bak)
  patch_snippet       TEXT,                   -- for manual_snippet type

  -- Health validation
  health_status       TEXT DEFAULT 'unknown', -- unknown|verifying|confirmed|partial|failed
  health_checked_at   INTEGER,

  -- Scan history
  request_count       INTEGER DEFAULT 0,
  last_seen           INTEGER,
  last_live_seen      INTEGER,
  detected_at         INTEGER NOT NULL,
  onboarded_at        INTEGER
);
```

---

## API Contracts

### `POST /api/consumers/scan`
Runs all 4 scanner phases. Upserts consumers table. Returns list of discovered consumers.

### `POST /api/consumers/{id}/include`
```json
{
  "restart_policy": "immediate|deferred",
  "force_streaming_override": false,
  "system_confirm": false
}
```

**Guard rails (backend-enforced, in order):**

| Condition | Response | Override |
|-----------|----------|----------|
| `is_managed_job=True` | 409 "Managed queue job — would deadlock" | None — hard block |
| `platform=windows` | 422 "Auto-patch unsupported on Windows. Use snippet." | None |
| `streaming_confirmed=True` AND `force_streaming_override=False` | 422 "Streaming detected. Proxy forces stream=False." | `force_streaming_override=True` |
| System path AND `system_confirm=False` | 422 "System path requires explicit confirm." | `system_confirm=True` |

**On success:**
- Patcher writes file (backs up to `.ollama-queue.bak` first)
- `restart_policy=deferred`: status → `pending_restart`
- `restart_policy=immediate`: restart service, status → `patched`, spawn background health validator

### `POST /api/consumers/{id}/ignore`
Sets `status=ignored`. No file changes.

### `POST /api/consumers/{id}/revert`
Restores `.ollama-queue.bak`. Restarts service if it was restarted during include. Status → `discovered`.

### `GET /api/consumers/{id}/health`
```json
{
  "old_port_clear": true,
  "new_port_active": false,
  "request_seen": false,
  "status": "partial"
}
```
Timeout: 60s → auto-mark `failed`.

---

## Patcher Logic

```
Before any write:
  cp <path> <path>.ollama-queue.bak

Systemd unit:
  inject under [Service]:
    Environment="OLLAMA_HOST=localhost:7683"
  systemctl --user daemon-reload
  if restart_policy=immediate:
    systemctl --user restart <service>

.env file:
  if OLLAMA_HOST exists: replace value
  else: append OLLAMA_HOST=localhost:7683

config.yaml:
  parse with ruamel.yaml (preserves formatting)
  update ollama.host or base_url key

.toml:
  parse with tomlkit (preserves formatting)
  update equivalent key

Revert:
  cp <path>.ollama-queue.bak <path>
  rm <path>.ollama-queue.bak
  daemon-reload + restart if service was restarted
```

---

## SPA: Consumers Tab

**Placement:** 6th tab alongside Jobs / Schedule / DLQ / Eval / Settings

**First-run wizard:**
- Triggers on first scan result (not buried in settings)
- Banner: "X services detected calling Ollama directly. Review below."

**Table columns:**
`Name | Type | ⚠ Streaming? | Requests | Last Seen | Status | Actions`

**Per-row states:**

| Status | Actions | Badges |
|--------|---------|--------|
| `discovered` | [Include ▾] [Ignore] | — |
| `streaming_confirmed` | Include disabled until confirm checkbox ticked | ⚠ Streaming confirmed |
| `streaming_suspect` | Include shows confirm modal | ⚠ Streaming suspected |
| `is_managed_job` | Include disabled, no override | 🔒 Queue job |
| system-level path | Include shows system-confirm modal | 🛡 System path |
| `pending_restart` | [Restart now] [Revert] | — |
| `patched` | [Revert] | ⏳ Verifying / ✓ Confirmed / ⚠ Partial / ✗ Failed |
| `ignored` | [Re-evaluate] | — |

**Manual snippet (Windows / unknown type):**
Inline code block with copy button:
```
export OLLAMA_HOST=localhost:7683
```
Status: "Manual — unverifiable" (no health check possible)

**Scan Now button:** top right, `useActionFeedback` hook (idle → loading → success/error → idle)

---

## Implementation Order

1. DB migration — `consumers` table
2. `scanner.py` — all 4 phases + platform detection
3. `patcher.py` — all patch types + revert
4. API endpoints — with all guard rails
5. Background health validator
6. SPA Consumers tab — table + wizard + per-row actions
7. Tests

---

## Reference

- Inspiration: [ollama_proxy_server](https://github.com/ParisNeo/ollama_proxy_server) (setup wizard, client activity table)
- Inspiration: [Olla](https://github.com/thushan/olla) (post-routing health validation pattern)
- Lesson #1733: deadlock — queue job calling back through proxy
- Existing proxy guard: `api.py:370-466` `_proxy_ollama_request()`
- Existing pattern: `useActionFeedback` hook, `src/hooks/useActionFeedback.js`
