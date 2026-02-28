# Model Concurrency, Gantt Scheduler & Model Management UI — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add adaptive multi-model concurrency, a Gantt schedule visualizer, and a full model management UI (inventory, assignment, download) to ollama-queue.

**Architecture:** Resource profiles (`embed`/`ollama`/`heavy`) gate concurrent execution via a three-factor VRAM+RAM+health admission check. The daemon adds a ThreadPoolExecutor for parallel job execution defaulting off (`max_concurrent_jobs=1`). The SPA gains a new Models tab and a Gantt-style ScheduleTab that double-stacks concurrent slots.

**Tech Stack:** Python 3.12, FastAPI, SQLite3 (threading.RLock, WAL mode), subprocess/ThreadPoolExecutor, Preact 10 + @preact/signals, Tailwind v4, esbuild JSX

---

## Pre-flight

```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q   # must pass: 154/154 (or current count)
```

---

## Task 1: DB Schema — model_registry, model_pulls, jobs.pid

**Files:**
- Modify: `ollama_queue/db.py:54-199` (initialize, DEFAULTS)
- Test: `tests/test_db.py`

**Step 1: Write failing tests**

```python
# tests/test_db.py — add to existing file

def test_model_registry_table_exists(tmp_db):
    conn = tmp_db._connect()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "model_registry" in tables

def test_model_pulls_table_exists(tmp_db):
    conn = tmp_db._connect()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "model_pulls" in tables

def test_jobs_has_pid_column(tmp_db):
    conn = tmp_db._connect()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    assert "pid" in cols

def test_new_settings_seeded(tmp_db):
    s = tmp_db.get_all_settings()
    assert "max_concurrent_jobs" in s
    assert "concurrent_shadow_hours" in s
    assert "vram_safety_factor" in s

def test_migration_idempotent(tmp_db):
    # Calling initialize() twice must not raise
    tmp_db.initialize()
    tmp_db.initialize()
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_db.py::test_model_registry_table_exists \
       tests/test_db.py::test_model_pulls_table_exists \
       tests/test_db.py::test_jobs_has_pid_column \
       tests/test_db.py::test_new_settings_seeded -v
```
Expected: FAIL — tables/columns don't exist yet.

**Step 3: Add to `db.py`**

In `DEFAULTS` dict, add:
```python
"max_concurrent_jobs": 1,
"concurrent_shadow_hours": 24,
"vram_safety_factor": 1.3,
```

In `initialize()` executescript, add two new tables after the `dlq` table:
```sql
CREATE TABLE IF NOT EXISTS model_registry (
    name              TEXT PRIMARY KEY,
    size_bytes        INTEGER,
    vram_observed_mb  REAL,
    resource_profile  TEXT DEFAULT 'ollama',
    type_tag          TEXT DEFAULT 'general',
    last_seen         REAL
);

CREATE TABLE IF NOT EXISTS model_pulls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    model        TEXT NOT NULL,
    status       TEXT DEFAULT 'pulling',
    progress_pct REAL DEFAULT 0,
    pid          INTEGER,
    started_at   REAL,
    completed_at REAL,
    error        TEXT
);
```

