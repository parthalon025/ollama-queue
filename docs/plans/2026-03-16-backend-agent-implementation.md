# Backend Agent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace docker-mgmt-sidecar with a proper backend agent — Docker image on GHCR, reconciliation loop, hardware-aware model assignment, expanded heartbeat, queue-side command dispatch, bootstrap script, and CLI subcommand.

**Architecture:** Backend agent runs alongside Ollama on each remote host. Queue is the single control plane — agents pull their model list from the queue, push rich heartbeats, and accept commands. All traffic over Tailscale (no auth tokens).

**Tech Stack:** Python 3.12, FastAPI, httpx, Docker SDK, Click CLI. Tests: pytest + FastAPI TestClient.

**Design:** `docs/plans/2026-03-16-backend-agent-design.md`

---

## Batch 1: Queue-Side Foundations (parallelizable)

### Task 1: Seed required_models Setting

**Files:**

- Modify: `ollama_queue/db/schema.py` (DEFAULTS dict, ~line 13)
- Test: `tests/test_required_models_setting.py`

**Step 1: Write the failing test**

```python
# tests/test_required_models_setting.py
"""Tests for required_models setting seeding."""

from ollama_queue.db import Database


def test_required_models_seeded(tmp_path):
    """required_models setting is seeded with model list on DB init."""
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    settings = db.get_all_settings()
    assert "required_models" in settings
    models = settings["required_models"]
    assert isinstance(models, list)
    assert len(models) > 0
    # Each entry has name, vram_mb, tier
    first = models[0]
    assert "name" in first
    assert "vram_mb" in first
    assert "tier" in first
    assert first["tier"] in ("core", "standard", "optional")


def test_required_models_contains_core_models(tmp_path):
    """required_models includes known core models."""
    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    settings = db.get_all_settings()
    names = [m["name"] for m in settings["required_models"]]
    assert "nomic-embed-text" in names  # embedding model is core
    assert "qwen3.5:2b" in names  # small model is core
```

**Step 2: Run test to verify it fails**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_required_models_setting.py -v`
Expected: FAIL — `required_models` not in settings

**Step 3: Add required_models to DEFAULTS**

In `ollama_queue/db/schema.py`, add to the `DEFAULTS` dict (after the existing entries, around line 30):

```python
    "required_models": [
        {"name": "qwen3.5:9b", "vram_mb": 6200, "tier": "core"},
        {"name": "qwen2.5-coder:14b", "vram_mb": 9500, "tier": "core"},
        {"name": "qwen3:14b", "vram_mb": 9500, "tier": "standard"},
        {"name": "nomic-embed-text", "vram_mb": 300, "tier": "core"},
        {"name": "qwen3.5:4b", "vram_mb": 2800, "tier": "core"},
        {"name": "qwen3.5:2b", "vram_mb": 1800, "tier": "core"},
        {"name": "qwen2.5:7b", "vram_mb": 4800, "tier": "standard"},
        {"name": "deepseek-r1:8b", "vram_mb": 5200, "tier": "standard"},
        {"name": "deepseek-r1:8b-0528-qwen3-q4_K_M", "vram_mb": 5200, "tier": "standard"},
        {"name": "gemma3:12b", "vram_mb": 8200, "tier": "standard"},
        {"name": "functiongemma:latest", "vram_mb": 2100, "tier": "standard"},
        {"name": "fixt/home-3b-v3", "vram_mb": 2100, "tier": "optional"},
        {"name": "qwen3-vl:4b", "vram_mb": 3200, "tier": "standard"},
    ],
```

**Step 4: Run test to verify it passes**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_required_models_setting.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/db/schema.py tests/test_required_models_setting.py
git commit -m "feat: seed required_models setting with hardware-tiered model list"
```

---

### Task 2: GET /api/required-models Endpoint

**Files:**

- Create: `ollama_queue/api/required_models.py`
- Modify: `ollama_queue/api/__init__.py` (register router, ~line 33 and ~line 62)
- Test: `tests/test_required_models_api.py`

**Step 1: Write the failing tests**

```python
# tests/test_required_models_api.py
"""Tests for GET /api/required-models endpoint."""

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import Database
import ollama_queue.api.backend_router as _router


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


@pytest.fixture
def client(db):
    return TestClient(create_app(db))


def test_required_models_unfiltered(client):
    """GET /api/required-models without backend_url returns all models."""
    resp = client.get("/api/required-models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) > 0
    # All entries have required fields
    for m in data["models"]:
        assert "name" in m
        assert "vram_mb" in m
        assert "tier" in m


def test_required_models_filtered_by_vram(client, db):
    """GET /api/required-models?backend_url= filters out models that don't fit."""
    backend_url = "http://testhost:11434"
    db.add_backend(backend_url)

    # Simulate heartbeat: 8GB VRAM backend
    now = time.monotonic()
    _router._vram_total_cache[backend_url] = (now, 8.0)

    resp = client.get(f"/api/required-models?backend_url={backend_url}")
    assert resp.status_code == 200
    data = resp.json()

    names = [m["name"] for m in data["models"]]
    # 8GB = 8192MB * 0.95 = 7782MB threshold
    # nomic-embed-text (300MB) should be included
    assert "nomic-embed-text" in names
    # qwen2.5-coder:14b (9500MB) should be excluded
    assert "qwen2.5-coder:14b" not in names


def test_required_models_no_vram_data_returns_core_only(client, db):
    """Backend with no VRAM data gets only core models."""
    backend_url = "http://newhost:11434"
    db.add_backend(backend_url)
    # No heartbeat data — no vram_total_cache entry

    resp = client.get(f"/api/required-models?backend_url={backend_url}")
    assert resp.status_code == 200
    data = resp.json()

    # Should only return core tier (safe default when VRAM unknown)
    for m in data["models"]:
        assert m["tier"] == "core"
```

**Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_required_models_api.py -v`
Expected: FAIL — 404 (endpoint doesn't exist)

**Step 3: Create the endpoint**

```python
# ollama_queue/api/required_models.py
"""Required models endpoint — returns hardware-filtered model list for backend agents.

Plain English: Backend agents call this to find out which models they should have
installed. The queue filters the canonical model list based on each backend's VRAM
capacity (from heartbeat data), so small-GPU hosts don't waste time pulling models
they can't run efficiently.

Decision it drives: Which models each backend agent pulls during reconciliation.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Query

import ollama_queue.api as _api
import ollama_queue.api.backend_router as _router

_log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/required-models")
def get_required_models(backend_url: str | None = Query(None)):
    """Return the canonical model list, optionally filtered for a specific backend.

    Without backend_url: returns all models (for dashboard/CLI display).
    With backend_url: filters by the backend's VRAM capacity from last heartbeat.

    Filtering logic:
      - core: always included
      - standard: included if vram_mb <= backend's VRAM * 0.95
      - optional: included if assigned to this backend (best-fit in fleet)
      - If no VRAM data exists for the backend, return core only (safe default)
    """
    db = _api.db
    settings = db.get_all_settings() if db else {}
    all_models = settings.get("required_models", [])

    if not backend_url:
        return {"models": all_models}

    url = backend_url.rstrip("/")

    # Look up VRAM from heartbeat cache
    cached = _router._vram_total_cache.get(url)
    if not cached:
        # Try with trailing slash variations
        for key in list(_router._vram_total_cache.keys()):
            if key.rstrip("/") == url:
                cached = _router._vram_total_cache[key]
                break

    if not cached or (time.monotonic() - cached[0]) > 3600:
        # No VRAM data or stale (>1hr) — return core only
        _log.info("required-models: no VRAM data for %s, returning core only", url)
        return {"models": [m for m in all_models if m.get("tier") == "core"]}

    vram_total_gb = cached[1]
    vram_threshold_mb = vram_total_gb * 1024 * 0.95  # 5% headroom

    filtered = []
    for m in all_models:
        tier = m.get("tier", "standard")
        vram_mb = m.get("vram_mb", 0)

        if tier == "core":
            filtered.append(m)
        elif tier == "standard" and vram_mb <= vram_threshold_mb:
            filtered.append(m)
        elif tier == "optional":
            # Optional: assign to backend with most VRAM (simple heuristic)
            # Find the backend with the highest VRAM in the fleet
            best_url = _find_best_backend_for_optional(vram_mb)
            if best_url and best_url.rstrip("/") == url:
                filtered.append(m)

    return {"models": filtered}


def _find_best_backend_for_optional(vram_mb: int) -> str | None:
    """Find the backend with the most VRAM that can fit this model."""
    best_url = None
    best_vram = 0.0
    now = time.monotonic()

    for url, (ts, vram_gb) in _router._vram_total_cache.items():
        if (now - ts) > 3600:
            continue  # stale
        if vram_gb * 1024 * 0.95 >= vram_mb and vram_gb > best_vram:
            best_vram = vram_gb
            best_url = url

    return best_url
```

**Step 4: Register the router**

In `ollama_queue/api/__init__.py`, add to the import block (~line 33):

```python
        required_models,
```

And add to the router registration (~line 62):

```python
    app.include_router(required_models.router)
```

**Step 5: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_required_models_api.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add ollama_queue/api/required_models.py ollama_queue/api/__init__.py tests/test_required_models_api.py
git commit -m "feat: add GET /api/required-models with hardware-aware filtering"
```

---

### Task 3: Expand Heartbeat for CPU/RAM/Disk

**Files:**

- Modify: `ollama_queue/api/backends.py` (HeartbeatRequest model, ~line 313)
- Modify: `ollama_queue/api/backend_router.py` (new caches + receive_heartbeat, ~line 88 and ~line 481)
- Test: `tests/test_heartbeat_expanded.py`

**Step 1: Write the failing test**

```python
# tests/test_heartbeat_expanded.py
"""Tests for expanded heartbeat fields (CPU/RAM/disk)."""

import time

import ollama_queue.api.backend_router as _router


def test_receive_heartbeat_cpu_ram(self_cleanup=True):
    """receive_heartbeat stores cpu_pct and ram_pct in caches."""
    url = "http://testhost:11434"
    now = time.monotonic()

    _router.receive_heartbeat(url, {
        "healthy": True,
        "cpu_pct": 45.2,
        "ram_pct": 61.0,
        "ram_total_gb": 32.0,
        "disk_pct": 16.5,
        "disk_total_gb": 500.0,
        "disk_used_gb": 82.3,
        "ollama_storage_gb": 47.2,
        "agent_version": "0.1.0",
        "ollama_version": "0.5.13",
    }, now)

    # CPU cache
    assert url in _router._cpu_cache
    assert _router._cpu_cache[url][1] == 45.2

    # RAM cache
    assert url in _router._ram_cache
    assert _router._ram_cache[url][1] == 61.0

    # Cleanup
    _router._cpu_cache.pop(url, None)
    _router._ram_cache.pop(url, None)
    _router._health_cache.pop(url, None)
    _router._hw_cache.pop(url, None)


def test_receive_heartbeat_partial_no_cpu():
    """receive_heartbeat without cpu_pct does not create cpu cache entry."""
    url = "http://testhost2:11434"
    now = time.monotonic()

    _router.receive_heartbeat(url, {"healthy": True}, now)

    assert url not in _router._cpu_cache
    assert url not in _router._ram_cache

    # Cleanup
    _router._health_cache.pop(url, None)
```

**Step 2: Run test to verify it fails**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_heartbeat_expanded.py -v`
Expected: FAIL — `_cpu_cache` attribute not found

**Step 3: Add new caches to backend_router.py**

In `ollama_queue/api/backend_router.py`, add after the existing caches (~line 92):

```python
_cpu_cache: dict[str, tuple[float, float]] = {}
_ram_cache: dict[str, tuple[float, float]] = {}
```

Update `receive_heartbeat()` (~line 519, after the available_models block) to add:

```python
    # CPU pressure cache (used by routing to skip overloaded hosts)
    if "cpu_pct" in data:
        _cpu_cache[url] = (now, float(data["cpu_pct"]))

    # RAM pressure cache (used by routing to skip memory-pressured hosts)
    if "ram_pct" in data:
        _ram_cache[url] = (now, float(data["ram_pct"]))
```

Update `invalidate_backend_caches()` (~line 458) to include the new caches:

```python
    for cache in (_health_cache, _models_cache, _loaded_cache, _hw_cache, _gpu_name_cache, _vram_total_cache, _cpu_cache, _ram_cache):
```

**Step 4: Expand HeartbeatRequest in backends.py**

In `ollama_queue/api/backends.py`, add new fields to `HeartbeatRequest` (~line 325):

```python
    cpu_pct: float = 0.0
    ram_pct: float = 0.0
    ram_total_gb: float = 0.0
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_pct: float = 0.0
    ollama_storage_gb: float = 0.0
    agent_version: str | None = None
    ollama_version: str | None = None
    last_reconcile: float | None = None
```

**Step 5: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_heartbeat_expanded.py -v`
Expected: PASS

**Step 6: Run existing heartbeat tests to verify no regression**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_backends_api.py -v -k heartbeat`
Expected: PASS (existing tests still work)

**Step 7: Commit**

```bash
git add ollama_queue/api/backends.py ollama_queue/api/backend_router.py tests/test_heartbeat_expanded.py
git commit -m "feat: expand heartbeat with CPU/RAM/disk metrics and new caches"
```

---

## Batch 2: Queue-Side Commands

### Task 4: POST /api/backends/{url}/command Endpoint

**Files:**

- Modify: `ollama_queue/api/backends.py` (add command endpoint after heartbeat, ~line 373)
- Test: `tests/test_backend_command.py`

**Step 1: Write the failing test**

```python
# tests/test_backend_command.py
"""Tests for POST /api/backends/{url}/command endpoint."""

import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from ollama_queue.app import create_app
from ollama_queue.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d


@pytest.fixture
def client(db):
    return TestClient(create_app(db))


def test_command_sync_models(client):
    """POST /command dispatches sync-models to the agent."""
    url = "http://testhost:11434"
    encoded = urllib.parse.quote(url, safe="")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "pulled": 3}

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("ollama_queue.api.backends.httpx.AsyncClient", return_value=mock_client):
        resp = client.post(f"/api/backends/{encoded}/command", json={"action": "sync-models"})

    assert resp.status_code == 200
    # Verify it called the agent on port 11435
    call_url = mock_client.post.call_args[0][0]
    assert ":11435/" in call_url
    assert "sync-models" in call_url


def test_command_invalid_action(client):
    """POST /command with unsupported action returns 400."""
    url = "http://testhost:11434"
    encoded = urllib.parse.quote(url, safe="")
    resp = client.post(f"/api/backends/{encoded}/command", json={"action": "rm-rf"})
    assert resp.status_code == 400


def test_command_agent_unreachable(client):
    """POST /command returns 502 when agent is unreachable."""
    url = "http://testhost:11434"
    encoded = urllib.parse.quote(url, safe="")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

    with patch("ollama_queue.api.backends.httpx.AsyncClient", return_value=mock_client):
        resp = client.post(f"/api/backends/{encoded}/command", json={"action": "status"})

    assert resp.status_code == 502
```

**Step 2: Run test to verify it fails**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_backend_command.py -v`
Expected: FAIL — 404 or 405

**Step 3: Implement the command endpoint**

Add to `ollama_queue/api/backends.py` after the heartbeat endpoint (~line 373):

```python
# ── POST /api/backends/{url}/command ─────────────────────────────────────────

_ALLOWED_ACTIONS = frozenset({"sync-models", "update-ollama", "restart-ollama", "status"})
_AGENT_PORT = 11435


class CommandRequest(BaseModel):
    action: str


@router.post("/api/backends/{url:path}/command")
async def backend_command(url: str = Path(...), req: CommandRequest = None):
    """Dispatch a command to a remote backend agent.

    Plain English: The dashboard or CLI calls this endpoint, and the queue
    forwards the request to the backend agent running on port 11435 of the
    same host. The agent executes the command locally (sync models, update
    Ollama, etc.) and returns the result.

    Supported actions: sync-models, update-ollama, restart-ollama, status
    """
    url = unquote(url)
    if req is None or req.action not in _ALLOWED_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"action must be one of: {', '.join(sorted(_ALLOWED_ACTIONS))}",
        )

    # Derive agent URL: same host, port 11435
    parsed = urlparse(url)
    agent_base = f"{parsed.scheme}://{parsed.hostname}:{_AGENT_PORT}"
    agent_endpoint = f"{agent_base}/{req.action}"

    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:  # noqa: S501
            resp = await client.post(agent_endpoint)
            return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        _log.warning("command %s to agent %s failed: %s", req.action, agent_base, e)
        raise HTTPException(status_code=502, detail=f"agent unreachable: {e}") from e
    except Exception as e:
        _log.error("unexpected error dispatching %s to %s: %s", req.action, agent_base, e)
        raise HTTPException(status_code=500, detail="internal error") from e
```

**Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_backend_command.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add ollama_queue/api/backends.py tests/test_backend_command.py
git commit -m "feat: add POST /api/backends/{url}/command for agent dispatch"
```

---

### Task 5: CLI backend Subcommand

**Files:**

- Create: `ollama_queue/cli_backend.py`
- Modify: `ollama_queue/cli.py` (register subcommand group)
- Test: `tests/test_cli_backend.py`

**Step 1: Write the failing test**

```python
# tests/test_cli_backend.py
"""Tests for ollama-queue backend CLI subcommand."""

from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from ollama_queue.cli import main


def test_backend_status_all(tmp_path):
    """ollama-queue backend status calls GET /api/backends."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"url": "http://host1:11434", "healthy": True, "gpu_name": "RTX 5080"},
    ]

    with patch("ollama_queue.cli_backend.httpx.get", return_value=mock_resp):
        runner = CliRunner()
        result = runner.invoke(main, ["--db", str(tmp_path / "test.db"), "backend", "status"])

    assert result.exit_code == 0
    assert "host1" in result.output


