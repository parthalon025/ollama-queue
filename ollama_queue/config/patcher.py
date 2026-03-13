"""Patch consumer services to route Ollama calls through port 7683."""

from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import subprocess

_log = logging.getLogger(__name__)
_QUEUE_HOST = "localhost:7683"
_BAK_SUFFIX = ".ollama-queue.bak"


def patch_consumer(consumer: dict) -> dict:
    """Apply env-var patch for consumer. Returns updated consumer dict."""
    patch_path = consumer.get("patch_path")
    if not patch_path:
        return {
            **consumer,
            "patch_type": "manual_snippet",
            "patch_snippet": f"export OLLAMA_HOST={_QUEUE_HOST}",
            "patch_applied": False,
        }

    path = pathlib.Path(patch_path)

    # TOCTOU guard: reject patch if file was modified since scan
    scanned_mtime = consumer.get("scanned_mtime")
    if scanned_mtime is not None:
        current_mtime = os.path.getmtime(path)
        if current_mtime != scanned_mtime:
            raise ValueError(f"Config file '{path}' modified since scan (re-scan before patching)")

    _backup(path)

    ctype = consumer.get("type", "unknown")
    try:
        if ctype == "systemd":
            _patch_systemd(path)
            if consumer.get("restart_policy") == "immediate":
                _reload_systemd()
                _restart_service(consumer["name"])
        elif ctype == "env_file":
            _patch_env(path)
        elif ctype == "config_yaml":
            _patch_yaml(path)
        elif ctype == "config_toml":
            _patch_toml(path)
        else:
            return {
                **consumer,
                "patch_type": "manual_snippet",
                "patch_snippet": f"export OLLAMA_HOST={_QUEUE_HOST}",
                "patch_applied": False,
            }
    except Exception:
        _log.error(
            "patch_consumer failed: name=%s type=%s path=%s",
            consumer.get("name"),
            ctype,
            patch_path,
            exc_info=True,
        )
        raise

    status = "pending_restart" if consumer.get("restart_policy") == "deferred" else "patched"
    return {**consumer, "patch_type": ctype, "patch_applied": True, "status": status}


def revert_consumer(consumer: dict) -> None:
    """Restore backup. Restarts service if it was restarted."""
    patch_path = consumer.get("patch_path")
    if not patch_path:
        _log.info(
            "revert_consumer: no patch_path for '%s' (type=%s) — nothing to revert on disk",
            consumer.get("name"),
            consumer.get("type"),
        )
        return
    path = pathlib.Path(patch_path)
    bak = pathlib.Path(str(path) + _BAK_SUFFIX)
    if bak.exists():
        shutil.copy2(str(bak), str(path))
        bak.unlink()
    if consumer.get("type") == "systemd":
        _reload_systemd()
        if consumer.get("restart_policy") == "immediate":
            _restart_service(consumer["name"])


def _backup(path: pathlib.Path) -> None:
    bak = pathlib.Path(str(path) + _BAK_SUFFIX)
    if not bak.exists():
        shutil.copy2(str(path), str(bak))


def _patch_systemd(path: pathlib.Path) -> None:
    text = path.read_text()
    inject = f'Environment="OLLAMA_HOST={_QUEUE_HOST}"'
    if inject in text:
        return
    text = re.sub(r"(\[Service\]\n)", f"\\1{inject}\n", text, count=1)
    path.write_text(text)


def _patch_env(path: pathlib.Path) -> None:
    text = path.read_text()
    new_line = f"OLLAMA_HOST={_QUEUE_HOST}"
    if re.search(r"^OLLAMA_HOST\s*=", text, re.MULTILINE):
        text = re.sub(r"^OLLAMA_HOST\s*=.*$", new_line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"
    path.write_text(text)


def _patch_yaml(path: pathlib.Path) -> None:
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    with open(path) as f:
        data = yaml.load(f)
    if data is None:
        data = {}
    if "ollama" in data and isinstance(data["ollama"], dict):
        data["ollama"]["host"] = _QUEUE_HOST
    elif "base_url" in data:
        data["base_url"] = f"http://{_QUEUE_HOST}"
    else:
        data.setdefault("ollama", {})["host"] = _QUEUE_HOST
    with open(path, "w") as f:
        yaml.dump(data, f)


def _patch_toml(path: pathlib.Path) -> None:
    import tomlkit

    text = path.read_text()
    data = tomlkit.loads(text)
    if "ollama" in data and isinstance(data["ollama"], dict):
        data["ollama"]["host"] = _QUEUE_HOST
    else:
        data.setdefault("ollama", tomlkit.table())["host"] = _QUEUE_HOST
    path.write_text(tomlkit.dumps(data))


def _reload_systemd() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            _log.warning("daemon-reload exited %d: %s", result.returncode, result.stderr.strip())
            return False
        return True
    except Exception:
        _log.warning("daemon-reload failed", exc_info=True)
        return False


def _restart_service(name: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart", name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            _log.warning("restart %s exited %d: %s", name, result.returncode, result.stderr.strip())
            return False
        return True
    except Exception:
        _log.warning("restart %s failed", name, exc_info=True)
        return False


def check_health(consumer: dict, db, plat: str | None = None) -> dict:
    """Two-signal post-patch health check. Updates DB health_status."""
    import platform as _platform
    import time

    plat = plat or (
        "linux" if _platform.system() == "Linux" else "macos" if _platform.system() == "Darwin" else "windows"
    )

    name = consumer.get("name", "")
    old_clear = not _port_has_process("11434", name, plat)
    new_active = _port_has_process("7683", name, plat)

    request_seen = False
    if consumer.get("onboarded_at") and consumer.get("request_count", 0) > 0:
        request_seen = True

    if old_clear and (new_active or request_seen):
        status = "confirmed"
    elif old_clear:
        status = "partial"
    else:
        status = "failed"

    db.update_consumer(consumer["id"], health_status=status, health_checked_at=int(time.time()))
    return {"old_port_clear": old_clear, "new_port_active": new_active, "request_seen": request_seen, "status": status}


def _port_has_process(port: str, name: str, plat: str) -> bool:
    """Check if a named process has a connection on the given port."""
    try:
        if plat == "linux":
            result = subprocess.run(
                ["ss", "-tp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                _log.warning("ss -tp exited %d: %s", result.returncode, result.stderr.strip())
                return False
            return any(f":{port}" in line and name.split(".")[0] in line for line in result.stdout.splitlines())
        if plat == "macos":
            result = subprocess.run(
                ["lsof", f"-i:{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                _log.warning("lsof -i:%s exited %d: %s", port, result.returncode, result.stderr.strip())
                return False
            return any(name.split(".")[0] in line for line in result.stdout.splitlines())
        return False
    except Exception:
        _log.warning("_port_has_process failed for port %s", port, exc_info=True)
        return False