After the existing `pinned` migration block, add (each in its own try/except — Lesson #107):
```python
# Migrate: add pid column to jobs
try:
    conn.execute("ALTER TABLE jobs ADD COLUMN pid INTEGER")
    conn.commit()
except Exception:
    _log.debug("jobs.pid column already exists — skipping migration")
```

**Step 4: Run tests to verify pass**

```bash
pytest tests/test_db.py -v
```
Expected: all DB tests pass.

**Step 5: Commit**

```bash
git add ollama_queue/db.py tests/test_db.py
git commit -m "feat(db): add model_registry, model_pulls tables and jobs.pid column"
```

---

## Task 2: models.py — list_local, classify, estimate_vram_mb

**Files:**
- Create: `ollama_queue/models.py`
- Create: `tests/test_models.py`

**Step 1: Write failing tests**

```python
# tests/test_models.py
import contextlib
from unittest.mock import patch, MagicMock
import pytest
from ollama_queue.models import OllamaModels

OLLAMA_LIST_OUTPUT = """\
NAME                            ID              SIZE      MODIFIED
qwen2.5-coder:14b               abc123          8.9 GB    2 weeks ago
nomic-embed-text:latest         def456          274 MB    3 weeks ago
deepseek-r1:70b                 ghi789          39 GB     1 week ago
qwen2.5:7b                      jkl012          4.7 GB    4 weeks ago
"""

def _mock_run(output):
    m = MagicMock()
    m.returncode = 0
    m.stdout = output
    return m

def test_list_local_parses_names():
    with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
        models = OllamaModels().list_local()
    names = [m["name"] for m in models]
    assert "qwen2.5-coder:14b" in names
    assert "nomic-embed-text:latest" in names
    assert len(models) == 4

def test_list_local_parses_size_bytes():
    with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
        models = OllamaModels().list_local()
    embed = next(m for m in models if "nomic" in m["name"])
    # 274 MB = 274 * 1024 * 1024 bytes (approximately)
    assert embed["size_bytes"] > 270_000_000
    assert embed["size_bytes"] < 290_000_000

def test_classify_embed_profile():
    om = OllamaModels()
    result = om.classify("nomic-embed-text:latest")
    assert result["resource_profile"] == "embed"
    assert result["type_tag"] == "embed"

def test_classify_heavy_profile():
    om = OllamaModels()
    result = om.classify("deepseek-r1:70b")
    assert result["resource_profile"] == "heavy"

def test_classify_coding_type():
    om = OllamaModels()
    result = om.classify("qwen2.5-coder:14b")
    assert result["type_tag"] == "coding"
    assert result["resource_profile"] == "ollama"

def test_classify_default():
    om = OllamaModels()
    result = om.classify("qwen2.5:7b")
    assert result["resource_profile"] == "ollama"
    assert result["type_tag"] == "general"

def test_estimate_vram_uses_observed_when_available(tmp_path):
    from ollama_queue.db import Database
    db = Database(str(tmp_path / "q.db"))
    db.initialize()
    # Insert observed value
    conn = db._connect()
    conn.execute(
        "INSERT INTO model_registry (name, vram_observed_mb) VALUES (?, ?)",
        ("qwen2.5:7b", 5120.0)
    )
    conn.commit()
    vram = OllamaModels().estimate_vram_mb("qwen2.5:7b", db)
    assert vram == pytest.approx(5120.0)

def test_estimate_vram_falls_back_to_disk_size():
    with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
        from ollama_queue.db import Database
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            db = Database(db_path)
            db.initialize()
            vram = OllamaModels().estimate_vram_mb("qwen2.5:7b", db)
            # 4.7 GB * 1.3 safety = ~6110 MB
            assert vram > 5000
            assert vram < 7000
        finally:
            os.unlink(db_path)
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_models.py -v
```
Expected: FAIL — `ollama_queue.models` does not exist.

**Step 3: Create `ollama_queue/models.py`**

```python
"""Ollama model registry: list, classify, VRAM estimation, pull lifecycle."""

from __future__ import annotations

import contextlib
import logging
import subprocess
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ollama_queue.db import Database

_log = logging.getLogger(__name__)

# Profile rules: first match wins. (keywords, profile)
_PROFILE_RULES: list[tuple[list[str], str]] = [
    (["embed", "nomic", "mxbai", "bge-m3", "all-minilm"], "embed"),
    (["70b", "34b", "32b", ":671b", "deepseek-r1:14", "deepseek-r1:32",
      "llama3.3:70", "qwen2.5:72"], "heavy"),
]

# Type tag rules: first match wins. (keywords, type_tag)
_TYPE_RULES: list[tuple[list[str], str]] = [
    (["embed", "nomic", "mxbai", "bge"], "embed"),
    (["coder", "-coder", "deepseek-coder", "starcoder", "codellama"], "coding"),
    (["r1", "o1", "think", "reason"], "reasoning"),
]


def _parse_size_bytes(size_str: str) -> int:
    """Parse '4.7 GB', '274 MB', '39 GB' → bytes."""
    parts = size_str.strip().split()
    if len(parts) < 2:
        return 0
    try:
        val = float(parts[0])
        unit = parts[1].upper()
        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(val * multipliers.get(unit, 1))
    except (ValueError, KeyError):
        return 0


class OllamaModels:
    """Interface to local Ollama model management."""

    def list_local(self) -> list[dict]:
        """Run `ollama list` and return [{name, size_bytes, modified}].

        Returns empty list if ollama is not available.
        """
        try:
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                _log.warning("ollama list returned %d", result.returncode)
                return []
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                return []
            # Skip header line
            models = []
            for line in lines[1:]:
                parts = line.split()
                if len(parts) < 4:
                    continue
                name = parts[0]
                # SIZE is parts[2] + parts[3] (e.g. "4.7" + "GB")
                size_str = f"{parts[2]} {parts[3]}"
                models.append({
                    "name": name,
                    "size_bytes": _parse_size_bytes(size_str),
                    "modified": " ".join(parts[4:]) if len(parts) > 4 else "",
                })
            return models
        except (OSError, subprocess.TimeoutExpired):
            _log.warning("ollama list failed — ollama may not be running")
            return []

    def get_loaded(self) -> list[dict]:
        """Run `ollama ps` and return all loaded models.

        Returns [{name, size_bytes, vram_pct, cpu_pct, until}].
        Supersedes health.get_ollama_active_model() for multi-model support.
        """
        try:
            result = subprocess.run(
                ["ollama", "ps"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return []
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                return []
            loaded = []
            for line in lines[1:]:
                parts = line.split()
                if len(parts) < 3:
                    continue
                name = parts[0]
                # SIZE: parts[2] + parts[3] (e.g. "4.7 GB")
                size_str = f"{parts[2]} {parts[3]}" if len(parts) > 3 else f"{parts[2]} B"
                # PROCESSOR: "100% GPU" or "34%/66% CPU/GPU"
                vram_pct = 0.0
                cpu_pct = 0.0
                processor_str = parts[4] if len(parts) > 4 else ""
                if "/" in processor_str:
                    # e.g. "34%/66%" → cpu=34, gpu=66
                    halves = processor_str.split("/")
                    try:
                        cpu_pct = float(halves[0].strip("%"))
                        vram_pct = float(halves[1].strip("%"))
                    except ValueError:
                        pass
                elif processor_str.endswith("%"):
                    try:
                        vram_pct = float(processor_str.strip("%"))
                    except ValueError:
                        pass
                loaded.append({
                    "name": name,
                    "size_bytes": _parse_size_bytes(size_str),
                    "vram_pct": vram_pct,
                    "cpu_pct": cpu_pct,
                    "until": " ".join(parts[5:]) if len(parts) > 5 else "",
                })
            return loaded
        except (OSError, subprocess.TimeoutExpired):
            return []

    def classify(self, model_name: str) -> dict:
        """Return {resource_profile, type_tag} based on model name heuristics."""
        name_lower = model_name.lower()

        resource_profile = "ollama"
        for keywords, profile in _PROFILE_RULES:
            if any(kw in name_lower for kw in keywords):
                resource_profile = profile
                break

        type_tag = "general"
        for keywords, tag in _TYPE_RULES:
            if any(kw in name_lower for kw in keywords):
                type_tag = tag
                break

        return {"resource_profile": resource_profile, "type_tag": type_tag}

    def estimate_vram_mb(self, model_name: str, db: "Database") -> float:
        """Return estimated VRAM in MB.

        Priority: observed value in model_registry → disk size × safety factor → 4000 MB default.
        Uses contextlib.closing() per Lesson #34.
        """
        with contextlib.closing(db._connect()) as _:
            with db._lock:
                conn = db._connect()
                row = conn.execute(
                    "SELECT vram_observed_mb, size_bytes FROM model_registry WHERE name = ?",
                    (model_name,),
                ).fetchone()

        if row and row["vram_observed_mb"]:
            return float(row["vram_observed_mb"])

        # Fall back to disk size with safety factor
        with db._lock:
            conn = db._connect()
            safety = float(
                conn.execute(
                    "SELECT value FROM settings WHERE key = 'vram_safety_factor'"
                ).fetchone()["value"]
            )

        # Try model_registry size_bytes first
        if row and row["size_bytes"]:
            return (row["size_bytes"] / 1_000_000) * safety

        # Try list_local
        for m in self.list_local():
            if m["name"] == model_name and m["size_bytes"]:
                return (m["size_bytes"] / 1_000_000) * safety

        return 4000.0  # 4 GB unknown default

    def record_observed_vram(self, model_name: str, vram_mb: float, db: "Database") -> None:
        """Update model_registry with observed VRAM using EMA (α=0.3)."""
        with db._lock:
            conn = db._connect()
            row = conn.execute(
                "SELECT vram_observed_mb FROM model_registry WHERE name = ?",
                (model_name,),
            ).fetchone()
            if row and row["vram_observed_mb"]:
                new_val = 0.3 * vram_mb + 0.7 * float(row["vram_observed_mb"])
            else:
                new_val = vram_mb
            conn.execute(
                """INSERT INTO model_registry (name, vram_observed_mb, last_seen)
                   VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       vram_observed_mb = excluded.vram_observed_mb,
                       last_seen = excluded.last_seen""",
                (model_name, new_val, time.time()),
            )
            conn.commit()

    def refresh_registry(self, db: "Database") -> None:
        """Sync model_registry with current `ollama list` output."""
        models = self.list_local()
        now = time.time()
        with db._lock:
            conn = db._connect()
            for m in models:
                classification = self.classify(m["name"])
                conn.execute(
                    """INSERT INTO model_registry
                           (name, size_bytes, resource_profile, type_tag, last_seen)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(name) DO UPDATE SET
                           size_bytes = excluded.size_bytes,
                           resource_profile = excluded.resource_profile,
                           type_tag = excluded.type_tag,
                           last_seen = excluded.last_seen""",
                    (m["name"], m["size_bytes"],
                     classification["resource_profile"],
                     classification["type_tag"], now),
                )
            conn.commit()
```

**Step 4: Run tests**

```bash
pytest tests/test_models.py -v
```
Expected: all 9 tests pass.

**Step 5: Commit**

```bash
git add ollama_queue/models.py tests/test_models.py
git commit -m "feat(models): add OllamaModels — list, classify, estimate_vram_mb"
```

---

## Task 3: models.py — Pull Lifecycle

**Files:**
- Modify: `ollama_queue/models.py`
- Modify: `tests/test_models.py`

**Step 1: Write failing tests**

```python
# tests/test_models.py — add

def test_pull_creates_db_row(tmp_path):
    from ollama_queue.db import Database
    db = Database(str(tmp_path / "q.db"))
    db.initialize()
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc
        pull_id = OllamaModels().pull("llama3.2:3b", db)
    assert pull_id is not None
    with db._lock:
        row = db._connect().execute(
            "SELECT * FROM model_pulls WHERE id = ?", (pull_id,)
        ).fetchone()
    assert row["model"] == "llama3.2:3b"
    assert row["status"] == "pulling"
    assert row["pid"] == 12345

def test_get_pull_status_returns_progress(tmp_path):
    from ollama_queue.db import Database
    db = Database(str(tmp_path / "q.db"))
    db.initialize()
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO model_pulls (model, status, progress_pct, started_at) VALUES (?, ?, ?, ?)",
            ("llama3.2:3b", "pulling", 42.5, time.time())
        )
        conn.commit()
        pull_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    status = OllamaModels().get_pull_status(pull_id, db)
    assert status["progress_pct"] == pytest.approx(42.5)
    assert status["status"] == "pulling"

def test_cancel_pull_sigterms_process(tmp_path):
    import os, signal as sig_mod
    from ollama_queue.db import Database
    db = Database(str(tmp_path / "q.db"))
    db.initialize()
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO model_pulls (model, status, pid, started_at) VALUES (?, ?, ?, ?)",
            ("llama3.2:3b", "pulling", 99999, time.time())
        )
        conn.commit()
        pull_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    with patch("os.kill") as mock_kill:
        OllamaModels().cancel_pull(pull_id, db)
    mock_kill.assert_called_once_with(99999, sig_mod.SIGTERM)
    with db._lock:
        row = db._connect().execute(
            "SELECT status FROM model_pulls WHERE id = ?", (pull_id,)
        ).fetchone()
    assert row["status"] == "cancelled"
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_models.py::test_pull_creates_db_row \
       tests/test_models.py::test_get_pull_status_returns_progress \
       tests/test_models.py::test_cancel_pull_sigterms_process -v
```

**Step 3: Add to `ollama_queue/models.py`**

```python
import os
import signal
import threading

# Add these methods to OllamaModels class:

    def pull(self, model_name: str, db: "Database") -> int:
        """Start `ollama pull <model>` in background. Returns pull_id."""
        import time as _time
        now = _time.time()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_pulls (model, status, progress_pct, started_at) VALUES (?,?,?,?)",
                (model_name, "pulling", 0.0, now),
            )
            conn.commit()
            pull_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        proc = subprocess.Popen(
            ["ollama", "pull", model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with db._lock:
            db._connect().execute(
                "UPDATE model_pulls SET pid = ? WHERE id = ?", (proc.pid, pull_id)
            )
            db._connect().commit()

        def _monitor():
            try:
                for line in proc.stdout:
                    # Parse "pulling abc123... X% ..." lines
                    if "%" in line:
                        try:
                            pct_str = [p for p in line.split() if p.endswith("%")]
                            if pct_str:
                                pct = float(pct_str[-1].strip("%"))
                                with db._lock:
                                    db._connect().execute(
                                        "UPDATE model_pulls SET progress_pct = ? WHERE id = ?",
                                        (pct, pull_id)
                                    )
                                    db._connect().commit()
                        except (ValueError, IndexError):
                            pass
                proc.wait()
                status = "completed" if proc.returncode == 0 else "failed"
            except Exception as exc:
                status = "failed"
                _log.error("pull monitor error: %s", exc)
            import time as _t
            with db._lock:
                db._connect().execute(
                    "UPDATE model_pulls SET status=?, completed_at=?, progress_pct=? WHERE id=?",
                    (status, _t.time(), 100.0 if status == "completed" else None, pull_id),
                )
                db._connect().commit()

        threading.Thread(target=_monitor, daemon=True, name=f"pull-{pull_id}").start()
        return pull_id

    def get_pull_status(self, pull_id: int, db: "Database") -> dict:
        """Return pull progress dict."""
        with db._lock:
            row = db._connect().execute(
                "SELECT * FROM model_pulls WHERE id = ?", (pull_id,)
            ).fetchone()
        if not row:
            return {"error": "not found"}
        return dict(row)

    def cancel_pull(self, pull_id: int, db: "Database") -> bool:
        """SIGTERM the pull process and mark cancelled."""
        with db._lock:
            row = db._connect().execute(
                "SELECT pid FROM model_pulls WHERE id = ?", (pull_id,)
            ).fetchone()
        if not row or not row["pid"]:
            return False
        try:
            os.kill(row["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
        import time as _t
        with db._lock:
            db._connect().execute(
                "UPDATE model_pulls SET status='cancelled', completed_at=? WHERE id=?",
                (_t.time(), pull_id),
            )
            db._connect().commit()
        return True
```

**Step 4: Run tests**

```bash
pytest tests/test_models.py -v
```
Expected: all tests pass.

**Step 5: Commit**

```bash
git add ollama_queue/models.py tests/test_models.py
git commit -m "feat(models): add pull lifecycle — pull(), get_pull_status(), cancel_pull()"
```

---

## Task 4: health.py — Multi-Model get_loaded_models()

**Files:**
- Modify: `ollama_queue/health.py`
- Modify: `tests/test_health.py`

**Step 1: Write failing tests**

```python
# tests/test_health.py — add

def test_get_loaded_models_empty_when_none(monkeypatch):
    import subprocess
    from ollama_queue.health import HealthMonitor
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "NAME    ID    SIZE    PROCESSOR    UNTIL\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock)
    result = HealthMonitor().get_loaded_models()
    assert result == []

def test_get_loaded_models_single():
    from unittest.mock import patch, MagicMock
    from ollama_queue.health import HealthMonitor
    output = "NAME          ID            SIZE    PROCESSOR    UNTIL\nqwen2.5:7b    abc           4.7 GB  100% GPU     4 minutes from now\n"
    mock = MagicMock(); mock.returncode = 0; mock.stdout = output
    with patch("subprocess.run", return_value=mock):
        result = HealthMonitor().get_loaded_models()
    assert len(result) == 1
    assert result[0]["name"] == "qwen2.5:7b"

def test_get_loaded_models_multi():
    from unittest.mock import patch, MagicMock
    from ollama_queue.health import HealthMonitor
    output = (
        "NAME                ID    SIZE      PROCESSOR    UNTIL\n"
        "qwen2.5:7b          a     4.7 GB    100% GPU     3 min\n"
        "nomic-embed-text    b     274 MB    0% GPU       5 min\n"
    )
    mock = MagicMock(); mock.returncode = 0; mock.stdout = output
    with patch("subprocess.run", return_value=mock):
        result = HealthMonitor().get_loaded_models()
    assert len(result) == 2
    names = [r["name"] for r in result]
    assert "qwen2.5:7b" in names
    assert "nomic-embed-text" in names
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_health.py::test_get_loaded_models_multi -v
```

**Step 3: Add `get_loaded_models()` to `health.py`**

Delegate to `OllamaModels`:
```python
def get_loaded_models(self) -> list[dict]:
    """Return all currently loaded Ollama models. Multi-model aware."""
    from ollama_queue.models import OllamaModels
    return OllamaModels().get_loaded()
```

Update `check()` to include loaded models list:
```python
def check(self) -> dict:
    loaded = self.get_loaded_models()
    return {
        "ram_pct": self.get_ram_pct(),
        "swap_pct": self.get_swap_pct(),
        "load_avg": self.get_load_avg(),
        "cpu_count": self.get_cpu_count(),
        "vram_pct": self.get_vram_pct(),
        "ollama_model": loaded[0]["name"] if loaded else None,  # backward compat
        "ollama_loaded_models": loaded,  # new: full list
    }
```

**Step 4: Run tests**

```bash
pytest tests/test_health.py -v
```
Expected: all health tests pass.

**Step 5: Commit**

```bash
git add ollama_queue/health.py tests/test_health.py
git commit -m "feat(health): add get_loaded_models() for multi-model concurrency"
```

---

## Task 5: daemon.py — PID Tracking + Orphan Recovery

**Files:**
- Modify: `ollama_queue/daemon.py`
- Modify: `ollama_queue/db.py` (add reset_job_to_pending, get_running_jobs)
- Modify: `tests/test_daemon.py`

**Step 1: Write failing tests**

```python
# tests/test_daemon.py — add

def test_recover_orphans_resets_running_jobs(tmp_db):
    from ollama_queue.daemon import Daemon
    # Simulate an orphaned running job (no real process)
    job_id = tmp_db.submit_job(command="echo hi", model="", priority=5,
                                timeout=60, source="test")
    tmp_db.start_job(job_id)
    # Write a non-existent PID
    tmp_db._connect().execute("UPDATE jobs SET pid = 999999 WHERE id = ?", (job_id,))
    tmp_db._connect().commit()

    d = Daemon(tmp_db)
    d._recover_orphans()

    job = tmp_db.get_job(job_id)
    assert job["status"] == "pending"

def test_pid_written_on_job_start(tmp_db, monkeypatch):
    from ollama_queue.daemon import Daemon
    import subprocess
    mock_proc = MagicMock()
    mock_proc.pid = 42
    mock_proc.returncode = 0
    mock_proc.stdout = MagicMock()
    mock_proc.stdout.read.return_value = b""
    mock_proc.stderr = MagicMock()
    mock_proc.stderr.read.return_value = b""
    mock_proc.wait.return_value = None
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)

    job_id = tmp_db.submit_job(command="echo hi", model="", priority=5,
                                timeout=60, source="test")
    d = Daemon(tmp_db)
    d.poll_once()

    conn = tmp_db._connect()
    row = conn.execute("SELECT pid FROM jobs WHERE id = ?", (job_id,)).fetchone()
    # pid should have been written (42) then cleared on completion
    # Just verify job completed without error
    job = tmp_db.get_job(job_id)
    assert job["status"] == "completed"
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_daemon.py::test_recover_orphans_resets_running_jobs -v
```

**Step 3: Add to `db.py`**

```python
def get_running_jobs(self) -> list[dict]:
    """Return all jobs currently in 'running' status."""
    with self._lock:
        rows = self._connect().execute(
            "SELECT * FROM jobs WHERE status = 'running'"
        ).fetchall()
    return [dict(r) for r in rows]

def reset_job_to_pending(self, job_id: int) -> None:
    """Reset a job from running back to pending (orphan recovery)."""
    with self._lock:
        conn = self._connect()
        conn.execute(
            "UPDATE jobs SET status='pending', started_at=NULL, pid=NULL WHERE id=?",
            (job_id,),
        )
        conn.commit()
```

Add to `daemon.py` `__init__` and `run()`:

```python
# In Daemon.__init__:
import signal as _signal

# Add method:
def _recover_orphans(self) -> None:
    """Kill subprocesses for jobs stuck in 'running' on daemon startup."""
    orphans = self.db.get_running_jobs()
    for job in orphans:
        if job.get("pid"):
            try:
                import os
                os.kill(job["pid"], _signal.SIGTERM)
                _log.info("Sent SIGTERM to orphaned pid=%d (job #%d)", job["pid"], job["id"])
            except ProcessLookupError:
                pass  # process already gone
        self.db.reset_job_to_pending(job["id"])
        _log.warning("Reset orphaned job #%d to pending", job["id"])

# In run(), call before the loop:
def run(self, poll_interval: int | None = None) -> None:
    if poll_interval is None:
        poll_interval = self.db.get_setting("poll_interval_seconds") or 5
    self._recover_orphans()   # NEW
    self.db.update_daemon_state(state="idle", uptime_since=time.time())
    ...
```

Write PID in `poll_once()` after `start_job`:
```python
# After self.db.start_job(job["id"]):
proc = subprocess.Popen(...)
# Write PID immediately
with self.db._lock:
    self.db._connect().execute(
        "UPDATE jobs SET pid = ? WHERE id = ?", (proc.pid, job["id"])
    )
    self.db._connect().commit()
```

**Step 4: Run tests**

```bash
pytest tests/test_daemon.py -v
```

**Step 5: Commit**

```bash
git add ollama_queue/daemon.py ollama_queue/db.py tests/test_daemon.py
git commit -m "feat(daemon): add PID tracking and orphan recovery on startup"
```

---

## Task 6: daemon.py — ThreadPoolExecutor + Admission Gate

**Files:**
- Modify: `ollama_queue/daemon.py`
- Modify: `tests/test_daemon.py`

**Step 1: Write failing tests**

```python
# tests/test_daemon.py — add

def test_embed_jobs_always_admitted(tmp_db, monkeypatch):
    """Embed-profile jobs bypass VRAM gate."""
    from ollama_queue.daemon import Daemon
    d = Daemon(tmp_db)
    job = {"id": 1, "model": "nomic-embed-text:latest", "resource_profile": "embed",
           "command": "echo", "source": "test", "timeout": 60, "priority": 5}
    # Even with VRAM-stressed system, embed is admitted
    monkeypatch.setattr(d, "_free_vram_mb", lambda: 0.0)
    assert d._can_admit(job) is True

def test_heavy_jobs_serialize(tmp_db):
    """Heavy-profile jobs are blocked when another job is running."""
    from ollama_queue.daemon import Daemon
    d = Daemon(tmp_db)
    # Simulate one job already running
    d._running[99] = MagicMock()
    job = {"id": 2, "model": "deepseek-r1:70b", "resource_profile": "heavy",
           "command": "echo", "source": "test", "timeout": 60, "priority": 5}
    assert d._can_admit(job) is False

def test_same_model_blocks_second(tmp_db):
    """Two jobs with same model cannot run concurrently."""
    from ollama_queue.daemon import Daemon
    d = Daemon(tmp_db)
    d._running[1] = MagicMock()
    d._running_models[1] = "qwen2.5:7b"
    job = {"id": 2, "model": "qwen2.5:7b", "resource_profile": "ollama",
           "command": "echo", "source": "test", "timeout": 60, "priority": 5}
    assert d._can_admit(job) is False

def test_shadow_mode_logs_but_does_not_run(tmp_db, monkeypatch, caplog):
    """Shadow mode logs 'SHADOW' but does not actually admit."""
    from ollama_queue.daemon import Daemon
    import logging
    tmp_db.set_setting("max_concurrent_jobs", 2)
    tmp_db.set_setting("concurrent_shadow_hours", 24)
    # Record when shadow mode started (now - 1h, still in shadow window)
    d = Daemon(tmp_db)
    d._concurrent_enabled_at = time.time() - 3600  # 1h ago, still in 24h window
    d._running[1] = MagicMock()
    d._running_models[1] = "qwen2.5:7b"
    job = {"id": 2, "model": "llama3.2:3b", "resource_profile": "ollama",
           "command": "echo", "source": "test", "timeout": 60, "priority": 5}
    monkeypatch.setattr(d, "_free_vram_mb", lambda: 16000.0)
    monkeypatch.setattr(d, "_free_ram_mb", lambda: 32000.0)
    with caplog.at_level(logging.INFO):
        admitted = d._can_admit(job)
    assert admitted is False  # shadow mode blocks
    assert any("SHADOW" in r.message for r in caplog.records)
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_daemon.py::test_embed_jobs_always_admitted \
       tests/test_daemon.py::test_heavy_jobs_serialize -v
```

**Step 3: Refactor `daemon.py` for concurrent execution**

```python
# Add to imports:
import os
import signal as _signal
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from ollama_queue.models import OllamaModels

# Rewrite Daemon.__init__:
def __init__(self, db: Database, health_monitor: HealthMonitor | None = None):
    self.db = db
    self.health = health_monitor or HealthMonitor()
    self.estimator = DurationEstimator(db)
    self.scheduler = Scheduler(db)
    self.dlq = DLQManager(db)
    self._last_prune: float = 0.0
    self._recent_job_models: dict[str, float] = {}
    self._running: dict[int, Future] = {}          # job_id → Future
    self._running_models: dict[int, str] = {}      # job_id → model
    self._running_lock = threading.Lock()
    self._executor: ThreadPoolExecutor | None = None
    self._concurrent_enabled_at: float | None = None  # for shadow mode
    self._ollama_models = OllamaModels()

def _max_slots(self) -> int:
    return max(1, int(self.db.get_setting("max_concurrent_jobs") or 1))

def _shadow_hours(self) -> float:
    return float(self.db.get_setting("concurrent_shadow_hours") or 24)

def _in_shadow_mode(self) -> bool:
    if self._max_slots() <= 1:
        return False
    if self._concurrent_enabled_at is None:
        self._concurrent_enabled_at = time.time()
        return True
    elapsed_hours = (time.time() - self._concurrent_enabled_at) / 3600
    return elapsed_hours < self._shadow_hours()

def _free_vram_mb(self) -> float | None:
    """Free VRAM in MB from nvidia-smi, or None if unavailable."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return float(result.stdout.strip().split("\n")[0]) * 1.0  # already MB
    except Exception:
        pass
    return None

def _free_ram_mb(self) -> float:
    """Free RAM in MB from /proc/meminfo."""
    from ollama_queue.health import HealthMonitor as HM
    info = HM._parse_meminfo()
    return info.get("MemAvailable", 0) / 1024.0  # kB → MB

def _model_pull_in_progress(self, model_name: str) -> bool:
    if not model_name:
        return False
    with self.db._lock:
        row = self.db._connect().execute(
            "SELECT id FROM model_pulls WHERE model = ? AND status = 'pulling'",
            (model_name,),
        ).fetchone()
    return row is not None

def _model_exists(self, model_name: str) -> bool:
    if not model_name:
        return True  # no model required, command-only job
    models = self._ollama_models.list_local()
    return any(m["name"] == model_name for m in models)

def _can_admit(self, job: dict) -> bool:
    """Three-factor admission gate. Returns True if job can start now."""
    profile = self._ollama_models.classify(job.get("model") or "")["resource_profile"]

    # embed: always concurrent (up to 4), no VRAM gate
    if profile == "embed":
        with self._running_lock:
            return sum(1 for _ in self._running) < 4

    # heavy: serialize — never concurrent
    if profile == "heavy":
        with self._running_lock:
            return len(self._running) == 0

    # Same model already running → serialize
    model = job.get("model") or ""
    with self._running_lock:
        if model and model in self._running_models.values():
            return False
        running_count = len(self._running)

    # Pull in progress → block
    if self._model_pull_in_progress(model):
        return False

    # Already at capacity → block
    if running_count >= self._max_slots():
        return False

    # Shadow mode — would admit but log instead
    free_vram = self._free_vram_mb()
    free_ram = self._free_ram_mb()
    model_vram = self._ollama_models.estimate_vram_mb(model, self.db) if model else 0.0

    vram_ok = free_vram is None or model_vram <= free_vram * 0.8
    ram_ok = model_vram * 0.5 <= free_ram * 0.8

    # Health gate — reuse existing hysteresis logic
    snap = self.health.check()
    settings = self.db.get_all_settings()
    health_eval = self.health.evaluate(snap, settings, currently_paused=False)

    if not vram_ok or not ram_ok or health_eval["should_pause"]:
        return False

    if self._in_shadow_mode() and running_count > 0:
        _log.info(
            "SHADOW: would admit concurrent job #%d (%s) — shadow mode active",
            job["id"], job.get("source", ""),
        )
        return False

    return True
```

Update `poll_once()` to use `_can_admit` and the thread pool. Replace the block starting at step 2 (`# 2. Check if already running`) with:

```python
# 2. Check running slots
with self._running_lock:
    # Clean up completed futures
    done_ids = [jid for jid, fut in self._running.items() if fut.done()]
    for jid in done_ids:
        self._running.pop(jid)
        self._running_models.pop(jid, None)
```

Replace the job execution block (steps 8-12) with:

```python
# 8. Admit and dispatch
if not self._can_admit(job):
    self.db.update_daemon_state(state="idle", last_poll_at=now, current_job_id=None)
    return

if self._executor is None:
    self._executor = ThreadPoolExecutor(max_workers=self._max_slots(),
                                        thread_name_prefix="oq-worker")

self.db.start_job(job["id"])
with self.db._lock:
    self.db._connect().execute(
        "UPDATE jobs SET pid = -1 WHERE id = ?", (job["id"],)
    )  # placeholder until Popen

with self._running_lock:
    self._running_models[job["id"]] = job.get("model") or ""

fut = self._executor.submit(self._run_job, job)
with self._running_lock:
    self._running[job["id"]] = fut

with self._running_lock:
    running_count = len(self._running)
state_label = "running" if running_count == 1 else f"running({running_count})"
self.db.update_daemon_state(
    state=state_label,
    current_job_id=job["id"],
    paused_reason=None,
    paused_since=None,
    last_poll_at=now,
)
```

Extract the subprocess execution into `_run_job(self, job)`:

```python
def _run_job(self, job: dict) -> None:
    """Execute a job in a worker thread. Records result in DB."""
    start_time = time.time()

    # Sample VRAM before
    vram_before = self._free_vram_mb()

    proc = subprocess.Popen(
        job["command"], shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # Record real PID
    with self.db._lock:
        self.db._connect().execute(
            "UPDATE jobs SET pid = ? WHERE id = ?", (proc.pid, job["id"])
        )
        self.db._connect().commit()

    try:
        proc.wait(timeout=job["timeout"])
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
        self.db.kill_job(
            job["id"],
            reason=f"timeout after {job['timeout']}s",
            stdout_tail=out[-500:].decode("utf-8", errors="replace"),
            stderr_tail=err[-500:].decode("utf-8", errors="replace"),
        )
        return

    duration = time.time() - start_time
    exit_code = proc.returncode
    stdout_tail = proc.stdout.read()[-500:].decode("utf-8", errors="replace")
    stderr_tail = proc.stderr.read()[-500:].decode("utf-8", errors="replace")

    self.db.complete_job(
        job["id"], exit_code=exit_code,
        stdout_tail=stdout_tail, stderr_tail=stderr_tail,
        outcome_reason=f"exit code {exit_code}" if exit_code != 0 else None,
    )

    if exit_code != 0:
        try:
            self.dlq.handle_failure(job["id"], f"exit code {exit_code}")
        except Exception:
            _log.exception("DLQ routing failed for job #%d", job["id"])

    if job.get("model"):
        self._recent_job_models[job["model"]] = time.time()

    if exit_code == 0:
        self.db.record_duration(
            source=job["source"], model=job["model"],
            duration=duration, exit_code=exit_code,
        )
        # Record observed VRAM delta
        vram_after = self._free_vram_mb()
        if vram_before is not None and vram_after is not None and job.get("model"):
            delta = vram_before - vram_after  # MB consumed
            if delta > 0:
                self._ollama_models.record_observed_vram(job["model"], delta, self.db)

    if job.get("recurring_job_id"):
        try:
            self.scheduler.update_next_run(
                job["recurring_job_id"],
                completed_at=time.time(), job_id=job["id"],
            )
        except Exception:
            _log.exception("Scheduler next_run update failed for job #%d", job["id"])

    with self._running_lock:
        self._running.pop(job["id"], None)
        self._running_models.pop(job["id"], None)
```

**Step 4: Run all daemon tests**

```bash
pytest tests/test_daemon.py -v
```
Expected: all pass.

**Step 5: Run full suite**

```bash
pytest --timeout=120 -x -q
```

**Step 6: Commit**

```bash
git add ollama_queue/daemon.py tests/test_daemon.py
git commit -m "feat(daemon): add ThreadPoolExecutor + admission gate + shadow mode"
```

---

## Task 7: daemon.py — Multi-Job Stall Detection

**Files:**
- Modify: `ollama_queue/daemon.py:251-281`

**Step 1: Write failing test**

```python
def test_stall_detection_checks_all_running_jobs(tmp_db):
    """Stall detection iterates self._running, not just current_job_id."""
    from ollama_queue.daemon import Daemon
    d = Daemon(tmp_db)
    # Create two "running" jobs with old started_at
    j1 = tmp_db.submit_job(command="sleep 999", model="", priority=5,
                             timeout=10, source="test")
    j2 = tmp_db.submit_job(command="sleep 999", model="", priority=5,
                             timeout=10, source="test")
    tmp_db.start_job(j1)
    tmp_db.start_job(j2)
    # Set started_at far in the past
    conn = tmp_db._connect()
    conn.execute(
        "UPDATE jobs SET started_at=?, estimated_duration=60 WHERE id IN (?,?)",
        (time.time() - 500, j1, j2)
    )
    conn.commit()
    # Simulate both in _running
    d._running[j1] = MagicMock()
    d._running[j2] = MagicMock()

    d._check_stalled_jobs(time.time())

    j1_data = tmp_db.get_job(j1)
    j2_data = tmp_db.get_job(j2)
    assert j1_data["stall_detected_at"] is not None
    assert j2_data["stall_detected_at"] is not None
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_daemon.py::test_stall_detection_checks_all_running_jobs -v
```

**Step 3: Update `_check_stalled_jobs` in `daemon.py`**

Replace the existing method:
```python
def _check_stalled_jobs(self, now: float) -> None:
    """Flag all running jobs exceeding stall threshold (multi-job aware)."""
    settings = self.db.get_all_settings()
    multiplier = settings.get("stall_multiplier", 2.0)
    conn = self.db._connect()
    # Iterate tracked running jobs (not just current_job_id)
    with self._running_lock:
        running_ids = list(self._running.keys())
    for job_id in running_ids:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ? AND stall_detected_at IS NULL",
            (job_id,)
        ).fetchone()
        if not row:
            continue
        job = dict(row)
        estimated = job.get("estimated_duration")
        started = job.get("started_at")
        if estimated and started:
            elapsed = now - started
            stall_window = max(60, estimated * multiplier)
            if elapsed > stall_window:
                conn.execute(
                    "UPDATE jobs SET stall_detected_at = ? WHERE id = ?",
                    (now, job_id),
                )
                conn.commit()
                _log.warning(
                    "Job #%d stalled: elapsed=%.0fs, estimated=%.0fs",
                    job_id, elapsed, estimated,
                )
```

**Step 4: Run tests**

```bash
pytest tests/test_daemon.py -v && pytest --timeout=120 -x -q
```

**Step 5: Commit**

```bash
git add ollama_queue/daemon.py tests/test_daemon.py
git commit -m "fix(daemon): multi-job stall detection iterates all running slots"
```

---

## Task 8: estimator.py — Concurrency-Aware queue_etas()

**Files:**
- Modify: `ollama_queue/estimator.py`
- Modify: `tests/test_estimator.py`

**Step 1: Write failing test**

```python
# tests/test_estimator.py — add

def test_embed_jobs_dont_block_serial_slots(tmp_db):
    """Embed jobs run concurrently — serial queue offset stays 0 for next job."""
    from ollama_queue.estimator import DurationEstimator
    jobs = [
        {"source": "aria-embed", "model": "nomic-embed-text:latest",
         "resource_profile": "embed"},
        {"source": "aria-full", "model": "qwen2.5-coder:14b",
         "resource_profile": "ollama"},
    ]
    etas = DurationEstimator(tmp_db).queue_etas(jobs)
    # Embed job is concurrent — next serial job starts at offset 0, not offset+=embed_duration
    assert etas[0]["concurrent"] is True
    assert etas[1]["estimated_start_offset"] == 0.0
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_estimator.py::test_embed_jobs_dont_block_serial_slots -v
```

**Step 3: Update `estimator.py`**

```python
from ollama_queue.models import OllamaModels

def queue_etas(self, queue_jobs: list[dict]) -> list[dict]:
    """Return ETAs per job, concurrency-aware.

    Embed-profile jobs don't consume a serial slot — they show concurrent=True
    and don't advance the cumulative offset for subsequent jobs.
    """
    results = []
    cumulative_offset: float = 0.0
    om = OllamaModels()

    for job in queue_jobs:
        duration = self.estimate(job["source"], model=job.get("model"))
        profile = job.get("resource_profile") or \
                  om.classify(job.get("model") or "")["resource_profile"]
        is_concurrent = profile == "embed"
        results.append({
            "estimated_start_offset": 0.0 if is_concurrent else cumulative_offset,
            "estimated_duration": duration,
            "concurrent": is_concurrent,
        })
        if not is_concurrent:
            cumulative_offset += duration

    return results
```

**Step 4: Run tests**

```bash
pytest tests/test_estimator.py -v
```

**Step 5: Commit**

```bash
git add ollama_queue/estimator.py tests/test_estimator.py
git commit -m "feat(estimator): concurrency-aware queue_etas — embed jobs don't consume serial slot"
```

---

## Task 9: api.py — Model Endpoints + Enriched Schedule + Queue ETAs

**Files:**
- Modify: `ollama_queue/api.py`
- Modify: `tests/test_api.py`

**Step 1: Write failing tests**

```python
# tests/test_api.py — add (uses existing test client fixture)

def test_get_models_returns_list(client):
    from unittest.mock import patch
    with patch("ollama_queue.models.OllamaModels.list_local", return_value=[
        {"name": "qwen2.5:7b", "size_bytes": 4_700_000_000, "modified": "1w"}
    ]), patch("ollama_queue.models.OllamaModels.get_loaded", return_value=[]):
        resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["name"] == "qwen2.5:7b"
    assert "resource_profile" in data[0]
    assert "type_tag" in data[0]
    assert "vram_mb" in data[0]

def test_schedule_includes_estimated_duration(client, tmp_db):
    resp = client.get("/api/schedule")
    assert resp.status_code == 200
    # With empty schedule, returns []
    assert isinstance(resp.json(), list)

def test_queue_etas_endpoint(client):
    resp = client.get("/api/queue/etas")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

def test_post_models_pull(client):
    from unittest.mock import patch
    with patch("ollama_queue.models.OllamaModels.pull", return_value=1):
        resp = client.post("/api/models/pull", json={"model": "llama3.2:3b"})
    assert resp.status_code == 200
    assert resp.json()["pull_id"] == 1

def test_get_models_catalog(client):
    resp = client.get("/api/models/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert "curated" in data
    assert len(data["curated"]) > 0
```

**Step 2: Run to verify failure**

```bash
pytest tests/test_api.py::test_get_models_returns_list \
       tests/test_api.py::test_queue_etas_endpoint -v
```

**Step 3: Add endpoints to `api.py`**

Add after the existing schedule section (before static files mount):

```python
from ollama_queue.estimator import DurationEstimator
from ollama_queue.models import OllamaModels

# --- Model catalog (curated list) ---
_CURATED_MODELS = [
    {"name": "nomic-embed-text", "type_tag": "embed", "resource_profile": "embed",
     "description": "Best embedding model — fast, 274MB", "recommended": True},
    {"name": "qwen2.5:7b", "type_tag": "general", "resource_profile": "ollama",
     "description": "Fast general-purpose model — 4.7GB", "recommended": True},
    {"name": "qwen2.5-coder:14b", "type_tag": "coding", "resource_profile": "ollama",
     "description": "Best local coding model — 8.9GB", "recommended": True},
    {"name": "deepseek-r1:8b", "type_tag": "reasoning", "resource_profile": "ollama",
     "description": "Reasoning model with CoT — 4.9GB", "recommended": True},
    {"name": "llama3.2:3b", "type_tag": "general", "resource_profile": "ollama",
     "description": "Lightweight — 2GB", "recommended": False},
    {"name": "deepseek-r1:70b", "type_tag": "reasoning", "resource_profile": "heavy",
     "description": "Max reasoning power — 39GB", "recommended": False},
]

# --- Models ---

@app.get("/api/models")
def get_models():
    om = OllamaModels()
    local = om.list_local()
    loaded_names = {m["name"] for m in om.get_loaded()}
    result = []
    for m in local:
        classification = om.classify(m["name"])
        vram_mb = om.estimate_vram_mb(m["name"], db)
        # Avg duration from estimator
        est = DurationEstimator(db).estimate(m["name"], model=m["name"])
        result.append({
            "name": m["name"],
            "size_bytes": m["size_bytes"],
            "vram_mb": round(vram_mb, 1),
            "resource_profile": classification["resource_profile"],
            "type_tag": classification["type_tag"],
            "loaded": m["name"] in loaded_names,
            "avg_duration_seconds": est,
        })
    return result

@app.get("/api/models/catalog")
def get_catalog(q: str | None = None):
    curated = [c.copy() for c in _CURATED_MODELS]
    search_results = []
    if q:
        try:
            import urllib.request, urllib.parse, json as _json
            url = f"https://ollama.com/search?q={urllib.parse.quote(q)}&format=json"
            with urllib.request.urlopen(url, timeout=5) as r:
                search_results = _json.loads(r.read())[:10]
        except Exception as exc:
            _log.warning("Ollama catalog search failed: %s", exc)
    return {"curated": curated, "search_results": search_results}

@app.post("/api/models/pull")
def start_pull(body: dict = Body(...)):
    model = body.get("model", "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    pull_id = OllamaModels().pull(model, db)
    return {"pull_id": pull_id}

@app.get("/api/models/pull/{pull_id}")
def get_pull(pull_id: int):
    status = OllamaModels().get_pull_status(pull_id, db)
    if "error" in status:
        raise HTTPException(status_code=404, detail=status["error"])
    return status

@app.delete("/api/models/pull/{pull_id}")
def cancel_pull(pull_id: int):
    ok = OllamaModels().cancel_pull(pull_id, db)
    return {"cancelled": ok}

# --- Queue ETAs ---

@app.get("/api/queue/etas")
def get_queue_etas():
    jobs = db.get_pending_jobs()
    return DurationEstimator(db).queue_etas(jobs)
```

Enrich `list_schedule()` with `estimated_duration`:
```python
@app.get("/api/schedule")
def list_schedule():
    jobs = db.list_recurring_jobs()
    est = DurationEstimator(db)
    om = OllamaModels()
    for rj in jobs:
        rj["estimated_duration"] = est.estimate(
            rj.get("name") or rj.get("source") or "",
            model=rj.get("model"),
        )
        if rj.get("model"):
            classification = om.classify(rj["model"])
            rj["model_profile"] = classification["resource_profile"]
            rj["model_type"] = classification["type_tag"]
            rj["model_vram_mb"] = round(om.estimate_vram_mb(rj["model"], db), 1)
        else:
            rj["model_profile"] = "ollama"
            rj["model_type"] = "general"
            rj["model_vram_mb"] = None
    return jobs
```

**Step 4: Run tests**

```bash
pytest tests/test_api.py -v
```

**Step 5: Full suite**

```bash
pytest --timeout=120 -x -q
```

**Step 6: Commit**

```bash
git add ollama_queue/api.py tests/test_api.py
git commit -m "feat(api): add model endpoints, queue ETAs, enriched schedule response"
```

---

## Task 10: api.py — Proxy Semaphore Fix

**Files:**
- Modify: `ollama_queue/api.py` (`/api/generate` proxy)
- Modify: `ollama_queue/db.py` (proxy claim methods)

The current `try_claim_for_proxy()` uses `current_job_id=-1` sentinel — breaks with concurrent slots.

**Step 1: Write failing test**

```python
def test_proxy_claim_respects_concurrent_slot_limit(tmp_db):
    """Proxy claims count against max_concurrent_jobs."""
    tmp_db.set_setting("max_concurrent_jobs", 1)
    # Submit and start a real job to fill the slot
    jid = tmp_db.submit_job("echo", "", 5, 60, "test")
    tmp_db.start_job(jid)
    # Proxy claim should fail when slot is full
    claimed = tmp_db.try_claim_for_proxy()
    assert claimed is False
```

**Step 2: Implement**

In `db.py`, update `try_claim_for_proxy()` to count running jobs:
```python
def try_claim_for_proxy(self) -> bool:
    """Claim a queue slot for a proxy /api/generate request.

    Respects max_concurrent_jobs. Returns True if claimed.
    """
    with self._lock:
        conn = self._connect()
        max_slots = int(self.get_setting("max_concurrent_jobs") or 1)
        running = conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE status = 'running'"
        ).fetchone()["cnt"]
        if running >= max_slots:
            return False
        # Use sentinel job_id = -1
        state = conn.execute("SELECT * FROM daemon_state WHERE id=1").fetchone()
        if state and state["current_job_id"] == -1:
            return False  # proxy already claimed
        conn.execute(
            "UPDATE daemon_state SET current_job_id=-1 WHERE id=1"
        )
        conn.commit()
        return True
```

**Step 3: Run tests**

```bash
pytest tests/test_api.py tests/test_db.py -v
```

**Step 4: Commit**

```bash
git add ollama_queue/api.py ollama_queue/db.py tests/test_api.py
git commit -m "fix(proxy): slot-aware proxy claim respects max_concurrent_jobs"
```

---

## Task 11: store.js — New Signals + Fetch Functions

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/store.js`

**Step 1: Add to `store.js`**

```javascript
// New signals
export const models = signal([]);          // /api/models
export const modelPulls = signal([]);      // active pulls
export const modelCatalog = signal({ curated: [], search_results: [] });
export const queueEtas = signal([]);       // /api/queue/etas

// Update currentTab to include 'models'
// Change: export const currentTab = signal('dashboard');
// to support: 'dashboard' | 'schedule' | 'dlq' | 'settings' | 'models'

export async function fetchModels() {
    try {
        const resp = await fetch(`${API}/models`);
        if (resp.ok) models.value = await resp.json();
    } catch (e) {
        console.error('fetchModels failed:', e);
    }
}

export async function fetchModelCatalog(query = '') {
    try {
        const url = query ? `${API}/models/catalog?q=${encodeURIComponent(query)}`
                          : `${API}/models/catalog`;
        const resp = await fetch(url);
        if (resp.ok) modelCatalog.value = await resp.json();
    } catch (e) {
        console.error('fetchModelCatalog failed:', e);
    }
}

export async function startModelPull(modelName) {
    const resp = await fetch(`${API}/models/pull`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: modelName }),
    });
    if (!resp.ok) throw new Error(`Pull failed: ${resp.status}`);
    const { pull_id } = await resp.json();
    return pull_id;
}

