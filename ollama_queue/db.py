"""SQLite database layer for ollama-queue."""

import json
import sqlite3
import time

DEFAULTS = {
    "poll_interval_seconds": 5,
    "ram_pause_pct": 85,
    "ram_resume_pct": 75,
    "vram_pause_pct": 90,
    "vram_resume_pct": 80,
    "load_pause_multiplier": 2.0,
    "load_resume_multiplier": 1.5,
    "swap_pause_pct": 50,
    "swap_resume_pct": 40,
    "yield_to_interactive": True,
    "health_log_retention_days": 7,
    "job_log_retention_days": 30,
    "duration_stats_retention_days": 90,
    "default_timeout_seconds": 600,
    "default_priority": 5,
}


class Database:
    """Synchronous SQLite database for the ollama-queue daemon."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def initialize(self) -> None:
        """Create all tables and seed defaults."""
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL,
                model TEXT,
                priority INTEGER DEFAULT 5,
                timeout INTEGER DEFAULT 600,
                source TEXT,
                status TEXT DEFAULT 'pending',
                submitted_at REAL NOT NULL,
                started_at REAL,
                completed_at REAL,
                exit_code INTEGER,
                outcome_reason TEXT,
                stdout_tail TEXT,
                stderr_tail TEXT,
                estimated_duration REAL
            );

            CREATE TABLE IF NOT EXISTS duration_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                model TEXT,
                duration REAL NOT NULL,
                exit_code INTEGER,
                recorded_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS health_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                ram_pct REAL,
                vram_pct REAL,
                load_avg REAL,
                swap_pct REAL,
                ollama_model TEXT,
                queue_depth INTEGER,
                daemon_state TEXT
            );

            CREATE TABLE IF NOT EXISTS daemon_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL DEFAULT 'idle',
                current_job_id INTEGER,
                paused_reason TEXT,
                paused_since REAL,
                last_poll_at REAL,
                jobs_completed_today INTEGER DEFAULT 0,
                jobs_failed_today INTEGER DEFAULT 0,
                uptime_since REAL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL
            );
        """)

        # Seed settings defaults
        now = time.time()
        for key, value in DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), now),
            )

        # Seed daemon_state singleton
        conn.execute(
            "INSERT OR IGNORE INTO daemon_state (id, state) VALUES (1, 'idle')"
        )

        conn.commit()

    # --- Jobs ---

    def submit_job(
        self, command: str, model: str, priority: int, timeout: int, source: str
    ) -> int:
        conn = self._connect()
        cur = conn.execute(
            """INSERT INTO jobs (command, model, priority, timeout, source, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (command, model, priority, timeout, source, time.time()),
        )
        conn.commit()
        return cur.lastrowid

    def get_job(self, job_id: int) -> dict | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def get_next_job(self) -> dict | None:
        conn = self._connect()
        row = conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'pending'
               ORDER BY priority ASC, submitted_at ASC
               LIMIT 1"""
        ).fetchone()
        return dict(row) if row else None

    def start_job(self, job_id: int) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
            (time.time(), job_id),
        )
        conn.commit()

    def complete_job(
        self,
        job_id: int,
        exit_code: int,
        stdout_tail: str,
        stderr_tail: str,
        outcome_reason: str | None,
    ) -> None:
        status = "completed" if exit_code == 0 else "failed"
        conn = self._connect()
        conn.execute(
            """UPDATE jobs
               SET status = ?, exit_code = ?, stdout_tail = ?, stderr_tail = ?,
                   outcome_reason = ?, completed_at = ?
               WHERE id = ?""",
            (status, exit_code, stdout_tail, stderr_tail, outcome_reason, time.time(), job_id),
        )
        conn.commit()

    def kill_job(self, job_id: int, reason: str) -> None:
        conn = self._connect()
        conn.execute(
            """UPDATE jobs
               SET status = 'killed', outcome_reason = ?, completed_at = ?
               WHERE id = ?""",
            (reason, time.time(), job_id),
        )
        conn.commit()

    def cancel_job(self, job_id: int) -> None:
        conn = self._connect()
        conn.execute(
            """UPDATE jobs
               SET status = 'cancelled', outcome_reason = 'user cancelled', completed_at = ?
               WHERE id = ? AND status = 'pending'""",
            (time.time(), job_id),
        )
        conn.commit()

    def get_pending_jobs(self) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'pending'
               ORDER BY priority ASC, submitted_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_history(
        self, limit: int = 20, offset: int = 0, source: str | None = None
    ) -> list[dict]:
        conn = self._connect()
        if source is not None:
            rows = conn.execute(
                """SELECT * FROM jobs
                   WHERE status IN ('completed', 'failed', 'killed', 'cancelled')
                     AND source = ?
                   ORDER BY completed_at DESC
                   LIMIT ? OFFSET ?""",
                (source, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM jobs
                   WHERE status IN ('completed', 'failed', 'killed', 'cancelled')
                   ORDER BY completed_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Settings ---

    def get_setting(self, key: str):
        conn = self._connect()
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["value"])

    def set_setting(self, key: str, value) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time()),
        )
        conn.commit()

    def get_all_settings(self) -> dict:
        conn = self._connect()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    # --- Duration History ---

    def record_duration(
        self, source: str, model: str, duration: float, exit_code: int
    ) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO duration_history (source, model, duration, exit_code, recorded_at)
               VALUES (?, ?, ?, ?, ?)""",
            (source, model, duration, exit_code, time.time()),
        )
        conn.commit()

    def get_duration_history(self, source: str, limit: int = 5) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM duration_history
               WHERE source = ?
               ORDER BY recorded_at DESC
               LIMIT ?""",
            (source, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def estimate_duration(self, source: str) -> float | None:
        conn = self._connect()
        row = conn.execute(
            """SELECT AVG(duration) as avg_dur
               FROM (
                   SELECT duration FROM duration_history
                   WHERE source = ? AND exit_code = 0
                   ORDER BY recorded_at DESC
                   LIMIT 5
               )""",
            (source,),
        ).fetchone()
        if row is None or row["avg_dur"] is None:
            return None
        return row["avg_dur"]

    # --- Health Log ---

    def log_health(
        self,
        ram_pct: float,
        vram_pct: float,
        load_avg: float,
        swap_pct: float,
        ollama_model: str,
        queue_depth: int,
        daemon_state: str,
    ) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO health_log
               (timestamp, ram_pct, vram_pct, load_avg, swap_pct, ollama_model, queue_depth, daemon_state)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), ram_pct, vram_pct, load_avg, swap_pct, ollama_model, queue_depth, daemon_state),
        )
        conn.commit()

    def get_health_log(self, hours: int = 24) -> list[dict]:
        conn = self._connect()
        cutoff = time.time() - (hours * 3600)
        rows = conn.execute(
            """SELECT * FROM health_log
               WHERE timestamp >= ?
               ORDER BY timestamp DESC""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Daemon State ---

    def update_daemon_state(self, **kwargs) -> None:
        if not kwargs:
            return
        conn = self._connect()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values())
        conn.execute(f"UPDATE daemon_state SET {sets} WHERE id = 1", vals)
        conn.commit()

    def get_daemon_state(self) -> dict | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM daemon_state WHERE id = 1").fetchone()
        return dict(row) if row else None

    # --- Maintenance ---

    def prune_old_data(self) -> None:
        conn = self._connect()
        now = time.time()

        job_retention = self.get_setting("job_log_retention_days") or 30
        health_retention = self.get_setting("health_log_retention_days") or 7
        duration_retention = self.get_setting("duration_stats_retention_days") or 90

        conn.execute(
            "DELETE FROM jobs WHERE completed_at IS NOT NULL AND completed_at < ?",
            (now - job_retention * 86400,),
        )
        conn.execute(
            "DELETE FROM health_log WHERE timestamp < ?",
            (now - health_retention * 86400,),
        )
        conn.execute(
            "DELETE FROM duration_history WHERE recorded_at < ?",
            (now - duration_retention * 86400,),
        )
        conn.commit()

    # --- Utility ---

    def list_tables(self) -> list[str]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]
