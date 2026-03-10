from unittest.mock import MagicMock, patch

from ollama_queue.config.scanner import detect_platform, live_scan


def test_detect_platform_linux():
    with patch("ollama_queue.config.scanner.platform.system", return_value="Linux"):
        assert detect_platform() == "linux"


def test_detect_platform_macos():
    with patch("ollama_queue.config.scanner.platform.system", return_value="Darwin"):
        assert detect_platform() == "macos"


def test_detect_platform_windows():
    with patch("ollama_queue.config.scanner.platform.system", return_value="Windows"):
        assert detect_platform() == "windows"


def test_live_scan_linux_parses_ss_output():
    ss_output = 'tcp   ESTAB  0  0  127.0.0.1:52340  127.0.0.1:11434  users:(("aria",pid=1234,fd=7))\n'
    with patch("ollama_queue.config.scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=ss_output, stderr="")
        results = live_scan("linux")
    assert len(results) == 1
    assert results[0]["name"] == "aria"
    assert results[0]["pid"] == 1234
    assert results[0]["type"] == "transient"


def test_live_scan_returns_empty_on_failure():
    with patch("ollama_queue.config.scanner.subprocess.run", side_effect=OSError("no ss")):
        results = live_scan("linux")
    assert results == []


def test_live_scan_macos_parses_lsof_output():
    lsof_output = (
        "COMMAND  PID  USER  FD  TYPE  DEVICE  SIZE/OFF  NODE  NAME\n"
        "python3  5678 user  10u IPv4  0x1234  0t0  TCP 127.0.0.1:52000->127.0.0.1:11434 (ESTABLISHED)\n"
    )
    with patch("ollama_queue.config.scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=lsof_output, stderr="")
        results = live_scan("macos")
    assert len(results) == 1
    assert results[0]["name"] == "python3"
    assert results[0]["pid"] == 5678


# ── Static scan tests ──────────────────────────────────────────────────────
from ollama_queue.config.scanner import static_scan


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


# ── Phases 3+4 and run_scan ────────────────────────────────────────────────
from ollama_queue.config.scanner import deadlock_check, run_scan, stream_check


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


def test_run_scan_returns_merged_consumers(tmp_path):
    db_path = str(tmp_path / "test.db")
    from ollama_queue.db import Database

    db = Database(db_path)
    db.initialize()

    env = tmp_path / "myproject" / ".env"
    env.parent.mkdir()
    env.write_text("OLLAMA_HOST=localhost:11434\n")

    with patch("ollama_queue.config.scanner.live_scan", return_value=[]):
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

    with patch("ollama_queue.config.scanner.live_scan", return_value=[]):
        run_scan(db, search_dirs=[str(tmp_path)])

    consumers = db.list_consumers()
    assert len(consumers) == 1
    assert consumers[0]["status"] == "discovered"


# ── Coverage gap tests ────────────────────────────────────────────────────

import pathlib

from ollama_queue.config.scanner import (
    _check_config_file,
    _live_scan_linux,
    _live_scan_macos,
    _live_scan_windows,
)


def test_live_scan_windows_dispatches():
    """Line 65: live_scan('windows') dispatches to _live_scan_windows."""
    with patch("ollama_queue.config.scanner._live_scan_windows", return_value=[]) as mock_win:
        results = live_scan("windows")
    mock_win.assert_called_once()
    assert results == []


def test_live_scan_linux_nonzero_returncode():
    """Lines 79-80: ss non-zero returncode returns empty list."""
    mock_result = MagicMock(returncode=1, stdout="", stderr="error")
    with patch("ollama_queue.config.scanner.subprocess.run", return_value=mock_result):
        results = _live_scan_linux()
    assert results == []


def test_live_scan_macos_nonzero_returncode():
    """Lines 107-108: lsof non-zero returncode returns empty list."""
    mock_result = MagicMock(returncode=1, stdout="", stderr="error")
    with patch("ollama_queue.config.scanner.subprocess.run", return_value=mock_result):
        results = _live_scan_macos()
    assert results == []


def test_live_scan_macos_short_line():
    """Line 113: lsof line with < 2 parts is skipped."""
    lsof_output = "HEADER\nshort\n"
    mock_result = MagicMock(returncode=0, stdout=lsof_output, stderr="")
    with patch("ollama_queue.config.scanner.subprocess.run", return_value=mock_result):
        results = _live_scan_macos()
    assert results == []


def test_live_scan_windows_parses_netstat():
    """Lines 126-149: _live_scan_windows parses netstat output."""
    netstat_output = (
        "  Proto  Local Address          Foreign Address        State           PID\n"
        "  TCP    127.0.0.1:52000        127.0.0.1:11434        ESTABLISHED     4567\n"
    )
    mock_result = MagicMock(returncode=0, stdout=netstat_output, stderr="")
    with patch("ollama_queue.config.scanner.subprocess.run", return_value=mock_result):
        results = _live_scan_windows()
    assert len(results) == 1
    assert results[0]["name"] == "pid:4567"
    assert results[0]["pid"] == 4567


def test_live_scan_windows_nonzero_returncode():
    """Lines 133-134: netstat non-zero returncode returns empty list."""
    mock_result = MagicMock(returncode=1, stdout="", stderr="error")
    with patch("ollama_queue.config.scanner.subprocess.run", return_value=mock_result):
        results = _live_scan_windows()
    assert results == []


def test_check_config_file_oserror(tmp_path):
    """Lines 190-191: unreadable config file returns None."""
    path = tmp_path / "unreadable.yaml"
    path.write_text("OLLAMA_HOST=localhost:11434")
    # Patch read_text to raise
    with patch.object(pathlib.Path, "read_text", side_effect=OSError("permission denied")):
        result = _check_config_file(path)
    assert result is None


def test_check_config_file_yaml_type(tmp_path):
    """Lines 205-206: yaml file detected as config_yaml type."""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text("ollama_host: localhost:11434\n")
    result = _check_config_file(yaml_file)
    assert result is not None
    assert result["type"] == "config_yaml"


def test_check_config_file_toml_type(tmp_path):
    """Lines 205-206: toml file detected as config_toml type."""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('ollama_host = "localhost:11434"\n')
    result = _check_config_file(toml_file)
    assert result is not None
    assert result["type"] == "config_toml"


def test_stream_check_oserror(tmp_path):
    """Lines 236-237: OSError reading source file is skipped gracefully."""
    src = tmp_path / "broken.py"
    src.write_text("stream=True")
    with patch.object(pathlib.Path, "read_text", side_effect=OSError("unreadable")):
        result = stream_check(str(tmp_path))
    assert result["streaming_confirmed"] is False
    assert result["streaming_suspect"] is False


def test_deadlock_check_exception(tmp_path):
    """Lines 252, 254-256: deadlock_check returns False on DB exception."""
    mock_db = MagicMock()
    # Make _connect().execute() raise to trigger the except branch
    mock_db._connect.return_value.execute.side_effect = RuntimeError("db error")
    result = deadlock_check("test", "cmd", mock_db)
    assert result is False


def test_live_scan_linux_skips_non_ollama_and_non_matching_lines():
    """Lines 84, 87: lines without :11434 are skipped; lines without users: pattern are skipped."""
    ss_output = (
        "tcp   ESTAB  0  0  127.0.0.1:52340  127.0.0.1:5432   users:((...))  \n"  # no :11434
        "tcp   ESTAB  0  0  127.0.0.1:52340  127.0.0.1:11434  no-match-here  \n"  # no users:
        'tcp   ESTAB  0  0  127.0.0.1:52341  127.0.0.1:11434  users:(("real",pid=999,fd=3))\n'
    )
    with patch("ollama_queue.config.scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=ss_output, stderr="")
        results = _live_scan_linux()
    assert len(results) == 1
    assert results[0]["name"] == "real"


def test_stream_check_no_source_dir():
    """Line 223: stream_check with source_dir=None returns suspect."""
    result = stream_check(source_dir=None)
    assert result["streaming_confirmed"] is False
    assert result["streaming_suspect"] is True


def test_deadlock_check_matches_command(tmp_path):
    """Lines 251-252: deadlock_check matches by command substring."""
    from ollama_queue.db import Database

    db = Database(str(tmp_path / "test.db"))
    db.initialize()
    db.add_recurring_job(name="other-job", command="aria predict --full", interval_seconds=3600)
    # Name does NOT match, but command[:50] is in cmdline
    result = deadlock_check("unrelated-name", "aria predict --full --verbose", db)
    assert result is True


def test_run_scan_merges_live_consumer_not_in_static(tmp_path):
    """Lines 270-273: live consumer not in static results gets its own entry."""
    from ollama_queue.db import Database

    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    live_consumer = {
        "name": "live-process",
        "pid": 1234,
        "type": "transient",
        "last_live_seen": 1000000,
    }
    with (
        patch("ollama_queue.config.scanner.live_scan", return_value=[live_consumer]),
        patch("ollama_queue.config.scanner.static_scan", return_value=[]),
    ):
        results = run_scan(db, search_dirs=[str(tmp_path)])
    assert len(results) == 1
    assert results[0]["name"] == "live-process"
    assert "detected_at" in results[0]


def test_static_scan_default_search_dirs(tmp_path):
    """Lines 157-158: static_scan with no search_dirs defaults to home."""
    with patch("os.path.expanduser", return_value=str(tmp_path)):
        # Create an env file in the fake home
        env = tmp_path / ".env"
        env.write_text("OLLAMA_HOST=localhost:11434\n")
        results = static_scan()
    assert len(results) == 1
