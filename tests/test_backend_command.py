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
    call_url = mock_client.post.call_args[0][0]
    assert ":11435/" in call_url
    assert "sync-models" in call_url


def test_command_invalid_action(client):
    """POST /command with unsupported action returns 400."""
    url = "http://testhost:11434"
    encoded = urllib.parse.quote(url, safe="")
    resp = client.post(f"/api/backends/{encoded}/command", json={"action": "rm-rf"})
    assert resp.status_code == 400


def test_command_status_uses_get(client):
    """POST /command with action=status dispatches GET (not POST) to agent."""
    url = "http://testhost:11434"
    encoded = urllib.parse.quote(url, safe="")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"backend_url": url, "version": "0.1.0"}
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.post = AsyncMock()
    with patch("ollama_queue.api.backends.httpx.AsyncClient", return_value=mock_client):
        resp = client.post(f"/api/backends/{encoded}/command", json={"action": "status"})
    assert resp.status_code == 200
    mock_client.get.assert_called_once()
    mock_client.post.assert_not_called()
    call_url = mock_client.get.call_args[0][0]
    assert ":11435/" in call_url
    assert "status" in call_url


def test_command_agent_unreachable(client):
    """POST /command returns 502 when agent is unreachable."""
    url = "http://testhost:11434"
    encoded = urllib.parse.quote(url, safe="")
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("ollama_queue.api.backends.httpx.AsyncClient", return_value=mock_client):
        resp = client.post(f"/api/backends/{encoded}/command", json={"action": "sync-models"})
    assert resp.status_code == 502
