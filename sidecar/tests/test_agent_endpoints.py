"""Tests for backend agent HTTP endpoints."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set env vars before importing the agent module
os.environ.setdefault("QUEUE_URL", "http://queue:7683")
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("BACKEND_URL", "http://100.1.2.3:11434")
os.environ.setdefault("AGENT_DATA_DIR", "/tmp/agent-test-data")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend_agent import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_health(client):
    """GET /health returns ok and metadata."""
    with patch("backend_agent._ollama_healthy", new_callable=AsyncMock, return_value=True):
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "version" in data
    assert "ollama_url" in data
    assert "queue_url" in data
    assert "backend_url" in data


def test_health_ollama_down(client):
    """GET /health reports ollama_healthy=False when Ollama is unreachable."""
    with patch("backend_agent._ollama_healthy", new_callable=AsyncMock, return_value=False):
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["ollama_healthy"] is False


def test_version(client):
    """GET /version returns agent version."""
    resp = client.get("/version")
    assert resp.status_code == 200
    assert "version" in resp.json()


def test_status(client):
    """GET /status returns full agent report."""
    with (
        patch(
            "backend_agent._ollama_tags",
            new_callable=AsyncMock,
            return_value=["qwen3.5:9b"],
        ),
        patch(
            "backend_agent._fetch_required_models",
            new_callable=AsyncMock,
            return_value=[{"name": "qwen3.5:9b", "vram_mb": 6200, "tier": "core"}],
        ),
        patch(
            "backend_agent._ollama_healthy",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "backend_agent._ollama_version",
            new_callable=AsyncMock,
            return_value="0.5.13",
        ),
        patch("backend_agent._read_cpu_pct", return_value=25.0),
        patch("backend_agent._read_ram", return_value=(45.0, 32.0)),
        patch("backend_agent._read_disk", return_value=(82.3, 500.0, 16.5)),
        patch("backend_agent._read_ollama_storage_gb", return_value=47.2),
    ):
        resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "system" in data
    assert data["models"]["installed"] == ["qwen3.5:9b"]
    assert data["models"]["missing"] == []
    assert data["ollama_version"] == "0.5.13"
    assert data["system"]["cpu_pct"] == 25.0
    assert data["system"]["ram_total_gb"] == 32.0


def test_status_missing_models(client):
    """GET /status shows models that are required but not installed."""
    with (
        patch(
            "backend_agent._ollama_tags",
            new_callable=AsyncMock,
            return_value=["qwen3.5:9b"],
        ),
        patch(
            "backend_agent._fetch_required_models",
            new_callable=AsyncMock,
            return_value=[
                {"name": "qwen3.5:9b", "vram_mb": 6200, "tier": "core"},
                {"name": "llama3:8b", "vram_mb": 5000, "tier": "core"},
            ],
        ),
        patch("backend_agent._ollama_healthy", new_callable=AsyncMock, return_value=True),
        patch("backend_agent._ollama_version", new_callable=AsyncMock, return_value="0.5.13"),
        patch("backend_agent._read_cpu_pct", return_value=10.0),
        patch("backend_agent._read_ram", return_value=(30.0, 16.0)),
        patch("backend_agent._read_disk", return_value=(50.0, 256.0, 128.0)),
        patch("backend_agent._read_ollama_storage_gb", return_value=20.0),
    ):
        resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["models"]["missing"] == ["llama3:8b"]


def test_sync_models(client):
    """POST /sync-models triggers reconciliation."""
    with patch(
        "backend_agent._reconcile",
        new_callable=AsyncMock,
        return_value={
            "required": 1,
            "installed": 1,
            "missing_before": 0,
            "pulled": [],
            "failed": [],
            "extra": [],
        },
    ):
        resp = client.post("/sync-models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["required"] == 1
    assert data["pulled"] == []


def test_restart_ollama(client):
    """POST /restart-ollama calls Docker restart."""
    mock_container = MagicMock()
    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container
    with patch.dict("sys.modules", {"docker": MagicMock(**{"from_env.return_value": mock_docker})}):
        resp = client.post("/restart-ollama")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "restarted"


def test_restart_ollama_no_docker(client):
    """POST /restart-ollama returns error when Docker is unavailable."""
    with patch.dict("sys.modules", {"docker": None}):
        # Force re-import to get the ImportError path
        import importlib

        import backend_agent

        importlib.reload(backend_agent)
        # Simulate docker import failure by patching at a higher level
    # Easier approach: mock docker.from_env to raise
    mock_docker_mod = MagicMock()
    mock_docker_mod.from_env.side_effect = Exception("Docker not installed")
    with patch.dict("sys.modules", {"docker": mock_docker_mod}):
        resp = client.post("/restart-ollama")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "Docker" in data["error"] or "error" in data


def test_update_ollama_no_docker(client):
    """POST /update-ollama returns error when Docker is unavailable."""
    mock_docker_mod = MagicMock()
    mock_docker_mod.from_env.side_effect = Exception("Docker not found")
    with patch.dict("sys.modules", {"docker": mock_docker_mod}):
        resp = client.post("/update-ollama")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "Docker" in data["error"]


def test_update_ollama_success(client):
    """POST /update-ollama pulls image and recreates container."""
    mock_container = MagicMock()
    mock_container.attrs = {
        "HostConfig": {
            "PortBindings": {"11434/tcp": [{"HostPort": "11434"}]},
            "Binds": ["/ollama:/root/.ollama"],
            "RestartPolicy": {"Name": "always"},
            "Runtime": None,
            "DeviceRequests": None,
        },
        "Config": {"Env": ["FOO=bar"]},
    }
    mock_new_container = MagicMock()
    mock_new_container.status = "running"

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container
    mock_docker.containers.run.return_value = mock_new_container

    with (
        patch.dict(
            "sys.modules",
            {"docker": MagicMock(**{"from_env.return_value": mock_docker})},
        ),
        patch("time.sleep"),
    ):
        resp = client.post("/update-ollama")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "running"
    assert len(data["log"]) >= 3
    mock_container.stop.assert_called_once_with(timeout=10)
    mock_container.remove.assert_called_once()
