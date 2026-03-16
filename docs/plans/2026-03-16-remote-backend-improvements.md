# Remote Backend Improvements — 2026-03-16

## Summary

Six bug fixes and one new feature across the multi-backend layer:
`GET /api/backends` schema gaps, VRAM threshold mismatch, weight validation,
env-var backend 404/409 distinction, and a heartbeat push endpoint for remote
ollama-queue instances.

---

## Problems Solved

### 1. `GET /api/backends` missing `weight` and `checked_at`

`BackendCard` in the Backends tab displayed routing weight and a `ShFrozen`
freshness indicator, but both fields were absent from the API response:

- `weight` was stored in the DB but never serialized
- `last_checked` (now `checked_at`) was undefined — the DB has no such column

**Fix:** `get_backends()` now derives both:
- `weight` — from DB row, falls back to `OLLAMA_BACKEND_WEIGHTS` env var, then 1.0
- `checked_at` — derived from the health-cache entry's monotonic timestamp via
  `time.time() - (time.monotonic() - cached_ts)`, giving a wall-time unix seconds
  value without any new DB column

Frontend `BackendCard` updated to use `checked_at` (renamed from `last_checked`).

### 2. VRAM threshold mismatch in `BackendsPanel.jsx`

`BackendsPanel.jsx` used `>80%` error, `>60%` warning.
`HostCard.jsx` and the design spec (CLAUDE.md) use `>90%` error, `>80%` warning.

**Fix:** `BackendsPanel.jsx` thresholds corrected to match spec.

### 3. `BackendCard` weight validation accepted out-of-range values

Client validated `w < 0` — allowing 0, 0.05, and values > 10.
Server enforces `0.1 <= weight <= 10.0` and returns 400 on violation.

**Fix:** Client validation updated to `w < 0.1 || w > 10.0` with message
"Weight must be 0.1 – 10.0".

### 4. `PUT /api/backends/{url}/weight` returned 404 for env-var backends

Env-var backends exist in `BACKENDS` (from `OLLAMA_BACKENDS`) but have no DB
row until a write operation creates one. The `inference-mode` endpoint had
auto-registration logic; `weight` did not.

**Fix:** Auto-register guard added, matching the `inference-mode` pattern.

### 5. `DELETE /api/backends/{url}` returned misleading 404 for env-var backends

When a user tried to remove an env-var backend via the API, the endpoint
returned 404 ("not found") — technically the DB row doesn't exist, but the
backend clearly does exist and is routing traffic. The user had no way to know
why the delete failed.

**Fix:** Returns 409 Conflict with an actionable message:
> "backend {url} is configured via OLLAMA_BACKENDS and cannot be removed via
> the API — update the env var and restart the service"

### 6. Duplicated env-var check logic

The `any(b.rstrip("/") == url.rstrip("/") for b in _router.BACKENDS)` check
appeared inline in 3 endpoints. Any normalization change (e.g. case-insensitive
hostname comparison) would require updating all three.

**Fix:** Extracted two module-level helpers in `api/backends.py`:
- `_is_env_backend(url)` — checks BACKENDS with trailing-slash normalization
- `_auto_register_if_env_backend(url, db, reason)` — inserts DB row if absent

---

## New Feature: Remote Backend Heartbeat Push

### Motivation

In a multi-backend setup, the primary ollama-queue polls each remote backend
on every routing decision — health, VRAM, GPU name, loaded models, available
models. With a remote Docker container on a Tailscale node, this is 5 outbound
HTTP calls per request to the remote host.

The heartbeat endpoint inverts this: the remote pushes its own state
periodically, and the primary reads from in-process caches. No outbound polling
on the hot path.

### API

```
PUT /api/backends/{url}/heartbeat
Content-Type: application/json

{
  "healthy": true,
  "gpu_name": "RTX 2070 Max-Q",    // optional
  "vram_pct": 45.2,                // optional
  "vram_total_gb": 8.0,            // optional
  "loaded_models": ["qwen2.5:7b"], // optional
  "available_models": ["qwen2.5:7b", "llama3:8b"]  // optional
}
```

