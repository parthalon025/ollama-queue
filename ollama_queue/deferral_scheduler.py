"""Proactive deferral scheduler — sweeps deferred jobs and resumes or schedules them.

Periodically checks all unscheduled deferred jobs. If a slot is available now
(slot_index == 0), resumes immediately. If a future slot scores well, records
the scheduled time. If a scheduled time has already passed, resumes the job.
"""

import json
import logging
import threading
import time

from ollama_queue.slot_scoring import find_fitting_slot

logger = logging.getLogger(__name__)


class DeferralScheduler:
    """Sweeps deferred jobs and resumes them when conditions are favorable."""

    def __init__(self, db, estimator, load_map_fn):
        self.db = db
        self.estimator = estimator
        self.load_map_fn = load_map_fn
        self._sweep_lock = threading.Lock()

    def sweep(self) -> list[dict]:
        """Check deferred jobs and resume those that can now run.

        Returns a list of dicts with deferral_id and job_id for each resumed job.
        Non-blocking: returns [] immediately if another sweep is in progress.
        """
        if not self._sweep_lock.acquire(blocking=False):
            return []
        try:
            return self._do_sweep()
        finally:
            self._sweep_lock.release()

    def _do_sweep(self) -> list[dict]:
        """Process deferred jobs — resume if conditions cleared."""
        deferred = self.db.list_deferred(unscheduled_only=True)
        if not deferred:
            return []

        resumed = []
        load_map = self.load_map_fn()
        now = time.time()

        for entry in deferred:
            job = self.db.get_job(entry["job_id"])
            if not job or job["status"] != "deferred":
                continue

            # Check if a scheduled time has passed
            if entry.get("scheduled_for") and entry["scheduled_for"] <= now:
                self.db.resume_deferred_job(entry["id"])
                resumed.append({"deferral_id": entry["id"], "job_id": entry["job_id"]})
                logger.info(
                    "Resumed deferred job %s (deferral %s) — scheduled time passed",
                    entry["job_id"],
                    entry["id"],
                )
                continue

            # Try to find a slot
            est = self.estimator.estimate(
                job.get("model", ""),
                job.get("command", ""),
                job.get("resource_profile", "ollama"),
            )

            estimated_slots = max(1, int(est.total_upper / 1800) + 1)
            slot = find_fitting_slot(
                load_map,
                job_vram_needed_gb=0,
                total_vram_gb=24.0,
                estimated_slots=estimated_slots,
                job_model=job.get("model"),
            )

            if slot is None:
                continue

            # If the best slot is NOW (slot_index == 0), resume immediately
            if slot["slot_index"] == 0:
                self.db.resume_deferred_job(entry["id"])
                resumed.append({"deferral_id": entry["id"], "job_id": entry["job_id"]})
                logger.info(
                    "Resumed deferred job %s (deferral %s) — slot available now",
                    entry["job_id"],
                    entry["id"],
                )
            else:
                # Schedule for later
                scoring = json.dumps(
                    {
                        "slot_index": slot["slot_index"],
                        "score": slot["score"],
                        "estimate": {"mean": est.total_mean, "upper": est.total_upper},
                    }
                )
                self.db.update_deferral_schedule(
                    entry["id"],
                    scheduled_for=slot["scheduled_time"],
                    scoring_snapshot=scoring,
                )
                logger.debug(
                    "Scheduled deferred job %s (deferral %s) for slot %d at %.0f",
                    entry["job_id"],
                    entry["id"],
                    slot["slot_index"],
                    slot["scheduled_time"],
                )

        return resumed