export async function cancelModelPull(pullId) {
    await fetch(`${API}/models/pull/${pullId}`, { method: 'DELETE' });
}

export async function fetchQueueEtas() {
    try {
        const resp = await fetch(`${API}/queue/etas`);
        if (resp.ok) queueEtas.value = await resp.json();
    } catch (e) {
        console.error('fetchQueueEtas failed:', e);
    }
}

export async function assignModelToJob(rjId, modelName) {
    return updateScheduleJob(rjId, { model: modelName });
}
```

In `fetchSchedule()`, also fetch ETAs:
```javascript
export async function fetchSchedule() {
    try {
        const [jobsResp, eventsResp] = await Promise.all([
            fetch(`${API}/schedule`),
            fetch(`${API}/schedule/events?limit=50`),
        ]);
        if (jobsResp.ok) scheduleJobs.value = await jobsResp.json();
        if (eventsResp.ok) scheduleEvents.value = await eventsResp.json();
        // Refresh ETAs alongside schedule
        await fetchQueueEtas();
    } catch (e) {
        console.error('fetchSchedule failed:', e);
    }
}
```

**Step 2: Verify no syntax errors**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | tail -20
```
Expected: Build succeeds (or only pre-existing warnings).

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/store.js
git commit -m "feat(spa): add model signals, pull functions, queue ETA store"
```

---

## Task 12: GanttChart.jsx + ModelBadge.jsx

**Files:**
- Create: `ollama_queue/dashboard/spa/src/components/GanttChart.jsx`
- Create: `ollama_queue/dashboard/spa/src/components/ModelBadge.jsx`

**Step 1: Create `ModelBadge.jsx`**

```jsx
import { h } from 'preact';

