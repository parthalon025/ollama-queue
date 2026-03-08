"""Detect services calling Ollama on port 11434."""

from __future__ import annotations

import logging
import pathlib
import platform
import re
import subprocess
import time

_log = logging.getLogger(__name__)
_OLLAMA_PORT = "11434"
_QUEUE_PORT = "7683"
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
_STREAM_PATTERN = re.compile(r"\bstream\s*[=:]\s*[Tt]rue", re.IGNORECASE)
_SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".rb", ".go", ".rs", ".sh"}
_OLLAMA_11434_PATTERN = re.compile(
    r'(OLLAMA_HOST|OLLAMA_BASE_URL|ollama[._]host|base_url)\s*[=:]\s*["\']?' r"(localhost|127\.0\.0\.1):" + _OLLAMA_PORT
)


def detect_platform() -> str:
    system = platform.system()
    if system == "Linux":
        return "linux"
    if system == "Darwin":
        return "macos"
    return "windows"


def live_scan(plat: str | None = None) -> list[dict]:
    """Phase 1: Find processes with active connections to port 11434."""
    plat = plat or detect_platform()
    try:
        if plat == "linux":
            return _live_scan_linux()
        if plat == "macos":
            return _live_scan_macos()
        return _live_scan_windows()
    except Exception:
        _log.warning("live_scan failed on platform %s", plat, exc_info=True)
        return []


def _live_scan_linux() -> list[dict]:
    result = subprocess.run(  # noqa: S603
        ["ss", "-tp"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return []
    consumers = []
    for line in result.stdout.splitlines():
        if f":{_OLLAMA_PORT}" not in line:
            continue
        m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        if not m:
            continue
        consumers.append(
            {
                "name": m.group(1),
                "pid": int(m.group(2)),
                "type": "transient",
                "last_live_seen": int(time.time()),
            }
        )
    return consumers


def _live_scan_macos() -> list[dict]:
    result = subprocess.run(  # noqa: S603
        ["lsof", f"-i:{_OLLAMA_PORT}", "-sTCP:ESTABLISHED"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return []
    consumers = []
    for line in result.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 2:
            continue
        consumers.append(
            {
                "name": parts[0],
                "pid": int(parts[1]),
                "type": "transient",
                "last_live_seen": int(time.time()),
            }
        )
    return consumers


def _live_scan_windows() -> list[dict]:
    result = subprocess.run(  # noqa: S603
        ["netstat", "-ano"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return []
    consumers = []
    for line in result.stdout.splitlines():
        if f":{_OLLAMA_PORT}" not in line or "ESTABLISHED" not in line:
            continue
        parts = line.split()
        pid = int(parts[-1]) if parts else 0
        consumers.append(
            {
                "name": f"pid:{pid}",
                "pid": pid,
                "type": "transient",
                "last_live_seen": int(time.time()),
            }
        )
    return consumers


def static_scan(search_dirs: list[str] | None = None) -> list[dict]:
    """Phase 2: Grep config files for :11434 references."""
    import os

    if search_dirs is None:
        home = os.path.expanduser("~")
        search_dirs = [home]

    seen_paths: set[str] = set()
    consumers: list[dict] = []

    for base in search_dirs:
        for path in _walk_configs(base):
            if str(path) in seen_paths:
                continue
            seen_paths.add(str(path))
            consumer = _check_config_file(path)
            if consumer:
                consumers.append(consumer)

    return consumers


def _walk_configs(base: str):
    """Yield .service, .env, .yaml, .toml files, skipping junk dirs."""
    import os

    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if fname.endswith((".service", ".env", ".yaml", ".yml", ".toml")):
                yield pathlib.Path(root) / fname


def _check_config_file(path: pathlib.Path) -> dict | None:
    """Return consumer dict if file references :11434 (not :7683)."""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None
    if not _OLLAMA_11434_PATTERN.search(text):
        return None

    suffix = path.suffix
    is_system = str(path).startswith("/etc/systemd/system")

    if path.name.endswith(".service"):
        ctype = "systemd"
        name = path.name
    elif path.name == ".env":
        ctype = "env_file"
        name = path.parent.name + "/.env"
    else:
        ctype = "config_yaml" if suffix in (".yaml", ".yml") else "config_toml"
        name = str(path)

    return {
        "name": name,
        "type": ctype,
        "patch_path": str(path),
        "is_system_path": is_system,
        "last_seen": int(time.time()),
        "detected_at": int(time.time()),
    }


def stream_check(source_dir: str | None = None, has_source: bool = True) -> dict:
    """Phase 3: Detect streaming usage in source. Returns streaming flags."""
    if not has_source:
        return {"streaming_confirmed": False, "streaming_suspect": True}
    if not source_dir:
        return {"streaming_confirmed": False, "streaming_suspect": True}

    import os

    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if not any(fname.endswith(ext) for ext in _SOURCE_EXTENSIONS):
                continue
            try:
                text = pathlib.Path(root, fname).read_text(errors="ignore")
                if _STREAM_PATTERN.search(text):
                    return {"streaming_confirmed": True, "streaming_suspect": False}
            except OSError:
                continue

    return {"streaming_confirmed": False, "streaming_suspect": False}


def deadlock_check(name: str, cmdline: str, db) -> bool:
    """Phase 4: True if process matches a managed recurring job (deadlock risk)."""
    try:
        conn = db._connect()
        rows = conn.execute("SELECT name, command FROM recurring_jobs WHERE is_active = 1").fetchall()
        for row in rows:
            if row["name"] in name or name in row["name"]:
                return True
            if row["command"] and row["command"][:50] in cmdline:
                return True
        return False
    except Exception:
        _log.warning("deadlock_check failed", exc_info=True)
        return False


def run_scan(db, search_dirs: list[str] | None = None) -> list[dict]:
    """Run all 4 phases and persist results to DB. Returns enriched consumers."""
    plat = detect_platform()
    live = live_scan(plat)
    static = static_scan(search_dirs)

    seen: dict[str, dict] = {}
    for c in static:
        key = c.get("patch_path") or c["name"]
        seen[key] = {**c, "platform": plat}
    for c in live:
        key = c["name"]
        if key not in seen:
            seen[key] = {**c, "platform": plat, "detected_at": int(time.time())}
        seen[key]["last_live_seen"] = c.get("last_live_seen")

    results = []
    for consumer in seen.values():
        source_dir = None
        if "patch_path" in consumer:
            source_dir = str(pathlib.Path(consumer["patch_path"]).parent)

        streaming = stream_check(source_dir)
        is_deadlock = deadlock_check(consumer.get("name", ""), consumer.get("cmdline", ""), db)

        consumer.update(
            {
                **streaming,
                "is_managed_job": is_deadlock,
                "source_label": _make_source_label(consumer["name"]),
                "detected_at": consumer.get("detected_at", int(time.time())),
            }
        )
        consumer.setdefault("type", "unknown")
        consumer.setdefault("platform", plat)

        db.upsert_consumer(consumer)
        results.append(consumer)

    return results


def _make_source_label(name: str) -> str:
    """Generate a queue _source label from service/process name."""
    label = re.sub(r"\.service$", "", name)
    label = re.sub(r"[^a-z0-9-]", "-", label.lower())
    return label.strip("-")[:32]
