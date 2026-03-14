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

    def test_list_local_cache_only_fetches_once_on_concurrent_miss(self):
        """Concurrent list_local() calls on a cold cache must only call _fetch_list_local once."""
        import threading
        import time
        from unittest.mock import patch

        from ollama_queue.models.client import OllamaModels

        OllamaModels._invalidate_list_cache()

        fetch_count = {"n": 0}

        def slow_fetch():
            fetch_count["n"] += 1
            time.sleep(0.05)
            return [{"name": "qwen2.5:7b", "size_bytes": 0, "modified": ""}]

        with patch.object(OllamaModels, "_fetch_list_local", slow_fetch):
            threads = [threading.Thread(target=OllamaModels.list_local) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        OllamaModels._invalidate_list_cache()
        assert fetch_count["n"] == 1, f"Expected 1 fetch, got {fetch_count['n']}"


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

    def test_estimate_vram_logs_warning_for_unknown_model(self, tmp_path, caplog):
        """Fallback to 4000MB logs a warning identifying the unknown model."""
        import logging

        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        OllamaModels._list_local_cache = None
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        with (
            caplog.at_level(logging.WARNING, logger="ollama_queue.models.client"),
            patch("subprocess.run", return_value=mock),
        ):
            vram = OllamaModels().estimate_vram_mb("totally-unknown:latest", db)
        OllamaModels._list_local_cache = None
        assert vram == pytest.approx(4000.0)
        assert any("totally-unknown:latest" in r.message and "4000MB default" in r.message for r in caplog.records)


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

    monkeypatch.setattr("ollama_queue.models.client.subprocess.run", fake_run)
    om = OllamaModels()
    om.list_local()
    om.list_local()
    om.list_local()
    assert call_count == 1, "ollama list should be called once within TTL window"

    # Cleanup: reset cache so other tests get a fresh fetch
    OllamaModels._list_local_cache = None


def test_cache_ttl_is_15_seconds():
    """Cache TTL should be 15 seconds (reduced from 60)."""
    from ollama_queue.models import OllamaModels

    assert OllamaModels._LIST_LOCAL_TTL == 15.0


def test_invalidate_list_cache_returns_fresh_data(monkeypatch):
    """After _invalidate_list_cache(), list_local() returns new data, not stale cache."""
    from ollama_queue.models import OllamaModels

    OllamaModels._list_local_cache = None
    generation = [0]

    def fake_run(*args, **kwargs):
        generation[0] += 1
        # Each call returns a different model name
        name = f"model-gen{generation[0]}:7b"
        return type("R", (), {"returncode": 0, "stdout": f"NAME  ID  SIZE  MOD\n{name}  abc  4.7 GB  now\n"})()

    monkeypatch.setattr("ollama_queue.models.client.subprocess.run", fake_run)
    om = OllamaModels()
    first = om.list_local()
    assert any("gen1" in m["name"] for m in first)

    # Without invalidation, cached data is returned
    second = om.list_local()
    assert any("gen1" in m["name"] for m in second)

    # After invalidation, fresh data is returned
    OllamaModels._invalidate_list_cache()
    third = om.list_local()
    assert any("gen2" in m["name"] for m in third)
    assert not any("gen1" in m["name"] for m in third)

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

    monkeypatch.setattr("ollama_queue.models.client.subprocess.run", fake_run)
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


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


class TestParseSizeBytes:
    """_parse_size_bytes edge cases (lines 48, 54-55)."""

    def test_single_part_returns_zero(self):
        """len(parts) < 2 — line 48."""
        from ollama_queue.models.client import _parse_size_bytes

        assert _parse_size_bytes("4.7") == 0
        assert _parse_size_bytes("") == 0

    def test_invalid_value_returns_zero(self):
        """ValueError from float() — lines 54-55."""
        from ollama_queue.models.client import _parse_size_bytes

        assert _parse_size_bytes("abc GB") == 0

    def test_unknown_unit_uses_multiplier_1(self):
        """Unknown unit falls through to multiplier 1 — line 53."""
        from ollama_queue.models.client import _parse_size_bytes

        assert _parse_size_bytes("100 XB") == 100


class TestListLocalEdgeCases:
    """_fetch_list_local edge cases (lines 98, 116-118)."""

    def test_list_local_header_only(self):
        """Only header line — returns [] (line 98)."""
        from ollama_queue.models import OllamaModels

        OllamaModels._list_local_cache = None
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "NAME                            ID              SIZE      MODIFIED\n"
        with patch("subprocess.run", return_value=mock):
            result = OllamaModels().list_local()
        OllamaModels._list_local_cache = None
        assert result == []

    def test_list_local_oserror(self):
        """OSError from subprocess — lines 116-118."""
        from ollama_queue.models import OllamaModels

        OllamaModels._list_local_cache = None
        with patch("subprocess.run", side_effect=OSError("ollama not found")):
            result = OllamaModels().list_local()
        OllamaModels._list_local_cache = None
        assert result == []

    def test_list_local_timeout(self):
        """TimeoutExpired from subprocess — lines 116-118."""
        import subprocess as sp

        from ollama_queue.models import OllamaModels

        OllamaModels._list_local_cache = None
        with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="ollama", timeout=10)):
            result = OllamaModels().list_local()
        OllamaModels._list_local_cache = None
        assert result == []