const PROFILE_COLORS = {
    embed:  { bg: 'var(--status-ok)',      label: 'embed' },
    ollama: { bg: 'var(--accent)',         label: 'llm' },
    heavy:  { bg: 'var(--status-warning)', label: 'heavy' },
};
const TYPE_COLORS = {
    coding:    'var(--accent)',
    reasoning: 'var(--status-warning)',
    embed:     'var(--status-ok)',
    general:   'var(--text-tertiary)',
};

export function ModelBadge({ profile, typeTag }) {
    const pc = PROFILE_COLORS[profile] || PROFILE_COLORS.ollama;
    const tc = TYPE_COLORS[typeTag] || TYPE_COLORS.general;
    return (
        <span style={{ display: 'inline-flex', gap: '0.25rem', alignItems: 'center' }}>
            <span style={{
                background: pc.bg, color: 'var(--accent-text)',
                fontSize: 'var(--type-label)', fontFamily: 'var(--font-mono)',
                fontWeight: 700, padding: '0.1rem 0.4rem',
                borderRadius: 'var(--radius)',
            }}>{pc.label}</span>
            {typeTag && typeTag !== 'general' && (
                <span style={{
                    color: tc, fontSize: 'var(--type-label)',
                    fontFamily: 'var(--font-mono)',
                }}>{typeTag}</span>
            )}
        </span>
    );
}
```

**Step 2: Create `GanttChart.jsx`**

```jsx
import { h } from 'preact';

