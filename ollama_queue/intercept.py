"""iptables intercept mode — redirect :11434 → :7683 at the network layer."""

from __future__ import annotations

import logging
import platform
import subprocess

_log = logging.getLogger(__name__)
_OLLAMA_PORT = 11434


def enable_intercept(uid: int, queue_port: int = 7683) -> dict:
    """Add iptables REDIRECT rule. Returns status dict."""
    if platform.system() != "Linux":
        return {"enabled": False, "error": "iptables intercept is Linux-only"}

    rule = _build_rule("-A", uid, queue_port)
    try:
        result = subprocess.run(
            ["sudo", "iptables", "-t", "nat"] + rule,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"enabled": False, "error": result.stderr.strip()}
        _persist_rules()
        return {"enabled": True, "uid": uid, "queue_port": queue_port}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"enabled": False, "error": str(e)}


def disable_intercept(uid: int, queue_port: int = 7683) -> dict:
    """Remove iptables REDIRECT rule."""
    if platform.system() != "Linux":
        return {"enabled": False, "error": "Linux-only"}

    rule = _build_rule("-D", uid, queue_port)
    try:
        result = subprocess.run(
            ["sudo", "iptables", "-t", "nat"] + rule,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            _log.error("disable_intercept: iptables exited %d: %s", result.returncode, result.stderr.strip())
            return {"enabled": True, "error": result.stderr.strip()}
        return {"enabled": False}
    except (OSError, subprocess.TimeoutExpired) as e:
        _log.error("disable_intercept failed: %s", e)
        return {"enabled": True, "error": str(e)}


def get_intercept_status(uid: int, queue_port: int = 7683) -> dict:
    """Return current intercept status — verifies iptables, not just DB flag."""
    present = _rule_present(uid, queue_port)
    return {"enabled": present, "uid": uid, "rule_present": present}


def _rule_present(uid: int, queue_port: int) -> bool:
    """Check if our redirect rule exists in iptables OUTPUT chain."""
    try:
        result = subprocess.run(
            ["sudo", "iptables", "-t", "nat", "-L", "OUTPUT", "-n", "--line-numbers"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return str(queue_port) in result.stdout and str(uid) in result.stdout
    except Exception as e:
        _log.warning("_rule_present check failed: %s", e)
        return False


def _build_rule(action: str, uid: int, queue_port: int) -> list[str]:
    return [
        action,
        "OUTPUT",
        "-p",
        "tcp",
        "--dport",
        str(_OLLAMA_PORT),
        "-m",
        "owner",
        "!",
        "--uid-owner",
        str(uid),
        "-j",
        "REDIRECT",
        "--to-port",
        str(queue_port),
    ]


def _persist_rules() -> None:
    """Save iptables rules so they survive reboot (best-effort)."""
    try:
        result = subprocess.run(["sudo", "iptables-save"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            with open("/etc/iptables/rules.v4", "w") as f:
                f.write(result.stdout)
    except Exception as e:
        _log.debug("iptables-save failed — rules will not persist across reboot: %s", e)
