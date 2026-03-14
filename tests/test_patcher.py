import pathlib
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

import pytest

from ollama_queue.config.patcher import patch_consumer, revert_consumer


@pytest.fixture
def systemd_unit(tmp_path):
    unit = tmp_path / "aria.service"
    unit.write_text(
        "[Unit]\nDescription=ARIA\n\n[Service]\nExecStart=/usr/bin/aria\n\n[Install]\nWantedBy=default.target\n"
    )
    return unit


@pytest.fixture
def env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=localhost:11434\nOTHER_VAR=hello\n")
    return env


def test_patch_systemd_injects_env(systemd_unit, monkeypatch):
    monkeypatch.setattr("ollama_queue.config.patcher._reload_systemd", lambda: None)
    monkeypatch.setattr("ollama_queue.config.patcher._restart_service", lambda name: None)
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
    monkeypatch.setattr("ollama_queue.config.patcher._reload_systemd", lambda: None)
    monkeypatch.setattr("ollama_queue.config.patcher._restart_service", lambda name: None)
    consumer = {
        "name": "aria.service",
        "type": "systemd",
        "patch_path": str(systemd_unit),
        "restart_policy": "deferred",
    }
    patch_consumer(consumer)
    bak = pathlib.Path(str(systemd_unit) + ".ollama-queue.bak")
    assert bak.exists()


def test_revert_systemd_restores_original(systemd_unit, monkeypatch):
    monkeypatch.setattr("ollama_queue.config.patcher._reload_systemd", lambda: None)
    monkeypatch.setattr("ollama_queue.config.patcher._restart_service", lambda name: None)
    consumer = {
        "name": "aria.service",
        "type": "systemd",
        "patch_path": str(systemd_unit),
        "restart_policy": "deferred",
    }
    original = systemd_unit.read_text()
    patch_consumer(consumer)
    revert_consumer(consumer)
    assert systemd_unit.read_text() == original


def test_patch_env_file_replaces_host(env_file, monkeypatch):
    consumer = {"name": "proj/.env", "type": "env_file", "patch_path": str(env_file), "restart_policy": "deferred"}
    result = patch_consumer(consumer)
    assert result["patch_applied"] is True
    text = env_file.read_text()
    assert "OLLAMA_HOST=localhost:7683" in text
    assert "OTHER_VAR=hello" in text


def test_patch_env_file_appends_if_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER_VAR=hello\n")
    consumer = {"name": "proj/.env", "type": "env_file", "patch_path": str(env), "restart_policy": "deferred"}
    patch_consumer(consumer)
    assert "OLLAMA_HOST=localhost:7683" in env.read_text()


from ollama_queue.config.patcher import check_health


def test_health_confirmed_when_both_signals_clear(tmp_path):
    db_path = str(tmp_path / "test.db")
    from ollama_queue.db import Database

    db = Database(db_path)
    db.initialize()
    consumer_id = db.upsert_consumer(
        {
            "name": "aria",
            "type": "systemd",
            "platform": "linux",
            "source_label": "aria",
            "detected_at": 0,
            "onboarded_at": 1,
            "request_count": 5,
        }
    )
    consumer = db.get_consumer(consumer_id)

    with mock_patch("ollama_queue.config.patcher._port_has_process", side_effect=[False, True]):
        result = check_health(consumer, db, plat="linux")

    assert result["old_port_clear"] is True
    assert result["new_port_active"] is True
    assert result["status"] == "confirmed"


def test_health_partial_when_only_old_port_clear(tmp_path):
    db_path = str(tmp_path / "test.db")
    from ollama_queue.db import Database

    db = Database(db_path)
    db.initialize()
    consumer_id = db.upsert_consumer(
        {
            "name": "aria",
            "type": "systemd",
            "platform": "linux",
            "source_label": "aria",
            "detected_at": 0,
        }
    )
    consumer = db.get_consumer(consumer_id)

    with mock_patch("ollama_queue.config.patcher._port_has_process", side_effect=[False, False]):
        result = check_health(consumer, db, plat="linux")

    assert result["old_port_clear"] is True
    assert result["new_port_active"] is False
    assert result["status"] == "partial"


# ---------------------------------------------------------------------------
# Coverage gap tests
# ---------------------------------------------------------------------------


class TestPatchConsumerNoPatchPath:
    """patch_consumer() with no patch_path returns manual_snippet (line 20)."""

    def test_returns_manual_snippet_when_no_patch_path(self):
        consumer = {"name": "my-app", "type": "env_file"}
        result = patch_consumer(consumer)
        assert result["patch_applied"] is False
        assert result["patch_type"] == "manual_snippet"
        assert "OLLAMA_HOST=localhost:7683" in result["patch_snippet"]