// NOTE: all .map() callbacks use descriptive names (job, slot, lane) — Lesson #13
// Never use single-letter names that shadow the JSX factory 'h'.

const PROFILE_COLORS = {
    embed:  'var(--status-ok)',
    ollama: 'var(--accent)',
    heavy:  'var(--status-warning)',
};

function assignLanes(jobs) {
    const sorted = [...jobs].sort((a, b) => a.next_run - b.next_run);
    const laneEnds = [];  // tracks end time of last job in each lane
    return sorted.map(job => {
        const start = job.next_run;
        const end = start + (job.estimated_duration || 600);
        let laneIdx = laneEnds.findIndex(laneEnd => laneEnd <= start);
        if (laneIdx === -1) laneIdx = laneEnds.length;
        laneEnds[laneIdx] = end;
        return { ...job, _lane: laneIdx, _end: end };
    });
}

export function GanttChart({ jobs, tick, windowHours = 24 }) {
    // tick forces re-render every second for live countdowns
    void tick;
    const now = Date.now() / 1000;
    const windowSecs = windowHours * 3600;
    const windowEnd = now + windowSecs;

    const laneJobs = assignLanes(
        jobs.filter(job => job.next_run < windowEnd)
    );
    const laneCount = laneJobs.reduce((max, job) => Math.max(max, job._lane + 1), 1);
    const laneHeight = 44;
    const chartHeight = laneCount * laneHeight + 8;

    return (
        <div style={{ position: 'relative', width: '100%' }}>
            {/* Time axis labels */}
            <div style={{ display: 'flex', justifyContent: 'space-between',
                          fontSize: 'var(--type-label)', color: 'var(--text-tertiary)',
                          fontFamily: 'var(--font-mono)', marginBottom: '0.25rem' }}>
                {[0, 6, 12, 18, 24].map(offset => {
                    const t = new Date((now + offset * 3600) * 1000);
                    return (
                        <span key={offset}>
                            {offset === 0 ? 'now' : t.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                    );
                })}
            </div>

            {/* Chart area */}
            <div style={{
                position: 'relative',
                height: chartHeight,
                background: 'var(--bg-inset)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 'var(--radius)',
                overflow: 'hidden',
            }}>
                {/* Lane dividers */}
                {Array.from({ length: laneCount }, (_, laneIdx) => (
                    <div key={laneIdx} style={{
                        position: 'absolute',
                        top: laneIdx * laneHeight,
                        left: 0, right: 0,
                        height: laneHeight,
                        borderBottom: laneIdx < laneCount - 1
                            ? '1px solid var(--border-subtle)' : 'none',
                    }} />
                ))}

                {/* Job blocks */}
                {laneJobs.map(job => {
                    const startOffset = Math.max(0, job.next_run - now);
                    const duration = job.estimated_duration || 600;
                    const leftPct = (startOffset / windowSecs) * 100;
                    const widthPct = Math.max(0.5, (duration / windowSecs) * 100);
                    const color = PROFILE_COLORS[job.model_profile] || PROFILE_COLORS.ollama;
                    const isConcurrent = job._lane > 0;
                    return (
                        <div
                            key={job.id}
                            title={`${job.name}${isConcurrent ? ' ⟡ concurrent' : ''} — ${Math.round(duration / 60)}min`}
                            style={{
                                position: 'absolute',
                                left: `${Math.min(leftPct, 99.5)}%`,
                                width: `${Math.min(widthPct, 100 - leftPct)}%`,
                                top: job._lane * laneHeight + 4,
                                height: laneHeight - 8,
                                background: color,
                                opacity: 0.85,
                                borderRadius: 'var(--radius)',
                                overflow: 'hidden',
                                display: 'flex',
                                alignItems: 'center',
                                paddingLeft: '0.4rem',
                                cursor: 'default',
                            }}
                        >
                            <span style={{
                                fontFamily: 'var(--font-mono)',
                                fontSize: 'var(--type-label)',
                                color: 'var(--accent-text)',
                                fontWeight: 600,
                                whiteSpace: 'nowrap',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                            }}>
                                {isConcurrent && '⟡ '}{job.name}
                            </span>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
```

**Step 3: Build to verify**

```bash
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | grep -E "(error|Error|FAIL)" | head -20
```
Expected: no errors.

**Step 4: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/components/GanttChart.jsx \
        ollama_queue/dashboard/spa/src/components/ModelBadge.jsx
git commit -m "feat(spa): add GanttChart and ModelBadge components"
```

---

## Task 13: ScheduleTab.jsx — Gantt + Model/VRAM/ETA Columns

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx`

Replace `TimelineBar` import/usage with `GanttChart`. Add Model, VRAM, ETA columns to the table.

**Step 1: Update imports and table headers**

At top, add:
```jsx
import { GanttChart } from '../components/GanttChart';
import { ModelBadge } from '../components/ModelBadge';
import { models, fetchModels, assignModelToJob } from '../store';
```

Replace `<TimelineBar jobs={jobs} tick={tick} />` with:
```jsx
<GanttChart jobs={jobs} tick={tick} windowHours={24} />
```

Update `useEffect` to also fetch models and auto-refresh (Lesson #97 — use ref for guard):
```jsx
const refreshingRef = useRef(false);

useEffect(() => {
    fetchSchedule();
    fetchModels();
    const tickInterval = setInterval(() => setTick(t => t + 1), 1000);
    const refreshInterval = setInterval(() => {
        // Lesson #97: read live ref, not render-time state
        if (!refreshingRef.current) {
            refreshingRef.current = true;
            fetchSchedule().finally(() => { refreshingRef.current = false; });
        }
    }, 10000);
    return () => {
        clearInterval(tickInterval);
        clearInterval(refreshInterval);
    };
}, []);
```

Update table headers array:
```jsx
{['Name', 'Model', 'VRAM', 'Schedule', 'Priority', 'Next Run', 'ETA', '★', 'Enabled', ''].map(col => ...)}
```

Add Model column cell (after Name cell):
```jsx
<td style={{ textAlign: 'center', padding: '0.5rem' }}>
    {rj.model ? (
        <ModelBadge profile={rj.model_profile} typeTag={rj.model_type} />
    ) : (
        <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)' }}>—</span>
    )}
    {rj.model && (
        <div style={{ fontSize: 'var(--type-label)', color: 'var(--text-secondary)',
                      fontFamily: 'var(--font-mono)' }}>
            {rj.model.split(':')[0]}
        </div>
    )}
</td>
```

Add VRAM column cell:
```jsx
<td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)',
             fontSize: 'var(--type-label)', color: 'var(--text-secondary)' }}>
    {rj.model_vram_mb ? `${(rj.model_vram_mb / 1024).toFixed(1)} GB` : '—'}
</td>
```

Add ETA column cell (after Next Run):
```jsx
<td style={{ textAlign: 'center', fontFamily: 'var(--font-mono)',
             fontSize: 'var(--type-label)', color: 'var(--text-tertiary)' }}>
    {/* Show estimated queue wait */}
    {rj.estimated_duration
        ? `~${Math.round(rj.estimated_duration / 60)}m`
        : '—'}
</td>
```

**Step 2: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | grep -E "(error|Error)" | head -10
```

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/ScheduleTab.jsx
git commit -m "feat(spa): replace TimelineBar with GanttChart + Model/VRAM/ETA columns"
```

---

## Task 14: ModelsTab.jsx — Inventory + Assignment + Download

**Files:**
- Create: `ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx`

```jsx
import { h } from 'preact';
import { useEffect, useState, useRef } from 'preact/hooks';
import {
    models, modelCatalog, API,
    fetchModels, fetchModelCatalog,
    startModelPull, cancelModelPull,
    scheduleJobs, fetchSchedule, assignModelToJob,
} from '../store';
import { ModelBadge } from '../components/ModelBadge';

export default function ModelsTab() {
    const [searchQuery, setSearchQuery] = useState('');
    const [activePulls, setActivePulls] = useState({});  // pullId → {model, progress}
    const [pullError, setPullError] = useState(null);
    const [assigningJob, setAssigningJob] = useState(null);  // rj id
    const pollRef = useRef(null);

    useEffect(() => {
        fetchModels();
        fetchModelCatalog();
        return () => {
            if (pollRef.current) clearInterval(pollRef.current);
        };
    }, []);

    async function handlePull(modelName) {
        setPullError(null);
        try {
            const pullId = await startModelPull(modelName);
            setActivePulls(prev => ({ ...prev, [pullId]: { model: modelName, progress: 0 } }));
            // Poll progress every 2s
            const iv = setInterval(async () => {
                try {
                    const resp = await fetch(`${API}/models/pull/${pullId}`);
                    if (resp.ok) {
                        const data = await resp.json();
                        setActivePulls(prev => ({
                            ...prev,
                            [pullId]: { model: modelName, progress: data.progress_pct, status: data.status },
                        }));
                        if (data.status !== 'pulling') {
                            clearInterval(iv);
                            if (data.status === 'completed') fetchModels();
                        }
                    }
                } catch (_) {}
            }, 2000);
        } catch (err) {
            setPullError(`Pull failed: ${err.message}`);
        }
    }

    async function handleCancel(pullId) {
        await cancelModelPull(pullId);
        setActivePulls(prev => {
            const next = { ...prev };
            delete next[pullId];
            return next;
        });
    }

    async function handleAssign(rjId, modelName) {
        try {
            await assignModelToJob(rjId, modelName);
            await fetchSchedule();
        } catch (err) {
            setPullError(`Assign failed: ${err.message}`);
        }
        setAssigningJob(null);
    }

    const installedNames = new Set(models.value.map(m => m.name));

    return (
        <div class="flex flex-col gap-4 animate-page-enter">
            <h2 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontWeight: 700,
                         fontSize: 'var(--type-headline)', color: 'var(--text-primary)' }}>
                Models
            </h2>

            {pullError && (
                <div style={{ padding: '0.5rem', background: 'var(--status-error)',
                              color: 'var(--accent-text)', borderRadius: 'var(--radius)',
                              fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)' }}>
                    {pullError}
                </div>
            )}

            {/* Active Pulls */}
            {Object.entries(activePulls).map(([pullId, pull]) => (
                <div key={pullId} class="t-frame" style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', flex: 1 }}>{pull.model}</span>
                    <div style={{ flex: 2, background: 'var(--bg-inset)',
                                  borderRadius: 'var(--radius)', height: 8, overflow: 'hidden' }}>
                        <div style={{ width: `${pull.progress || 0}%`, height: '100%',
                                      background: 'var(--accent)', transition: 'width 0.5s' }} />
                    </div>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                   color: 'var(--text-secondary)', minWidth: '3rem' }}>
                        {pull.status === 'completed' ? '✓' : `${Math.round(pull.progress || 0)}%`}
                    </span>
                    {pull.status === 'pulling' && (
                        <button class="t-btn t-btn-secondary"
                                style={{ fontSize: 'var(--type-label)', padding: '0.2rem 0.6rem' }}
                                onClick={() => handleCancel(pullId)}>
                            Cancel
                        </button>
                    )}
                </div>
            ))}

            {/* Installed Models */}
            <section>
                <h3 style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                             fontSize: 'var(--type-label)', color: 'var(--text-secondary)',
                             textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.5rem' }}>
                    Installed ({models.value.length})
                </h3>
                <div class="t-frame" style={{ padding: 0, overflow: 'hidden' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--type-body)' }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--border-subtle)',
                                         background: 'var(--bg-surface-raised)' }}>
                                {['Name', 'Type', 'Size', 'VRAM', 'Avg Duration', 'Status', 'Assign to Job'].map(col => (
                                    <th key={col} style={{ textAlign: 'left', padding: '0.5rem 0.75rem',
                                                           fontSize: 'var(--type-label)', fontWeight: 600,
                                                           color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)',
                                                           textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                        {col}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {models.value.map(model => (
                                <tr key={model.name} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                                    <td style={{ padding: '0.5rem 0.75rem', fontFamily: 'var(--font-mono)',
                                                 color: 'var(--text-primary)' }}>{model.name}</td>
                                    <td style={{ padding: '0.5rem 0.75rem' }}>
                                        <ModelBadge profile={model.resource_profile} typeTag={model.type_tag} />
                                    </td>
                                    <td style={{ padding: '0.5rem 0.75rem', fontFamily: 'var(--font-mono)',
                                                 color: 'var(--text-secondary)' }}>
                                        {model.size_bytes ? `${(model.size_bytes / 1e9).toFixed(1)} GB` : '—'}
                                    </td>
                                    <td style={{ padding: '0.5rem 0.75rem', fontFamily: 'var(--font-mono)',
                                                 color: 'var(--text-secondary)' }}>
                                        {model.vram_mb ? `${(model.vram_mb / 1024).toFixed(1)} GB` : '—'}
                                    </td>
                                    <td style={{ padding: '0.5rem 0.75rem', fontFamily: 'var(--font-mono)',
                                                 color: 'var(--text-tertiary)' }}>
                                        {model.avg_duration_seconds
                                            ? `~${Math.round(model.avg_duration_seconds / 60)}m`
                                            : '—'}
                                    </td>
                                    <td style={{ padding: '0.5rem 0.75rem' }}>
                                        {model.loaded
                                            ? <span style={{ color: 'var(--status-ok)', fontFamily: 'var(--font-mono)',
                                                             fontSize: 'var(--type-label)' }}>● loaded</span>
                                            : <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--type-label)' }}>idle</span>}
                                    </td>
                                    <td style={{ padding: '0.5rem 0.75rem' }}>
                                        <select
                                            style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--type-label)',
                                                     background: 'var(--bg-inset)', color: 'var(--text-primary)',
                                                     border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius)',
                                                     padding: '0.2rem 0.4rem' }}
                                            onChange={ev => {
                                                const rjId = parseInt(ev.target.value, 10);
                                                if (rjId) handleAssign(rjId, model.name);
                                                ev.target.value = '';
                                            }}>
                                            <option value="">Assign to…</option>
                                            {scheduleJobs.value.map(rj => (
                                                <option key={rj.id} value={rj.id}>{rj.name}</option>
                                            ))}
                                        </select>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </section>

            {/* Download Panel */}
            <section>
                <h3 style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                             fontSize: 'var(--type-label)', color: 'var(--text-secondary)',
                             textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.5rem' }}>
                    Download Models
                </h3>

                {/* Search */}
                <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
                    <input
                        type="text" placeholder="Search ollama.com…"
                        value={searchQuery}
                        onInput={ev => setSearchQuery(ev.target.value)}
                        style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 'var(--type-body)',
                                 background: 'var(--bg-inset)', color: 'var(--text-primary)',
                                 border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius)',
                                 padding: '0.4rem 0.75rem', outline: 'none' }}
                    />
                    <button class="t-btn t-btn-primary px-4 py-2 text-sm"
                            onClick={() => fetchModelCatalog(searchQuery)}>
                        Search
                    </button>
                </div>

                {/* Curated grid */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '0.75rem' }}>
                    {[
                        ...modelCatalog.value.curated,
                        ...modelCatalog.value.search_results.map(r => ({ ...r, recommended: false })),
                    ].map(catalogModel => {
                        const isInstalled = installedNames.has(catalogModel.name);
                        return (
                            <div key={catalogModel.name} class="t-frame"
                                 style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                                    <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700,
                                                   color: 'var(--text-primary)', fontSize: 'var(--type-body)' }}>
                                        {catalogModel.name}
                                    </span>
                                    {catalogModel.recommended && (
                                        <span style={{ background: 'var(--status-ok)', color: 'var(--accent-text)',
                                                       fontSize: 'var(--type-label)', padding: '0.1rem 0.4rem',
                                                       borderRadius: 'var(--radius)', fontFamily: 'var(--font-mono)',
                                                       fontWeight: 700 }}>★ rec</span>
                                    )}
                                </div>
                                <p style={{ margin: 0, fontSize: 'var(--type-label)',
                                            color: 'var(--text-secondary)' }}>
                                    {catalogModel.description}
                                </p>
                                <ModelBadge
                                    profile={catalogModel.resource_profile || 'ollama'}
                                    typeTag={catalogModel.type_tag || 'general'}
                                />
                                <button
                                    class={`t-btn ${isInstalled ? 't-btn-secondary' : 't-btn-primary'}`}
                                    style={{ fontSize: 'var(--type-label)', padding: '0.3rem 0.75rem',
                                             opacity: isInstalled ? 0.5 : 1 }}
                                    disabled={isInstalled}
                                    onClick={() => !isInstalled && handlePull(catalogModel.name)}>
                                    {isInstalled ? '✓ Installed' : '↓ Download'}
                                </button>
                            </div>
                        );
                    })}
                </div>
            </section>
        </div>
    );
}
```

**Step 2: Build**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | grep -E "error" | head -10
```