class TestGetLoaded:
    """get_loaded() exercises lines 125-170."""

    def test_get_loaded_parses_models(self):
        from ollama_queue.models import OllamaModels

        ps_output = (
            "NAME                ID            SIZE      PROCESSOR    UNTIL\n"
            "qwen2.5:7b          abc123        4.7 GB    100%         4 minutes from now\n"
        )
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ps_output
        with patch("subprocess.run", return_value=mock):
            loaded = OllamaModels().get_loaded()
        assert len(loaded) == 1
        assert loaded[0]["name"] == "qwen2.5:7b"
        assert loaded[0]["vram_pct"] == 100.0
        assert loaded[0]["cpu_pct"] == 0.0
        assert loaded[0]["size_bytes"] > 0

    def test_get_loaded_split_processor(self):
        """Processor like '10%/90%' — splits CPU/GPU (lines 147-153)."""
        from ollama_queue.models import OllamaModels

        ps_output = (
            "NAME                ID            SIZE      PROCESSOR    UNTIL\n"
            "qwen2.5:7b          abc123        4.7 GB    10%/90%      4 minutes from now\n"
        )
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ps_output
        with patch("subprocess.run", return_value=mock):
            loaded = OllamaModels().get_loaded()
        assert loaded[0]["cpu_pct"] == 10.0
        assert loaded[0]["vram_pct"] == 90.0

    def test_get_loaded_split_processor_invalid(self):
        """Processor like 'abc/def' — ValueError caught (lines 152-153)."""
        from ollama_queue.models import OllamaModels

        ps_output = (
            "NAME                ID            SIZE      PROCESSOR    UNTIL\n"
            "qwen2.5:7b          abc123        4.7 GB    abc/def      4 minutes from now\n"
        )
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ps_output
        with patch("subprocess.run", return_value=mock):
            loaded = OllamaModels().get_loaded()
        assert loaded[0]["cpu_pct"] == 0.0
        assert loaded[0]["vram_pct"] == 0.0

    def test_get_loaded_single_pct_invalid(self):
        """Processor like 'abc%' — ValueError caught (lines 157-158)."""
        from ollama_queue.models import OllamaModels

        ps_output = (
            "NAME                ID            SIZE      PROCESSOR    UNTIL\n"
            "qwen2.5:7b          abc123        4.7 GB    abc%         4 minutes from now\n"
        )
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ps_output
        with patch("subprocess.run", return_value=mock):
            loaded = OllamaModels().get_loaded()
        assert loaded[0]["vram_pct"] == 0.0

    def test_get_loaded_nonzero_exit(self):
        """returncode != 0 — returns [] (line 133)."""
        from ollama_queue.models import OllamaModels

        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        with patch("subprocess.run", return_value=mock):
            assert OllamaModels().get_loaded() == []

    def test_get_loaded_header_only(self):
        """Only header — returns [] (lines 135-136)."""
        from ollama_queue.models import OllamaModels

        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "NAME                ID            SIZE      PROCESSOR    UNTIL\n"
        with patch("subprocess.run", return_value=mock):
            assert OllamaModels().get_loaded() == []

    def test_get_loaded_short_line(self):
        """Line with fewer than 3 parts — skipped (line 141)."""
        from ollama_queue.models import OllamaModels

        ps_output = "NAME                ID            SIZE      PROCESSOR    UNTIL\nab cd\n"
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ps_output
        with patch("subprocess.run", return_value=mock):
            assert OllamaModels().get_loaded() == []

    def test_get_loaded_oserror(self):
        """OSError — returns [] (lines 169-170)."""
        from ollama_queue.models import OllamaModels

        with patch("subprocess.run", side_effect=OSError("no ollama")):
            assert OllamaModels().get_loaded() == []

    def test_get_loaded_timeout(self):
        """TimeoutExpired — returns [] (lines 169-170)."""
        import subprocess as sp

        from ollama_queue.models import OllamaModels

        with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="ollama", timeout=5)):
            assert OllamaModels().get_loaded() == []

    def test_get_loaded_3_parts_no_unit(self):
        """Line with exactly 3 parts — size_str uses 'B' suffix (line 143)."""
        from ollama_queue.models import OllamaModels

        ps_output = "NAME  ID  SIZE\nmodel abc 1234\n"
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = ps_output
        with patch("subprocess.run", return_value=mock):
            loaded = OllamaModels().get_loaded()
        assert len(loaded) == 1
        assert loaded[0]["name"] == "model"


