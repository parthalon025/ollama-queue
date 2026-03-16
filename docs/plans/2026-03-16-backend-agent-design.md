# Backend Agent Design

**Date:** 2026-03-16
**Status:** Approved
**Replaces:** `scripts/docker-mgmt-sidecar.py` + `scripts/backend-onboard.sh`

## Summary

Evolve the Docker management sidecar into a full **backend agent** — a proper Docker image
(GHCR) that runs alongside Ollama on each remote host. The agent self-heals (reconciles models
on a schedule), accepts commands from the queue (sync, update, restart), and pushes rich
heartbeats including CPU/RAM/disk metrics. The queue becomes the single source of truth for
which models each backend should have, assigned per-host based on hardware capabilities.

## Decisions

| Question | Answer | Rationale |
|----------|--------|-----------|
| Communication model | Bidirectional | Queue pushes commands; agent self-heals on schedule |
| Fleet size | Small (3 nodes) | Local + Razer (8GB) + new host (RTX 5080, 16GB) |
| Model source of truth | Queue server API | Single control plane, agents just ask "what should I have?" |
| Auth | Tailscale-only | All traffic on private mesh, no tokens needed |

## Architecture

```
┌──────────────────────────────┐
│   Queue Server (primary)      │  justin-linux, port 7683
│                               │
│   Existing:                   │
│   PUT /api/backends/{url}     │
│       /heartbeat              │  agent pushes health every 30s
│   GET /api/backends           │  dashboard reads fleet status
│                               │
│   New:                        │
│   GET  /api/required-models   │  hardware-filtered model list
│        ?backend_url=...       │
│   POST /api/backends/{url}    │
│        /command               │  dispatch command to agent
│                               │
│   CLI: ollama-queue backend   │
│        sync-models [url]      │
│        update-ollama [url]    │
│        status [url]           │
└───────────────┬───────────────┘
                │ Tailscale mesh (no auth)
    ┌───────────┼───────────────┐
    ▼           ▼               ▼
┌────────┐  ┌────────┐  ┌────────┐
│ Local  │  │ Razer  │  │ 5080   │
│ Agent  │  │ Agent  │  │ Agent  │
│ :11435 │  │ :11435 │  │ :11435 │
└────────┘  └────────┘  └────────┘
    │           │               │
    ▼           ▼               ▼
┌────────┐  ┌────────┐  ┌────────┐
│ Ollama │  │ Ollama │  │ Ollama │
│ :11434 │  │ :11434 │  │ :11434 │
└────────┘  └────────┘  └────────┘
```

Communication patterns:
1. **Agent → Queue (periodic):** Heartbeat every 30s, model list fetch during reconciliation
2. **Queue → Agent (on-demand):** Direct HTTP to agent endpoints, triggered from dashboard/CLI
3. **Agent → local Ollama:** localhost:11434 for model pulls, tags, health

## Backend Agent

### Identity

- **Image:** `ghcr.io/parthalon025/ollama-backend-agent:latest`
- **Port:** 11435
- **Single file:** `backend_agent.py` (FastAPI + reconciliation loop)
- **No external state:** cached `required-models.json` in `/data` volume for offline resilience

### Env Vars

| Var | Purpose | Example |
|-----|---------|---------|
| `QUEUE_URL` | Primary queue server | `http://100.68.34.41:7683` |
| `OLLAMA_URL` | Local Ollama API | `http://host.docker.internal:11434` |
| `BACKEND_URL` | This host's Tailscale-routable Ollama URL | `http://100.91.20.72:11434` |

### Container Deployment

```bash
docker run -d --name ollama-agent \
  --restart always \
  -p 11435:11435 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v ollama:/ollama:ro \
  -v ollama-agent-data:/data \
  -e QUEUE_URL=http://100.68.34.41:7683 \
  -e OLLAMA_URL=http://host.docker.internal:11434 \
  -e BACKEND_URL=http://<tailscale-ip>:11434 \
  ghcr.io/parthalon025/ollama-backend-agent:latest
```

Volumes:
- `/var/run/docker.sock` — manage Ollama container (update, restart)
- `ollama:/ollama:ro` — read-only access to model data (disk usage, integrity)
- `ollama-agent-data:/data` — cached model list for offline resilience

### Reconciliation Loop

Every 5 minutes:

1. `GET {QUEUE_URL}/api/required-models?backend_url={BACKEND_URL}` → filtered model list
2. Cache response to `/data/required-models.json` (fallback if queue unreachable)
3. `GET {OLLAMA_URL}/api/tags` → currently installed models
4. Diff → missing models
5. For each missing: `POST {OLLAMA_URL}/api/pull {"model": "<name>"}`
6. `PUT {QUEUE_URL}/api/backends/{BACKEND_URL}/heartbeat` → push health + model inventory

If queue is unreachable in step 1, use last cached response. Heartbeat failure in step 6
is logged but non-fatal — the loop continues.

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Liveness + Ollama status + agent version + last reconcile timestamp |
| `/version` | GET | Agent image version (from build label) |
| `/status` | GET | Full report: installed/missing models, last pull results, container state |
| `/sync-models` | POST | Trigger immediate reconciliation |
| `/update-ollama` | POST | Pull latest Ollama image, recreate container |
| `/restart-ollama` | POST | Restart the Ollama container |

### Heartbeat Payload

```json
{
  "healthy": true,
  "gpu_name": "NVIDIA GeForce RTX 5080",
  "vram_pct": 22.1,
  "vram_total_gb": 16.0,
  "cpu_pct": 12.0,
  "ram_pct": 45.0,
  "ram_total_gb": 64.0,
  "disk_total_gb": 500.0,
  "disk_used_gb": 82.3,
  "disk_pct": 16.5,
  "ollama_storage_gb": 47.2,
  "loaded_models": ["qwen2.5-coder:14b"],
  "available_models": ["qwen2.5-coder:14b", "qwen3:14b", "qwen3.5:9b"],
  "agent_version": "0.1.0",
  "ollama_version": "0.5.13",
  "last_reconcile": 1742140800.0
}
```

New fields vs current heartbeat: `cpu_pct`, `ram_pct`, `ram_total_gb`, `disk_total_gb`,
`disk_used_gb`, `disk_pct`, `ollama_storage_gb`, `agent_version`, `ollama_version`,
`last_reconcile`.

System metrics collected via `/proc` reads (no psutil dependency). Ollama storage via
`shutil.disk_usage('/ollama')` on the mounted volume.

## Queue-Side Changes

### `GET /api/required-models`

Returns the canonical model list, filtered per-backend based on hardware.

**Settings storage:** `required_models` key in the existing settings table. Editable via
`PUT /api/settings/required_models`.

**Model schema:**

```json
{
  "models": [
    {"name": "qwen3.5:9b",        "vram_mb": 6200,  "tier": "core"},
    {"name": "qwen2.5-coder:14b", "vram_mb": 9500,  "tier": "core"},
    {"name": "qwen3:14b",         "vram_mb": 9500,  "tier": "standard"},
    {"name": "nomic-embed-text",   "vram_mb": 300,   "tier": "core"},
    {"name": "qwen3.5:4b",        "vram_mb": 2800,  "tier": "core"},
    {"name": "qwen3.5:2b",        "vram_mb": 1800,  "tier": "core"},
    {"name": "qwen2.5:7b",        "vram_mb": 4800,  "tier": "standard"},
    {"name": "deepseek-r1:8b",    "vram_mb": 5200,  "tier": "standard"},
    {"name": "deepseek-r1:8b-0528-qwen3-q4_K_M", "vram_mb": 5200, "tier": "standard"},
    {"name": "gemma3:12b",        "vram_mb": 8200,  "tier": "standard"},
    {"name": "functiongemma:latest","vram_mb": 2100, "tier": "standard"},
    {"name": "fixt/home-3b-v3",   "vram_mb": 2100,  "tier": "optional"},
    {"name": "qwen3-vl:4b",       "vram_mb": 3200,  "tier": "standard"}
  ]
}
```

**Tiers:**
- **`core`** — every backend gets these (essential for basic function, small enough to fit anywhere)
- **`standard`** — backends get these if the model fits in VRAM
- **`optional`** — at least one backend in the fleet has it, assigned to the best-fit host

**Filtering logic** (when `?backend_url=` is provided):

1. Look up backend's `vram_total_gb` from last heartbeat
2. Include all `core` models
3. Include `standard` models where `vram_mb ≤ vram_total_gb * 1024 * 0.95` (5% headroom)
4. Include `optional` models assigned to this backend (fleet coverage: ensure at least one
   healthy backend has each optional model; assign to host with most available VRAM)