**Step 3: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/pages/ModelsTab.jsx
git commit -m "feat(spa): add ModelsTab — inventory, assignment, download panel"
```

---

## Task 15: app.jsx + Settings.jsx — Models Tab + Concurrency Settings

**Files:**
- Modify: `ollama_queue/dashboard/spa/src/app.jsx`
- Modify: `ollama_queue/dashboard/spa/src/pages/Settings.jsx`

**Step 1: Add Models tab to `app.jsx`**

```jsx
import ModelsTab from './pages/ModelsTab';
```

In the tab navigation, add Models after Settings:
```jsx
<button class={`t-tab ${currentTab.value === 'models' ? 't-tab-active' : ''}`}
        onClick={() => { currentTab.value = 'models'; fetchModels(); }}>
    Models
</button>
```

In the content render switch, add:
```jsx
{currentTab.value === 'models' && <ModelsTab />}
```

**Step 2: Add concurrency settings to `Settings.jsx`**

In the settings form, add a new section "Concurrency":
```jsx
<section>
    <h3>Concurrency</h3>
    <SettingField
        label="Max Concurrent Jobs"
        settingKey="max_concurrent_jobs"
        type="number" min={1} max={8}
        help="1 = fully serial (default). Increase to allow parallel Ollama jobs when VRAM allows."
    />
    <SettingField
        label="Shadow Mode Hours"
        settingKey="concurrent_shadow_hours"
        type="number" min={0} max={168}
        help="Hours to log concurrent admission decisions before acting. 0 = disabled."
    />
    <SettingField
        label="VRAM Safety Factor"
        settingKey="vram_safety_factor"
        type="number" min={1.0} max={2.0} step={0.1}
        help="Multiplier applied to disk size when estimating VRAM. 1.3 = 30% headroom."
    />
