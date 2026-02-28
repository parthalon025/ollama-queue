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

        with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
            models = OllamaModels().list_local()
        names = [m["name"] for m in models]
        assert "qwen2.5-coder:14b" in names
        assert "nomic-embed-text:latest" in names
        assert len(models) == 4

    def test_list_local_parses_size_bytes(self):
        from ollama_queue.models import OllamaModels

        with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
            models = OllamaModels().list_local()
        embed = next(m for m in models if "nomic" in m["name"])
        # 274 MB
        assert embed["size_bytes"] > 270_000_000
        assert embed["size_bytes"] < 290_000_000

    def test_list_local_returns_empty_on_failure(self):
        from ollama_queue.models import OllamaModels

        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        with patch("subprocess.run", return_value=mock):
            result = OllamaModels().list_local()
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
        with patch("subprocess.run", return_value=_mock_run(OLLAMA_LIST_OUTPUT)):
            vram = OllamaModels().estimate_vram_mb("qwen2.5:7b", db)
        # 4.7 GB * 1.3 safety = ~6110 MB
        assert vram > 5000
        assert vram < 7000

    def test_estimate_vram_default_when_unknown(self, tmp_path):
        from ollama_queue.db import Database
        from ollama_queue.models import OllamaModels

        db = Database(str(tmp_path / "q.db"))
        db.initialize()
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        with patch("subprocess.run", return_value=mock):
            vram = OllamaModels().estimate_vram_mb("unknown-model:latest", db)
        assert vram == pytest.approx(4000.0)


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
