#!/usr/bin/env python3
"""Migrate systemd timer units to ollama-queue recurring jobs.

Covers the 10 systemd timers that currently submit through ollama-queue.
Timers with calendar-based schedules (weekday-only, monthly) are flagged
and excluded by default — they require manual cron-style scheduling support
that interval-based recurring jobs cannot express accurately.

Usage:
    python3 scripts/migrate_timers.py --dry-run          # preview only
    python3 scripts/migrate_timers.py --execute          # run migration
    python3 scripts/migrate_timers.py --dry-run --all    # include flagged timers
"""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

ARIA = str(Path.home() / "Documents/projects/ha-aria/.venv/bin/aria")
DOCS = str(Path.home() / "Documents/scripts")

# ---------------------------------------------------------------------------
# Timer map
#
# Each entry describes one systemd timer that currently submits through
# ollama-queue.  Fields:
#   interval  : str     — interval string (e.g. "24h", "6h", "5m")
#   model     : str     — Ollama model name
#   priority  : int     — queue priority (1=highest, 10=lowest)
#   timeout   : int     — job timeout in seconds
#   tag       : str     — tag for filtering in the dashboard
#   source    : str     — source label (matches existing timer name)
#   command   : str     — command to run (without `. ~/.env &&` wrapper)
#   skip      : bool    — True = requires calendar schedule; skip by default
#   skip_reason: str    — why this timer is skipped
# ---------------------------------------------------------------------------

TIMER_MAP = {
    # --- ARIA: daily/weekly Ollama jobs ---
    "aria-full": {
        "interval": "24h",
        "model": "deepseek-r1:8b",
        "priority": 5,
        "timeout": 1800,
        "tag": "aria",
        "source": "aria-full",
        "command": f"{ARIA} full",
    },
    "aria-meta-learn": {
        "interval": "7d",
        "model": "deepseek-r1:8b",
        "priority": 5,
        "timeout": 1200,
        "tag": "aria",
        "source": "aria-meta-learn",
        "command": f"{ARIA} meta-learn",
    },
    "aria-organic-discovery": {
        "interval": "7d",
        "model": "deepseek-r1:8b",
        "priority": 5,
        "timeout": 2700,
        "tag": "aria",
        "source": "aria-organic-discovery",
        "command": f"{ARIA} discover-organic",
    },
    "aria-suggest-automations": {
        "interval": "7d",
        "model": "deepseek-r1:8b",
        "priority": 5,
        "timeout": 1200,
        "tag": "aria",
        "source": "aria-suggest-automations",
        "command": f"{ARIA} suggest-automations",
    },
    # --- Telegram briefs ---
    "telegram-brief-morning": {
        "interval": "24h",
        "model": "qwen2.5:7b",
        "priority": 3,
        "timeout": 120,
        "tag": "telegram",
        "source": "telegram-brief-morning",
        "command": "telegram-brief --mode morning",
    },
    "telegram-brief-evening": {
        "interval": "24h",
        "model": "qwen2.5:7b",
        "priority": 3,
        "timeout": 120,
        "tag": "telegram",
        "source": "telegram-brief-evening",
        "command": "telegram-brief --mode evening",
    },
    "telegram-brief-alerts": {
        "interval": "5m",
        "model": "qwen2.5:7b",
        "priority": 2,
        "timeout": 60,
        "tag": "telegram",
        "source": "telegram-brief-alerts",
        "command": "telegram-brief --alerts",
    },
    # --- Notion ---
    "notion-vector-sync": {
        "interval": "6h",
        "model": "nomic-embed-text",
        "priority": 5,
        "timeout": 900,
        "tag": "notion",
        "source": "notion-vector-sync",
        "command": "notion-vector-sync",
    },
    # --- SKIPPED: requires calendar-based scheduling ---
    "telegram-brief-midday": {
        "interval": "24h",  # approximation — real schedule is Mon..Fri only
        "model": "qwen2.5:7b",
        "priority": 3,
        "timeout": 120,
        "tag": "telegram",
        "source": "telegram-brief-midday",
        "command": "telegram-brief --mode midday",
        "skip": True,
        "skip_reason": (
            "Weekday-only schedule (Mon..Fri 12:00) cannot be expressed as a "
            "fixed interval. Would run 7 days/week. Migrate manually when "
            "cron-style scheduling is added to ollama-queue."
        ),
    },
    "lessons-review": {
        "interval": "30d",  # approximation — real schedule is 14th of each month
        "model": "deepseek-r1:8b",
        "priority": 5,
        "timeout": 600,
        "tag": "maintenance",
        "source": "lessons-review",
        "command": f"bash {DOCS}/lessons-review.sh",
        "skip": True,
        "skip_reason": (
            "Monthly calendar schedule (*-*-14 10:00) cannot be expressed as "
            "a fixed interval without drifting off the 14th. Migrate manually "
            "when cron-style scheduling is added to ollama-queue."
        ),
    },
}

UNIT_DIR = Path.home() / ".config/systemd/user"