def test_backend_sync_models_specific(tmp_path):
    """ollama-queue backend sync-models <url> calls POST /api/backends/{url}/command."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True}

    with patch("ollama_queue.cli_backend.httpx.post", return_value=mock_resp):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--db", str(tmp_path / "test.db"), "backend", "sync-models", "http://host1:11434"],
        )

    assert result.exit_code == 0
```

**Step 2: Run test to verify it fails**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_cli_backend.py -v`
Expected: FAIL — no `backend` command

**Step 3: Create cli_backend.py**

```python
# ollama_queue/cli_backend.py
"""CLI subcommand group for backend agent management."""

import urllib.parse

import click
import httpx

DEFAULT_QUEUE = "http://127.0.0.1:7683"


@click.group("backend")
@click.option("--queue-url", default=DEFAULT_QUEUE, envvar="QUEUE_URL", help="Queue server URL")
@click.pass_context
def backend(ctx, queue_url):
    """Manage backend agents."""
    ctx.ensure_object(dict)
    ctx.obj["queue_url"] = queue_url.rstrip("/")


@backend.command("status")
@click.argument("url", required=False)
@click.pass_context
def backend_status(ctx, url):
    """Show backend agent status (all or specific)."""
    queue_url = ctx.obj["queue_url"]
    resp = httpx.get(f"{queue_url}/api/backends", timeout=10.0)
    backends = resp.json()

    if url:
        backends = [b for b in backends if b["url"].rstrip("/") == url.rstrip("/")]
        if not backends:
            click.echo(f"Backend {url} not found.")
            ctx.exit(1)
            return

    for b in backends:
        healthy = "OK" if b.get("healthy") else "DOWN"
        gpu = b.get("gpu_name") or "unknown"
        vram = b.get("vram_pct", 0)
        click.echo(f"  {b['url']}  [{healthy}]  GPU: {gpu}  VRAM: {vram:.0f}%")


def _dispatch_command(queue_url: str, backend_url: str, action: str):
    """Send a command to a specific backend via the queue."""
    encoded = urllib.parse.quote(backend_url, safe="")
    resp = httpx.post(
        f"{queue_url}/api/backends/{encoded}/command",
        json={"action": action},
        timeout=60.0,
    )
    if resp.status_code != 200:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")
        return
    click.echo(f"  {backend_url}: {resp.json()}")


def _dispatch_to_all_or_one(ctx, action, url):
    """Dispatch a command to one backend or all."""
    queue_url = ctx.obj["queue_url"]
    if url:
        _dispatch_command(queue_url, url, action)
    else:
        resp = httpx.get(f"{queue_url}/api/backends", timeout=10.0)
        for b in resp.json():
            _dispatch_command(queue_url, b["url"], action)


@backend.command("sync-models")
@click.argument("url", required=False)
@click.pass_context
def backend_sync_models(ctx, url):
    """Trigger model sync on backend(s)."""
    _dispatch_to_all_or_one(ctx, "sync-models", url)


@backend.command("update-ollama")
@click.argument("url", required=False)
@click.pass_context
def backend_update_ollama(ctx, url):
    """Update Ollama on backend(s)."""
    _dispatch_to_all_or_one(ctx, "update-ollama", url)
```

**Step 4: Register in cli.py**

In `ollama_queue/cli.py`, add after all existing command definitions (at the end of file):

```python
# Register backend subcommand group
from ollama_queue.cli_backend import backend
main.add_command(backend)
```

**Step 5: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_cli_backend.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add ollama_queue/cli_backend.py ollama_queue/cli.py tests/test_cli_backend.py
git commit -m "feat: add 'ollama-queue backend' CLI subcommand"
```

---

## Batch 3: Backend Agent

### Task 6: Backend Agent Core — Health, Version, Status Endpoints

**Files:**

- Create: `sidecar/backend_agent.py`
- Test: `sidecar/tests/test_agent_endpoints.py`

**Step 1: Write the failing tests**

```python
# sidecar/tests/test_agent_endpoints.py
"""Tests for backend agent HTTP endpoints."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("QUEUE_URL", "http://queue:7683")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("BACKEND_URL", "http://100.1.2.3:11434")
    # Import after env vars are set
    from backend_agent import app
    return TestClient(app)