5. Return filtered list

**Fleet assignment example:**

```
RTX 5080 (16GB):     ALL models — core + standard + optional
                     Primary backend for 14B models

RTX 2070 (8GB):      core + standard ≤ 8GB
                     Handles 9b and smaller

Local:               core + whatever fits
                     Fallback / low-latency for local tasks
```

### `POST /api/backends/{url}/command`

Dispatches a command to a remote agent. Thin proxy:

1. Derive agent URL from backend URL (same host, port 11435)
2. Forward to agent endpoint: `POST http://<host>:11435/<action>`
3. Return agent's response

Supported actions: `sync-models`, `update-ollama`, `restart-ollama`, `status`

### CLI: `ollama-queue backend`

```bash
ollama-queue backend sync-models              # all backends
ollama-queue backend sync-models http://...   # specific backend
ollama-queue backend update-ollama http://...
ollama-queue backend status                   # all agent statuses
ollama-queue backend status http://...        # specific agent
```

### Routing Enhancement

Extend `_route_by_model()` tier 4 to consider CPU/RAM pressure for `cpu_shared` backends.
Skip backends where `cpu_pct > 90` or `ram_pct > 90`. New cache entries for CPU/RAM in
`backend_router.py`, populated by `receive_heartbeat()`.

## Docker Image + CI

### Dockerfile (`sidecar/Dockerfile`)

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && rm -rf /var/lib/apt/lists/*

COPY backend_agent.py /app/
WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn httpx docker

VOLUME ["/data"]
EXPOSE 11435

LABEL org.opencontainers.image.source=https://github.com/parthalon025/ollama-queue

CMD ["python3", "backend_agent.py"]
```

Dependencies: fastapi, uvicorn, httpx, docker. System metrics via `/proc` reads (no psutil).

### GHCR Publishing

Extend `.github/workflows/release.yml` to build + push on tag:

```yaml
- name: Build and push backend-agent
  uses: docker/build-push-action@v5
  with:
    context: sidecar/
    push: true
    tags: |
      ghcr.io/parthalon025/ollama-backend-agent:latest
      ghcr.io/parthalon025/ollama-backend-agent:${{ github.ref_name }}
```

## Bootstrap

### `scripts/bootstrap-backend.sh`

One-command setup for a new host:

```bash
curl -sL https://raw.githubusercontent.com/parthalon025/ollama-queue/main/scripts/bootstrap-backend.sh | \
  bash -s -- --queue http://100.68.34.41:7683 --backend-url http://<tailscale-ip>:11434
```

Steps:
1. Check Docker installed (exit with instructions if not)
2. Detect NVIDIA GPU → set `--gpus all` flag
3. Start Ollama container if not running
4. Pull + start agent container from GHCR
5. Wait for agent `/health` to return OK
6. Agent's reconciliation loop auto-starts — pulls models, sends first heartbeat
7. Print summary

### Migration from Old Sidecar

```bash
docker stop docker-mgmt 2>/dev/null; docker rm docker-mgmt 2>/dev/null
# Then run bootstrap-backend.sh
```

## Retirement

After agent is deployed to all hosts:
- `scripts/docker-mgmt-sidecar.py` — archived
- `scripts/backend-onboard.sh` — archived (replaced by `bootstrap-backend.sh` + agent reconciliation)
- `MGMT_TOKEN` env var — removed from all hosts

## File Inventory

### New Files
- `sidecar/Dockerfile` — agent Docker image
- `sidecar/backend_agent.py` — agent service (FastAPI + reconciliation loop)
- `scripts/bootstrap-backend.sh` — one-command new host setup
- `ollama_queue/api/required_models.py` — `GET /api/required-models` endpoint
- `ollama_queue/cli_backend.py` — `ollama-queue backend` CLI subcommand

### Modified Files
- `ollama_queue/api/__init__.py` — register required_models router
- `ollama_queue/api/backends.py` — add `POST /command` endpoint
- `ollama_queue/api/backend_router.py` — extend heartbeat + routing for CPU/RAM
- `ollama_queue/db/schema.py` — seed `required_models` setting
- `ollama_queue/cli.py` — register backend subcommand
- `.github/workflows/release.yml` — add GHCR build step

### Archived
- `scripts/docker-mgmt-sidecar.py`
- `scripts/backend-onboard.sh`
