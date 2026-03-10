"""Dead Letter Queue (DLQ) CRUD for ollama-queue.

Plain English: Jobs that fail permanently land here. This module handles moving
failed jobs to the DLQ, retrying them back into the queue, dismissing resolved
entries, auto-reschedule bookkeeping, and clearing old entries.
"""

import time


class DLQMixin:
    """DLQ lifecycle operations."""

    def move_to_dlq(self, job_id, failure_reason):
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
            conn.execute(
                "UPDATE jobs SET status = 'dead', completed_at = ? WHERE id = ?",
                (time.time(), job_id),
            )
            conn.commit()
            return cur.lastrowid

    def get_dlq_entry(self, dlq_id):
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM dlq WHERE id = ?", (dlq_id,)).fetchone()
            return dict(row) if row else None

    def update_dlq_reschedule(self, dlq_id, rescheduled_job_id, rescheduled_for, reschedule_reasoning=None):
        """Mark a DLQ entry as auto-rescheduled."""
        with self._lock:
            conn = self._connect()
            now = time.time()
            conn.execute(
                """UPDATE dlq SET auto_rescheduled_at = ?,
                   rescheduled_job_id = ?,
                   rescheduled_for = ?,
                   reschedule_reasoning = ?,
                   auto_reschedule_count = COALESCE(auto_reschedule_count, 0) + 1,
                   resolution = 'rescheduled',
                   resolved_at = ?
                   WHERE id = ?""",
                (now, rescheduled_job_id, rescheduled_for, reschedule_reasoning, now, dlq_id),
            )
            conn.commit()

    def mark_dlq_scheduling(self, dlq_id, rescheduled_for, reschedule_reasoning=None):
        """Mark a DLQ entry as being rescheduled (crash-safety marker).

        Does NOT increment auto_reschedule_count or set resolution — those are
        written by update_dlq_reschedule once the job is confirmed created.
        """
        with self._lock:
            conn = self._connect()
            conn.execute(
                """UPDATE dlq SET auto_rescheduled_at = ?,
                   rescheduled_for = ?,
                   reschedule_reasoning = ?
                   WHERE id = ?""",
                (time.time(), rescheduled_for, reschedule_reasoning, dlq_id),
            )
            conn.commit()

    def list_dlq(self, include_resolved=False, unscheduled_only=False):
        with self._lock:
            conn = self._connect()
            if include_resolved:
                rows = conn.execute("SELECT * FROM dlq ORDER BY moved_at DESC").fetchall()
            else:
                where = "WHERE resolution IS NULL"
                if unscheduled_only:
                    where += " AND auto_rescheduled_at IS NULL"
                rows = conn.execute(f"SELECT * FROM dlq {where} ORDER BY moved_at DESC").fetchall()
            return [dict(r) for r in rows]

    def dismiss_dlq_entry(self, dlq_id):
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE dlq SET resolution = 'dismissed', resolved_at = ? WHERE id = ?",
                (time.time(), dlq_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def retry_dlq_entry(self, dlq_id):
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
                    entry["priority"] if entry["priority"] is not None else 5,
                    entry["timeout"] if entry.get("timeout") is not None else 600,
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

    def clear_dlq(self):
        with self._lock:
            conn = self._connect()
            cur = conn.execute("DELETE FROM dlq WHERE resolution IS NOT NULL")
            conn.commit()
            return cur.rowcount