def parse_interval(s: str) -> int:
    """Parse interval string to seconds. Supports: s, m, h, d."""
    unit_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if s[-1] not in unit_map:
        raise ValueError(f"Unknown interval unit in {s!r}. Use s/m/h/d.")
    return int(s[:-1]) * unit_map[s[-1]]


def _register_job(name: str, cfg: dict, db: str) -> bool:
    """Register one recurring job via CLI. Returns True on success."""
    cmd = [
        "ollama-queue",
        "--db",
        db,
        "schedule",
        "add",
        "--name",
        name,
        "--interval",
        cfg["interval"],
        "--model",
        cfg["model"],
        "--priority",
        str(cfg["priority"]),
        "--timeout",
        str(cfg["timeout"]),
        "--tag",
        cfg["tag"],
        "--source",
        cfg["source"],
        "--",
        *shlex.split(cfg["command"]),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    ERROR registering: {result.stderr.strip()}")
        return False
    print(f"    ✓ Registered: {result.stdout.strip()}")
    return True


def _disable_unit(name: str) -> None:
    """Disable and stop the systemd timer, then remove unit files."""
    disable = subprocess.run(
        ["systemctl", "--user", "disable", "--now", f"{name}.timer"],
        capture_output=True,
        text=True,
    )
    if disable.returncode != 0:
        print(f"    WARN disabling timer: {disable.stderr.strip()}")
    timer_file = UNIT_DIR / f"{name}.timer"
    service_file = UNIT_DIR / f"{name}.service"
    timer_file.unlink(missing_ok=True)
    service_file.unlink(missing_ok=True)
    print(f"    ✓ Removed: {timer_file.name}, {service_file.name}")


def _migrate_one(name: str, cfg: dict, db: str) -> bool:
    """Register job and remove systemd unit. Returns True on success."""
    if not _register_job(name, cfg, db):
        return False
    _disable_unit(name)
    return True


def _post_migrate(db: str) -> None:
    """Reload systemd and rebalance the schedule after migration."""
    print("Reloading systemd daemon...")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    print("Rebalancing schedule...")
    result = subprocess.run(
        ["ollama-queue", "--db", db, "schedule", "rebalance"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"  ✓ {result.stdout.strip()}")
    else:
        print(f"  WARN rebalance: {result.stderr.strip()}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate systemd timer units to ollama-queue recurring jobs.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--execute", action="store_true", help="Run migration")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="include_skipped",
        help="Include calendar-schedule timers (approximate intervals)",
    )
    parser.add_argument(
        "--db",
        default=str(Path.home() / ".local/share/ollama-queue/queue.db"),
        help="Path to ollama-queue database",
    )
    return parser


def _print_summary(
    migrated: int,
    skipped_missing: int,
    skipped_calendar: int,
    errors: int,
    dry_run: bool,
) -> None:
    print(f"\n{'=' * 60}")
    print(
        f"  Summary: {migrated} migrated, {skipped_missing} not found, "
        f"{skipped_calendar} calendar-skipped, {errors} errors"
    )
    if dry_run:
        print("  (dry run — no changes applied)")
    if skipped_calendar > 0:
        print("  Run with --all to include approximate-interval migrations.")
    print(f"{'=' * 60}\n")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.print_help()
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "EXECUTE"
    print(f"\n{'=' * 60}")
    print(f"  ollama-queue timer migration — {mode}")
    print(f"  DB: {args.db}")
    print(f"{'=' * 60}\n")

    migrated = skipped_missing = skipped_calendar = errors = 0

    for name, cfg in TIMER_MAP.items():
        timer_file = UNIT_DIR / f"{name}.timer"

        if cfg.get("skip") and not args.include_skipped:
            print(f"  SKIP (calendar) {name}")
            print(f"         {cfg['skip_reason']}")
            skipped_calendar += 1
            continue

        if not timer_file.exists():
            print(f"  SKIP (not found) {name} — {timer_file} does not exist")
            skipped_missing += 1
            continue

        interval_sec = parse_interval(cfg["interval"])
        note = " ⚠ approximate interval" if cfg.get("skip") else ""
        prefix = "[DRY RUN] " if args.dry_run else ""
        print(
            f"  {prefix}MIGRATE {name}\n"
            f"    interval={cfg['interval']} ({interval_sec}s){note}\n"
            f"    model={cfg['model']}  priority={cfg['priority']}  timeout={cfg['timeout']}s\n"
            f"    tag={cfg['tag']}  source={cfg['source']}\n"
            f"    command: {cfg['command']}"
        )

        if args.dry_run:
            print()
            migrated += 1
            continue

        if _migrate_one(name, cfg, args.db):
            migrated += 1
        else:
            errors += 1
        print()

    if args.execute and migrated > 0:
        _post_migrate(args.db)

    _print_summary(migrated, skipped_missing, skipped_calendar, errors, args.dry_run)

    if args.execute:
        print("Next steps:")
        print("  ollama-queue schedule list     # verify all jobs registered")
        print("  ollama-queue status            # confirm daemon running")
        print()

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
