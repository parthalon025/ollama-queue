"""Scheduler: recurring job promotion and schedule rebalancing."""

from __future__ import annotations

import logging
import time

from ollama_queue.db import Database

_log = logging.getLogger(__name__)


class Scheduler:
    """Manages recurring job promotion and schedule rebalancing."""

    def __init__(self, db: Database):
        self.db = db

    def promote_due_jobs(self, now: float | None = None) -> list[int]:
        """Promote due recurring jobs to pending. Coalesces duplicates.

        Returns list of new job IDs created.
        """
        if now is None:
            now = time.time()
        due = self.db.get_due_recurring_jobs(now)
        new_ids = []
        for rj in due:
            if self.db.has_pending_or_running_recurring(rj["id"]):
                self.db.log_schedule_event(
                    "skipped_duplicate",
                    recurring_job_id=rj["id"],
                    details={"name": rj["name"], "reason": "already pending or running"},
                )
                continue
            job_id = self.db.submit_job(
                command=rj["command"],
                model=rj["model"],
                priority=rj["priority"],
                timeout=rj["timeout"],
                source=rj["source"] or rj["name"],
                tag=rj.get("tag"),
                max_retries=rj.get("max_retries", 0),
                resource_profile=rj.get("resource_profile", "ollama"),
                recurring_job_id=rj["id"],
            )
            self.db.log_schedule_event(
                "promoted",
                recurring_job_id=rj["id"],
                job_id=job_id,
                details={"name": rj["name"]},
            )
            new_ids.append(job_id)
            _log.info("Promoted recurring job %r → job #%d", rj["name"], job_id)
        return new_ids

    def update_next_run(
        self, recurring_job_id: int, completed_at: float, job_id: int | None = None
    ) -> None:
        """Update next_run after job completion. Anchors to completed_at."""
        self.db.update_recurring_next_run(recurring_job_id, completed_at, job_id)
        rj = self.db.get_recurring_job(recurring_job_id)
        self.db.log_schedule_event(
            "next_run_updated",
            recurring_job_id=recurring_job_id,
            job_id=job_id,
            details={"name": rj["name"], "next_run": rj["next_run"]},
        )

    def rebalance(self, now: float | None = None) -> list[dict]:
        """Rebalance all enabled recurring jobs to spread load evenly.

        Higher priority jobs get earlier slots. Returns list of change dicts.
        """
        if now is None:
            now = time.time()
        rjs = [r for r in self.db.list_recurring_jobs() if r["enabled"]]
        if not rjs:
            return []

        # Sort by priority ascending (1 = highest = earliest slot)
        rjs.sort(key=lambda r: (r["priority"], r["name"]))

        # Window = shortest interval
        window = min(r["interval_seconds"] for r in rjs)
        n = len(rjs)
        changes = []

        for i, rj in enumerate(rjs):
            old_next_run = rj["next_run"]
            new_next_run = now + (window * i / n)
            conn = self.db._connect()
            conn.execute(
                "UPDATE recurring_jobs SET next_run = ? WHERE id = ?",
                (new_next_run, rj["id"]),
            )
            conn.commit()
            change = {
                "name": rj["name"],
                "old_next_run": old_next_run,
                "new_next_run": new_next_run,
            }
            changes.append(change)
            self.db.log_schedule_event(
                "rebalanced",
                recurring_job_id=rj["id"],
                details=change,
            )
            _log.info(
                "Rebalanced %r: next_run shifted by %.0fs",
                rj["name"],
                new_next_run - (old_next_run or now),
            )

        return changes
