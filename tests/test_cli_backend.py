"""Tests for ollama-queue backend CLI subcommand."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from ollama_queue.cli import main


def test_backend_status_all(tmp_path):
    """ollama-queue backend status calls GET /api/backends."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"url": "http://host1:11434", "healthy": True, "gpu_name": "RTX 5080", "vram_pct": 22.0},
    ]
    with patch("ollama_queue.cli_backend.httpx.get", return_value=mock_resp):
        runner = CliRunner()
        result = runner.invoke(main, ["--db", str(tmp_path / "test.db"), "backend", "status"])
    assert result.exit_code == 0
    assert "host1" in result.output


def test_backend_status_specific(tmp_path):
    """ollama-queue backend status <url> filters to a single backend."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"url": "http://host1:11434", "healthy": True, "gpu_name": "RTX 5080", "vram_pct": 22.0},
        {"url": "http://host2:11434", "healthy": False, "gpu_name": "GTX 1650", "vram_pct": 80.0},
    ]
    with patch("ollama_queue.cli_backend.httpx.get", return_value=mock_resp):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--db", str(tmp_path / "test.db"), "backend", "status", "http://host1:11434"],
        )
    assert result.exit_code == 0
    assert "host1" in result.output
    assert "host2" not in result.output


def test_backend_status_not_found(tmp_path):
    """ollama-queue backend status <url> shows message when backend not found."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"url": "http://host1:11434", "healthy": True, "gpu_name": "RTX 5080", "vram_pct": 22.0},
    ]
    with patch("ollama_queue.cli_backend.httpx.get", return_value=mock_resp):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--db", str(tmp_path / "test.db"), "backend", "status", "http://missing:11434"],
        )
    assert "not found" in result.output


def test_backend_sync_models_specific(tmp_path):
    """ollama-queue backend sync-models <url> calls POST /command."""
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


def test_backend_sync_models_all(tmp_path):
    """ollama-queue backend sync-models (no url) dispatches to all backends."""
    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json.return_value = [
        {"url": "http://host1:11434"},
        {"url": "http://host2:11434"},
    ]
    mock_post_resp = MagicMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json.return_value = {"ok": True}
    with (
        patch("ollama_queue.cli_backend.httpx.get", return_value=mock_get_resp),
        patch("ollama_queue.cli_backend.httpx.post", return_value=mock_post_resp) as mock_post,
    ):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--db", str(tmp_path / "test.db"), "backend", "sync-models"],
        )
    assert result.exit_code == 0
    assert mock_post.call_count == 2


def test_backend_update_ollama_specific(tmp_path):
    """ollama-queue backend update-ollama <url> calls POST /command."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True}
    with patch("ollama_queue.cli_backend.httpx.post", return_value=mock_resp):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--db", str(tmp_path / "test.db"), "backend", "update-ollama", "http://host1:11434"],
        )
    assert result.exit_code == 0


def test_backend_status_shows_healthy_and_down(tmp_path):
    """Status output distinguishes healthy vs down backends."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"url": "http://host1:11434", "healthy": True, "gpu_name": "RTX 5080", "vram_pct": 22.0},
        {"url": "http://host2:11434", "healthy": False, "gpu_name": "GTX 1650", "vram_pct": 80.0},
    ]
    with patch("ollama_queue.cli_backend.httpx.get", return_value=mock_resp):
        runner = CliRunner()
        result = runner.invoke(main, ["--db", str(tmp_path / "test.db"), "backend", "status"])
    assert result.exit_code == 0
    assert "OK" in result.output
    assert "DOWN" in result.output


def test_backend_command_error_response(tmp_path):
    """Command dispatch shows error on non-200 response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {"detail": "Backend unreachable"}
    mock_resp.text = "Internal Server Error"
    with patch("ollama_queue.cli_backend.httpx.post", return_value=mock_resp):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--db", str(tmp_path / "test.db"), "backend", "sync-models", "http://host1:11434"],
        )
    assert "Error" in result.output
