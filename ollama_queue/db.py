"""SQLite database layer for ollama-queue."""

import json
import logging
import sqlite3
import threading
import time

_log = logging.getLogger(__name__)

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
    "default_max_retries": 0,
    "retry_backoff_base_seconds": 60,
    "retry_backoff_multiplier": 2.0,
    "stall_multiplier": 2.0,
    "priority_categories": '{"critical":[1,2],"high":[3,4],"normal":[5,6],"low":[7,8],"background":[9,10]}',
    "priority_category_colors": '{"critical":"#ef4444","high":"#f97316","normal":"#3b82f6","low":"#6b7280","background":"#374151"}',  # noqa: E501
    "resource_profiles": '{"ollama":{"check_vram":true,"check_ram":true,"check_load":true},"any":{"check_vram":false,"check_ram":false,"check_load":false}}',  # noqa: E501
    "max_concurrent_jobs": 1,
    "concurrent_shadow_hours": 24,
    "vram_safety_factor": 1.3,
}


class Database:
    """Synchronous SQLite database for the ollama-queue daemon."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
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
                estimated_duration REAL,
                tag TEXT,
                max_retries INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                retry_after REAL,
                stall_detected_at REAL,
                recurring_job_id INTEGER REFERENCES recurring_jobs(id),
                resource_profile TEXT DEFAULT 'ollama'
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

            CREATE TABLE IF NOT EXISTS recurring_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                command TEXT NOT NULL,
                model TEXT,
                priority INTEGER DEFAULT 5,
                timeout INTEGER DEFAULT 600,
                source TEXT,
                tag TEXT,
                resource_profile TEXT DEFAULT 'ollama',
                interval_seconds INTEGER,
                cron_expression TEXT,
                next_run REAL,
                last_run REAL,
                last_job_id INTEGER,
                max_retries INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                pinned INTEGER DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schedule_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                recurring_job_id INTEGER REFERENCES recurring_jobs(id),
                job_id INTEGER,
                details TEXT
            );

            CREATE TABLE IF NOT EXISTS dlq (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_job_id INTEGER NOT NULL,
                command TEXT NOT NULL,
                model TEXT,
                source TEXT,
                tag TEXT,
                priority INTEGER,
                timeout INTEGER NOT NULL DEFAULT 600,
                resource_profile TEXT DEFAULT 'ollama',
                failure_reason TEXT NOT NULL,
                stdout_tail TEXT,
                stderr_tail TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 0,
                moved_at REAL NOT NULL,
                resolved_at REAL,
                resolution TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_recurring_job_id
                ON jobs (recurring_job_id) WHERE recurring_job_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS model_registry (
                name              TEXT PRIMARY KEY,
                size_bytes        INTEGER,
                vram_observed_mb  REAL,
                resource_profile  TEXT DEFAULT 'ollama',
                type_tag          TEXT DEFAULT 'general',
                last_seen         REAL
            );

            CREATE TABLE IF NOT EXISTS model_pulls (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                model        TEXT NOT NULL,
                status       TEXT DEFAULT 'pulling',
                progress_pct REAL DEFAULT 0,
                pid          INTEGER,
                started_at   REAL,
                completed_at REAL,
                error        TEXT
            );
        """)

        # Migrate pre-cron DBs that lack the cron_expression column
        try:
            conn.execute("ALTER TABLE recurring_jobs ADD COLUMN cron_expression TEXT")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                _log.debug("cron_expression column already exists — skipping migration")
            else:
                raise

        # Migrate DBs that lack the pinned column
        try:
            conn.execute("ALTER TABLE recurring_jobs ADD COLUMN pinned INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                _log.debug("pinned column already exists — skipping migration")
            else:
                raise

        # Migrate: add pid column to jobs
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN pid INTEGER")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                _log.debug("jobs.pid column already exists — skipping migration")
            else:
                raise

        # Seed settings defaults
        now = time.time()
        for key, value in DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), now),
            )

        # Seed daemon_state singleton
        conn.execute("INSERT OR IGNORE INTO daemon_state (id, state) VALUES (1, 'idle')")

        conn.commit()

    # --- Jobs ---

    def submit_job(
        self,
        command: str,
        model: str,
        priority: int,
        timeout: int,
        source: str,
        tag: str | None = None,
        max_retries: int = 0,
        resource_profile: str = "ollama",
        recurring_job_id: int | None = None,
    ) -> int:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """INSERT INTO jobs
                   (command, model, priority, timeout, source, submitted_at,
                    tag, max_retries, resource_profile, recurring_job_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    command,
                    model,
                    priority,
                    timeout,
                    source,
                    time.time(),
                    tag,
                    max_retries,
                    resource_profile,
                    recurring_job_id,
                ),
            )
            conn.commit()
            assert cur.lastrowid is not None
            return cur.lastrowid

    def get_job(self, job_id: int) -> dict | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def get_next_job(self) -> dict | None:
        conn = self._connect()
        now = time.time()
        row = conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'pending'
               AND (retry_after IS NULL OR retry_after <= ?)
               ORDER BY priority ASC, submitted_at ASC
               LIMIT 1""",
            (now,),
        ).fetchone()
        return dict(row) if row else None

    def start_job(self, job_id: int) -> None:
        with self._lock:
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
        outcome_reason: str | None = None,
    ) -> None:
        status = "completed" if exit_code == 0 else "failed"
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs
                   SET status = ?, exit_code = ?, stdout_tail = ?, stderr_tail = ?,
                       outcome_reason = ?, completed_at = ?
                   WHERE id = ?""",
                (status, exit_code, stdout_tail, stderr_tail, outcome_reason, time.time(), job_id),
            )
            conn.commit()

    def kill_job(self, job_id: int, reason: str, stdout_tail: str = "", stderr_tail: str = "") -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs
                   SET status = 'killed', outcome_reason = ?, completed_at = ?,
                       stdout_tail = ?, stderr_tail = ?
                   WHERE id = ?""",
                (reason, time.time(), stdout_tail, stderr_tail, job_id),
            )
            conn.commit()

    def get_running_jobs(self) -> list[dict]:
        """Return all jobs currently in 'running' status."""
        with self._lock:
            rows = self._connect().execute("SELECT * FROM jobs WHERE status = 'running'").fetchall()
        return [dict(r) for r in rows]

    def reset_job_to_pending(self, job_id: int) -> None:
        """Reset a job from running back to pending (orphan recovery)."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE jobs SET status='pending', started_at=NULL, pid=NULL WHERE id=?",
                (job_id,),
            )
            conn.commit()

    def cancel_job(self, job_id: int) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs
                   SET status = 'cancelled', outcome_reason = 'user cancelled', completed_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (time.time(), job_id),
            )
            conn.commit()

    def set_job_priority(self, job_id: int, priority: int) -> bool:
        """Update priority of a pending job. Returns True if updated."""
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE jobs SET priority = ? WHERE id = ? AND status = 'pending'",
                (priority, job_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_pending_jobs(self) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'pending'
               ORDER BY priority ASC, submitted_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_history(self, limit: int = 20, offset: int = 0, source: str | None = None) -> list[dict]:
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
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return json.loads(row["value"])

    def set_setting(self, key: str, value) -> None:
        with self._lock:
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

    def record_duration(self, source: str, model: str, duration: float, exit_code: int) -> None:
        with self._lock:
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
        with self._lock:
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

    _DAEMON_STATE_FIELDS = frozenset(
        {
            "state",
            "current_job_id",
            "paused_reason",
            "paused_since",
            "last_poll_at",
            "jobs_completed_today",
            "jobs_failed_today",
            "uptime_since",
        }
    )

    def update_daemon_state(self, **kwargs) -> None:
        if not kwargs:
            return
        unknown = set(kwargs) - self._DAEMON_STATE_FIELDS
        if unknown:
            raise ValueError(f"Unknown daemon_state fields: {unknown}")
        with self._lock:
            conn = self._connect()
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values())
            conn.execute(f"UPDATE daemon_state SET {sets} WHERE id = 1", vals)
            conn.commit()

    def get_daemon_state(self) -> dict:
        conn = self._connect()
        row = conn.execute("SELECT * FROM daemon_state WHERE id = 1").fetchone()
        if row is None:
            return {"state": "idle", "current_job_id": None, "paused_since": None, "paused_reason": None}
        return dict(row)

    # --- Proxy ---

    def try_claim_for_proxy(self) -> bool:
        """Claim a queue slot for a proxy /api/generate request.

        Respects max_concurrent_jobs. Returns True if claimed.
        """
        with self._lock:
            conn = self._connect()
            max_slots = int(self.get_setting("max_concurrent_jobs") or 1)
            # Count running jobs from jobs table
            running = conn.execute("SELECT COUNT(*) as cnt FROM jobs WHERE status = 'running'").fetchone()["cnt"]
            if running >= max_slots:
                return False
            # Block only when another proxy is already claimed (sentinel -1).
            # Real running jobs are already counted via the jobs table above.
            state = conn.execute("SELECT current_job_id FROM daemon_state WHERE id=1").fetchone()
            if state and state["current_job_id"] == -1:
                return False
            conn.execute("UPDATE daemon_state SET state='running', current_job_id=-1 WHERE id=1")
            conn.commit()
            return True

    def release_proxy_claim(self) -> None:
        """Release proxy claim back to idle."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE daemon_state SET state = 'idle', current_job_id = NULL " "WHERE id = 1 AND current_job_id = -1"
            )
            conn.commit()

    # --- Maintenance ---

    def prune_old_data(self) -> None:
        with self._lock:
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

    # --- Recurring Jobs ---

    def add_recurring_job(
        self,
        name: str,
        command: str,
        interval_seconds: int | None = None,
        cron_expression: str | None = None,
        model: str | None = None,
        priority: int = 5,
        timeout: int = 600,
        source: str | None = None,
        tag: str | None = None,
        resource_profile: str = "ollama",
        max_retries: int = 0,
        next_run: float | None = None,
        pinned: bool = False,
    ) -> int:
        if interval_seconds is None and cron_expression is None:
            raise ValueError("Either interval_seconds or cron_expression must be provided")
        conn = self._connect()
        now = time.time()
        if next_run is None and cron_expression:
            import datetime

            from croniter import croniter

            start_dt = datetime.datetime.fromtimestamp(now)
            next_run = croniter(cron_expression, start_dt).get_next(datetime.datetime).timestamp()
        elif next_run is None:
            next_run = now
        cur = conn.execute(
            """INSERT INTO recurring_jobs
               (name, command, model, priority, timeout, source, tag,
                resource_profile, interval_seconds, cron_expression, next_run, max_retries, pinned, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                command,
                model,
                priority,
                timeout,
                source,
                tag,
                resource_profile,
                interval_seconds,
                cron_expression,
                next_run,
                max_retries,
                1 if pinned else 0,
                now,
            ),
        )
        conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def get_recurring_job(self, rj_id: int) -> dict | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM recurring_jobs WHERE id = ?", (rj_id,)).fetchone()
        return dict(row) if row else None

    def get_recurring_job_by_name(self, name: str) -> dict | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM recurring_jobs WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def list_recurring_jobs(self) -> list[dict]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM recurring_jobs ORDER BY priority ASC, name ASC").fetchall()
        return [dict(r) for r in rows]

    def get_due_recurring_jobs(self, now: float) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT * FROM recurring_jobs
               WHERE enabled = 1 AND next_run <= ?
               ORDER BY priority ASC, next_run ASC""",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_recurring_next_run(self, rj_id: int, completed_at: float, job_id: int | None = None) -> None:
        with self._lock:
            conn = self._connect()
            rj_row = conn.execute("SELECT * FROM recurring_jobs WHERE id = ?", (rj_id,)).fetchone()
            if rj_row is None:
                _log.error("update_recurring_next_run: recurring job id=%d not found (deleted?)", rj_id)
                return
            rj = dict(rj_row)
            cron_expr = rj.get("cron_expression")
            if cron_expr:
                import datetime

                from croniter import croniter

                start_dt = datetime.datetime.fromtimestamp(completed_at)
                next_run = croniter(cron_expr, start_dt).get_next(datetime.datetime).timestamp()
            else:
                next_run = completed_at + rj["interval_seconds"]
            conn.execute(
                """UPDATE recurring_jobs
                   SET next_run = ?, last_run = ?, last_job_id = ?
                   WHERE id = ?""",
                (next_run, completed_at, job_id, rj_id),
            )
            conn.commit()

    def set_recurring_job_enabled(self, name: str, enabled: bool) -> bool:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE recurring_jobs SET enabled = ? WHERE name = ?",
                (1 if enabled else 0, name),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_recurring_job(self, name: str) -> bool:
        conn = self._connect()
        with self._lock:
            rj = conn.execute("SELECT id FROM recurring_jobs WHERE name = ?", (name,)).fetchone()
            if rj is None:
                return False
            rj_id = rj["id"]
            conn.execute(
                "UPDATE jobs SET recurring_job_id = NULL WHERE recurring_job_id = ?",
                (rj_id,),
            )
            conn.execute("DELETE FROM schedule_events WHERE recurring_job_id = ?", (rj_id,))
            cur = conn.execute("DELETE FROM recurring_jobs WHERE id = ?", (rj_id,))
            conn.commit()
            return cur.rowcount > 0

    def update_recurring_job(self, rj_id: int, **fields: object) -> bool:
        """Update allowed fields on a recurring job. Returns True if found."""
        allowed = {
            "name",
            "command",
            "interval_seconds",
            "cron_expression",
            "model",
            "priority",
            "timeout",
            "source",
            "tag",
            "enabled",
            "next_run",
            "pinned",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        conn = self._connect()
        with self._lock:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = [*list(updates.values()), rj_id]
            cur = conn.execute(
                f"UPDATE recurring_jobs SET {set_clause} WHERE id = ?",
                values,
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_recurring_job_by_id(self, rj_id: int) -> bool:
        """Delete a recurring job by ID with full cascade cleanup."""
        conn = self._connect()
        with self._lock:
            conn.execute(
                "UPDATE jobs SET recurring_job_id = NULL WHERE recurring_job_id = ?",
                (rj_id,),
            )
            conn.execute("DELETE FROM schedule_events WHERE recurring_job_id = ?", (rj_id,))
            cur = conn.execute("DELETE FROM recurring_jobs WHERE id = ?", (rj_id,))
            conn.commit()
            return cur.rowcount > 0

    def log_schedule_event(
        self,
        event_type: str,
        recurring_job_id: int | None = None,
        job_id: int | None = None,
        details: dict | None = None,
    ) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT INTO schedule_events
                   (timestamp, event_type, recurring_job_id, job_id, details)
                   VALUES (?, ?, ?, ?, ?)""",
                (time.time(), event_type, recurring_job_id, job_id, json.dumps(details) if details else None),
            )
            conn.commit()

    def get_schedule_events(self, limit: int = 100) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM schedule_events ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def has_pending_or_running_recurring(self, recurring_job_id: int) -> bool:
        conn = self._connect()
        row = conn.execute(
            """SELECT 1 FROM jobs
               WHERE recurring_job_id = ? AND status IN ('pending', 'running')
               LIMIT 1""",
            (recurring_job_id,),
        ).fetchone()
        return row is not None

    def _set_recurring_next_run(self, rj_id: int, next_run: float) -> None:
        """Update next_run for a recurring job. Single-purpose DB API — no direct _connect() outside this class."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE recurring_jobs SET next_run = ? WHERE id = ?",
                (next_run, rj_id),
            )
            conn.commit()

    def _set_job_retry_after(self, job_id: int, retry_after: float) -> None:
        """Increment retry_count and set retry_after timestamp, keeping status pending."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs
                   SET retry_count = retry_count + 1,
                       retry_after = ?,
                       status = 'pending'
                   WHERE id = ?""",
                (retry_after, job_id),
            )
            conn.commit()

    # --- DLQ ---

    def move_to_dlq(self, job_id: int, failure_reason: str) -> int | None:
        with self._lock:
            conn = self._connect()
            job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job_row:
                return None
            job = dict(job_row)
            cur = conn.execute(
                """INSERT INTO dlq
                   (original_job_id, command, model, source, tag, priority,
                    timeout, resource_profile, failure_reason, stdout_tail, stderr_tail,
                    retry_count, max_retries, moved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    job["command"],
                    job["model"],
                    job["source"],
                    job.get("tag"),
                    job["priority"],
                    job.get("timeout", 600),
                    job.get("resource_profile", "ollama"),
                    failure_reason,
                    job.get("stdout_tail", ""),
                    job.get("stderr_tail", ""),
                    job.get("retry_count", 0),
                    job.get("max_retries", 0),
                    time.time(),
                ),
            )
            conn.execute("UPDATE jobs SET status = 'dead' WHERE id = ?", (job_id,))
            conn.commit()
            return cur.lastrowid

    def get_dlq_entry(self, dlq_id: int) -> dict | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM dlq WHERE id = ?", (dlq_id,)).fetchone()
        return dict(row) if row else None

    def list_dlq(self, include_resolved: bool = False) -> list[dict]:
        conn = self._connect()
        if include_resolved:
            rows = conn.execute("SELECT * FROM dlq ORDER BY moved_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM dlq WHERE resolution IS NULL ORDER BY moved_at DESC").fetchall()
        return [dict(r) for r in rows]

    def dismiss_dlq_entry(self, dlq_id: int) -> bool:
        conn = self._connect()
        cur = conn.execute(
            "UPDATE dlq SET resolution = 'dismissed', resolved_at = ? WHERE id = ?",
            (time.time(), dlq_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def retry_dlq_entry(self, dlq_id: int) -> int | None:
        with self._lock:
            conn = self._connect()
            entry_row = conn.execute("SELECT * FROM dlq WHERE id = ?", (dlq_id,)).fetchone()
            if not entry_row:
                return None
            entry = dict(entry_row)
            # M2: guard against already-resolved entries
            if entry.get("resolution") is not None:
                return None
            cur = conn.execute(
                """INSERT INTO jobs
                   (command, model, priority, timeout, source, submitted_at,
                    tag, max_retries, resource_profile, recurring_job_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry["command"],
                    entry["model"],
                    entry["priority"] or 5,
                    entry.get("timeout") or 600,
                    entry["source"] or "dlq-retry",
                    time.time(),
                    entry.get("tag"),
                    entry.get("max_retries", 0),
                    entry.get("resource_profile", "ollama"),
                    None,
                ),
            )
            new_job_id = cur.lastrowid
            conn.execute(
                """UPDATE dlq SET resolution = 'retried', resolved_at = ?,
                   retry_count = retry_count + 1 WHERE id = ?""",
                (time.time(), dlq_id),
            )
            conn.commit()
            assert new_job_id is not None
            return new_job_id

    def clear_dlq(self) -> int:
        conn = self._connect()
        cur = conn.execute("DELETE FROM dlq WHERE resolution IS NOT NULL")
        conn.commit()
        return cur.rowcount

    def has_pulling_model(self, model_name: str) -> bool:
        """Return True if any pull for model_name is currently in 'pulling' status."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT 1 FROM model_pulls WHERE model = ? AND status = 'pulling' LIMIT 1",
                (model_name,),
            ).fetchone()
            return row is not None

    # --- Utility ---

    def list_tables(self) -> list[str]:
        conn = self._connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]
