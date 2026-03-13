"""Recurring job (schedule) CRUD for ollama-queue.

Plain English: Everything the queue does with recurring jobs — add, list, update,
delete, enable/disable, compute next run times, log schedule events, and manage
batch next-run updates for rebalancing.
"""

import datetime
import json
import logging
import time
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)


def _local_dt(ts: float) -> datetime.datetime:
    """Convert unix timestamp to timezone-aware local datetime for cron evaluation.

    Uses the system's local timezone so that cron expressions like '0 7 * * *'
    fire at 07:00 local time regardless of DST transitions (#10).
    """
    try:
        return datetime.datetime.fromtimestamp(ts, tz=ZoneInfo("localtime"))
    except Exception:
        return datetime.datetime.fromtimestamp(ts, tz=datetime.UTC)


class ScheduleMixin:
    """Recurring job lifecycle and schedule event operations."""

    def add_recurring_job(
        self,
        name,
        command,
        interval_seconds=None,
        cron_expression=None,
        model=None,
        priority=5,
        timeout=600,
        source=None,
        tag=None,
        resource_profile="ollama",
        max_retries=0,
        next_run=None,
        pinned=False,
        check_command=None,
        max_runs=None,
        description=None,
    ):
        if interval_seconds is None and cron_expression is None:
            raise ValueError("Either interval_seconds or cron_expression must be provided")
        if cron_expression:
            try:
                import datetime

                from croniter import croniter

                croniter(cron_expression, datetime.datetime.now())
            except (ValueError, KeyError) as e:
                raise ValueError(f"Invalid cron expression '{cron_expression}': {e}") from e
        with self._lock:
            conn = self._connect()
            now = time.time()
            if next_run is None and cron_expression:
                from croniter import croniter

                start_dt = _local_dt(now)
                next_run = croniter(cron_expression, start_dt).get_next(datetime.datetime).timestamp()
            elif next_run is None:
                next_run = now
            cur = conn.execute(
                """INSERT INTO recurring_jobs
                   (name, command, model, priority, timeout, source, tag,
                    resource_profile, interval_seconds, cron_expression, next_run,
                    max_retries, pinned, check_command, max_runs, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    check_command,
                    max_runs,
                    description,
                    now,
                ),
            )
            conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def get_recurring_job(self, rj_id):
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM recurring_jobs WHERE id = ?", (rj_id,)).fetchone()
            return dict(row) if row else None

    def get_recurring_job_by_name(self, name):
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM recurring_jobs WHERE name = ?", (name,)).fetchone()
            return dict(row) if row else None

    def list_recurring_jobs(self):
        with self._lock:
            conn = self._connect()
            rows = conn.execute("SELECT * FROM recurring_jobs ORDER BY priority ASC, name ASC").fetchall()
            return [dict(r) for r in rows]

    def get_due_recurring_jobs(self, now):
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """SELECT * FROM recurring_jobs
                   WHERE enabled = 1 AND next_run <= ?
                   ORDER BY priority ASC, next_run ASC""",
                (now,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_recurring_next_run(self, rj_id, completed_at, job_id=None):
        with self._lock:
            conn = self._connect()
            rj_row = conn.execute("SELECT * FROM recurring_jobs WHERE id = ?", (rj_id,)).fetchone()
            if rj_row is None:
                _log.error("update_recurring_next_run: recurring job id=%d not found (deleted?)", rj_id)
                return
            rj = dict(rj_row)
            cron_expr = rj.get("cron_expression")
            if cron_expr:
                from croniter import croniter

                start_dt = _local_dt(completed_at)
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

    def set_recurring_job_enabled(self, name, enabled):
        with self._lock:
            conn = self._connect()
            if enabled:
                cur = conn.execute(
                    "UPDATE recurring_jobs SET enabled = 1, outcome_reason = NULL WHERE name = ?",
                    (name,),
                )
            else:
                cur = conn.execute(
                    "UPDATE recurring_jobs SET enabled = 0 WHERE name = ?",
                    (name,),
                )
            conn.commit()
            return cur.rowcount > 0

    def disable_recurring_job(self, rj_id, reason):
        """Auto-disable a recurring job and record the reason."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE recurring_jobs SET enabled = 0, outcome_reason = ? WHERE id = ?",
                (reason, rj_id),
            )
            conn.commit()

    def delete_recurring_job(self, name):
        with self._lock:
            conn = self._connect()
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

    def update_recurring_job(self, rj_id, **fields):
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
            "max_retries",
            "check_command",
            "max_runs",
            "outcome_reason",
            "description",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        with self._lock:
            conn = self._connect()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = [*list(updates.values()), rj_id]
            cur = conn.execute(
                f"UPDATE recurring_jobs SET {set_clause} WHERE id = ?",
                values,
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_recurring_job_by_id(self, rj_id):
        """Delete a recurring job by ID with full cascade cleanup."""
        with self._lock:
            conn = self._connect()
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
        event_type,
        recurring_job_id=None,
        job_id=None,
        details=None,
    ):
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT INTO schedule_events
                   (timestamp, event_type, recurring_job_id, job_id, details)
                   VALUES (?, ?, ?, ?, ?)""",
                (time.time(), event_type, recurring_job_id, job_id, json.dumps(details) if details else None),
            )
            conn.commit()

    def get_schedule_events(self, limit=100):
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM schedule_events ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def has_pending_or_running_recurring(self, recurring_job_id):
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT 1 FROM jobs
                   WHERE recurring_job_id = ? AND status IN ('pending', 'running')
                   LIMIT 1""",
                (recurring_job_id,),
            ).fetchone()
            return row is not None

    def has_pending_recurring(self, recurring_job_id):
        """Return True if this recurring job already has a pending (waiting) instance.

        Plain English: Checks whether a follow-up run is already sitting in the queue,
        waiting to start. Used by the scheduler to avoid stacking multiple waiters for
        the same job — one queued follower is enough.
        Decision it drives: If pending exists → skip. If only running → still submit one follower.
        """
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT 1 FROM jobs
                   WHERE recurring_job_id = ? AND status = 'pending'
                   LIMIT 1""",
                (recurring_job_id,),
            ).fetchone()
            return row is not None

    def get_last_successful_run_time(self, recurring_job_id):
        """Return timestamp of most recent successful (exit_code=0) job for a recurring job.

        Uses exit_code=0 (not last_run which includes failures) for AoI accuracy.
        Returns None if the recurring job has never completed successfully.
        """
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """SELECT MAX(completed_at) as last_success
                   FROM jobs
                   WHERE recurring_job_id = ? AND exit_code = 0""",
                (recurring_job_id,),
            ).fetchone()
            if row is None or row["last_success"] is None:
                return None
            return float(row["last_success"])

    def _set_recurring_next_run(self, rj_id, next_run):
        """Update next_run for a recurring job. Single-purpose DB API — no direct _connect() outside this class."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE recurring_jobs SET next_run = ? WHERE id = ?",
                (next_run, rj_id),
            )
            conn.commit()

    def batch_set_recurring_next_runs(self, updates):
        """Update next_run for multiple recurring jobs in a single transaction.

        Accepts a mapping of {recurring_job_id: next_run_timestamp}.
        Uses executemany to reduce round-trips; all rows commit atomically.
        No-op if updates is empty.
        """
        if not updates:
            return
        with self._lock:
            conn = self._connect()
            conn.executemany(
                "UPDATE recurring_jobs SET next_run = ? WHERE id = ?",
                [(next_run, rj_id) for rj_id, next_run in updates.items()],
            )
            conn.commit()
