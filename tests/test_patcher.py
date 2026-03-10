import pathlib

import pytest

from ollama_queue.patcher import patch_consumer, revert_consumer


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
    monkeypatch.setattr("ollama_queue.patcher._reload_systemd", lambda: None)
    monkeypatch.setattr("ollama_queue.patcher._restart_service", lambda name: None)
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


from unittest.mock import patch as mock_patch

from ollama_queue.patcher import check_health


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

    with mock_patch("ollama_queue.patcher._port_has_process", side_effect=[False, False]):
        result = check_health(consumer, db, plat="linux")

    assert result["old_port_clear"] is True
    assert result["new_port_active"] is False
    assert result["status"] == "partial"
