"""Job CRUD operations for ollama-queue.

Plain English: Everything the queue does with individual jobs — submit, fetch,
start, complete, cancel, requeue, and query history. Also includes duration
estimation helpers, job metrics, model stats, model pulls, and utility queries.
"""

import json
import time


class JobsMixin:
    """Job lifecycle, duration history, job metrics, and utility operations."""

    # --- Jobs ---

    def submit_job(
        self,
        command,
        model,
        priority,
        timeout,
        source,
        tag=None,
        max_retries=0,
        resource_profile="ollama",
        recurring_job_id=None,
    ):
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

    def get_job(self, job_id):
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def get_next_job(self):
        """Return the highest-priority pending job for execution.

        .. deprecated::
            No longer used by the daemon (replaced by Daemon._dequeue_next_job()
            which implements SJF + aging). Retained for the proxy/embed path and
            backwards compatibility with callers outside the daemon.
        """
        with self._lock:
            conn = self._connect()
            now = time.time()
            row = conn.execute(
                """SELECT * FROM jobs
                   WHERE status = 'pending'
                   AND (retry_after IS NULL OR retry_after <= ?)
                   ORDER BY priority ASC,
                            CASE WHEN model LIKE '%embed%' OR model LIKE '%nomic%' OR model LIKE '%bge%'
                                      OR model LIKE '%mxbai%' OR model LIKE '%all-minilm%'
                                      OR command LIKE '%/api/embed%'
                                 THEN 0 ELSE 1 END ASC,
                            submitted_at ASC
                   LIMIT 1""",
                (now,),
            ).fetchone()
            return dict(row) if row else None

    def start_job(self, job_id):
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                (time.time(), job_id),
            )
            conn.commit()

    def complete_job(
        self,
        job_id,
        exit_code,
        stdout_tail,
        stderr_tail,
        outcome_reason=None,
    ):
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

    def kill_job(self, job_id, reason, stdout_tail="", stderr_tail=""):
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

    def get_running_jobs(self):
        """Return all jobs currently in 'running' status."""
        with self._lock:
            rows = self._connect().execute("SELECT * FROM jobs WHERE status = 'running'").fetchall()
        return [dict(r) for r in rows]

    def reset_job_to_pending(self, job_id):
        """Reset a job from running back to pending (orphan recovery)."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE jobs SET status='pending', started_at=NULL, pid=NULL WHERE id=?",
                (job_id,),
            )
            conn.commit()

    def requeue_preempted_job(self, job_id):
        """Reset a preempted job to pending and increment preemption_count.

        IMPORTANT: Never touches DLQ. Preempted jobs are healthy work interrupted
        deliberately. DLQ means 'permanent failure requiring human review' — using
        it for preemption corrupts its semantic meaning and requires manual recovery.
        """
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs SET
                       status = 'pending',
                       started_at = NULL,
                       pid = NULL,
                       submitted_at = ?,
                       preemption_count = COALESCE(preemption_count, 0) + 1
                   WHERE id = ?""",
                (time.time(), job_id),
            )
            conn.commit()

    def cancel_job(self, job_id):
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs
                   SET status = 'cancelled', outcome_reason = 'user cancelled', completed_at = ?
                   WHERE id = ? AND status = 'pending'""",
                (time.time(), job_id),
            )
            conn.commit()

    def set_job_priority(self, job_id, priority):
        """Update priority of a pending job. Returns True if updated."""
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE jobs SET priority = ? WHERE id = ? AND status = 'pending'",
                (priority, job_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_pending_jobs(self, exclude_sentinel=True):
        """Return pending jobs ordered by priority then submission time.

        Args:
            exclude_sentinel: When True (default), omits proxy sentinel jobs
                (command LIKE 'proxy:%'). Pass False only when you explicitly
                need to inspect sentinel rows (e.g. proxy tests, recovery logic).
        """
        sentinel_clause = "AND command NOT LIKE 'proxy:%'" if exclude_sentinel else ""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                f"""SELECT * FROM jobs
                   WHERE status = 'pending'
                     {sentinel_clause}
                   ORDER BY priority ASC, submitted_at ASC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def count_pending_jobs(self):
        """Return count of jobs currently waiting in the queue (status='pending')."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'pending' AND (retry_after IS NULL OR retry_after <= ?)",
                (time.time(),),
            ).fetchone()
            return row[0]

    def get_history(self, limit=20, offset=0, source=None):
        with self._lock:
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

    def set_stall_detected(self, job_id, now, signals):
        """Record stall detection timestamp and signal breakdown for a job."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE jobs SET stall_detected_at = ?, stall_signals = ? WHERE id = ?",
                (now, json.dumps(signals), job_id),
            )
            conn.commit()

    # --- Duration History ---

    def record_duration(self, source, model, duration, exit_code):
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT INTO duration_history (source, model, duration, exit_code, recorded_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (source, model, duration, exit_code, time.time()),
            )
            conn.commit()

    def get_duration_history(self, source, limit=5):
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT * FROM duration_history
                   WHERE source = ?
                   ORDER BY recorded_at DESC
                   LIMIT ?""",
                (source, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def estimate_duration(self, source):
        with self._lock:
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

    def estimate_duration_bulk(self, sources):
        """Return mean duration per source in a single query.

        Only counts successful runs (exit_code=0). Used by SJF sort to avoid
        N separate DB queries per dequeue cycle.
        """
        if not sources:
            return {}
        with self._lock:
            conn = self._connect()
            placeholders = ",".join("?" * len(sources))
            rows = conn.execute(
                f"""SELECT source, AVG(duration) as avg_dur
                    FROM duration_history
                    WHERE source IN ({placeholders}) AND exit_code = 0
                    GROUP BY source""",
                sources,
            ).fetchall()
            return {row["source"]: row["avg_dur"] for row in rows if row["avg_dur"] is not None}

    def estimate_duration_stats(self, source):
        """Return (mean, variance) from last 10 successful runs for a source.

        Uses the computational formula: Var = E[X^2] - E[X]^2
        Returns None if no history exists.
        Used by estimate_with_variance() for risk-adjusted SJF sort.
        """
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT AVG(duration) as mean_dur,
                          AVG(duration * duration) - AVG(duration) * AVG(duration) as variance
                   FROM (
                       SELECT duration FROM duration_history
                       WHERE source = ? AND exit_code = 0
                       ORDER BY recorded_at DESC
                       LIMIT 10
                   )""",
                (source,),
            ).fetchone()
            if row is None or row["mean_dur"] is None:
                return None
            return float(row["mean_dur"]), max(0.0, float(row["variance"]) if row["variance"] is not None else 0.0)

    def _set_job_retry(self, job_id, retry_after, delay):
        """Atomically increment retry_count, reset status, and set retry timing."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE jobs
                   SET retry_count = retry_count + 1,
                       retry_after = ?,
                       last_retry_delay = ?,
                       status = 'pending',
                       completed_at = NULL
                   WHERE id = ?""",
                (retry_after, delay, job_id),
            )
            conn.commit()

    # ── job_metrics CRUD ─────────────────────────────────────────────

    def store_job_metrics(self, job_id, metrics):
        """INSERT OR REPLACE a row in job_metrics from a dict of Ollama response fields."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT OR REPLACE INTO job_metrics
                   (job_id, model, command, resource_profile,
                    load_duration_ns, prompt_eval_count, prompt_eval_duration_ns,
                    eval_count, eval_duration_ns, total_duration_ns,
                    model_size_gb, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    metrics.get("model", ""),
                    metrics.get("command"),
                    metrics.get("resource_profile"),
                    metrics.get("load_duration_ns"),
                    metrics.get("prompt_eval_count"),
                    metrics.get("prompt_eval_duration_ns"),
                    metrics.get("eval_count"),
                    metrics.get("eval_duration_ns"),
                    metrics.get("total_duration_ns"),
                    metrics.get("model_size_gb"),
                    metrics.get("completed_at", time.time()),
                ),
            )
            conn.commit()

    def get_job_metrics(self, job_id):
        """Return the job_metrics row as a dict, or None if not found."""
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM job_metrics WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def get_tok_per_min(self, model):
        """Derive tok/min from eval_count and eval_duration_ns for recent jobs."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT eval_count, eval_duration_ns FROM job_metrics
                   WHERE model = ? AND eval_count IS NOT NULL
                     AND eval_duration_ns IS NOT NULL AND eval_duration_ns > 0
                   ORDER BY completed_at DESC LIMIT 50""",
                (model,),
            ).fetchall()
            return [(r[0] / (r[1] / 1_000_000_000)) * 60 for r in rows]

    def get_job_durations(self, model, command=None):
        """Wall-clock durations (seconds) from the jobs table (completed_at - started_at)."""
        with self._lock:
            conn = self._connect()
            if command is not None:
                rows = conn.execute(
                    """SELECT completed_at - started_at FROM jobs
                       WHERE model = ? AND command = ?
                         AND completed_at IS NOT NULL AND started_at IS NOT NULL
                       ORDER BY completed_at DESC LIMIT 50""",
                    (model, command),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT completed_at - started_at FROM jobs
                       WHERE model = ?
                         AND completed_at IS NOT NULL AND started_at IS NOT NULL
                       ORDER BY completed_at DESC LIMIT 50""",
                    (model,),
                ).fetchall()
            return [r[0] for r in rows]

    def get_load_durations(self, model):
        """Convert load_duration_ns to seconds for recent jobs."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT load_duration_ns FROM job_metrics
                   WHERE model = ? AND load_duration_ns IS NOT NULL
                   ORDER BY completed_at DESC LIMIT 50""",
                (model,),
            ).fetchall()
            return [r[0] / 1_000_000_000 for r in rows]

    def get_model_stats(self):
        """Aggregate stats per model: run_count, avg_tok_per_min, avg_warmup_s, model_size_gb."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT model,
                          COUNT(*) as run_count,
                          AVG(CASE WHEN eval_count IS NOT NULL AND eval_duration_ns IS NOT NULL
                                    AND eval_duration_ns > 0
                               THEN (CAST(eval_count AS REAL) / (eval_duration_ns / 1e9)) * 60
                               ELSE NULL END) as avg_tok_per_min,
                          AVG(CASE WHEN load_duration_ns IS NOT NULL
                               THEN load_duration_ns / 1e9
                               ELSE NULL END) as avg_warmup_s,
                          MAX(model_size_gb) as model_size_gb
                   FROM job_metrics
                   GROUP BY model"""
            ).fetchall()
            result = {}
            for r in rows:
                result[r[0]] = {
                    "run_count": r[1],
                    "avg_tok_per_min": r[2],
                    "avg_warmup_s": r[3],
                    "model_size_gb": r[4],
                }
            return result

    def has_pulling_model(self, model_name):
        """Return True if any pull for model_name is currently in 'pulling' status."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT 1 FROM model_pulls WHERE model = ? AND status = 'pulling' LIMIT 1",
                (model_name,),
            ).fetchone()
            return row is not None

    # --- Maintenance ---

    def prune_old_data(self):
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

    # --- Utility ---

    def list_tables(self):
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            return [row["name"] for row in rows]

    # --- Consumers ---

    def upsert_consumer(self, data):
        """Insert or update a consumer by (name, platform). Returns id."""
        with self._lock:
            conn = self._connect()
            existing = conn.execute(
                "SELECT id FROM consumers WHERE name = ? AND platform = ?",
                (data["name"], data["platform"]),
            ).fetchone()
            if existing:
                sets = ", ".join(f"{k} = ?" for k in data if k not in ("name", "platform"))
                if not sets:
                    return existing["id"]  # nothing to update beyond the key fields
                vals = [v for k, v in data.items() if k not in ("name", "platform")]
                conn.execute(f"UPDATE consumers SET {sets} WHERE id = ?", [*vals, existing["id"]])
                conn.commit()
                return existing["id"]
            cols = ", ".join(data.keys())
            placeholders = ", ".join("?" * len(data))
            cur = conn.execute(
                f"INSERT INTO consumers ({cols}) VALUES ({placeholders})",
                list(data.values()),
            )
            conn.commit()
            return cur.lastrowid

    def list_consumers(self):
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT * FROM consumers ORDER BY detected_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_consumer(self, consumer_id):
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM consumers WHERE id = ?", (consumer_id,)).fetchone()
            return dict(row) if row else None

    def update_consumer(self, consumer_id, **kwargs):
        with self._lock:
            conn = self._connect()
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            conn.execute(
                f"UPDATE consumers SET {sets} WHERE id = ?",
                [*kwargs.values(), consumer_id],
            )
            conn.commit()

    # ── Deferral lifecycle ──────────────────────────────────────────────

    def defer_job(self, job_id, reason, context=""):
        """Defer a job — sets status to 'deferred' and creates deferral record."""
        with self._lock:
            conn = self._connect()
            now = time.time()
            conn.execute("UPDATE jobs SET status = 'deferred' WHERE id = ?", (job_id,))
            cursor = conn.execute(
                """INSERT INTO deferrals (job_id, reason, context, deferred_at)
                   VALUES (?, ?, ?, ?)""",
                (job_id, reason, context, now),
            )
            conn.commit()
            return cursor.lastrowid

    def get_deferral(self, deferral_id):
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM deferrals WHERE id = ?", (deferral_id,)).fetchone()
            return dict(row) if row else None

    def list_deferred(self, unscheduled_only=False):
        with self._lock:
            conn = self._connect()
            where = "WHERE resumed_at IS NULL"
            if unscheduled_only:
                where += " AND scheduled_for IS NULL"
            return [
                dict(r) for r in conn.execute(f"SELECT * FROM deferrals {where} ORDER BY deferred_at ASC").fetchall()
            ]

    def update_deferral_schedule(self, deferral_id, scheduled_for, scoring_snapshot=None):
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE deferrals SET scheduled_for = ?, scoring_snapshot = ?
                   WHERE id = ?""",
                (scheduled_for, scoring_snapshot, deferral_id),
            )
            conn.commit()

    def resume_deferred_job(self, deferral_id):
        """Resume a deferred job — flip status back to pending, mark deferral as resumed."""
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT job_id FROM deferrals WHERE id = ?", (deferral_id,)).fetchone()
            if not row:
                return
            now = time.time()
            conn.execute("UPDATE jobs SET status = 'pending' WHERE id = ?", (row["job_id"],))
            conn.execute(
                "UPDATE deferrals SET resumed_at = ?, scheduled_for = NULL WHERE id = ?",
                (now, deferral_id),
            )
            conn.commit()