class TestPatchConsumerUnknownType:
    """Unknown type falls through to manual_snippet (lines 43-49)."""

    def test_returns_manual_snippet_for_unknown_type(self, tmp_path):
        f = tmp_path / "conf"
        f.write_text("some content")
        consumer = {"name": "mystery", "type": "unknown_format", "patch_path": str(f)}
        result = patch_consumer(consumer)
        assert result["patch_applied"] is False
        assert result["patch_type"] == "manual_snippet"


class TestPatchConsumerYaml:
    """_patch_yaml() exercises lines 111-126."""

    def test_patch_yaml_ollama_key(self, tmp_path):
        try:
            from ruamel.yaml import YAML
        except ImportError:
            pytest.skip("ruamel.yaml not installed")
        f = tmp_path / "config.yaml"
        f.write_text("ollama:\n  host: localhost:11434\n")
        consumer = {"name": "app", "type": "config_yaml", "patch_path": str(f), "restart_policy": "deferred"}
        result = patch_consumer(consumer)
        assert result["patch_applied"] is True
        yaml = YAML()
        with open(f) as fh:
            data = yaml.load(fh)
        assert data["ollama"]["host"] == "localhost:7683"

    def test_patch_yaml_base_url_key(self, tmp_path):
        try:
            from ruamel.yaml import YAML
        except ImportError:
            pytest.skip("ruamel.yaml not installed")
        f = tmp_path / "config.yaml"
        f.write_text("base_url: http://localhost:11434\n")
        consumer = {"name": "app", "type": "config_yaml", "patch_path": str(f), "restart_policy": "deferred"}
        result = patch_consumer(consumer)
        assert result["patch_applied"] is True
        yaml = YAML()
        with open(f) as fh:
            data = yaml.load(fh)
        assert data["base_url"] == "http://localhost:7683"

    def test_patch_yaml_empty_file(self, tmp_path):
        try:
            from ruamel.yaml import YAML
        except ImportError:
            pytest.skip("ruamel.yaml not installed")
        f = tmp_path / "config.yaml"
        f.write_text("")
        consumer = {"name": "app", "type": "config_yaml", "patch_path": str(f), "restart_policy": "deferred"}
        result = patch_consumer(consumer)
        assert result["patch_applied"] is True
        yaml = YAML()
        with open(f) as fh:
            data = yaml.load(fh)
        assert data["ollama"]["host"] == "localhost:7683"

    def test_patch_yaml_no_ollama_no_base_url(self, tmp_path):
        """No known keys — defaults to creating ollama.host (line 124)."""
        try:
            from ruamel.yaml import YAML
        except ImportError:
            pytest.skip("ruamel.yaml not installed")
        f = tmp_path / "config.yaml"
        f.write_text("other_key: value\n")
        consumer = {"name": "app", "type": "config_yaml", "patch_path": str(f), "restart_policy": "deferred"}
        result = patch_consumer(consumer)
        assert result["patch_applied"] is True
        yaml = YAML()
        with open(f) as fh:
            data = yaml.load(fh)
        assert data["ollama"]["host"] == "localhost:7683"


class TestPatchConsumerToml:
    """_patch_toml() exercises lines 130-138."""

    def test_patch_toml_ollama_key(self, tmp_path):
        try:
            import tomlkit
        except ImportError:
            pytest.skip("tomlkit not installed")
        f = tmp_path / "config.toml"
        f.write_text('[ollama]\nhost = "localhost:11434"\n')
        consumer = {"name": "app", "type": "config_toml", "patch_path": str(f), "restart_policy": "deferred"}
        result = patch_consumer(consumer)
        assert result["patch_applied"] is True
        data = tomlkit.loads(f.read_text())
        assert data["ollama"]["host"] == "localhost:7683"

    def test_patch_toml_no_ollama_key(self, tmp_path):
        """No ollama key — defaults to creating one (line 137)."""
        try:
            import tomlkit
        except ImportError:
            pytest.skip("tomlkit not installed")
        f = tmp_path / "config.toml"
        f.write_text('other_key = "value"\n')
        consumer = {"name": "app", "type": "config_toml", "patch_path": str(f), "restart_policy": "deferred"}
        result = patch_consumer(consumer)
        assert result["patch_applied"] is True
        data = tomlkit.loads(f.read_text())
        assert data["ollama"]["host"] == "localhost:7683"


