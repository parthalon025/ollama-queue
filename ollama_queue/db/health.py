"""Health log, daemon state, and proxy claim operations for ollama-queue.

Plain English: Records system health snapshots (RAM, VRAM, load, swap) every poll
cycle, manages the singleton daemon state row, and handles proxy claim/release
for the /api/generate pass-through endpoint.
"""

import time


class HealthMixin:
    """Health log, daemon state, and proxy claim operations."""

    # --- Health Log ---

    def log_health(
        self,
        ram_pct,
        vram_pct,
        load_avg,
        swap_pct,
        ollama_model,
        queue_depth,
        daemon_state,
    ):
        with self._lock:
            conn = self._connect()

            def _do():
                conn.execute(
                    """INSERT INTO health_log
                       (timestamp, ram_pct, vram_pct, load_avg, swap_pct, ollama_model, queue_depth, daemon_state)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (time.time(), ram_pct, vram_pct, load_avg, swap_pct, ollama_model, queue_depth, daemon_state),
                )
                conn.commit()

            self._retry_on_busy(_do)

    def get_health_log(self, hours=24):
        with self._lock:
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
            "burst_regime",
        }
    )

    def update_daemon_state(self, **kwargs):
        if not kwargs:
            return
        unknown = set(kwargs) - self._DAEMON_STATE_FIELDS
        if unknown:
            raise ValueError(f"Unknown daemon_state fields: {unknown}")
        with self._lock:
            conn = self._connect()
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values())

            def _do():
                conn.execute(f"UPDATE daemon_state SET {sets} WHERE id = 1", vals)
                conn.commit()

            self._retry_on_busy(_do)

    def get_daemon_state(self):
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM daemon_state WHERE id = 1").fetchone()
            if row is None:
                return {"state": "idle", "current_job_id": None, "paused_since": None, "paused_reason": None}
            return dict(row)

    # --- Proxy ---

    def try_claim_for_proxy(self):
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

    def release_proxy_claim(self):
        """Release proxy claim back to idle."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE daemon_state SET state = 'idle', current_job_id = NULL WHERE id = 1 AND current_job_id = -1"
            )
            conn.commit()