Response:
```json
{"url": "http://100.87.66.25:11434", "ok": true}
```

### Behavior

1. **SSRF protection** — same `_is_safe_backend_url` check as `POST /api/backends`
2. **Auto-registers** — if the URL isn't in BACKENDS or the DB, it's added. The
   act of pushing proves reachability (no separate connectivity test needed).
3. **Partial pushes** — uses `exclude_unset=True` on the Pydantic model so
   only explicitly-provided fields update their respective caches. A minimal
   push with only `{"healthy": true}` updates just `_health_cache` and leaves
   VRAM/GPU/model caches untouched.
4. **Cache writes** — `backend_router.receive_heartbeat()` writes directly to:
   - `_health_cache` (health + TTL)
   - `_hw_cache` (VRAM pressure)
   - `_vram_total_cache` (total VRAM, used by gpu_only filter)
   - `_gpu_name_cache` (GPU label)
   - `_loaded_cache` (models in VRAM — used by warm-model routing tier)
   - `_models_cache` (all available models — used by model-availability tier)

### Remote setup (Razer / Docker container)

```bash
# Add to a cron every 30s on the remote host:
curl -s -X PUT http://<primary-tailscale-ip>:7683/api/backends/http://100.87.66.25:11434/heartbeat \
  -H "Content-Type: application/json" \
  -d '{
    "healthy": true,
    "gpu_name": "RTX 2070 Max-Q",
    "vram_pct": 45.2,
    "vram_total_gb": 8.0,
    "loaded_models": ["qwen2.5:7b"]
  }'
```

The remote can also query its own `/api/health` endpoint to populate these
fields dynamically:

```bash
STATE=$(curl -s http://localhost:7683/api/health | jq '{
  healthy: true,
  gpu_name: .gpu_name,
  vram_pct: .log[0].vram_pct,
  vram_total_gb: .vram_total_gb,
  loaded_models: .loaded_models
}')
curl -s -X PUT http://<primary>:7683/api/backends/http://100.87.66.25:11434/heartbeat \
  -H "Content-Type: application/json" -d "$STATE"
```

### Schema change

`backends` table: `last_heartbeat_at REAL` column added via `_add_column_if_missing`
migration in `db/schema.py`. Not currently written by the heartbeat endpoint
(data is in-process only) — reserved for future persistence of last-seen time.

`inference_mode TEXT NOT NULL DEFAULT 'cpu_shared'` is now in the `CREATE TABLE`
definition directly (was migration-only). Functionally identical; the migration
still runs for upgrades.

---

## Files Changed

| File | Change |
|------|--------|
| `ollama_queue/api/backends.py` | `_is_env_backend`, `_auto_register_if_env_backend` helpers; `weight`/`checked_at` in GET response; DELETE 409; heartbeat endpoint |
| `ollama_queue/api/backend_router.py` | `receive_heartbeat()` function |
| `ollama_queue/db/schema.py` | `inference_mode` in CREATE TABLE; `last_heartbeat_at` migration |
| `ollama_queue/dashboard/spa/src/components/BackendsPanel.jsx` | VRAM thresholds |
| `ollama_queue/dashboard/spa/src/pages/BackendsTab.jsx` | `checked_at`, weight validation, dead prop removal |
| `tests/test_backends_api.py` | 10 new tests (33 total) |

## Tests

1,951 total (up from 1,943). All pass. New tests cover:
- `GET /api/backends` includes `weight` from DB and defaults to 1.0
- `GET /api/backends` includes `checked_at` after health check
- `DELETE /api/backends/{url}` returns 409 for env-var backend
- `PUT /api/backends/{url}/weight` auto-registers env-var backend
- Heartbeat: updates all caches, auto-registers unknown backend, partial payload,
  invalid URL, SSRF URL rejection