def test_health(client):
    """GET /health returns ok, agent version, and ollama_url."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "version" in data
    assert "ollama_url" in data


def test_version(client):
    """GET /version returns agent version string."""
    resp = client.get("/version")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
```

**Step 2: Run test to verify it fails**

Run: `cd ~/Documents/projects/ollama-queue && cd sidecar && python3 -m pytest tests/test_agent_endpoints.py -v`
Expected: FAIL — module not found

**Step 3: Create backend_agent.py with core endpoints**

```python
# sidecar/backend_agent.py
"""backend_agent.py — Ollama backend agent.

Runs alongside Ollama on each remote host. Provides:
- Reconciliation loop: fetches required models from queue, pulls missing ones
- Heartbeat: pushes health + system metrics to queue every 30s
- Command endpoints: sync-models, update-ollama, restart-ollama
- Status: reports installed/missing models, system resources

Deploy via Docker:
    docker run -d --name ollama-agent --restart always \
      -p 11435:11435 \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v ollama:/ollama:ro \
      -v ollama-agent-data:/data \
      -e QUEUE_URL=http://<queue-tailscale-ip>:7683 \
      -e OLLAMA_URL=http://host.docker.internal:11434 \
      -e BACKEND_URL=http://<this-tailscale-ip>:11434 \
      ghcr.io/parthalon025/ollama-backend-agent:latest
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI

VERSION = os.environ.get("AGENT_VERSION", "0.1.0")
QUEUE_URL = os.environ.get("QUEUE_URL", "http://127.0.0.1:7683").rstrip("/")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:11434").rstrip("/")
DATA_DIR = Path(os.environ.get("AGENT_DATA_DIR", "/data"))
RECONCILE_INTERVAL = int(os.environ.get("RECONCILE_INTERVAL", "300"))  # 5 minutes
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))
PORT = int(os.environ.get("AGENT_PORT", "11435"))

