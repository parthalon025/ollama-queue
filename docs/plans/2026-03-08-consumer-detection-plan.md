# Consumer Detection & Onboarding Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Detect services calling Ollama on port 11434, surface them in a new SPA Consumers tab, and auto-patch included consumers to route through the queue proxy on port 7683.

**Architecture:** Scanner (4-phase: live/static/stream/deadlock) writes to a `consumers` DB table. Patcher injects `OLLAMA_HOST=localhost:7683` into systemd units, .env, or config files — always backing up first. API exposes CRUD + health validation. SPA adds a sixth "Consumers" tab with include/ignore per row and a first-run wizard.

**Tech Stack:** Python (subprocess, sqlite3, ruamel.yaml, tomlkit), FastAPI/Pydantic, Preact + @preact/signals, existing `useActionFeedback` hook.

**Design doc:** `docs/plans/2026-03-08-consumer-detection-design.md`

---

## Batch 1: DB Migration + Scanner Foundation

### Task 1: consumers table migration

**Files:**
- Modify: `ollama_queue/db.py` (find `_run_migrations`, add new table + `_add_column_if_missing` calls)

**Step 1: Write the failing test**

```python
# tests/test_consumers.py
import pytest
from ollama_queue.db import Database

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d

def test_consumers_table_created(db):
    conn = db._connect()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='consumers'"
    ).fetchone()
    assert row is not None

def test_consumers_upsert_and_fetch(db):
    import time
    db.upsert_consumer({
        "name": "aria.service",
        "type": "systemd",
        "platform": "linux",
        "source_label": "aria",
        "detected_at": int(time.time()),
    })
    rows = db.list_consumers()
    assert len(rows) == 1
    assert rows[0]["name"] == "aria.service"

def test_consumers_upsert_deduplicates(db):
    import time
    now = int(time.time())
    db.upsert_consumer({"name": "svc", "type": "systemd", "platform": "linux",
                        "source_label": "svc", "detected_at": now})
    db.upsert_consumer({"name": "svc", "type": "systemd", "platform": "linux",
                        "source_label": "svc", "detected_at": now})
    assert len(db.list_consumers()) == 1

def test_consumer_update_status(db):
    import time
    db.upsert_consumer({"name": "svc", "type": "systemd", "platform": "linux",
                        "source_label": "svc", "detected_at": int(time.time())})
    rows = db.list_consumers()
    db.update_consumer(rows[0]["id"], status="included")
    updated = db.get_consumer(rows[0]["id"])
    assert updated["status"] == "included"
```

**Step 2: Run test to verify it fails**

```bash
cd ~/Documents/projects/ollama-queue
python3 -m pytest tests/test_consumers.py -v
```
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'upsert_consumer'`

**Step 3: Add table creation + DB methods to `db.py`**

In `_initialize_db()`, add after the last `CREATE TABLE IF NOT EXISTS`:

```python
conn.execute("""
    CREATE TABLE IF NOT EXISTS consumers (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        name                TEXT NOT NULL,
        type                TEXT NOT NULL,
        platform            TEXT NOT NULL,
        source_label        TEXT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'discovered',
        streaming_confirmed INTEGER DEFAULT 0,
        streaming_suspect   INTEGER DEFAULT 0,
        is_managed_job      INTEGER DEFAULT 0,
        patch_type          TEXT,
        restart_policy      TEXT DEFAULT 'deferred',
        patch_applied       INTEGER DEFAULT 0,
        patch_path          TEXT,
        patch_snippet       TEXT,
        health_status       TEXT DEFAULT 'unknown',
        health_checked_at   INTEGER,
        request_count       INTEGER DEFAULT 0,
        last_seen           INTEGER,
        last_live_seen      INTEGER,
        detected_at         INTEGER NOT NULL,
        onboarded_at        INTEGER
    )
""")
```

Add methods to `Database` class:

```python
def upsert_consumer(self, data: dict) -> int:
    """Insert or update a consumer by (name, platform). Returns id."""
    with self._lock:
        conn = self._connect()
        existing = conn.execute(
            "SELECT id FROM consumers WHERE name = ? AND platform = ?",
            (data["name"], data["platform"]),
        ).fetchone()
        if existing:
            sets = ", ".join(f"{k} = ?" for k in data if k not in ("name", "platform"))
            vals = [v for k, v in data.items() if k not in ("name", "platform")]
            conn.execute(f"UPDATE consumers SET {sets} WHERE id = ?", [*vals, existing["id"]])
            conn.commit()
            return existing["id"]
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        cur = conn.execute(f"INSERT INTO consumers ({cols}) VALUES ({placeholders})", list(data.values()))
        conn.commit()
        return cur.lastrowid

def list_consumers(self) -> list[dict]:
    conn = self._connect()
    rows = conn.execute("SELECT * FROM consumers ORDER BY detected_at DESC").fetchall()
    return [dict(r) for r in rows]

def get_consumer(self, consumer_id: int) -> dict | None:
    conn = self._connect()
    row = conn.execute("SELECT * FROM consumers WHERE id = ?", (consumer_id,)).fetchone()
    return dict(row) if row else None

def update_consumer(self, consumer_id: int, **kwargs) -> None:
    with self._lock:
        conn = self._connect()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(f"UPDATE consumers SET {sets} WHERE id = ?", [*kwargs.values(), consumer_id])
        conn.commit()
```

**Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_consumers.py -v
```
Expected: 4 PASS

**Step 5: Commit**

```bash
git add ollama_queue/db.py tests/test_consumers.py
git commit -m "feat: add consumers table and DB methods"
```

---

### Task 2: Platform detection + live scan (Phase 1)

**Files:**
- Create: `ollama_queue/scanner.py`
- Create: `tests/test_scanner.py`

**Step 1: Write the failing test**

```python
# tests/test_scanner.py
from unittest.mock import patch, MagicMock
from ollama_queue.scanner import detect_platform, live_scan

def test_detect_platform_linux():
    with patch("ollama_queue.scanner.platform.system", return_value="Linux"):
        assert detect_platform() == "linux"

def test_detect_platform_macos():
    with patch("ollama_queue.scanner.platform.system", return_value="Darwin"):
        assert detect_platform() == "macos"

def test_detect_platform_windows():
    with patch("ollama_queue.scanner.platform.system", return_value="Windows"):
        assert detect_platform() == "windows"

def test_live_scan_linux_parses_ss_output():
    ss_output = (
        'tcp   ESTAB  0  0  127.0.0.1:52340  127.0.0.1:11434  '
        'users:(("aria",pid=1234,fd=7))\n'
    )
    with patch("ollama_queue.scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=ss_output, stderr="")
        results = live_scan("linux")
    assert len(results) == 1
    assert results[0]["name"] == "aria"
    assert results[0]["pid"] == 1234
    assert results[0]["type"] == "transient"

def test_live_scan_returns_empty_on_failure():
    with patch("ollama_queue.scanner.subprocess.run", side_effect=OSError("no ss")):
        results = live_scan("linux")
    assert results == []

def test_live_scan_macos_parses_lsof_output():
    lsof_output = (
        "COMMAND  PID  USER  FD  TYPE  DEVICE  SIZE/OFF  NODE  NAME\n"
        "python3  5678 user  10u IPv4  0x1234  0t0  TCP 127.0.0.1:52000->127.0.0.1:11434 (ESTABLISHED)\n"
    )
    with patch("ollama_queue.scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=lsof_output, stderr="")
        results = live_scan("macos")
    assert len(results) == 1
    assert results[0]["name"] == "python3"
    assert results[0]["pid"] == 5678
```

**Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_scanner.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'ollama_queue.scanner'`

**Step 3: Create `ollama_queue/scanner.py`**

```python
"""Detect services calling Ollama on port 11434."""

from __future__ import annotations

import logging
import platform
import re
import subprocess
import time

_log = logging.getLogger(__name__)
_OLLAMA_PORT = "11434"
_QUEUE_PORT = "7683"


def detect_platform() -> str:
    system = platform.system()
    if system == "Linux":
        return "linux"
    if system == "Darwin":
        return "macos"
    return "windows"


def live_scan(plat: str | None = None) -> list[dict]:
    """Phase 1: Find processes with active connections to port 11434."""
    plat = plat or detect_platform()
    try:
        if plat == "linux":
            return _live_scan_linux()
        if plat == "macos":
            return _live_scan_macos()
        return _live_scan_windows()
    except Exception:
        _log.warning("live_scan failed on platform %s", plat, exc_info=True)
        return []


def _live_scan_linux() -> list[dict]:
    result = subprocess.run(
        ["ss", "-tp"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return []
    consumers = []
    for line in result.stdout.splitlines():
        if f":{_OLLAMA_PORT}" not in line:
            continue
        # Extract process name and pid from users:(("name",pid=N,fd=M))
        m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        if not m:
            continue
        consumers.append({
            "name": m.group(1),
            "pid": int(m.group(2)),
            "type": "transient",
            "last_live_seen": int(time.time()),
        })
    return consumers


def _live_scan_macos() -> list[dict]:
    result = subprocess.run(
        ["lsof", f"-i:{_OLLAMA_PORT}", "-sTCP:ESTABLISHED"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return []
    consumers = []
    for line in result.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 2:
            continue
        consumers.append({
            "name": parts[0],
            "pid": int(parts[1]),
            "type": "transient",
            "last_live_seen": int(time.time()),
        })
    return consumers


def _live_scan_windows() -> list[dict]:
    result = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return []
    consumers = []
    for line in result.stdout.splitlines():
        if f":{_OLLAMA_PORT}" not in line or "ESTABLISHED" not in line:
            continue
        parts = line.split()
        pid = int(parts[-1]) if parts else 0
        consumers.append({
            "name": f"pid:{pid}",
            "pid": pid,
            "type": "transient",
            "last_live_seen": int(time.time()),
        })
    return consumers
```

**Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_scanner.py -v
```
Expected: 6 PASS

**Step 5: Commit**

```bash
git add ollama_queue/scanner.py tests/test_scanner.py
git commit -m "feat: scanner phase 1 — live process detection"
```

---

### Task 3: Static scan (Phase 2)

**Files:**
- Modify: `ollama_queue/scanner.py`
- Modify: `tests/test_scanner.py`

**Step 1: Write the failing tests**

```python
# append to tests/test_scanner.py
from ollama_queue.scanner import static_scan
import pathlib

def test_static_scan_finds_systemd_unit(tmp_path):
    unit_dir = tmp_path / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    unit = unit_dir / "aria.service"
    unit.write_text("[Service]\nEnvironment=OLLAMA_HOST=127.0.0.1:11434\n")
    results = static_scan(search_dirs=[str(tmp_path)])
    assert any(r["name"] == "aria.service" and r["type"] == "systemd" for r in results)

def test_static_scan_finds_env_file(tmp_path):
    env = tmp_path / "myproject" / ".env"
    env.parent.mkdir()
    env.write_text("OLLAMA_HOST=localhost:11434\nOTHER=foo\n")
    results = static_scan(search_dirs=[str(tmp_path)])
    assert any(r["patch_path"] == str(env) and r["type"] == "env_file" for r in results)

def test_static_scan_skips_queue_proxy_itself(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:7683\n")
    results = static_scan(search_dirs=[str(tmp_path)])
    assert len(results) == 0

def test_static_scan_deduplicates(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\n")
    results = static_scan(search_dirs=[str(tmp_path), str(tmp_path)])
    assert len(results) == 1
```

**Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_scanner.py::test_static_scan_finds_systemd_unit -v
```
Expected: FAIL — `ImportError: cannot import name 'static_scan'`

**Step 3: Add `static_scan` to `scanner.py`**

```python
_OLLAMA_11434_PATTERN = re.compile(
    r'(OLLAMA_HOST|OLLAMA_BASE_URL|ollama[._]host|base_url)\s*[=:]\s*["\']?'
    r'(localhost|127\.0\.0\.1):'
    + _OLLAMA_PORT
)
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def static_scan(search_dirs: list[str] | None = None) -> list[dict]:
    """Phase 2: Grep config files for :11434 references."""
    if search_dirs is None:
        import os
        home = os.path.expanduser("~")
        search_dirs = [home]

    seen_paths: set[str] = set()
    consumers: list[dict] = []

    for base in search_dirs:
        for path in _walk_configs(base):
            if str(path) in seen_paths:
                continue
            seen_paths.add(str(path))
            consumer = _check_config_file(path)
            if consumer:
                consumers.append(consumer)

    return consumers


def _walk_configs(base: str):
    """Yield .service, .env, .yaml, .toml files, skipping junk dirs."""
    import os
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if fname.endswith((".service", ".env", ".yaml", ".yml", ".toml")):
                yield pathlib.Path(root) / fname


def _check_config_file(path: pathlib.Path) -> dict | None:
    """Return consumer dict if file references :11434 (not :7683)."""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None
    if not _OLLAMA_11434_PATTERN.search(text):
        return None

    suffix = path.suffix
    is_system = str(path).startswith("/etc/systemd/system")

    if path.name.endswith(".service"):
        ctype = "systemd"
        name = path.name
    elif path.name == ".env":
        ctype = "env_file"
        name = path.parent.name + "/.env"
    else:
        ctype = "config_yaml" if suffix in (".yaml", ".yml") else "config_toml"
        name = str(path)

    return {
        "name": name,
        "type": ctype,
        "patch_path": str(path),
        "is_system_path": is_system,
        "last_seen": int(time.time()),
        "detected_at": int(time.time()),
    }
```

**Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_scanner.py -v
```
Expected: all PASS (10 total)

**Step 5: Commit**

```bash
git add ollama_queue/scanner.py tests/test_scanner.py
git commit -m "feat: scanner phase 2 — static config file scan"
```

---

### Task 4: Stream check (Phase 3) + Deadlock check (Phase 4)

**Files:**
- Modify: `ollama_queue/scanner.py`
- Modify: `tests/test_scanner.py`

**Step 1: Write the failing tests**

```python
# append to tests/test_scanner.py
from ollama_queue.scanner import stream_check, deadlock_check

def test_stream_check_confirmed(tmp_path):
    src = tmp_path / "client.py"
    src.write_text("response = ollama.generate(model='x', stream=True)\n")
    result = stream_check(str(tmp_path))
    assert result["streaming_confirmed"] is True
    assert result["streaming_suspect"] is False

def test_stream_check_clean(tmp_path):
    src = tmp_path / "client.py"
    src.write_text("response = ollama.generate(model='x')\n")
    result = stream_check(str(tmp_path))
    assert result["streaming_confirmed"] is False
    assert result["streaming_suspect"] is False

def test_stream_check_suspect_when_no_source(tmp_path):
    # binary file present, no readable source
    bin_file = tmp_path / "app"
    bin_file.write_bytes(b"\x00\x01\x02")
    result = stream_check(str(tmp_path), has_source=False)
    assert result["streaming_suspect"] is True

def test_deadlock_check_detects_managed_job(tmp_path):
    db_path = str(tmp_path / "test.db")
    from ollama_queue.db import Database
    db = Database(db_path)
    db.initialize()
    db.add_recurring_job(
        name="aria-full",
        command="aria predict --full",
        interval_seconds=3600,
    )
    assert deadlock_check("aria-full", "aria predict --full", db) is True

def test_deadlock_check_safe(tmp_path):
    db_path = str(tmp_path / "test.db")
    from ollama_queue.db import Database
    db = Database(db_path)
    db.initialize()
    assert deadlock_check("telegram-bot", "python3 bot.py", db) is False
```

**Step 2: Run to verify fails**

```bash
python3 -m pytest tests/test_scanner.py::test_stream_check_confirmed -v
```
Expected: FAIL — `ImportError: cannot import name 'stream_check'`

**Step 3: Add `stream_check` and `deadlock_check` to `scanner.py`**

```python
_STREAM_PATTERN = re.compile(r'\bstream\s*[=:]\s*[Tt]rue', re.IGNORECASE)
_SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".rb", ".go", ".rs", ".sh"}


def stream_check(source_dir: str | None = None, has_source: bool = True) -> dict:
    """Phase 3: Detect streaming usage in source. Returns streaming flags."""
    if not has_source:
        return {"streaming_confirmed": False, "streaming_suspect": True}
    if not source_dir:
        return {"streaming_confirmed": False, "streaming_suspect": True}

    import os
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if not any(fname.endswith(ext) for ext in _SOURCE_EXTENSIONS):
                continue
            try:
                text = pathlib.Path(root, fname).read_text(errors="ignore")
                if _STREAM_PATTERN.search(text):
                    return {"streaming_confirmed": True, "streaming_suspect": False}
            except OSError:
                continue

    return {"streaming_confirmed": False, "streaming_suspect": False}


def deadlock_check(name: str, cmdline: str, db) -> bool:
    """Phase 4: True if process matches a managed recurring job (deadlock risk)."""
    try:
        conn = db._connect()
        rows = conn.execute(
            "SELECT name, command FROM recurring_jobs WHERE is_active = 1"
        ).fetchall()
        for row in rows:
            if row["name"] in name or name in row["name"]:
                return True
            if row["command"] and row["command"][:50] in cmdline:
                return True
        return False
    except Exception:
        _log.warning("deadlock_check failed", exc_info=True)
        return False
```

**Step 4: Run all scanner tests**

```bash
python3 -m pytest tests/test_scanner.py -v
```
Expected: 15 PASS

**Step 5: Commit**

```bash
git add ollama_queue/scanner.py tests/test_scanner.py
git commit -m "feat: scanner phases 3+4 — streaming detection and deadlock check"
```

---

### Task 5: Full scan orchestration

**Files:**
- Modify: `ollama_queue/scanner.py`
- Modify: `tests/test_scanner.py`

**Step 1: Write the failing test**

```python
# append to tests/test_scanner.py
from ollama_queue.scanner import run_scan

def test_run_scan_returns_merged_consumers(tmp_path):
    db_path = str(tmp_path / "test.db")
    from ollama_queue.db import Database
    db = Database(db_path)
    db.initialize()

    env = tmp_path / "myproject" / ".env"
    env.parent.mkdir()
    env.write_text("OLLAMA_HOST=localhost:11434\n")

    with patch("ollama_queue.scanner.live_scan", return_value=[]):
        results = run_scan(db, search_dirs=[str(tmp_path)])

    assert len(results) == 1
    assert results[0]["type"] == "env_file"
    assert results[0]["streaming_confirmed"] is False
    assert results[0]["is_managed_job"] is False

def test_run_scan_persists_to_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    from ollama_queue.db import Database
    db = Database(db_path)
    db.initialize()

    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\n")

    with patch("ollama_queue.scanner.live_scan", return_value=[]):
        run_scan(db, search_dirs=[str(tmp_path)])

    consumers = db.list_consumers()
    assert len(consumers) == 1
    assert consumers[0]["status"] == "discovered"
```

**Step 2: Run to verify fails**

```bash
python3 -m pytest tests/test_scanner.py::test_run_scan_returns_merged_consumers -v
```
Expected: FAIL — `ImportError: cannot import name 'run_scan'`

**Step 3: Add `run_scan` orchestration**

```python
def run_scan(db, search_dirs: list[str] | None = None) -> list[dict]:
    """Run all 4 phases and persist results to DB. Returns enriched consumers."""
    plat = detect_platform()
    live = live_scan(plat)
    static = static_scan(search_dirs)

    # Merge: static entries keyed by patch_path, live by name
    seen: dict[str, dict] = {}
    for c in static:
        key = c.get("patch_path") or c["name"]
        seen[key] = {**c, "platform": plat}
    for c in live:
        key = c["name"]
        if key not in seen:
            seen[key] = {**c, "platform": plat, "detected_at": int(time.time())}
        seen[key]["last_live_seen"] = c.get("last_live_seen")

    results = []
    for consumer in seen.values():
        source_dir = None
        if "patch_path" in consumer:
            source_dir = str(pathlib.Path(consumer["patch_path"]).parent)

        streaming = stream_check(source_dir)
        is_deadlock = deadlock_check(
            consumer.get("name", ""), consumer.get("cmdline", ""), db
        )

        consumer.update({
            **streaming,
            "is_managed_job": is_deadlock,
            "source_label": _make_source_label(consumer["name"]),
            "detected_at": consumer.get("detected_at", int(time.time())),
        })
        consumer.setdefault("type", "unknown")
        consumer.setdefault("platform", plat)

        db.upsert_consumer(consumer)
        results.append(consumer)

    return results


def _make_source_label(name: str) -> str:
    """Generate a queue _source label from service/process name."""
    label = re.sub(r'\.service$', '', name)
    label = re.sub(r'[^a-z0-9-]', '-', label.lower())
    return label.strip("-")[:32]
```

**Step 4: Run all scanner tests**

```bash
python3 -m pytest tests/test_scanner.py -v
```
Expected: 17 PASS

**Step 5: Commit**

```bash
git add ollama_queue/scanner.py tests/test_scanner.py
git commit -m "feat: scanner run_scan orchestration — all 4 phases + DB persistence"
```

---

## Batch 2: Patcher

### Task 6: Add ruamel.yaml + tomlkit dependencies

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add dependencies**

In `pyproject.toml`, update `dependencies`:

```toml
dependencies = [
    "click>=8.1.0",
    "croniter>=1.4.0",
    "fastapi>=0.109.0",
    "uvicorn[standard]>=0.27.0",
    "ruamel.yaml>=0.18.0",
    "tomlkit>=0.12.0",
]
```

**Step 2: Install**

```bash
cd ~/Documents/projects/ollama-queue
pip install ruamel.yaml tomlkit
```

**Step 3: Verify import**

```bash
python3 -c "import ruamel.yaml; import tomlkit; print('OK')"
```
Expected: `OK`

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add ruamel.yaml and tomlkit dependencies"
```

---

### Task 7: Patcher — systemd + .env

**Files:**
- Create: `ollama_queue/patcher.py`
- Create: `tests/test_patcher.py`

**Step 1: Write the failing tests**

```python
# tests/test_patcher.py
import pathlib
import pytest
from ollama_queue.patcher import patch_consumer, revert_consumer

@pytest.fixture
def systemd_unit(tmp_path):
    unit = tmp_path / "aria.service"
    unit.write_text(
        "[Unit]\nDescription=ARIA\n\n"
        "[Service]\nExecStart=/usr/bin/aria\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    return unit

@pytest.fixture
def env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\nOTHER_VAR=hello\n")
    return env

def test_patch_systemd_injects_env(systemd_unit, monkeypatch):
    monkeypatch.setattr("ollama_queue.patcher._reload_systemd", lambda: None)
    monkeypatch.setattr("ollama_queue.patcher._restart_service", lambda name: None)
    consumer = {
        "name": "aria.service",
        "type": "systemd",
        "patch_path": str(systemd_unit),
        "restart_policy": "immediate",
    }
    result = patch_consumer(consumer)
    assert result["patch_applied"] is True
    text = systemd_unit.read_text()
    assert 'Environment="OLLAMA_HOST=localhost:7683"' in text

def test_patch_systemd_creates_backup(systemd_unit, monkeypatch):
    monkeypatch.setattr("ollama_queue.patcher._reload_systemd", lambda: None)
    monkeypatch.setattr("ollama_queue.patcher._restart_service", lambda name: None)
    consumer = {"name": "aria.service", "type": "systemd",
                 "patch_path": str(systemd_unit), "restart_policy": "deferred"}
    patch_consumer(consumer)
    bak = pathlib.Path(str(systemd_unit) + ".ollama-queue.bak")
    assert bak.exists()

def test_revert_systemd_restores_original(systemd_unit, monkeypatch):
    monkeypatch.setattr("ollama_queue.patcher._reload_systemd", lambda: None)
    monkeypatch.setattr("ollama_queue.patcher._restart_service", lambda name: None)
    consumer = {"name": "aria.service", "type": "systemd",
                 "patch_path": str(systemd_unit), "restart_policy": "deferred"}
    original = systemd_unit.read_text()
    patch_consumer(consumer)
    revert_consumer(consumer)
    assert systemd_unit.read_text() == original

def test_patch_env_file_replaces_host(env_file, monkeypatch):
    consumer = {"name": "proj/.env", "type": "env_file",
                 "patch_path": str(env_file), "restart_policy": "deferred"}
    result = patch_consumer(consumer)
    assert result["patch_applied"] is True
    text = env_file.read_text()
    assert "OLLAMA_HOST=localhost:7683" in text
    assert "OTHER_VAR=hello" in text

def test_patch_env_file_appends_if_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER_VAR=hello\n")
    consumer = {"name": "proj/.env", "type": "env_file",
                 "patch_path": str(env), "restart_policy": "deferred"}
    patch_consumer(consumer)
    assert "OLLAMA_HOST=localhost:7683" in env.read_text()
```

**Step 2: Run to verify fails**

```bash
python3 -m pytest tests/test_patcher.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'ollama_queue.patcher'`

**Step 3: Create `ollama_queue/patcher.py`**

```python
"""Patch consumer services to route Ollama calls through port 7683."""

from __future__ import annotations

import logging
import pathlib
import re
import shutil
import subprocess

_log = logging.getLogger(__name__)
_QUEUE_HOST = "localhost:7683"
_BAK_SUFFIX = ".ollama-queue.bak"


def patch_consumer(consumer: dict) -> dict:
    """Apply env-var patch for consumer. Returns updated consumer dict."""
    patch_path = consumer.get("patch_path")
    if not patch_path:
        return {**consumer, "patch_type": "manual_snippet",
                "patch_snippet": f"export OLLAMA_HOST={_QUEUE_HOST}",
                "patch_applied": False}

    path = pathlib.Path(patch_path)
    _backup(path)

    ctype = consumer.get("type", "unknown")
    if ctype == "systemd":
        _patch_systemd(path)
        if consumer.get("restart_policy") == "immediate":
            _reload_systemd()
            _restart_service(consumer["name"])
    elif ctype == "env_file":
        _patch_env(path)
    elif ctype == "config_yaml":
        _patch_yaml(path)
    elif ctype == "config_toml":
        _patch_toml(path)
    else:
        # Unknown — generate snippet
        return {**consumer, "patch_type": "manual_snippet",
                "patch_snippet": f"export OLLAMA_HOST={_QUEUE_HOST}",
                "patch_applied": False}

    return {**consumer, "patch_type": ctype, "patch_applied": True,
            "status": "pending_restart" if consumer.get("restart_policy") == "deferred" else "patched"}


def revert_consumer(consumer: dict) -> None:
    """Restore backup. Restarts service if it was restarted."""
    patch_path = consumer.get("patch_path")
    if not patch_path:
        return
    path = pathlib.Path(patch_path)
    bak = pathlib.Path(str(path) + _BAK_SUFFIX)
    if bak.exists():
        shutil.copy2(str(bak), str(path))
        bak.unlink()
    if consumer.get("type") == "systemd":
        _reload_systemd()
        if consumer.get("restart_policy") == "immediate":
            _restart_service(consumer["name"])


# ── Internal helpers ──────────────────────────────────────────────────────────

def _backup(path: pathlib.Path) -> None:
    bak = pathlib.Path(str(path) + _BAK_SUFFIX)
    if not bak.exists():
        shutil.copy2(str(path), str(bak))


def _patch_systemd(path: pathlib.Path) -> None:
    text = path.read_text()
    inject = f'Environment="OLLAMA_HOST={_QUEUE_HOST}"'
    if inject in text:
        return
    # Insert after [Service] header
    text = re.sub(
        r'(\[Service\]\n)',
        f'\\1{inject}\n',
        text,
        count=1,
    )
    path.write_text(text)


def _patch_env(path: pathlib.Path) -> None:
    text = path.read_text()
    new_line = f"OLLAMA_HOST={_QUEUE_HOST}"
    if re.search(r'^OLLAMA_HOST\s*=', text, re.MULTILINE):
        text = re.sub(r'^OLLAMA_HOST\s*=.*$', new_line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"
    path.write_text(text)


def _patch_yaml(path: pathlib.Path) -> None:
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(path) as f:
        data = yaml.load(f)
    if data is None:
        data = {}
    # Try common key patterns
    if "ollama" in data and isinstance(data["ollama"], dict):
        data["ollama"]["host"] = _QUEUE_HOST
    elif "base_url" in data:
        data["base_url"] = f"http://{_QUEUE_HOST}"
    else:
        data.setdefault("ollama", {})["host"] = _QUEUE_HOST
    with open(path, "w") as f:
        yaml.dump(data, f)


def _patch_toml(path: pathlib.Path) -> None:
    import tomlkit
    text = path.read_text()
    data = tomlkit.loads(text)
    if "ollama" in data and isinstance(data["ollama"], dict):
        data["ollama"]["host"] = _QUEUE_HOST
    else:
        data.setdefault("ollama", tomlkit.table())["host"] = _QUEUE_HOST
    path.write_text(tomlkit.dumps(data))


def _reload_systemd() -> None:
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       capture_output=True, timeout=10)
    except Exception:
        _log.warning("daemon-reload failed", exc_info=True)


def _restart_service(name: str) -> None:
    try:
        subprocess.run(["systemctl", "--user", "restart", name],
                       capture_output=True, timeout=30)
    except Exception:
        _log.warning("restart %s failed", name, exc_info=True)
```

**Step 4: Run to verify passes**

```bash
python3 -m pytest tests/test_patcher.py -v
```
Expected: 7 PASS

**Step 5: Commit**

```bash
git add ollama_queue/patcher.py tests/test_patcher.py
git commit -m "feat: patcher — systemd + env_file patch and revert"
```

---

### Task 8: Patcher — health validator

**Files:**
- Modify: `ollama_queue/patcher.py`
- Modify: `tests/test_patcher.py`

**Step 1: Write the failing test**

```python
# append to tests/test_patcher.py
from unittest.mock import patch as mock_patch
from ollama_queue.patcher import check_health

def test_health_confirmed_when_both_signals_clear(tmp_path):
    db_path = str(tmp_path / "test.db")
    from ollama_queue.db import Database
    db = Database(db_path)
    db.initialize()
    consumer_id = db.upsert_consumer({
        "name": "aria", "type": "systemd", "platform": "linux",
        "source_label": "aria", "detected_at": 0, "onboarded_at": 1,
        "request_count": 5,
    })
    consumer = db.get_consumer(consumer_id)

    with mock_patch("ollama_queue.patcher._port_has_process", side_effect=[False, True]):
        result = check_health(consumer, db, plat="linux")

    assert result["old_port_clear"] is True
    assert result["new_port_active"] is True
    assert result["status"] == "confirmed"

def test_health_partial_when_only_old_port_clear(tmp_path):
    db_path = str(tmp_path / "test.db")
    from ollama_queue.db import Database
    db = Database(db_path)
    db.initialize()
    consumer_id = db.upsert_consumer({
        "name": "aria", "type": "systemd", "platform": "linux",
        "source_label": "aria", "detected_at": 0,
    })
    consumer = db.get_consumer(consumer_id)

    with mock_patch("ollama_queue.patcher._port_has_process", side_effect=[False, False]):
        result = check_health(consumer, db, plat="linux")

    assert result["old_port_clear"] is True
    assert result["new_port_active"] is False
    assert result["status"] == "partial"
```

**Step 2: Run to verify fails**

```bash
python3 -m pytest tests/test_patcher.py::test_health_confirmed_when_both_signals_clear -v
```
Expected: FAIL

**Step 3: Add `check_health` to `patcher.py`**

```python
def check_health(consumer: dict, db, plat: str | None = None) -> dict:
    """Two-signal post-patch health check. Updates DB health_status."""
    import platform as _platform
    import time
    plat = plat or ("linux" if _platform.system() == "Linux"
                    else "macos" if _platform.system() == "Darwin" else "windows")

    name = consumer.get("name", "")
    old_clear = not _port_has_process("11434", name, plat)
    new_active = _port_has_process("7683", name, plat)

    # Third signal: request seen after onboard
    request_seen = False
    if consumer.get("onboarded_at") and consumer.get("request_count", 0) > 0:
        request_seen = True

    if old_clear and (new_active or request_seen):
        status = "confirmed"
    elif old_clear:
        status = "partial"
    else:
        status = "failed"

    db.update_consumer(consumer["id"], health_status=status,
                       health_checked_at=int(time.time()))
    return {"old_port_clear": old_clear, "new_port_active": new_active,
            "request_seen": request_seen, "status": status}


def _port_has_process(port: str, name: str, plat: str) -> bool:
    """Check if a named process has a connection on the given port."""
    try:
        if plat == "linux":
            result = subprocess.run(["ss", "-tp"], capture_output=True, text=True, timeout=5)
            return f":{port}" in result.stdout and name.split(".")[0] in result.stdout
        if plat == "macos":
            result = subprocess.run(["lsof", f"-i:{port}"], capture_output=True, text=True, timeout=5)
            return name.split(".")[0] in result.stdout
        return False
    except Exception:
        return False
```

**Step 4: Run to verify passes**

```bash
python3 -m pytest tests/test_patcher.py -v
```
Expected: 9 PASS

**Step 5: Commit**

```bash
git add ollama_queue/patcher.py tests/test_patcher.py
git commit -m "feat: patcher health validator — two-signal post-patch check"
```

---

## Batch 3: API Endpoints

### Task 9: GET /api/consumers + POST /api/consumers/scan

**Files:**
- Modify: `ollama_queue/api.py`
- Create: `tests/test_consumers_api.py`

**Step 1: Write the failing tests**

```python
# tests/test_consumers_api.py
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from ollama_queue.api import create_app
from ollama_queue.db import Database

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.initialize()
    return d

@pytest.fixture
def client(db):
    return TestClient(create_app(db))

def test_list_consumers_empty(client):
    resp = client.get("/api/consumers")
    assert resp.status_code == 200
    assert resp.json() == []

def test_list_consumers_returns_rows(client, db):
    import time
    db.upsert_consumer({"name": "aria.service", "type": "systemd",
                         "platform": "linux", "source_label": "aria",
                         "detected_at": int(time.time())})
    resp = client.get("/api/consumers")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["name"] == "aria.service"

def test_scan_triggers_and_returns_consumers(client):
    with patch("ollama_queue.api.run_scan", return_value=[
        {"name": "aria.service", "type": "systemd", "platform": "linux",
         "source_label": "aria", "is_managed_job": False,
         "streaming_confirmed": False, "streaming_suspect": False}
    ]):
        resp = client.post("/api/consumers/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "aria.service"
```

**Step 2: Run to verify fails**

```bash
python3 -m pytest tests/test_consumers_api.py -v
```
Expected: FAIL — endpoints not found

**Step 3: Add endpoints to `api.py`**

At top of `api.py`, add imports:

```python
from ollama_queue.scanner import run_scan
from ollama_queue.patcher import patch_consumer, revert_consumer, check_health
```

Add endpoints (near the end, before `if __name__ == "__main__"`):

```python
@app.get("/api/consumers")
def list_consumers():
    return db.list_consumers()


@app.post("/api/consumers/scan")
def scan_consumers():
    import threading as _threading
    # Run scan in calling thread (fast enough for sync endpoint)
    results = run_scan(db)
    return results
```

**Step 4: Run to verify passes**

```bash
python3 -m pytest tests/test_consumers_api.py -v
```
Expected: 3 PASS

**Step 5: Commit**

```bash
git add ollama_queue/api.py tests/test_consumers_api.py
git commit -m "feat: GET /api/consumers and POST /api/consumers/scan endpoints"
```

---

### Task 10: POST /api/consumers/{id}/include — with all guards

**Files:**
- Modify: `ollama_queue/api.py`
- Modify: `tests/test_consumers_api.py`

**Step 1: Write the failing tests**

```python
# append to tests/test_consumers_api.py
import time

def _seed_consumer(db, **overrides):
    defaults = {"name": "aria.service", "type": "systemd", "platform": "linux",
                "source_label": "aria", "detected_at": int(time.time())}
    defaults.update(overrides)
    return db.upsert_consumer(defaults)

def test_include_managed_job_returns_409(client, db):
    cid = _seed_consumer(db, is_managed_job=1)
    resp = client.post(f"/api/consumers/{cid}/include", json={"restart_policy": "deferred"})
    assert resp.status_code == 409
    assert "deadlock" in resp.json()["detail"].lower()

def test_include_streaming_confirmed_without_override_returns_422(client, db):
    cid = _seed_consumer(db, streaming_confirmed=1)
    resp = client.post(f"/api/consumers/{cid}/include",
                       json={"restart_policy": "deferred", "force_streaming_override": False})
    assert resp.status_code == 422
    assert "stream" in resp.json()["detail"].lower()

def test_include_streaming_confirmed_with_override_proceeds(client, db, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\n")
    cid = _seed_consumer(db, streaming_confirmed=1,
                          patch_path=str(env), type="env_file")
    with patch("ollama_queue.api.patch_consumer",
               return_value={"patch_applied": True, "status": "patched", "patch_type": "env_file"}):
        resp = client.post(f"/api/consumers/{cid}/include",
                           json={"restart_policy": "deferred", "force_streaming_override": True})
    assert resp.status_code == 200

def test_include_system_path_without_confirm_returns_422(client, db):
    cid = _seed_consumer(db, patch_path="/etc/systemd/system/aria.service")
    resp = client.post(f"/api/consumers/{cid}/include",
                       json={"restart_policy": "deferred", "system_confirm": False})
    assert resp.status_code == 422
    assert "system" in resp.json()["detail"].lower()

def test_include_deferred_sets_pending_restart(client, db, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\n")
    cid = _seed_consumer(db, patch_path=str(env), type="env_file")
    with patch("ollama_queue.api.patch_consumer",
               return_value={"patch_applied": True, "status": "pending_restart", "patch_type": "env_file"}):
        resp = client.post(f"/api/consumers/{cid}/include",
                           json={"restart_policy": "deferred"})
    assert resp.status_code == 200
    assert db.get_consumer(cid)["status"] == "pending_restart"
```

**Step 2: Run to verify fails**

```bash
python3 -m pytest tests/test_consumers_api.py::test_include_managed_job_returns_409 -v
```
Expected: FAIL

**Step 3: Add include endpoint to `api.py`**

```python
class ConsumerIncludeRequest(BaseModel):
    restart_policy: str = "deferred"
    force_streaming_override: bool = False
    system_confirm: bool = False


@app.post("/api/consumers/{consumer_id}/include")
def include_consumer(consumer_id: int, body: ConsumerIncludeRequest):
    import threading as _threading
    consumer = db.get_consumer(consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="Consumer not found")

    # Guard 1: deadlock
    if consumer.get("is_managed_job"):
        raise HTTPException(status_code=409,
            detail="Managed queue job — including would cause a deadlock (Lesson #1733)")

    # Guard 2: Windows no auto-patch
    if consumer.get("platform") == "windows":
        raise HTTPException(status_code=422,
            detail="Auto-patch not supported on Windows. Use the generated snippet.")

    # Guard 3: streaming
    if consumer.get("streaming_confirmed") and not body.force_streaming_override:
        raise HTTPException(status_code=422,
            detail="Streaming detected. Proxy forces stream=False. Send force_streaming_override=true to confirm.")

    # Guard 4: system path
    patch_path = consumer.get("patch_path", "")
    if patch_path.startswith("/etc/systemd/system") and not body.system_confirm:
        raise HTTPException(status_code=422,
            detail="System path requires explicit confirmation. Send system_confirm=true.")

    result = patch_consumer({**consumer, "restart_policy": body.restart_policy})
    db.update_consumer(consumer_id, status=result.get("status", "patched"),
                       patch_applied=1 if result.get("patch_applied") else 0,
                       patch_type=result.get("patch_type"),
                       patch_snippet=result.get("patch_snippet"),
                       onboarded_at=int(__import__("time").time()))

    if result.get("status") == "patched":
        _threading.Thread(
            target=check_health,
            args=({**consumer, "id": consumer_id}, db),
            daemon=True,
        ).start()

    return db.get_consumer(consumer_id)
```

**Step 4: Run all consumer API tests**

```bash
python3 -m pytest tests/test_consumers_api.py -v
```
Expected: 8 PASS

**Step 5: Commit**

```bash
git add ollama_queue/api.py tests/test_consumers_api.py
git commit -m "feat: POST /api/consumers/{id}/include with all safety guards"
```

---

### Task 11: ignore, revert, health endpoints

**Files:**
- Modify: `ollama_queue/api.py`
- Modify: `tests/test_consumers_api.py`

**Step 1: Write the failing tests**

```python
# append to tests/test_consumers_api.py
def test_ignore_sets_status(client, db):
    cid = _seed_consumer(db)
    resp = client.post(f"/api/consumers/{cid}/ignore")
    assert resp.status_code == 200
    assert db.get_consumer(cid)["status"] == "ignored"

def test_revert_calls_revert_and_resets_status(client, db):
    cid = _seed_consumer(db, status="patched", patch_applied=1)
    with patch("ollama_queue.api.revert_consumer"):
        resp = client.post(f"/api/consumers/{cid}/revert")
    assert resp.status_code == 200
    assert db.get_consumer(cid)["status"] == "discovered"

def test_health_endpoint_returns_status(client, db):
    cid = _seed_consumer(db, status="patched")
    with patch("ollama_queue.api.check_health",
               return_value={"old_port_clear": True, "new_port_active": True,
                             "request_seen": False, "status": "confirmed"}):
        resp = client.get(f"/api/consumers/{cid}/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
```

**Step 2: Run to verify fails**

```bash
python3 -m pytest tests/test_consumers_api.py::test_ignore_sets_status -v
```
Expected: FAIL

**Step 3: Add endpoints**

```python
@app.post("/api/consumers/{consumer_id}/ignore")
def ignore_consumer(consumer_id: int):
    consumer = db.get_consumer(consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="Consumer not found")
    db.update_consumer(consumer_id, status="ignored")
    return db.get_consumer(consumer_id)


@app.post("/api/consumers/{consumer_id}/revert")
def revert_consumer_endpoint(consumer_id: int):
    consumer = db.get_consumer(consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="Consumer not found")
    revert_consumer(consumer)
    db.update_consumer(consumer_id, status="discovered", patch_applied=0,
                       health_status="unknown")
    return db.get_consumer(consumer_id)


@app.get("/api/consumers/{consumer_id}/health")
def consumer_health(consumer_id: int):
    consumer = db.get_consumer(consumer_id)
    if not consumer:
        raise HTTPException(status_code=404, detail="Consumer not found")
    return check_health(consumer, db)
```

**Step 4: Run all consumer API tests**

```bash
python3 -m pytest tests/test_consumers_api.py -v
```
Expected: 11 PASS

**Step 5: Run full test suite**

```bash
python3 -m pytest --timeout=120 -x -q
```
Expected: all existing + new tests pass

**Step 6: Commit**

```bash
git add ollama_queue/api.py tests/test_consumers_api.py
git commit -m "feat: ignore, revert, health endpoints for consumers"
```

---

## Batch 4: SPA — Consumers Tab

### Task 12: store.js — consumer state + API calls

**Files:**
- Modify: `src/store.js`

**Step 1: Add to `store.js`** (after existing signal declarations):

```javascript
// Consumers
export const consumers = signal([]);
export const consumersScanning = signal(false);

export async function fetchConsumers() {
  try {
    const res = await fetch(`${API}/consumers`);
    if (res.ok) consumers.value = await res.json();
  } catch (e) { console.error('fetchConsumers failed:', e); }
}

export async function scanConsumers() {
  consumersScanning.value = true;
  try {
    const res = await fetch(`${API}/consumers/scan`, { method: 'POST' });
    if (res.ok) consumers.value = await res.json();
  } finally {
    consumersScanning.value = false;
  }
}

export async function includeConsumer(id, opts = {}) {
  const res = await fetch(`${API}/consumers/${id}/include`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ restart_policy: 'deferred', ...opts }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  await fetchConsumers();
  return res.json();
}

export async function ignoreConsumer(id) {
  const res = await fetch(`${API}/consumers/${id}/ignore`, { method: 'POST' });
  if (res.ok) await fetchConsumers();
}

export async function revertConsumer(id) {
  const res = await fetch(`${API}/consumers/${id}/revert`, { method: 'POST' });
  if (res.ok) await fetchConsumers();
}

export async function fetchConsumerHealth(id) {
  const res = await fetch(`${API}/consumers/${id}/health`);
  if (res.ok) return res.json();
  return null;
}
```

**Step 2: Verify no syntax errors**

```bash
cd ~/Documents/projects/ollama-queue
node --input-type=module < src/store.js 2>&1 | head -5
```
Expected: no errors (or just ESM import warnings — acceptable)

**Step 3: Commit**

```bash
git add src/store.js
git commit -m "feat: consumers store signals and API helpers"
```

---

### Task 13: ConsumerRow component

**Files:**
- Create: `src/components/consumers/ConsumerRow.jsx`

**Step 1: Create component**

```jsx
// src/components/consumers/ConsumerRow.jsx
import { useState } from 'preact/hooks';
import { useActionFeedback } from '../../hooks/useActionFeedback.js';
import { includeConsumer, ignoreConsumer, revertConsumer } from '../../store.js';

const STATUS_BADGE = {
  discovered:      { cls: 'badge--neutral',  label: 'Discovered' },
  included:        { cls: 'badge--info',     label: 'Included' },
  pending_restart: { cls: 'badge--warning',  label: 'Pending restart' },
  patched:         { cls: 'badge--success',  label: 'Patched' },
  ignored:         { cls: 'badge--neutral',  label: 'Ignored' },
  error:           { cls: 'badge--error',    label: 'Error' },
};

const HEALTH_BADGE = {
  unknown:   { cls: '',               label: '' },
  verifying: { cls: 'badge--info',    label: '⏳ Verifying' },
  confirmed: { cls: 'badge--success', label: '✓ Confirmed' },
  partial:   { cls: 'badge--warning', label: '⚠ Partial' },
  failed:    { cls: 'badge--error',   label: '✗ Failed' },
};

export function ConsumerRow({ consumer }) {
  const [fb, run] = useActionFeedback();
  const [showStreamingConfirm, setShowStreamingConfirm] = useState(false);
  const [restartPolicy, setRestartPolicy] = useState('deferred');

  const status = STATUS_BADGE[consumer.status] || STATUS_BADGE.discovered;
  const health = HEALTH_BADGE[consumer.health_status] || HEALTH_BADGE.unknown;

  async function doInclude(opts = {}) {
    await run(
      'Including…',
      () => includeConsumer(consumer.id, { restart_policy: restartPolicy, ...opts }),
      c => `Included — ${c.patch_type || 'snippet generated'}`,
    );
  }

  function handleInclude() {
    if (consumer.is_managed_job) return; // button disabled
    if (consumer.streaming_confirmed && !showStreamingConfirm) {
      setShowStreamingConfirm(true);
      return;
    }
    doInclude({ force_streaming_override: showStreamingConfirm });
  }

  const isDisabled = consumer.is_managed_job || fb.phase === 'loading';
  const streamingLabel = consumer.streaming_confirmed
    ? '⚠ Streaming confirmed'
    : consumer.streaming_suspect
    ? '⚠ Streaming suspected'
    : null;

  return (
    <tr class={`consumer-row consumer-row--${consumer.status}`}>
      <td>
        <span class="consumer-name">{consumer.name}</span>
        {consumer.is_managed_job && <span class="badge badge--lock" title="Queue job — cannot include">🔒 Queue job</span>}
        {consumer.patch_path?.startsWith('/etc/systemd') && <span class="badge badge--system">🛡 System path</span>}
      </td>
      <td>{consumer.type}</td>
      <td>
        {streamingLabel
          ? <span class={`badge ${consumer.streaming_confirmed ? 'badge--warning' : 'badge--caution'}`}>{streamingLabel}</span>
          : <span class="badge badge--ok">Safe</span>}
      </td>
      <td>{consumer.request_count ?? 0}</td>
      <td>{consumer.last_seen ? new Date(consumer.last_seen * 1000).toLocaleTimeString() : '—'}</td>
      <td>
        <span class={`badge ${status.cls}`}>{status.label}</span>
        {health.label && <span class={`badge ${health.cls}`}>{health.label}</span>}
      </td>
      <td class="consumer-actions">
        {showStreamingConfirm && (
          <div class="streaming-confirm">
            <span>Proxy forces stream=False. Streaming responses will break.</span>
            <button onClick={() => doInclude({ force_streaming_override: true })}>Confirm include</button>
            <button onClick={() => setShowStreamingConfirm(false)}>Cancel</button>
          </div>
        )}
        {!showStreamingConfirm && (consumer.status === 'discovered' || consumer.status === 'ignored') && (
          <>
            <select value={restartPolicy} onChange={e => setRestartPolicy(e.target.value)}
                    disabled={isDisabled}>
              <option value="deferred">Apply on next restart</option>
              <option value="immediate">Apply now (restarts service)</option>
            </select>
            <button
              class={`action-fb--${fb.phase}`}
              onClick={handleInclude}
              disabled={isDisabled}
              title={consumer.is_managed_job ? 'Cannot include managed queue jobs' : undefined}
            >
              {fb.phase === 'loading' ? fb.msg : 'Include'}
            </button>
            {consumer.status !== 'ignored' && (
              <button onClick={() => run('Ignoring…', () => ignoreConsumer(consumer.id), 'Ignored')}>
                Ignore
              </button>
            )}
          </>
        )}
        {consumer.status === 'pending_restart' && (
          <button onClick={() => run('Reverting…', () => revertConsumer(consumer.id), 'Reverted')}>
            Revert
          </button>
        )}
        {consumer.status === 'patched' && (
          <>
            <button onClick={() => run('Reverting…', () => revertConsumer(consumer.id), 'Reverted')}>
              Revert
            </button>
          </>
        )}
        {consumer.patch_snippet && (
          <details>
            <summary>Manual snippet</summary>
            <pre class="snippet">{consumer.patch_snippet}</pre>
            <button onClick={() => navigator.clipboard.writeText(consumer.patch_snippet)}>Copy</button>
          </details>
        )}
        {fb.phase === 'error' && <span class="action-fb--error">{fb.msg}</span>}
      </td>
    </tr>
  );
}
```

**Step 2: Verify no import errors**

```bash
cd ~/Documents/projects/ollama-queue && npm run build 2>&1 | tail -10
```
Expected: build succeeds or only unrelated warnings

**Step 3: Commit**

```bash
git add src/components/consumers/ConsumerRow.jsx
git commit -m "feat: ConsumerRow component with badges and include/ignore/revert actions"
```

---

### Task 14: Consumers.jsx page + first-run wizard

**Files:**
- Create: `src/pages/Consumers.jsx`

**Step 1: Create page**

```jsx
// src/pages/Consumers.jsx
import { useEffect } from 'preact/hooks';
import { useActionFeedback } from '../hooks/useActionFeedback.js';
import {
  consumers, consumersScanning,
  fetchConsumers, scanConsumers,
} from '../store.js';
import { ConsumerRow } from '../components/consumers/ConsumerRow.jsx';

export function Consumers() {
  useEffect(() => { fetchConsumers(); }, []);

  const [scanFb, runScan] = useActionFeedback();
  const list = consumers.value;
  const newlyDiscovered = list.filter(c => c.status === 'discovered');
  const showWizard = newlyDiscovered.length > 0 && list.every(c => c.status === 'discovered');

  return (
    <div class="consumers-page">
      <div class="consumers-header">
        <h2>Consumers</h2>
        <button
          class={`action-fb--${scanFb.phase}`}
          onClick={() => runScan('Scanning…', scanConsumers, `Found ${consumers.value.length} consumer(s)`)}
          disabled={consumersScanning.value || scanFb.phase === 'loading'}
        >
          {scanFb.phase === 'loading' ? 'Scanning…' : 'Scan Now'}
        </button>
      </div>

      {showWizard && (
        <div class="consumers-wizard-banner">
          <strong>{newlyDiscovered.length} service{newlyDiscovered.length > 1 ? 's' : ''} detected calling Ollama directly.</strong>
          {' '}Review below and include or ignore each one.
        </div>
      )}

      {list.length === 0 ? (
        <div class="consumers-empty">
          <p>No Ollama consumers detected. Click <strong>Scan Now</strong> to search.</p>
        </div>
      ) : (
        <table class="consumers-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Streaming?</th>
              <th>Requests</th>
              <th>Last Seen</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {list.map(c => <ConsumerRow key={c.id} consumer={c} />)}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add src/pages/Consumers.jsx
git commit -m "feat: Consumers page with first-run wizard and table"
```

---

### Task 15: Wire Consumers tab into App.jsx + basic CSS

**Files:**
- Modify: `src/App.jsx` (or wherever tabs are defined — check existing tab pattern)
- Modify: `src/index.css`

**Step 1: Find the tab registration pattern**

```bash
grep -n "Schedule\|Eval\|DLQ\|tab" ~/Documents/projects/ollama-queue/src/App.jsx | head -20
```

**Step 2: Add Consumers tab** (follow exact pattern of existing tabs)

Import at top:
```javascript
import { Consumers } from './pages/Consumers.jsx';
```

Add to tabs array/object (same pattern as Schedule, Eval, DLQ):
```javascript
{ id: 'consumers', label: 'Consumers', component: Consumers },
```

**Step 3: Add minimal CSS to `index.css`**

```css
/* Consumers tab */
.consumers-page { padding: 1rem; }
.consumers-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
.consumers-wizard-banner {
  background: var(--color-warning-bg, #fff3cd);
  border: 1px solid var(--color-warning, #ffc107);
  border-radius: 4px;
  padding: 0.75rem 1rem;
  margin-bottom: 1rem;
}
.consumers-table { width: 100%; border-collapse: collapse; }
.consumers-table th, .consumers-table td { padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid var(--color-border, #dee2e6); }
.consumers-empty { color: var(--color-muted, #6c757d); padding: 2rem; text-align: center; }
.consumer-actions { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.streaming-confirm { background: var(--color-warning-bg, #fff3cd); padding: 0.5rem; border-radius: 4px; }
.snippet { font-family: monospace; background: var(--color-code-bg, #f8f9fa); padding: 0.5rem; border-radius: 4px; }
.badge--lock { background: #dc3545; color: white; }
.badge--system { background: #6c757d; color: white; }
.badge--caution { background: #fd7e14; color: white; }
.badge--ok { background: #d1e7dd; color: #0a3622; }
```

**Step 4: Build and verify**

```bash
cd ~/Documents/projects/ollama-queue && npm run build 2>&1 | tail -5
```
Expected: build succeeds

**Step 5: Run full test suite**

```bash
python3 -m pytest --timeout=120 -x -q
```
Expected: all tests pass

**Step 6: Commit**

```bash
git add src/App.jsx src/index.css
git commit -m "feat: wire Consumers tab into App + CSS"
```

---

## Batch 5: Integration + Hardening

### Task 16: Auto-scan on startup + request_count tracking

**Files:**
- Modify: `ollama_queue/api.py`
- Modify: `ollama_queue/db.py`

**Step 1: Auto-scan on startup**

In `create_app()` or the startup event handler (find existing `@app.on_event("startup")` or lifespan):

```python
# Add to startup:
import threading as _threading
_threading.Thread(target=lambda: run_scan(db), daemon=True).start()
```

**Step 2: Increment request_count when proxy request matches a consumer source_label**

In `_proxy_ollama_request()` (api.py ~line 400), after `source = body.pop("_source", "proxy")`:

```python
# Track request against known consumer
try:
    rows = db.list_consumers()
    for row in rows:
        if row.get("source_label") == source and row.get("status") in ("patched", "included"):
            db.update_consumer(row["id"], request_count=(row["request_count"] or 0) + 1,
                               last_seen=int(__import__("time").time()))
            break
except Exception:
    pass  # Never block proxy for tracking failure
```

**Step 3: Run full suite**

```bash
python3 -m pytest --timeout=120 -x -q
```
Expected: all tests pass

**Step 4: Commit**

```bash
git add ollama_queue/api.py ollama_queue/db.py
git commit -m "feat: auto-scan on startup + request_count tracking per consumer"
```

---

### Task 17: Final verification

**Step 1: Run complete test suite**

```bash
cd ~/Documents/projects/ollama-queue
python3 -m pytest --timeout=120 -q
```
Expected: all tests pass, count printed at end

**Step 2: Build SPA**

```bash
npm run build
```
Expected: no errors

**Step 3: Smoke test API (if service running)**

```bash
curl -s http://localhost:7683/api/consumers | python3 -m json.tool | head -20
curl -s -X POST http://localhost:7683/api/consumers/scan | python3 -m json.tool | head -20
```

**Step 4: Final commit**

```bash
git add -p  # stage any remaining changes
git commit -m "feat: consumer detection & onboarding — complete implementation"
```
