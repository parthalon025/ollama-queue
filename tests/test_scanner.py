from unittest.mock import MagicMock, patch

from ollama_queue.scanner import detect_platform, live_scan


def test_detect_platform_linux():
    with patch("ollama_queue.scanner.platform.system", return_value="Linux"):
        assert detect_platform() == "linux"


def test_detect_platform_macos():
    with patch("ollama_queue.scanner.platform.system", return_value="Darwin"):
        assert detect_platform() == "macos"


def test_detect_platform_windows():
    with patch("ollama_queue.scanner.platform.system", return_value="Windows"):
        assert detect_platform() == "windows"


def test_live_scan_linux_parses_ss_output():
    ss_output = 'tcp   ESTAB  0  0  127.0.0.1:52340  127.0.0.1:11434  users:(("aria",pid=1234,fd=7))\n'
    with patch("ollama_queue.scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=ss_output, stderr="")
        results = live_scan("linux")
    assert len(results) == 1
    assert results[0]["name"] == "aria"
    assert results[0]["pid"] == 1234
    assert results[0]["type"] == "transient"


def test_live_scan_returns_empty_on_failure():
    with patch("ollama_queue.scanner.subprocess.run", side_effect=OSError("no ss")):
        results = live_scan("linux")
    assert results == []


def test_live_scan_macos_parses_lsof_output():
    lsof_output = (
        "COMMAND  PID  USER  FD  TYPE  DEVICE  SIZE/OFF  NODE  NAME\n"
        "python3  5678 user  10u IPv4  0x1234  0t0  TCP 127.0.0.1:52000->127.0.0.1:11434 (ESTABLISHED)\n"
    )
    with patch("ollama_queue.scanner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=lsof_output, stderr="")
        results = live_scan("macos")
    assert len(results) == 1
    assert results[0]["name"] == "python3"
    assert results[0]["pid"] == 5678


# ── Static scan tests ──────────────────────────────────────────────────────
from ollama_queue.scanner import static_scan


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
from ollama_queue.scanner import deadlock_check, run_scan, stream_check


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

    with patch("ollama_queue.scanner.live_scan", return_value=[]):
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

    with patch("ollama_queue.scanner.live_scan", return_value=[]):
        run_scan(db, search_dirs=[str(tmp_path)])

    consumers = db.list_consumers()
    assert len(consumers) == 1
    assert consumers[0]["status"] == "discovered"
