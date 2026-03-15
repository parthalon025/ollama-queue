"""DLQ manager: routes failed jobs to retry queue or dead letter queue.

Plain English: The failure triage desk. When a job fails, this module asks:
"Has it used up all its retry attempts?" If not, it schedules another try with
exponential backoff (wait 1 min, then 2, then 4…). If retries are exhausted,
the job goes to the Dead Letter Queue — a holding area for manual review.

Decision it drives: Retry this job (and how long to wait) or declare it dead?
"""

from __future__ import annotations

import logging
import random
import time

from ollama_queue.db import Database

_log = logging.getLogger(__name__)


class DLQManager:
    """Routes failed jobs to retry or DLQ based on retry budget."""

    def __init__(self, db: Database):
        self.db = db

    def handle_failure(self, job_id: int, failure_reason: str) -> str:
        """Route a failed job. Returns 'retry' or 'dlq'.

        Acquires db._lock for the full decision. Do NOT call this from inside
        an existing db._lock block — use _handle_failure_locked instead to
        preserve the single-lock atomicity guarantee.
        """
        with self.db._lock:
            return self._handle_failure_locked(job_id, failure_reason)

    def _handle_failure_locked(self, job_id: int, failure_reason: str) -> str:
        """Route a failed job — assumes db._lock is already held by the caller.

        Used by executor.py which calls this from inside its own db._lock block.
        The RLock would re-enter handle_failure's lock silently, breaking atomicity.
        """
        job = self.db.get_job(job_id)
        if not job:
            _log.warning("handle_failure: job #%d not found", job_id)
            return "dlq"
        retry_count = job.get("retry_count", 0)
        max_retries = job.get("max_retries", 0)
        if retry_count < max_retries:
            return self._schedule_retry(job_id, retry_count, job=job)
        else:
            return self._move_to_dlq(job_id, failure_reason)

    def _schedule_retry(self, job_id: int, retry_count: int, job: dict | None = None) -> str:
        settings = self.db.get_all_settings()
        base = float(settings.get("retry_backoff_base_seconds") or 60)
        cap = float(settings.get("retry_backoff_cap_seconds") or 3600)

        # Decorrelated jitter: each delay is random in [base, prev_delay * 3]
        # Breaks synchronization between retrying jobs (prevents thundering herd)
        # Use already-fetched job if provided, otherwise re-fetch
        if job is None:
            job = self.db.get_job(job_id)
        prev_delay = job.get("last_retry_delay") or base if job else base
        hi = max(base, prev_delay * 3)  # guard: ensure upper bound >= base
        delay = min(cap, random.uniform(base, hi))  # noqa: S311

        retry_after = time.time() + delay
        self.db._set_job_retry(job_id, retry_after, delay)
        self.db.log_schedule_event(
            "retried",
            job_id=job_id,
            details={"retry_count": retry_count + 1, "retry_after": retry_after, "delay_seconds": delay},
        )
        _log.info("Scheduled retry for job #%d in %.0fs (attempt %d)", job_id, delay, retry_count + 1)
        return "retry"

    def _move_to_dlq(self, job_id: int, failure_reason: str) -> str:
        dlq_id = self.db.move_to_dlq(job_id, failure_reason)
        self.db.log_schedule_event(
            "dlq_moved",
            job_id=job_id,
            details={"dlq_id": dlq_id, "failure_reason": failure_reason},
        )
        _log.warning("Job #%d moved to DLQ: %s", job_id, failure_reason)
        return "dlq"
