"""Tests for OllamaModels — list, classify, estimate_vram_mb."""

import time
from unittest.mock import MagicMock, patch

import pytest

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


class TestListLocal:
    def test_list_local_parses_names(self):
        from ollama_queue.models import OllamaModels

        OllamaModels._list_local_cache = None
        with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
            models = OllamaModels().list_local()
        OllamaModels._list_local_cache = None
        names = [m["name"] for m in models]
        assert "qwen2.5-coder:14b" in names
        assert "nomic-embed-text:latest" in names
        assert len(models) >= 4

    def test_list_local_parses_size_bytes(self):
        from ollama_queue.models import OllamaModels

        OllamaModels._list_local_cache = None
        with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
            models = OllamaModels().list_local()
        OllamaModels._list_local_cache = None
        embed = next(m for m in models if "nomic" in m["name"])
        # 274 MB
        assert embed["size_bytes"] > 270_000_000
        assert embed["size_bytes"] < 290_000_000

    def test_list_local_returns_empty_on_failure(self):
        from ollama_queue.models import OllamaModels

        # Clear class-level cache so we exercise the subprocess path
        OllamaModels._list_local_cache = None
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        with patch("subprocess.run", return_value=mock):
            result = OllamaModels().list_local()
        OllamaModels._list_local_cache = None  # Reset so other tests aren't affected
        assert result == []


class TestClassify:
    def test_classify_embed_profile(self):
        from ollama_queue.models import OllamaModels

        result = OllamaModels().classify("nomic-embed-text:latest")
        assert result["resource_profile"] == "embed"
        assert result["type_tag"] == "embed"

    def test_classify_heavy_profile(self):
        from ollama_queue.models import OllamaModels

        result = OllamaModels().classify("deepseek-r1:70b")
        assert result["resource_profile"] == "heavy"

    def test_classify_coding_type(self):
        from ollama_queue.models import OllamaModels

        result = OllamaModels().classify("qwen2.5-coder:14b")
        assert result["type_tag"] == "coding"
        assert result["resource_profile"] == "ollama"

    def test_classify_default(self):
        from ollama_queue.models import OllamaModels

        result = OllamaModels().classify("qwen2.5:7b")
        assert result["resource_profile"] == "ollama"
        assert result["type_tag"] == "general"


class TestEstimateVram:
    def test_estimate_vram_uses_observed_when_available(self, tmp_path):
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_registry (name, vram_observed_mb) VALUES (?, ?)",
                ("qwen2.5:7b", 5120.0),
            )
            conn.commit()
        vram = OllamaModels().estimate_vram_mb("qwen2.5:7b", db)
        assert vram == pytest.approx(5120.0)

    def test_estimate_vram_falls_back_to_disk_size(self, tmp_path):
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        OllamaModels._list_local_cache = None
        with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
            vram = OllamaModels().estimate_vram_mb("qwen2.5:7b", db)
        OllamaModels._list_local_cache = None
        # 4.7 GB * 1.3 safety = ~6110 MB
        assert vram > 5000
        assert vram < 7000

    def test_estimate_vram_default_when_unknown(self, tmp_path):
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        OllamaModels._list_local_cache = None
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        with patch("subprocess.run", return_value=mock):
            vram = OllamaModels().estimate_vram_mb("unknown-model:latest", db)
        OllamaModels._list_local_cache = None
        assert vram == pytest.approx(4000.0)