class TestMinEstimatedVramGaps:
    """min_estimated_vram_mb edge cases (lines 222-225, 250)."""

    def test_min_estimated_vram_uses_size_bytes_with_safety(self, tmp_path):
        """Model has size_bytes but no vram_observed — uses safety factor (lines 222-223)."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_registry (name, size_bytes) VALUES (?, ?)",
                ("test-model:7b", 5_000_000_000),
            )
            conn.commit()
        result = OllamaModels().min_estimated_vram_mb(db)
        # 5_000_000_000 / 1_000_000 * 1.3 = 6500
        assert result == 6500

    def test_min_estimated_vram_no_observed_no_size(self, tmp_path):
        """Model has neither vram_observed nor size_bytes — uses 2000 floor (lines 224-225)."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_registry (name) VALUES (?)",
                ("unknown-model",),
            )
            conn.commit()
        result = OllamaModels().min_estimated_vram_mb(db)
        assert result == 2000

    def test_min_estimated_vram_with_custom_safety_factor(self, tmp_path):
        """Custom vram_safety_factor setting — line 216."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_registry (name, size_bytes) VALUES (?, ?)",
                ("test-model:7b", 4_000_000_000),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("vram_safety_factor", "2.0"),
            )
            conn.commit()
        result = OllamaModels().min_estimated_vram_mb(db)
        # 4_000_000_000 / 1_000_000 * 2.0 = 8000
        assert result == 8000


class TestEstimateVramGaps:
    """estimate_vram_mb edge cases (line 250)."""

    def test_estimate_vram_uses_registry_size_bytes(self, tmp_path):
        """Model in registry with size_bytes but no vram_observed — line 250."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_registry (name, size_bytes) VALUES (?, ?)",
                ("test-model:7b", 4_000_000_000),
            )
            conn.commit()
        OllamaModels._list_local_cache = None
        result = OllamaModels().estimate_vram_mb("test-model:7b", db)
        OllamaModels._list_local_cache = None
        # 4_000_000_000 / 1_000_000 * 1.3 = 5200
        assert result == pytest.approx(5200.0)


class TestRecordObservedVram:
    """record_observed_vram exercises lines 261-279."""

    def test_record_observed_first_time(self, tmp_path):
        """First observation — stores value directly (line 270)."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        OllamaModels().record_observed_vram("test-model:7b", 5000.0, db)
        with db._lock:
            row = (
                db._connect()
                .execute(
                    "SELECT vram_observed_mb FROM model_registry WHERE name = ?",
                    ("test-model:7b",),
                )
                .fetchone()
            )
        assert row["vram_observed_mb"] == pytest.approx(5000.0)

    def test_record_observed_ema_update(self, tmp_path):
        """Second observation — EMA: 0.3 * new + 0.7 * old (line 268)."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        om = OllamaModels()
        om.record_observed_vram("test-model:7b", 5000.0, db)
        om.record_observed_vram("test-model:7b", 6000.0, db)
        with db._lock:
            row = (
                db._connect()
                .execute(
                    "SELECT vram_observed_mb FROM model_registry WHERE name = ?",
                    ("test-model:7b",),
                )
                .fetchone()
            )
        # 0.3 * 6000 + 0.7 * 5000 = 1800 + 3500 = 5300
        assert row["vram_observed_mb"] == pytest.approx(5300.0)