class TestPatchConsumerErrorBranch:
    """patch_consumer raises on internal error (lines 50-58)."""

    def test_raises_on_patch_error(self, tmp_path):
        f = tmp_path / "conf.env"
        f.write_text("OLLAMA_HOST=old")
        consumer = {"name": "app", "type": "env_file", "patch_path": str(f)}
        with (
            mock_patch("ollama_queue.config.patcher._patch_env", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            patch_consumer(consumer)


class TestPatchSystemdIdempotent:
    """_patch_systemd returns early if inject already present (line 95)."""

    def test_systemd_idempotent(self, systemd_unit, monkeypatch):
        monkeypatch.setattr("ollama_queue.config.patcher._reload_systemd", lambda: None)
        monkeypatch.setattr("ollama_queue.config.patcher._restart_service", lambda name: None)
        consumer = {
            "name": "aria.service",
            "type": "systemd",
            "patch_path": str(systemd_unit),
            "restart_policy": "immediate",
        }
        # Patch once
        patch_consumer(consumer)
        text_after_first = systemd_unit.read_text()
        # Patch again — should not duplicate the inject
        patch_consumer(consumer)
        assert systemd_unit.read_text() == text_after_first


class TestRevertConsumer:
    """revert_consumer branches (lines 68-73, 82)."""

    def test_revert_no_patch_path_logs_and_returns(self):
        consumer = {"name": "app", "type": "env_file"}
        # Should not raise
        revert_consumer(consumer)

    def test_revert_systemd_immediate_restarts(self, systemd_unit, monkeypatch):
        reload_calls = []
        restart_calls = []
        monkeypatch.setattr("ollama_queue.config.patcher._reload_systemd", lambda: reload_calls.append(1))
        monkeypatch.setattr("ollama_queue.config.patcher._restart_service", lambda name: restart_calls.append(name))
        consumer = {
            "name": "aria.service",
            "type": "systemd",
            "patch_path": str(systemd_unit),
            "restart_policy": "immediate",
        }
        patch_consumer(consumer)
        reload_calls.clear()
        restart_calls.clear()
        revert_consumer(consumer)
        # revert_consumer calls _reload_systemd and _restart_service for immediate systemd
        assert len(reload_calls) == 1
        assert "aria.service" in restart_calls


class TestReloadSystemd:
    """_reload_systemd() exercises lines 142-155."""

    def test_reload_systemd_success(self):
        from ollama_queue.config.patcher import _reload_systemd

        with mock_patch("ollama_queue.config.patcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            assert _reload_systemd() is True

    def test_reload_systemd_nonzero(self):
        from ollama_queue.config.patcher import _reload_systemd

        with mock_patch("ollama_queue.config.patcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error msg")
            assert _reload_systemd() is False

    def test_reload_systemd_exception(self):
        from ollama_queue.config.patcher import _reload_systemd

        with mock_patch("ollama_queue.config.patcher.subprocess.run", side_effect=OSError("no systemctl")):
            assert _reload_systemd() is False


class TestRestartService:
    """_restart_service() exercises lines 159-172."""

    def test_restart_service_success(self):
        from ollama_queue.config.patcher import _restart_service

        with mock_patch("ollama_queue.config.patcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            assert _restart_service("aria.service") is True

    def test_restart_service_nonzero(self):
        from ollama_queue.config.patcher import _restart_service

        with mock_patch("ollama_queue.config.patcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="fail")
            assert _restart_service("aria.service") is False

    def test_restart_service_exception(self):
        from ollama_queue.config.patcher import _restart_service

        with mock_patch("ollama_queue.config.patcher.subprocess.run", side_effect=OSError("no systemctl")):
            assert _restart_service("aria.service") is False


class TestCheckHealthFailed:
    """check_health status='failed' when old port is NOT clear (line 197)."""

    def test_health_failed_when_old_port_active(self, tmp_path):
        from ollama_queue.db import Database

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        consumer_id = db.upsert_consumer(
            {
                "name": "aria",
                "type": "systemd",
                "platform": "linux",
                "source_label": "aria",
                "detected_at": 0,
            }
        )
        consumer = db.get_consumer(consumer_id)
        # old_port NOT clear (True = process on 11434), new_port active
        with mock_patch("ollama_queue.config.patcher._port_has_process", side_effect=[True, True]):
            result = check_health(consumer, db, plat="linux")
        assert result["status"] == "failed"


class TestPortHasProcess:
    """_port_has_process exercises lines 205-231."""

    def test_linux_found(self):
        from ollama_queue.config.patcher import _port_has_process

        ss_output = 'State  Recv-Q Send-Q  Local Address:Port  Peer Address:Port\nESTAB  0      0      127.0.0.1:7683    127.0.0.1:5432  users:(("aria",pid=1234,fd=5))'
        with mock_patch("ollama_queue.config.patcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ss_output, stderr="")
            result = _port_has_process("7683", "aria.service", "linux")
        assert result is True

    def test_linux_not_found(self):
        from ollama_queue.config.patcher import _port_has_process

        ss_output = 'State  Recv-Q Send-Q  Local Address:Port  Peer Address:Port\nESTAB  0      0      127.0.0.1:5432    127.0.0.1:5432  users:(("postgres",pid=1234,fd=5))'
        with mock_patch("ollama_queue.config.patcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=ss_output, stderr="")
            result = _port_has_process("7683", "aria.service", "linux")
        assert result is False

    def test_linux_nonzero_returncode(self):
        from ollama_queue.config.patcher import _port_has_process

        with mock_patch("ollama_queue.config.patcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = _port_has_process("7683", "aria.service", "linux")
        assert result is False

    def test_macos_found(self):
        from ollama_queue.config.patcher import _port_has_process

        lsof_output = "COMMAND  PID  USER  FD  TYPE  DEVICE  SIZE/OFF  NODE  NAME\naria  1234  user  5u  IPv4  12345  0t0  TCP  *:7683 (LISTEN)"
        with mock_patch("ollama_queue.config.patcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=lsof_output, stderr="")
            result = _port_has_process("7683", "aria.service", "macos")
        assert result is True

    def test_macos_not_found(self):
        from ollama_queue.config.patcher import _port_has_process

        lsof_output = "COMMAND  PID  USER  FD  TYPE  DEVICE  SIZE/OFF  NODE  NAME\npostgres  1234  user  5u  IPv4  12345  0t0  TCP  *:5432 (LISTEN)"
        with mock_patch("ollama_queue.config.patcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=lsof_output, stderr="")
            result = _port_has_process("7683", "aria.service", "macos")
        assert result is False

    def test_macos_nonzero_returncode(self):
        from ollama_queue.config.patcher import _port_has_process

        with mock_patch("ollama_queue.config.patcher.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = _port_has_process("7683", "aria.service", "macos")
        assert result is False

    def test_unsupported_platform(self):
        from ollama_queue.config.patcher import _port_has_process

        result = _port_has_process("7683", "aria.service", "windows")
        assert result is False

    def test_exception_returns_false(self):
        from ollama_queue.config.patcher import _port_has_process

        with mock_patch("ollama_queue.config.patcher.subprocess.run", side_effect=OSError("no ss")):
            result = _port_has_process("7683", "aria.service", "linux")
        assert result is False


class TestMtimeGuard:
    """TOCTOU guard: patch_consumer rejects if file modified since scan (#17)."""

    def test_rejects_patch_after_file_modified(self, tmp_path):
        import os
        import time

        f = tmp_path / ".env"
        f.write_text("OLLAMA_HOST=localhost:11434\n")
        scanned_mtime = os.path.getmtime(f)

        # Ensure filesystem mtime granularity is exceeded
        time.sleep(0.05)
        f.write_text("OLLAMA_HOST=localhost:11434\n# modified\n")

        consumer = {
            "name": "app",
            "type": "env_file",
            "patch_path": str(f),
            "restart_policy": "deferred",
            "scanned_mtime": scanned_mtime,
        }
        with pytest.raises(ValueError, match="modified since scan"):
            patch_consumer(consumer)

    def test_allows_patch_when_mtime_matches(self, tmp_path):
        import os

        f = tmp_path / ".env"
        f.write_text("OLLAMA_HOST=localhost:11434\n")
        scanned_mtime = os.path.getmtime(f)

        consumer = {
            "name": "app",
            "type": "env_file",
            "patch_path": str(f),
            "restart_policy": "deferred",
            "scanned_mtime": scanned_mtime,
        }
        result = patch_consumer(consumer)
        assert result["patch_applied"] is True

    def test_skips_check_when_no_scanned_mtime(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("OLLAMA_HOST=localhost:11434\n")
        consumer = {
            "name": "app",
            "type": "env_file",
            "patch_path": str(f),
            "restart_policy": "deferred",
        }
        # No scanned_mtime — should proceed without checking
        result = patch_consumer(consumer)
        assert result["patch_applied"] is True


class TestPatchEnvFile:
    """_patch_env with env_file type (line 39 — via patch_consumer)."""

    def test_patch_env_via_consumer(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("OLLAMA_HOST=old_value\n")
        consumer = {"name": "app", "type": "env_file", "patch_path": str(f), "restart_policy": "deferred"}
        result = patch_consumer(consumer)
        assert result["patch_applied"] is True
        assert result["status"] == "pending_restart"
        assert "OLLAMA_HOST=localhost:7683" in f.read_text()