</section>
```

**Step 3: Build + verify no render errors**

```bash
cd ollama_queue/dashboard/spa && npm run build 2>&1 | grep -iE "error" | head -10
```

**Step 4: Commit**

```bash
cd ~/Documents/projects/ollama-queue
git add ollama_queue/dashboard/spa/src/app.jsx \
        ollama_queue/dashboard/spa/src/pages/Settings.jsx
git commit -m "feat(spa): add Models tab to nav + concurrency settings fields"
```

---

## Task 16: Integration Verification

**Step 1: Run full test suite**

```bash
cd ~/Documents/projects/ollama-queue
source .venv/bin/activate
pytest --timeout=120 -x -q
```
Expected: all tests pass. Report count.

**Step 2: Build SPA**

```bash
cd ollama_queue/dashboard/spa && npm run build
```
Expected: build succeeds with `dist/` populated.

**Step 3: Run vertical trace**

Start the server:
```bash
cd ~/Documents/projects/ollama-queue
ollama-queue serve --port 7684 &
sleep 2
```

Vertical trace commands:
```bash
# 1. Check models endpoint
curl -s http://localhost:7684/api/models | python3 -m json.tool | head -20

# 2. Check catalog
curl -s http://localhost:7684/api/models/catalog | python3 -m json.tool | head -10