class TestMinEstimatedVram:
    def test_min_estimated_vram_mb_returns_minimum(self, tmp_path):
        """min_estimated_vram_mb returns the smallest VRAM estimate across all known models."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        # Seed model_registry with observed VRAM values
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_registry (name, vram_observed_mb) VALUES (?, ?)",
                ("small-model:7b", 4096.0),
            )
            conn.execute(
                "INSERT INTO model_registry (name, vram_observed_mb) VALUES (?, ?)",
                ("large-model:70b", 40000.0),
            )
            conn.commit()
        min_vram = OllamaModels().min_estimated_vram_mb(db)
        # small-model:7b was seeded with vram_observed_mb=4096 — that is the exact minimum
        assert min_vram == 4096

    def test_min_estimated_vram_mb_with_fallback(self, tmp_path):
        """When fallback_mb is larger than the catalog minimum, returns fallback_mb."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_registry (name, vram_observed_mb) VALUES (?, ?)",
                ("small-model:7b", 4096.0),
            )
            conn.commit()
        # fallback larger than any catalog minimum should be returned
        result = OllamaModels().min_estimated_vram_mb(db, fallback_mb=99999)
        assert result == 99999

    def test_min_estimated_vram_mb_empty_registry_returns_fallback(self, tmp_path):
        """When model_registry is empty and ollama list fails, returns fallback or default."""
        from unittest.mock import MagicMock, patch

        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        with patch("subprocess.run", return_value=mock):
            result = OllamaModels().min_estimated_vram_mb(db)
        # Empty registry with no list_local data → hardcoded 2000 MB floor
        assert result == 2000


# --- TTL cache tests ---


def test_list_local_cached(monkeypatch):
    """ollama list subprocess called at most once per TTL window."""
    from ollama_queue.models import OllamaModels

    # Clear any pre-existing class-level cache from other tests
    OllamaModels._list_local_cache = None

    call_count = 0

    def fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return type("R", (), {"returncode": 0, "stdout": "NAME\nqwen2.5:7b\n"})()

    monkeypatch.setattr("ollama_queue.models.subprocess.run", fake_run)
    om = OllamaModels()
    om.list_local()
    om.list_local()
    om.list_local()
    assert call_count == 1, "ollama list should be called once within TTL window"

    # Cleanup: reset cache so other tests get a fresh fetch
    OllamaModels._list_local_cache = None


def test_invalidate_list_cache_forces_refetch(monkeypatch):
    """After _invalidate_list_cache(), the next list_local() call fetches fresh data."""
    from ollama_queue.models import OllamaModels

    OllamaModels._list_local_cache = None

    call_count = 0

    def fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return type("R", (), {"returncode": 0, "stdout": "NAME\nqwen2.5:7b\n"})()

    monkeypatch.setattr("ollama_queue.models.subprocess.run", fake_run)
    om = OllamaModels()
    om.list_local()
    assert call_count == 1
    OllamaModels._invalidate_list_cache()
    om.list_local()
    assert call_count == 2, "After cache invalidation, subprocess should be called again"

    OllamaModels._list_local_cache = None


# --- Pull lifecycle tests (Task 3) ---


class TestPullLifecycle:
    def test_pull_creates_db_row(self, tmp_path):
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        with patch("subprocess.Popen") as mock_popen, patch("threading.Thread") as mock_thread:
            # Prevent monitor thread from running so status stays 'pulling'
            mock_thread.return_value.start = MagicMock()
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            pull_id = OllamaModels().pull("llama3.2:3b", db)
        assert pull_id is not None
        with db._lock:
            row = db._connect().execute("SELECT * FROM model_pulls WHERE id = ?", (pull_id,)).fetchone()
        assert row["model"] == "llama3.2:3b"
        assert row["status"] == "pulling"
        assert row["pid"] == 12345

    def test_get_pull_status_returns_progress(self, tmp_path):
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_pulls (model, status, progress_pct, started_at) VALUES (?, ?, ?, ?)",
                ("llama3.2:3b", "pulling", 42.5, time.time()),
            )
            conn.commit()
            pull_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        status = OllamaModels().get_pull_status(pull_id, db)
        assert status["progress_pct"] == pytest.approx(42.5)
        assert status["status"] == "pulling"

    def test_cancel_pull_sigterms_process(self, tmp_path):
        import signal as sig_mod

        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_pulls (model, status, pid, started_at) VALUES (?, ?, ?, ?)",
                ("llama3.2:3b", "pulling", 99999, time.time()),
            )
            conn.commit()
            pull_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        with patch("os.kill") as mock_kill:
            OllamaModels().cancel_pull(pull_id, db)
        mock_kill.assert_called_once_with(99999, sig_mod.SIGTERM)
        with db._lock:
            row = db._connect().execute("SELECT status FROM model_pulls WHERE id = ?", (pull_id,)).fetchone()
        assert row["status"] == "cancelled"