OLLAMA_VOLUME = Path(os.environ.get("OLLAMA_VOLUME", "/ollama"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_log = logging.getLogger("backend-agent")

app = FastAPI(title="ollama-backend-agent", docs_url=None, redoc_url=None)

# ── State ────────────────────────────────────────────────────────────────────

_last_reconcile: float | None = None
_last_reconcile_result: dict = {}
_cached_models_path = DATA_DIR / "required-models.json"


# ── System metrics ───────────────────────────────────────────────────────────


def _read_cpu_pct() -> float:
    """Read CPU usage from /proc/stat (two samples, 100ms apart)."""
    try:
        def _read_idle():
            with open("/proc/stat") as f:
                parts = f.readline().split()
            total = sum(int(x) for x in parts[1:])
            idle = int(parts[4])
            return total, idle

        t1, i1 = _read_idle()
        time.sleep(0.1)
        t2, i2 = _read_idle()
        delta_total = t2 - t1
        delta_idle = i2 - i1
        if delta_total == 0:
            return 0.0
        return round((1.0 - delta_idle / delta_total) * 100, 1)
    except Exception:
        return 0.0


def _read_ram() -> tuple[float, float]:
    """Read RAM usage from /proc/meminfo. Returns (pct, total_gb)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total_kb = info.get("MemTotal", 0)
        avail_kb = info.get("MemAvailable", 0)
        if total_kb == 0:
            return 0.0, 0.0
        pct = round((1.0 - avail_kb / total_kb) * 100, 1)
        total_gb = round(total_kb / 1024 / 1024, 1)
        return pct, total_gb
    except Exception:
        return 0.0, 0.0


def _read_disk() -> tuple[float, float, float]:
    """Read disk usage for Ollama volume. Returns (pct, total_gb, used_gb)."""
    try:
        usage = shutil.disk_usage(str(OLLAMA_VOLUME) if OLLAMA_VOLUME.exists() else "/")
        total_gb = round(usage.total / 1024**3, 1)
        used_gb = round(usage.used / 1024**3, 1)
        pct = round(usage.used / usage.total * 100, 1) if usage.total else 0.0
        return pct, total_gb, used_gb
    except Exception:
        return 0.0, 0.0, 0.0


def _read_ollama_storage_gb() -> float:
    """Read total size of Ollama models directory."""
    try:
        models_dir = OLLAMA_VOLUME / "models"
        if not models_dir.exists():
            return 0.0
        total = sum(f.stat().st_size for f in models_dir.rglob("*") if f.is_file())
        return round(total / 1024**3, 1)
    except Exception:
        return 0.0


# ── Ollama helpers ───────────────────────────────────────────────────────────


async def _ollama_tags() -> list[str]:
    """Get list of installed model names from Ollama."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            resp = await c.get(f"{OLLAMA_URL}/api/tags")
            if resp.status_code == 200:
                return [m["name"] for m in resp.json().get("models", [])]
    except Exception as e:
        _log.warning("Failed to get Ollama tags: %s", e)
    return []


async def _ollama_version() -> str | None:
    """Get Ollama version string."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            resp = await c.get(f"{OLLAMA_URL}/api/version")
            if resp.status_code == 200:
                return resp.json().get("version")
    except Exception:
        pass
    return None


async def _ollama_healthy() -> bool:
    """Check if Ollama is reachable."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            resp = await c.get(f"{OLLAMA_URL}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False


async def _ollama_pull(model: str) -> bool:
    """Pull a model from Ollama. Returns True on success."""
    _log.info("Pulling model: %s", model)
    try:
        async with httpx.AsyncClient(timeout=3600.0) as c:
            resp = await c.post(
                f"{OLLAMA_URL}/api/pull",
                json={"model": model, "stream": False},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    _log.info("Pull complete: %s", model)
                    return True
            _log.warning("Pull failed for %s: HTTP %d", model, resp.status_code)
    except Exception as e:
        _log.error("Pull error for %s: %s", model, e)
    return False


# ── Required models ──────────────────────────────────────────────────────────


async def _fetch_required_models() -> list[dict]:
    """Fetch required models from queue, falling back to cached file."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.get(
                f"{QUEUE_URL}/api/required-models",
                params={"backend_url": BACKEND_URL},
            )
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                # Cache to disk
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                _cached_models_path.write_text(json.dumps(models))
                return models
    except Exception as e:
        _log.warning("Failed to fetch required models from queue: %s", e)

    # Fallback: cached file
    if _cached_models_path.exists():
        try:
            return json.loads(_cached_models_path.read_text())
        except Exception:
            pass

    return []


# ── Reconciliation ───────────────────────────────────────────────────────────


async def _reconcile() -> dict:
    """Run one reconciliation cycle: fetch required, diff, pull missing."""
    global _last_reconcile, _last_reconcile_result

    required = await _fetch_required_models()
    required_names = [m["name"] for m in required]
    installed = await _ollama_tags()

    missing = [n for n in required_names if n not in installed]
    extra = [n for n in installed if n not in required_names]

    pulled = []
    failed = []
    for model in missing:
        if await _ollama_pull(model):
            pulled.append(model)
        else:
            failed.append(model)

    _last_reconcile = time.time()
    _last_reconcile_result = {
        "required": len(required_names),
        "installed": len(installed),
        "missing_before": len(missing),
        "pulled": pulled,
        "failed": failed,
        "extra": extra,
    }

    _log.info(
        "Reconcile: %d required, %d installed, %d pulled, %d failed",
        len(required_names), len(installed), len(pulled), len(failed),
    )
    return _last_reconcile_result


# ── Heartbeat ────────────────────────────────────────────────────────────────


async def _send_heartbeat():
    """Push health metrics to the queue server."""
    installed = await _ollama_tags()
    ollama_ver = await _ollama_version()
    healthy = await _ollama_healthy()
    ram_pct, ram_total_gb = _read_ram()
    disk_pct, disk_total_gb, disk_used_gb = _read_disk()

    payload = {
        "healthy": healthy,
        "cpu_pct": _read_cpu_pct(),
        "ram_pct": ram_pct,
        "ram_total_gb": ram_total_gb,
        "disk_total_gb": disk_total_gb,
        "disk_used_gb": disk_used_gb,
        "disk_pct": disk_pct,
        "ollama_storage_gb": _read_ollama_storage_gb(),
        "available_models": installed,
        "agent_version": VERSION,
        "ollama_version": ollama_ver,
        "last_reconcile": _last_reconcile,
    }

    try:
        import urllib.parse
        encoded_url = urllib.parse.quote(BACKEND_URL, safe="")
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.put(
                f"{QUEUE_URL}/api/backends/{encoded_url}/heartbeat",
                json=payload,
            )
            if resp.status_code != 200:
                _log.warning("Heartbeat rejected: HTTP %d", resp.status_code)
    except Exception as e:
        _log.warning("Heartbeat failed: %s", e)


# ── Background loops ─────────────────────────────────────────────────────────


async def _reconcile_loop():
    """Run reconciliation on a schedule."""
    # Initial reconciliation after short delay (let Ollama start)
    await asyncio.sleep(10)
    while True:
        try:
            await _reconcile()
        except Exception as e:
            _log.error("Reconcile loop error: %s", e)
        await asyncio.sleep(RECONCILE_INTERVAL)


async def _heartbeat_loop():
    """Send heartbeats on a schedule."""
    await asyncio.sleep(5)
    while True:
        try:
            await _send_heartbeat()
        except Exception as e:
            _log.error("Heartbeat loop error: %s", e)
        await asyncio.sleep(HEARTBEAT_INTERVAL)


@app.on_event("startup")
async def startup():
    asyncio.create_task(_reconcile_loop())
    asyncio.create_task(_heartbeat_loop())
    _log.info("Backend agent started: queue=%s ollama=%s backend=%s", QUEUE_URL, OLLAMA_URL, BACKEND_URL)


# ── HTTP Endpoints ───────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    ollama_ok = await _ollama_healthy()
    return {
        "ok": True,
        "ollama_healthy": ollama_ok,
        "ollama_url": OLLAMA_URL,
        "queue_url": QUEUE_URL,
        "backend_url": BACKEND_URL,
        "version": VERSION,
        "last_reconcile": _last_reconcile,
    }


@app.get("/version")
def version():
    return {"version": VERSION}


@app.get("/status")
async def status():
    installed = await _ollama_tags()
    required = await _fetch_required_models()
    required_names = [m["name"] for m in required]
    missing = [n for n in required_names if n not in installed]
    ram_pct, ram_total_gb = _read_ram()
    disk_pct, disk_total_gb, disk_used_gb = _read_disk()

    return {
        "backend_url": BACKEND_URL,
        "version": VERSION,
        "ollama_healthy": await _ollama_healthy(),
        "ollama_version": await _ollama_version(),
        "models": {
            "installed": sorted(installed),
            "required": sorted(required_names),
            "missing": sorted(missing),
        },
        "system": {
            "cpu_pct": _read_cpu_pct(),
            "ram_pct": ram_pct,
            "ram_total_gb": ram_total_gb,
            "disk_pct": disk_pct,
            "disk_total_gb": disk_total_gb,
            "disk_used_gb": disk_used_gb,
            "ollama_storage_gb": _read_ollama_storage_gb(),
        },
        "last_reconcile": _last_reconcile,
        "last_reconcile_result": _last_reconcile_result,
    }


@app.post("/sync-models")
async def sync_models():
    result = await _reconcile()
    return {"ok": True, **result}


@app.post("/update-ollama")
async def update_ollama():
    """Pull latest Ollama Docker image and recreate the container."""
    try:
        import docker as docker_sdk
        client = docker_sdk.from_env()
    except Exception as e:
        return {"ok": False, "error": f"Docker unavailable: {e}"}

    log = []
    container_name = os.environ.get("OLLAMA_CONTAINER", "ollama")

    # Pull latest image
    log.append("Pulling ollama/ollama:latest...")
    try:
        client.images.pull("ollama/ollama", tag="latest")
        log.append("Pull complete.")
    except Exception as e:
        return {"ok": False, "error": f"Pull failed: {e}", "log": log}

    # Capture existing config
    try:
        old = client.containers.get(container_name)
        hc = old.attrs["HostConfig"]
        cfg = old.attrs["Config"]

        port_bindings = hc.get("PortBindings") or {}
        binds = [b for b in (hc.get("Binds") or []) if "docker.sock" not in b]

        run_config = {
            "image": "ollama/ollama:latest",
            "name": container_name,
            "detach": True,
            "restart_policy": {"Name": (hc.get("RestartPolicy") or {}).get("Name", "always")},
            "ports": {k: v[0]["HostPort"] for k, v in port_bindings.items() if v},
            "volumes": binds,
            "environment": cfg.get("Env") or [],
            "runtime": hc.get("Runtime"),
            "device_requests": hc.get("DeviceRequests"),
        }
        run_config = {k: v for k, v in run_config.items() if v is not None}

        log.append("Stopping old container...")
        old.stop(timeout=10)
        old.remove()
        log.append("Removed old container.")
    except Exception:
        log.append(f"Container '{container_name}' not found, using defaults.")
        run_config = {
            "image": "ollama/ollama:latest",
            "name": container_name,
            "detach": True,
            "restart_policy": {"Name": "always"},
            "ports": {"11434/tcp": "11434"},
            "volumes": ["ollama:/root/.ollama"],
        }

    # Recreate
    try:
        container = client.containers.run(**run_config)
        time.sleep(2)
        container.reload()
        log.append(f"Container started (status={container.status}).")
        return {"ok": True, "log": log, "status": container.status}
    except Exception as e:
        return {"ok": False, "error": f"Recreate failed: {e}", "log": log}


@app.post("/restart-ollama")
async def restart_ollama():
    """Restart the Ollama Docker container."""
    try:
        import docker as docker_sdk
        client = docker_sdk.from_env()
        container_name = os.environ.get("OLLAMA_CONTAINER", "ollama")
        container = client.containers.get(container_name)
        container.restart(timeout=10)
        return {"ok": True, "status": "restarted"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")  # noqa: S104
```

**Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue/sidecar && PYTHONPATH=. python3 -m pytest tests/test_agent_endpoints.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add sidecar/backend_agent.py sidecar/tests/test_agent_endpoints.py
git commit -m "feat: create backend agent with health, status, reconciliation, and command endpoints"
```

---

## Batch 4: Docker Image + Bootstrap + CI

### Task 7: Dockerfile for Backend Agent

**Files:**

- Create: `sidecar/Dockerfile`

**Step 1: Create the Dockerfile**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend_agent.py .

RUN pip install --no-cache-dir fastapi uvicorn httpx docker

VOLUME ["/data"]
EXPOSE 11435

ARG AGENT_VERSION=dev
ENV AGENT_VERSION=${AGENT_VERSION}

LABEL org.opencontainers.image.source=https://github.com/parthalon025/ollama-queue
LABEL org.opencontainers.image.description="Ollama backend agent — model sync, health monitoring, Docker management"

CMD ["python3", "backend_agent.py"]
```

**Step 2: Test Docker build locally**

Run: `cd ~/Documents/projects/ollama-queue && docker build -t ollama-backend-agent:test -f sidecar/Dockerfile sidecar/`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add sidecar/Dockerfile
git commit -m "feat: add Dockerfile for backend agent"
```

---

### Task 8: Bootstrap Script

**Files:**

- Create: `scripts/bootstrap-backend.sh`

**Step 1: Create the bootstrap script**

```bash
#!/usr/bin/env bash
# bootstrap-backend.sh — One-command setup for a new Ollama backend host.
#
# Usage:
#   curl -sL https://raw.githubusercontent.com/parthalon025/ollama-queue/main/scripts/bootstrap-backend.sh | \
#     bash -s -- --queue http://<queue-ip>:7683 --backend-url http://<this-tailscale-ip>:11434
#
# Prerequisites: Docker installed, Tailscale connected.

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────

QUEUE_URL=""
BACKEND_URL=""
OLLAMA_CONTAINER="ollama"
AGENT_IMAGE="ghcr.io/parthalon025/ollama-backend-agent:latest"
AGENT_CONTAINER="ollama-agent"
AGENT_PORT=11435

# ── Helpers ──────────────────────────────────────────────────────────────────

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

usage() {
    echo "Usage: bootstrap-backend.sh --queue <QUEUE_URL> --backend-url <BACKEND_URL>"
    echo ""
    echo "Options:"
    echo "  --queue        Queue server URL (e.g., http://100.68.34.41:7683)"
    echo "  --backend-url  This host's Tailscale-routable Ollama URL (e.g., http://100.91.20.72:11434)"
    echo "  --help         Show this help"
    exit 1
}

# ── Parse args ───────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --queue)       QUEUE_URL="$2"; shift 2 ;;
        --backend-url) BACKEND_URL="$2"; shift 2 ;;
        --help)        usage ;;
        *)             red "Unknown option: $1"; usage ;;
    esac
done

[[ -z "$QUEUE_URL" ]] && { red "ERROR: --queue is required"; usage; }
[[ -z "$BACKEND_URL" ]] && { red "ERROR: --backend-url is required"; usage; }

# ── Checks ───────────────────────────────────────────────────────────────────

bold "Ollama Backend Bootstrap"
echo "  Queue:   ${QUEUE_URL}"
echo "  Backend: ${BACKEND_URL}"
echo ""

if ! command -v docker &> /dev/null; then
    red "ERROR: Docker is not installed."
    echo "  Install: https://docs.docker.com/engine/install/"
    exit 1
fi

# ── Detect GPU ───────────────────────────────────────────────────────────────

GPU_FLAGS=""
if command -v nvidia-smi &> /dev/null; then
    bold "NVIDIA GPU detected"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || true
    GPU_FLAGS="--gpus all"
    echo ""
fi

# ── Start Ollama ─────────────────────────────────────────────────────────────

if docker inspect "$OLLAMA_CONTAINER" > /dev/null 2>&1; then
    green "Ollama container '${OLLAMA_CONTAINER}' already running."
else
    bold "Starting Ollama container..."
    # shellcheck disable=SC2086
    docker run -d --name "$OLLAMA_CONTAINER" \
        --restart always \
        $GPU_FLAGS \
        -p 11434:11434 \
        -v ollama:/root/.ollama \
        ollama/ollama:latest
    green "Ollama started."
fi
echo ""

# ── Start Agent ──────────────────────────────────────────────────────────────

# Remove old sidecar if present
if docker inspect docker-mgmt > /dev/null 2>&1; then
    yellow "Removing old docker-mgmt sidecar..."
    docker stop docker-mgmt 2>/dev/null || true
    docker rm docker-mgmt 2>/dev/null || true
fi

# Remove old agent if present (for re-runs)
if docker inspect "$AGENT_CONTAINER" > /dev/null 2>&1; then
    yellow "Removing existing agent container..."
    docker stop "$AGENT_CONTAINER" 2>/dev/null || true
    docker rm "$AGENT_CONTAINER" 2>/dev/null || true
fi

bold "Pulling backend agent image..."
docker pull "$AGENT_IMAGE"

bold "Starting backend agent..."
docker run -d --name "$AGENT_CONTAINER" \
    --restart always \
    -p ${AGENT_PORT}:${AGENT_PORT} \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v ollama:/ollama:ro \
    -v ollama-agent-data:/data \
    -e QUEUE_URL="$QUEUE_URL" \
    -e OLLAMA_URL="http://host.docker.internal:11434" \
    -e BACKEND_URL="$BACKEND_URL" \
    "$AGENT_IMAGE"

# ── Wait for health ──────────────────────────────────────────────────────────

bold "Waiting for agent health check..."
for i in $(seq 1 15); do
    if curl -sf --connect-timeout 2 "http://localhost:${AGENT_PORT}/health" > /dev/null 2>&1; then
        green "Agent is healthy!"
        echo ""

        # Print status
        bold "Agent Status:"
        curl -sf "http://localhost:${AGENT_PORT}/health" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for k, v in d.items():
    print(f'  {k}: {v}')
" 2>/dev/null || true

        echo ""
        green "Bootstrap complete!"
        echo "  Agent: http://localhost:${AGENT_PORT}"
        echo "  Reconciliation will start automatically (models sync in ~10s)"
        echo "  Monitor: curl http://localhost:${AGENT_PORT}/status"
        exit 0
    fi
    sleep 2
done

yellow "Agent health check timed out after 30s."
echo "  Check logs: docker logs ${AGENT_CONTAINER}"
exit 1
```

**Step 2: Make executable and commit**

```bash
chmod +x scripts/bootstrap-backend.sh
git add scripts/bootstrap-backend.sh
git commit -m "feat: add bootstrap-backend.sh for one-command host setup"
```

---

### Task 9: GHCR CI Workflow

**Files:**

- Modify: `.github/workflows/release.yml`

**Step 1: Update release workflow**

Replace the existing `release.yml` with:

```yaml
name: Release
on:
  push:
    tags: ['v*']
permissions:
  contents: write
  packages: write
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: softprops/action-gh-release@v2
        with:
          generate_release_notes: true

  docker-agent:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v5
        with:
          context: sidecar/
          push: true
          build-args: AGENT_VERSION=${{ github.ref_name }}
          tags: |
            ghcr.io/parthalon025/ollama-backend-agent:latest
            ghcr.io/parthalon025/ollama-backend-agent:${{ github.ref_name }}
```

**Step 2: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add GHCR build for backend agent on release"
```

---

### Task 10: Routing Enhancement — CPU/RAM in Tier 4

**Files:**

- Modify: `ollama_queue/api/backend_router.py` (`_route_by_model` function, ~line 356)
- Test: `tests/test_routing_cpu_ram.py`

**Step 1: Write the failing test**

```python
# tests/test_routing_cpu_ram.py
"""Tests for CPU/RAM-aware routing in multi-backend selection."""

import time
from unittest.mock import AsyncMock, patch

import pytest

import ollama_queue.api.backend_router as _router


@pytest.fixture(autouse=True)
def clean_caches():
    """Clean router caches before and after each test."""
    saved_backends = _router.BACKENDS[:]
    yield
    _router.BACKENDS[:] = saved_backends
    for c in (_router._health_cache, _router._models_cache, _router._loaded_cache,
              _router._hw_cache, _router._gpu_name_cache, _router._vram_total_cache,
              _router._cpu_cache, _router._ram_cache):
        c.clear()


@pytest.mark.asyncio
async def test_skip_high_cpu_backend():
    """Backend with cpu_pct > 90 is deprioritized."""
    _router.BACKENDS[:] = ["http://a:11434", "http://b:11434"]
    now = time.monotonic()

    # Both healthy, both have the model, neither warm
    _router._health_cache["http://a:11434"] = (now, True)
    _router._health_cache["http://b:11434"] = (now, True)
    _router._models_cache["http://a:11434"] = (now, frozenset(["test:7b"]))
    _router._models_cache["http://b:11434"] = (now, frozenset(["test:7b"]))
    _router._hw_cache["http://a:11434"] = (now, 50.0)
    _router._hw_cache["http://b:11434"] = (now, 50.0)

    # A has high CPU, B is fine
    _router._cpu_cache["http://a:11434"] = (now, 95.0)
    _router._cpu_cache["http://b:11434"] = (now, 20.0)

    result = await _router.select_backend("test:7b")
    assert result == "http://b:11434"
```

**Step 2: Run test to verify it fails**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_routing_cpu_ram.py -v`
Expected: FAIL — routing doesn't consider CPU

**Step 3: Add CPU/RAM filter to _route_by_model**

In `ollama_queue/api/backend_router.py`, add a new constant and a filter step in `_route_by_model()` after the HW pressure check (~line 388):

Add constants near the top cache TTLs section (~line 78):

```python
_CPU_TTL = 30.0
_RAM_TTL = 30.0
_CPU_OVERLOAD_PCT = 90.0
_RAM_OVERLOAD_PCT = 90.0
```

Add a new filter at the end of `_route_by_model()`, before the return (after the VRAM pressure filter, ~line 388):

```python
    # 5. Skip backends with critically high CPU or RAM pressure
    now = time.monotonic()
    not_overloaded = []
    for b in healthy:
        cpu_entry = _cpu_cache.get(b)
        ram_entry = _ram_cache.get(b)
        cpu_ok = not cpu_entry or (now - cpu_entry[0]) > _CPU_TTL or cpu_entry[1] < _CPU_OVERLOAD_PCT
        ram_ok = not ram_entry or (now - ram_entry[0]) > _RAM_TTL or ram_entry[1] < _RAM_OVERLOAD_PCT
        if cpu_ok and ram_ok:
            not_overloaded.append(b)
    # Fail-open: if all are overloaded, use the original list
    if not_overloaded:
        healthy = not_overloaded
```

**Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest tests/test_routing_cpu_ram.py -v`
Expected: PASS

**Step 5: Run full test suite for regression**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest --timeout=120 -x -q`
Expected: All pass

**Step 6: Commit**

```bash
git add ollama_queue/api/backend_router.py tests/test_routing_cpu_ram.py
git commit -m "feat: add CPU/RAM pressure to multi-backend routing"
```

---

## Batch 5: Final Integration

### Task 11: Full Test Suite + Final Commit

**Step 1: Run full test suite**

Run: `cd ~/Documents/projects/ollama-queue && python3 -m pytest --timeout=120 -x -q`
Expected: All pass

**Step 2: Build Docker image locally and verify**

Run: `cd ~/Documents/projects/ollama-queue && docker build -t ollama-backend-agent:test -f sidecar/Dockerfile sidecar/`
Expected: Build succeeds

**Step 3: Final squash/cleanup commit if needed**

If WIP commits accumulated, create a clean final commit message.

---

## Dependency Graph

```
Batch 1 (parallel):
  Task 1: Seed required_models setting
  Task 2: GET /api/required-models endpoint (depends on Task 1)
  Task 3: Expand heartbeat + caches

Batch 2 (parallel, after Batch 1):
  Task 4: POST /command endpoint
  Task 5: CLI backend subcommand

Batch 3 (parallel with Batch 2):
  Task 6: Backend agent (full: endpoints + reconciliation + heartbeat)

Batch 4 (after Batch 3):
  Task 7: Dockerfile
  Task 8: Bootstrap script
  Task 9: GHCR CI workflow
  Task 10: Routing enhancement (CPU/RAM)

Batch 5 (after all):
  Task 11: Integration testing + cleanup
```

## Parallelization Notes for Subagents

- **Batch 1:** Tasks 1+3 can run in parallel (different files). Task 2 depends on Task 1.
- **Batch 2:** Tasks 4+5 can run in parallel (different files).
- **Batch 3:** Task 6 is independent — can start as soon as the API contract is known (after Batch 1).
- **Batch 4:** Tasks 7+8+9+10 are all independent of each other.