class TestRefreshRegistry:
    """refresh_registry exercises lines 283-306."""

    def test_refresh_registry_syncs_models(self, tmp_path):
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        om = OllamaModels()
        OllamaModels._list_local_cache = None
        with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
            om.refresh_registry(db)
        OllamaModels._list_local_cache = None
        with db._lock:
            rows = db._connect().execute("SELECT * FROM model_registry").fetchall()
        names = [r["name"] for r in rows]
        assert "qwen2.5-coder:14b" in names
        assert "nomic-embed-text:latest" in names
        assert len(rows) >= 4

    def test_refresh_registry_updates_classification(self, tmp_path):
        """Models are classified with resource_profile and type_tag."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        om = OllamaModels()
        OllamaModels._list_local_cache = None
        with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
            om.refresh_registry(db)
        OllamaModels._list_local_cache = None
        with db._lock:
            row = (
                db._connect()
                .execute(
                    "SELECT resource_profile, type_tag FROM model_registry WHERE name = ?",
                    ("nomic-embed-text:latest",),
                )
                .fetchone()
            )
        assert row["resource_profile"] == "embed"
        assert row["type_tag"] == "embed"


class TestPullMonitor:
    """Pull _monitor thread exercises lines 334-369."""

    def test_monitor_completes_successfully(self, tmp_path):
        """_monitor marks pull as completed on success (lines 334-369)."""
        import threading

        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        om = OllamaModels()

        # Create a mock Popen that outputs progress then exits 0
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.stdout = iter(["downloading 50%\n", "downloading 100%\n"])
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0

        threads_started = []
        original_thread = threading.Thread

        def capture_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            threads_started.append(t)
            return t

        OllamaModels._list_local_cache = None
        with patch("subprocess.Popen", return_value=mock_proc), patch("threading.Thread", side_effect=capture_thread):
            pull_id = om.pull("llama3.2:3b", db)

        # Run the monitor thread and wait for it
        for t in threads_started:
            t.join(timeout=5)

        with db._lock:
            row = db._connect().execute("SELECT * FROM model_pulls WHERE id = ?", (pull_id,)).fetchone()
        assert row["status"] == "completed"
        assert row["progress_pct"] == pytest.approx(100.0)
        OllamaModels._list_local_cache = None

    def test_monitor_marks_failed_on_nonzero(self, tmp_path):
        """_monitor marks pull as failed on nonzero exit."""
        import threading

        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        om = OllamaModels()

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.stdout = iter(["error: pull failed\n"])
        mock_proc.wait.return_value = None
        mock_proc.returncode = 1

        threads_started = []
        original_thread = threading.Thread

        def capture_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            threads_started.append(t)
            return t

        OllamaModels._list_local_cache = None
        with patch("subprocess.Popen", return_value=mock_proc), patch("threading.Thread", side_effect=capture_thread):
            pull_id = om.pull("llama3.2:3b", db)

        for t in threads_started:
            t.join(timeout=5)

        with db._lock:
            row = db._connect().execute("SELECT * FROM model_pulls WHERE id = ?", (pull_id,)).fetchone()
        assert row["status"] == "failed"
        OllamaModels._list_local_cache = None

    def test_monitor_handles_exception(self, tmp_path):
        """_monitor catches exception and marks as failed (lines 357-359)."""
        import threading

        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        om = OllamaModels()

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        # stdout iteration raises
        mock_proc.stdout.__iter__ = MagicMock(side_effect=RuntimeError("pipe broken"))

        threads_started = []
        original_thread = threading.Thread

        def capture_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            threads_started.append(t)
            return t

        OllamaModels._list_local_cache = None
        with patch("subprocess.Popen", return_value=mock_proc), patch("threading.Thread", side_effect=capture_thread):
            pull_id = om.pull("llama3.2:3b", db)

        for t in threads_started:
            t.join(timeout=5)

        with db._lock:
            row = db._connect().execute("SELECT * FROM model_pulls WHERE id = ?", (pull_id,)).fetchone()
        assert row["status"] == "failed"
        OllamaModels._list_local_cache = None


class TestPullMonitorDbErrors:
    """Pull _monitor DB error branches (lines 349-352, 368-369)."""

    def test_monitor_progress_db_error_continues(self, tmp_path):
        """DB error during progress update is logged but doesn't kill monitor (lines 349-350)."""
        import threading

        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        om = OllamaModels()

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.stdout = iter(["downloading 50%\n"])
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0

        threads_started = []
        original_thread = threading.Thread

        def capture_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            threads_started.append(t)
            return t

        OllamaModels._list_local_cache = None

        # Make _connect return a connection that fails on execute for progress updates
        original_connect = db._connect
        call_count = [0]

        def connect_that_fails_once():
            call_count[0] += 1
            conn = original_connect()
            # Calls 1-2 are from pull() itself (INSERT + UPDATE pid).
            # Call 3 is the progress update inside the monitor — make it fail.
            if call_count[0] == 3:
                mock_conn = MagicMock()
                mock_conn.execute.side_effect = Exception("simulated progress DB error")
                return mock_conn
            return conn

        db._connect = connect_that_fails_once

        with patch("subprocess.Popen", return_value=mock_proc), patch("threading.Thread", side_effect=capture_thread):
            pull_id = om.pull("llama3.2:3b", db)

        for t in threads_started:
            t.join(timeout=5)

        # Restore
        db._connect = original_connect

        # Despite progress DB error, the monitor should complete and write final status
        with db._lock:
            row = db._connect().execute("SELECT * FROM model_pulls WHERE id = ?", (pull_id,)).fetchone()
        assert row["status"] == "completed"
        OllamaModels._list_local_cache = None

    def test_monitor_final_status_db_error(self, tmp_path):
        """DB error during final status update is caught (lines 368-369)."""
        import threading

        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        om = OllamaModels()

        # Use an event to synchronize: we rename the table AFTER stdout is consumed
        # but BEFORE the final status update
        rename_event = threading.Event()

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        # No progress lines — goes straight to wait/final update
        def stdout_iter():
            return iter([])

        mock_proc.stdout = stdout_iter()

        def fake_wait():
            rename_event.set()
            # Give the main thread time to rename the table
            import time

            time.sleep(0.2)

        mock_proc.wait = fake_wait
        mock_proc.returncode = 0

        threads_started = []
        original_thread = threading.Thread

        def capture_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            threads_started.append(t)
            return t

        OllamaModels._list_local_cache = None
        with patch("subprocess.Popen", return_value=mock_proc), patch("threading.Thread", side_effect=capture_thread):
            pull_id = om.pull("llama3.2:3b", db)

        # Wait for monitor to reach wait(), then rename table to break final update
        rename_event.wait(timeout=5)
        with db._lock:
            conn = db._connect()
            conn.execute("ALTER TABLE model_pulls RENAME TO model_pulls_bak")
            conn.commit()

        for t in threads_started:
            t.join(timeout=5)

        # Restore table
        with db._lock:
            conn = db._connect()
            conn.execute("ALTER TABLE model_pulls_bak RENAME TO model_pulls")
            conn.commit()

        # The monitor should not have raised — it logs the exception
        OllamaModels._list_local_cache = None

    def test_monitor_progress_value_error(self, tmp_path):
        """Non-numeric percentage in stdout — ValueError caught (lines 351-352)."""
        import threading

        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        om = OllamaModels()

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        # "abc%" has a % but float("abc") raises ValueError
        mock_proc.stdout = iter(["downloading abc%\n"])
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0

        threads_started = []
        original_thread = threading.Thread

        def capture_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            threads_started.append(t)
            return t

        OllamaModels._list_local_cache = None
        with patch("subprocess.Popen", return_value=mock_proc), patch("threading.Thread", side_effect=capture_thread):
            pull_id = om.pull("llama3.2:3b", db)

        for t in threads_started:
            t.join(timeout=5)

        with db._lock:
            row = db._connect().execute("SELECT * FROM model_pulls WHERE id = ?", (pull_id,)).fetchone()
        assert row["status"] == "completed"
        OllamaModels._list_local_cache = None