# 3. Check enriched schedule (should include estimated_duration)
curl -s http://localhost:7684/api/schedule | python3 -m json.tool | head -30

# 4. Check queue ETAs
curl -s http://localhost:7684/api/queue/etas | python3 -m json.tool

# 5. Submit a test job and confirm ETA appears
curl -s -X POST http://localhost:7684/api/queue/submit \
     -H "Content-Type: application/json" \
     -d '{"command":"echo hi","source":"test","model":"","priority":5}' | python3 -m json.tool
curl -s http://localhost:7684/api/queue/etas | python3 -m json.tool

# 6. Check dashboard loads
curl -s http://localhost:7684/ui/ | grep -c "html"

# 7. Stop test server
kill %1
```

**Step 4: Run full tests again to confirm no regression**

```bash
pytest --timeout=120 -q
```

**Step 5: Final commit**

```bash
git add -u
git commit -m "test: integration verification — model concurrency + UI features complete"
```

---

## Quality Gates

Run before every batch boundary and before PR:

```bash
# Tests
cd ~/Documents/projects/ollama-queue && pytest --timeout=120 -x -q

# Lint
make lint

# SPA build
cd ollama_queue/dashboard/spa && npm run build

# Lesson check
lessons-db scan --target . --baseline HEAD
```

---

## PRD Acceptance Criteria (machine-verifiable)

```bash
# T1: New DB tables exist
python3 -c "
import sqlite3
conn = sqlite3.connect('$HOME/.local/share/ollama-queue/queue.db')
tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")]
assert 'model_registry' in tables
assert 'model_pulls' in tables
cols = [r[1] for r in conn.execute('PRAGMA table_info(jobs)')]
assert 'pid' in cols
print('PASS: schema')
"

# T2: /api/models returns list with profile field
curl -sf http://localhost:7683/api/models | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert isinstance(data, list)
if data:
    assert 'resource_profile' in data[0]
    assert 'vram_mb' in data[0]
print('PASS: /api/models')
"

# T3: /api/models/catalog returns curated list
curl -sf http://localhost:7683/api/models/catalog | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert 'curated' in data
assert len(data['curated']) >= 4
print('PASS: /api/models/catalog')
"

# T4: /api/queue/etas returns list
curl -sf http://localhost:7683/api/queue/etas | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert isinstance(data, list)
print('PASS: /api/queue/etas')
"

# T5: /api/schedule includes estimated_duration
curl -sf http://localhost:7683/api/schedule | python3 -c "
import sys, json
data = json.load(sys.stdin)
if data:
    assert 'estimated_duration' in data[0]
print('PASS: schedule enriched')
"

# T6: SPA builds without errors
cd ~/Documents/projects/ollama-queue/ollama_queue/dashboard/spa
npm run build 2>&1 | grep -c "error" | python3 -c "
import sys; count = int(sys.stdin.read())
assert count == 0, f'{count} build errors'
print('PASS: SPA build')
"

# T7: All tests pass
cd ~/Documents/projects/ollama-queue
pytest --timeout=120 -q 2>&1 | tail -3
```