class TestGetPullStatusNotFound:
    """get_pull_status not-found branch (line 382)."""

    def test_get_pull_status_not_found(self, tmp_path):
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        result = OllamaModels().get_pull_status(99999, db)
        assert result == {"error": "not found"}


class TestCancelPullEdgeCases:
    """cancel_pull edge cases (lines 390, 393-394)."""

    def test_cancel_pull_no_pid(self, tmp_path):
        """No PID — returns False (line 390)."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        with db._lock:
            conn = db._connect()
            conn.execute(
                "INSERT INTO model_pulls (model, status, started_at) VALUES (?, ?, ?)",
                ("llama3.2:3b", "pulling", time.time()),
            )
            conn.commit()
            pull_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        result = OllamaModels().cancel_pull(pull_id, db)
        assert result is False

    def test_cancel_pull_not_found(self, tmp_path):
        """Pull ID doesn't exist — returns False (line 390)."""
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        result = OllamaModels().cancel_pull(99999, db)
        assert result is False

    def test_cancel_pull_process_already_dead(self, tmp_path):
        """ProcessLookupError caught — line 393-394."""
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
        with patch("os.kill", side_effect=ProcessLookupError("process gone")):
            result = OllamaModels().cancel_pull(pull_id, db)
        assert result is True
        with db._lock:
            row = db._connect().execute("SELECT status FROM model_pulls WHERE id = ?", (pull_id,)).fetchone()
        assert row["status"] == "cancelled"
